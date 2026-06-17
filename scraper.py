"""
scraper.py — Phase 1: Nonprofit Discovery
==========================================
Searches Google Maps (via Outscraper) for community orgs across all
SEARCH_QUERIES × TARGET_CITIES combos defined in config.py.

The key filter: we only keep results where the `site` field is empty.
That's our entire qualification criteria at this stage — no website = they need us.

Deduplicates on google_place_id, so re-running never creates dupes.
Cooldown window (SCRAPE_COOLDOWN_DAYS) prevents hammering the same
search combo more than once a month.

Run this first, then qualifier.py, then builder.py (via the Streamlit UI).

Cron (if you want it running automatically on a server):
    0 6 * * 1  cd /path/to/giveback && python3 scraper.py >> logs/scraper.log 2>&1
"""

import os
import json
import time
import logging
import sys
from datetime import datetime, timezone, timedelta
from outscraper import ApiClient

from db import init_db, fetch_one, fetch_all, execute, get_conn
from config import (
    OUTSCRAPER_API_KEY,
    SEARCH_QUERIES,
    TARGET_CITIES,
    RESULTS_PER_SEARCH,
    SCRAPE_COOLDOWN_DAYS,
    MIN_REVIEWS_FOR_HOT,
    MIN_REVIEWS_FOR_WARM,
)

# ── Setup ─────────────────────────────────────────────────────────────────────
os.makedirs('logs', exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('logs/scraper.log', mode='a'),
    ]
)
log = logging.getLogger(__name__)

client = ApiClient(api_key=OUTSCRAPER_API_KEY)


# ── Helpers ───────────────────────────────────────────────────────────────────

