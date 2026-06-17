"""
qualifier.py — Phase 2: Lead Qualification
===========================================
Reads orgs with pipeline_stage = 'scraped' and decides which ones
are worth building a site for.

Without a website to screenshot (that's the whole point — they don't have one),
qualification is based on what the Maps listing tells us:
  - Do they have a phone number? (real, reachable org)
  - Do they have reviews? (people know about them, they're active)
  - Is the name clean enough to build a site for?

Tier assignment:
  hot  — has phone + reviews >= MIN_REVIEWS_FOR_HOT  → build these first
  warm — has phone OR some reviews                    → build after hot
  cold — no phone, no reviews                         → skip for now

Orgs that fail basic sanity checks (no name, duplicate entries, etc.)
get flagged with pipeline_stage = 'rejected' so they don't clog the queue.

Run after scraper.py:
    python3 qualifier.py
"""

import re
import logging
import sys
from db import init_db, fetch_all, execute, fetch_one
from config import MIN_REVIEWS_FOR_HOT, MIN_REVIEWS_FOR_WARM

# ── Setup ─────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('logs/qualifier.log', mode='a'),
    ]
)
log = logging.getLogger(__name__)


# ── Sanity checks ─────────────────────────────────────────────────────────────

# These patterns show up in Maps results but aren't real orgs —
# things like "Food Pantry Hours" as a business name, or location pins
# that got scraped as if they were organizations.
JUNK_NAME_PATTERNS = [
    r'^\d+$',                       # pure numbers
    r'^(hours|location|directions)$',
    r'(\.com|\.org|\.net)\s*$',     # URL accidentally used as name
]

def is_junk_name(name: str) -> bool:
    if not name or len(name.strip()) < 3:
        return True
    n = name.strip().lower()
    for pattern in JUNK_NAME_PATTERNS:
        if re.search(pattern, n):
            return True
    return False


def has_valid_phone(phone: str) -> bool:
    if not phone:
        return False
    # Strip everything except digits and check length
    digits = re.sub(r'\D', '', phone)
    return len(digits) >= 10


# ── Tier logic ────────────────────────────────────────────────────────────────

def assign_tier(org: dict) -> str:
    """
    Simple decision tree. No AI needed here — the Maps data is enough
    to sort orgs into rough priority buckets.
    """
    reviews = org.get('review_count') or 0
    phone   = org.get('phone') or ''
    has_phone = has_valid_phone(phone)

    # Hot: established org with a real phone line and community presence
    if has_phone and reviews >= MIN_REVIEWS_FOR_HOT:
        return 'hot'

    # Warm: either has a phone or has some reviews, but not both
    if has_phone or reviews >= MIN_REVIEWS_FOR_WARM:
        return 'warm'

    # Cold: almost no data — still valid but lower priority
    return 'cold'


# ── Per-org processing ────────────────────────────────────────────────────────

def process_org(org: dict) -> None:
    org_id = org['id']
    name   = org.get('name', '')

    # Reject junk entries outright — these waste builder time
    if is_junk_name(name):
        execute(
            "UPDATE orgs SET pipeline_stage='rejected', notes=? WHERE id=?",
            ('rejected: junk name', org_id)
        )
        log.info(f'  REJECT [{org_id}] "{name}" — junk name')
        return

    tier = assign_tier(org)

    execute(
        "UPDATE orgs SET lead_tier=?, pipeline_stage='qualified' WHERE id=?",
        (tier, org_id)
    )

    # Small log line that matches what the real pipeline does —
    # easy to read when tailing the log file
    phone_flag = '📞' if has_valid_phone(org.get('phone') or '') else '  '
    reviews    = org.get('review_count') or 0
    log.info(
        f'  [{tier.upper():4s}] {phone_flag} {name[:45]:45s} '
        f'({org.get("city", "")}) — {reviews} reviews'
    )


# ── Main ──────────────────────────────────────────────────────────────────────

def run(batch_size: int = 500) -> None:
    init_db()

    orgs = fetch_all(
        "SELECT * FROM orgs WHERE pipeline_stage = 'scraped' LIMIT ?",
        (batch_size,)
    )

    if not orgs:
        log.info('No scraped orgs to qualify — run scraper.py first.')
        return

    log.info('=' * 60)
    log.info(f'Qualifier — {len(orgs)} orgs to process')
    log.info('=' * 60)

    for org in orgs:
        try:
            process_org(org)
        except Exception as e:
            log.error(f'Failed on org {org.get("id")}: {e}')

    # Summary so we know what the builder queue looks like
    counts = fetch_all("""
        SELECT pipeline_stage, lead_tier, COUNT(*) as c
        FROM orgs
        WHERE pipeline_stage IN ('qualified', 'rejected')
        GROUP BY pipeline_stage, lead_tier
        ORDER BY pipeline_stage, lead_tier
    """)

    log.info('─' * 60)
    log.info('Qualification complete:')
    for row in counts:
        stage = row['pipeline_stage']
        tier  = row['lead_tier'] or '—'
        log.info(f'  {stage:12s} / {tier:5s}: {row["c"]}')

    hot_count = fetch_one(
        "SELECT COUNT(*) as c FROM orgs WHERE pipeline_stage='qualified' AND lead_tier='hot'"
    )['c']
    log.info(f'\n  → {hot_count} hot orgs ready for builder.py')
    log.info('=' * 60)


if __name__ == '__main__':
    run()