def was_recently_scraped(query: str, city: str) -> bool:
    """
    Returns True if we already ran this exact query+city combo within
    the cooldown window. Stops us from wasting Outscraper credits on
    searches we just did.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=SCRAPE_COOLDOWN_DAYS)
    # SQLite stores datetimes as text in ISO format, so string comparison works fine
    cutoff_str = cutoff.strftime('%Y-%m-%d %H:%M:%S')
    row = fetch_one(
        """
        SELECT 1 FROM scrape_runs
        WHERE query = ? AND city = ? AND status = 'success' AND ran_at > ?
        """,
        (query, city, cutoff_str),
    )
    return row is not None


def safe_int(val, default=0) -> int:
    # Outscraper sometimes sends reviews as a string or None
    try:
        return int(val) if val is not None else default
    except (TypeError, ValueError):
        return default


def extract_photos(raw_photos) -> list:
    """Pull up to 6 image URLs from the Outscraper photos_sample field."""
    if not raw_photos:
        return []
    urls = [p.get('image_url', '') for p in raw_photos[:6]]
    return [u for u in urls if u]


def assign_tier(review_count: int) -> str:
    """
    Without a website to screenshot we can't do the full AI scoring pass
    from the real pipeline. Tier is based purely on review count — more
    reviews means a more established org that's worth prioritizing.
    """
    if review_count >= MIN_REVIEWS_FOR_HOT:
        return 'hot'
    if review_count >= MIN_REVIEWS_FOR_WARM:
        return 'warm'
    return 'cold'


def org_type_from_query(query: str) -> str:
    """
    Maps the search query string to a rough category label.
    Used for image selection in the builder — keeps things organized.
    """
    q = query.lower()
    if 'food' in q or 'meal' in q or 'soup' in q or 'fridge' in q:
        return 'food pantry'
    if 'shelter' in q and ('home' in q or 'transit' in q):
        return 'homeless shelter'
    if 'animal' in q or 'pet' in q or 'rescue' in q:
        return 'animal shelter'
    if 'water' in q or 'watershed' in q or 'river' in q or 'environment' in q:
        return 'environmental'
    if 'youth' in q or 'after school' in q or 'mentor' in q:
        return 'youth'
    return 'default'


# ── Core scrape ───────────────────────────────────────────────────────────────

def scrape_one(query: str, city: str) -> None:
    """
    Runs one Outscraper search, filters for no-website results,
    and inserts them into the orgs table.
    """
    if was_recently_scraped(query, city):
        log.info(f'SKIP  "{query}" in {city} — already scraped within {SCRAPE_COOLDOWN_DAYS}d')
        return

    log.info(f'START "{query}" in {city}')

    try:
        raw = client.google_maps_search(
            f'{query} in {city}',
            limit=RESULTS_PER_SEARCH,
            language='en',
            fields=[
                'name', 'phone', 'site', 'city', 'state', 'full_address',
                'category', 'rating', 'reviews', 'place_id',
                'google_id', 'business_status', 'photos_sample',
            ],
        )

        # Outscraper returns list-of-lists (one inner list per query).
        # We always send one query at a time so unwrap safely.
        if raw and isinstance(raw[0], list):
            results: list = raw[0]
        else:
            results = raw or []

        # City/state fallback for when Outscraper doesn't return them
        city_parts = city.rsplit(' ', 1)
        fallback_city  = city_parts[0] if len(city_parts) == 2 else city
        fallback_state = city_parts[1] if len(city_parts) == 2 else ''

        category_label = org_type_from_query(query)
        new_count   = 0
        dupe_count  = 0
        skip_count  = 0  # has a website — not our target

        with get_conn() as conn:
            for r in results:
                # Permanently closed listings are useless to us
                if r.get('business_status') == 'CLOSED_PERMANENTLY':
                    skip_count += 1
                    continue

                place_id = r.get('place_id') or r.get('google_id')
                if not place_id:
                    skip_count += 1
                    continue

                # ── The core filter: skip anyone who already has a website ──
                # This is what makes the whole pipeline valid — we are only
                # targeting orgs that genuinely don't have a web presence.
                site = (r.get('site') or '').strip()
                if site:
                    skip_count += 1
                    continue

                # Deduplicate on place_id
                existing = conn.execute(
                    'SELECT id FROM orgs WHERE google_place_id = ?', (place_id,)
                ).fetchone()
                if existing:
                    dupe_count += 1
                    continue

                reviews      = safe_int(r.get('reviews'))
                tier         = assign_tier(reviews)
                photos       = extract_photos(r.get('photos_sample'))
                maps_url     = f'https://www.google.com/maps/place/?q=place_id:{place_id}'

                conn.execute("""
                    INSERT INTO orgs (
                        name, phone, city, state, full_address,
                        category, rating, review_count,
                        google_place_id, google_maps_url,
                        photos, has_website, lead_tier,
                        pipeline_stage
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, 'scraped')
                """, (
                    r.get('name', '').strip(),
                    r.get('phone', ''),
                    r.get('city')  or fallback_city,
                    r.get('state') or fallback_state,
                    r.get('full_address', ''),
                    category_label,
                    r.get('rating'),
                    reviews,
                    place_id,
                    maps_url,
                    json.dumps(photos),
                ))
                new_count += 1

            # Log this run in the same transaction
            conn.execute("""
                INSERT INTO scrape_runs (query, city, records_returned, new_records, dupes_skipped, status)
                VALUES (?, ?, ?, ?, ?, 'success')
            """, (query, city, len(results), new_count, dupe_count))

            conn.commit()

        log.info(
            f'DONE  "{query}" in {city} — '
            f'{new_count} new, {dupe_count} dupes, {skip_count} had websites (skipped)'
        )

        # Pause between Outscraper calls — stays polite and avoids rate limits
        time.sleep(2)

    except Exception as exc:
        log.error(f'ERROR "{query}" in {city} — {exc}')
        execute(
            "INSERT INTO scrape_runs (query, city, status) VALUES (?, ?, ?)",
            (query, city, f'error: {str(exc)[:200]}'),
        )


# ── Entry point ───────────────────────────────────────────────────────────────

def run():
    log.info('=' * 60)
    log.info('GiveBack Scraper — Phase 1')
    log.info('=' * 60)

    init_db()  # no-op if tables already exist

    total_before = fetch_one('SELECT COUNT(*) as c FROM orgs')['c']

    for city in TARGET_CITIES:
        for query in SEARCH_QUERIES:
            scrape_one(query, city)

    total_after = fetch_one('SELECT COUNT(*) as c FROM orgs')['c']
    new_total   = total_after - total_before

    log.info('=' * 60)
    log.info(f'Scrape complete. {new_total} new orgs added. Total in DB: {total_after}')

    # Quick breakdown by tier so we know what we're working with
    tiers = fetch_all('SELECT lead_tier, COUNT(*) as c FROM orgs GROUP BY lead_tier')
    for t in tiers:
        log.info(f'  {t["lead_tier"]:6s}: {t["c"]} orgs')
    log.info('=' * 60)


if __name__ == '__main__':
    run()
