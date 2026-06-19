"""
builder.py — Phase 3: Site Builder + GitHub Deployer
=====================================================
Takes a qualified org from the DB, generates all website copy via Gemini,
fills the HTML template, and deploys to GitHub Pages via the GitHub REST API.

This file does NOT deploy automatically — it's called by the Streamlit UI
after the human-in-the-loop approval step. The Streamlit UI imports and
calls build_and_deploy(org_id) directly.

You can also run it standalone for a single org:
    python3 builder.py --org-id 42
    python3 builder.py --dry-run   # generates HTML locally, skips GitHub

Pipeline stages this file moves orgs through:
    qualified → building → built (awaiting UI approval) → deploying → deployed
"""

import os
import re
import json
import time
import base64
import logging
import argparse
import requests
from datetime import datetime
from google import genai

from db import init_db, fetch_one, execute, fetch_all
from config import (
    GEMINI_API_KEY, GEMINI_MODEL, GEMINI_MAX_RETRIES, SLEEP_BETWEEN_LEADS,
    GITHUB_TOKEN, GITHUB_USERNAME, GITHUB_ORG, GITHUB_PAGES_BRANCH,
    PEXELS_API_KEY, FALLBACK_IMAGES, PEXELS_QUERIES,
    TEMPLATE_PATH,
)

# ── Setup ─────────────────────────────────────────────────────────────────────
os.makedirs('logs', exist_ok=True)
os.makedirs('generated', exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('logs/builder.log', mode='a'),
    ]
)
log = logging.getLogger(__name__)

gemini_client = genai.Client(api_key=GEMINI_API_KEY)

# GitHub actor — either the org or the personal account
GH_ACTOR = GITHUB_ORG if GITHUB_ORG else GITHUB_USERNAME
GH_API   = 'https://api.github.com'
GH_HEADERS = {
    'Authorization': f'Bearer {GITHUB_TOKEN}',
    'Accept':        'application/vnd.github+json',
    'X-GitHub-Api-Version': '2022-11-28',
}

# Contents API rejects requests once the base64 payload gets too large
# (observed cutoff is between 48,000 and 50,000 base64 chars). Anything
# at or above this threshold gets routed through the Git Data API instead,
# which has no such limit.
CONTENTS_API_SAFE_LIMIT = 45000

MADE_BY_NAME  = 'GiveBack'
MADE_BY_URL   = f'https://github.com/{GH_ACTOR}'
MADE_BY_EMAIL = 'hello@inovare.site'


# ── Gemini copy generation ─────────────────────────────────────────────────────

def call_gemini(prompt: str, retries: int = GEMINI_MAX_RETRIES) -> str:
    """
    Calls Gemini with retry logic for 429s. Strips markdown fences
    before returning so the caller always gets raw text.
    """
    for attempt in range(retries):
        try:
            resp = gemini_client.models.generate_content(
                model=GEMINI_MODEL,
                contents=prompt,
            )
            text = resp.text.strip()
            # Strip any markdown fences Gemini adds despite being told not to
            text = re.sub(r'^```[a-z]*\n?', '', text)
            text = re.sub(r'\n?```$', '', text)
            return text.strip()
        except Exception as e:
            retry_match = re.search(r'retry in ([0-9.]+)s', str(e))
            if retry_match and attempt < retries - 1:
                wait = float(retry_match.group(1)) + 1
                log.warning(f'Gemini 429 — retrying in {wait:.0f}s')
                time.sleep(wait)
            else:
                log.error(f'Gemini call failed: {e}')
                raise
    raise RuntimeError('Gemini failed after all retries')


def generate_copy(org: dict) -> dict:
    """
    One Gemini call that returns all the text content for the site.
    Returns a dict with every copy key needed to fill the template.

    Framing note: the prompt tells Gemini to write AS the org, not
    ABOUT the org. This is the hard-won trick from the real pipeline —
    "write as them" produces copy that sounds like real people rather
    than AI-generated nonprofit boilerplate.
    """
    name     = org['name']
    city     = org['city']
    category = org['category']
    phone    = org.get('phone') or ''
    address  = org.get('full_address') or f'{city}, WA'

    prompt = f"""You are writing the website copy for {name}, a real {category} based in {city}, WA.
Write as if you ARE {name} — speak in first person plural ("we", "our", "us").
This is a real nonprofit that serves real people. Make it feel human and grounded.

VOICE GUIDE — apply this to every sentence:
WRONG: "We are committed to providing transformative solutions that empower community members."
RIGHT: "We show up every Tuesday and Thursday with hot meals. That's it. Come eat with us."

WRONG: "Our dedicated volunteers leverage their skills to create sustainable impact."
RIGHT: "Our volunteers are neighbors, students, and retirees who show up because they care."

RULES:
- Never use em dashes (— or –). Restructure the sentence instead.
- No corporate words: transformative, empower, leverage, synergy, holistic, impactful, robust.
- No hedging: pretty, usually, kind of, fairly, somewhat.
- Be specific wherever possible. If you mention something measurable, give a number.
- All copy must be appropriate for a multilingual community audience.

Respond ONLY with a valid JSON object — no markdown, no preamble, no trailing commas:

{{
  "meta_description": "One sentence, under 155 characters, plain English description of {name} for search engines.",
  "hero_headline": "3-6 words. Bold, direct, mission statement. What you DO, not who you are.",
  "hero_headline_accent": "3-5 words. The 'for who' or 'since when'. Quieter tone, shown smaller.",
  "hero_subheadline": "2-3 sentences. What the org does, where, and a specific thing someone can do right now. No fluff.",
  "programs_headline": "4-7 words. What your programs actually are. Not 'Our Programs'. Be specific to {category}.",
  "programs_subheadline": "1-2 sentences. Plain explanation of what the programs cover.",
  "programs": [
    {{"title": "Program name", "description": "2-3 sentences about this specific program. What happens, who it's for, any detail that makes it real."}},
    {{"title": "Program name", "description": "2-3 sentences."}},
    {{"title": "Program name", "description": "2-3 sentences."}}
  ],
  "about_headline": "5-8 words. The mission in one line. Specific to {name}.",
  "about_paragraph_1": "4-6 sentences. How {name} started or why it exists. Be specific about {city}. A story or a fact beats a mission statement every time. Cover: founding context, the gap we fill, who we serve.",
  "about_paragraph_2": "3-5 sentences. What day-to-day operations look like. How volunteers help. A concrete outcome we're proud of.",
  "gallery_headline": "4-6 words. What people will see in the photos.",
  "gallery_subheadline": "1 sentence. Caption-style description of the photo set.",
  "gallery_labels": ["label 1", "label 2", "label 3", "label 4", "label 5"],
  "cta_headline": "5-8 words. Urgent, direct call to the reader to get involved.",
  "cta_sub": "1-2 sentences. What getting involved looks like. Specific and welcoming.",
  "footer_tagline": "1 sentence. What {name} does, in plain English, under 120 characters.",
  "people_served": 150,
  "volunteer_count": 25,
  "years_serving": 8,
  "programs_count": 3,
  "founded_year": 2016,
  "donate_url": "https://www.paypal.com/donate",
  "volunteer_url": "#contact",
  "footer_program_titles": ["Title 1", "Title 2", "Title 3"]
}}

For numeric fields (people_served, volunteer_count, years_serving, programs_count, founded_year):
estimate reasonable numbers based on what you know about a {category} in {city}.
It is better to be conservative (low estimates) than to invent impressive numbers.
people_served should match programs_count × a realistic per-program reach."""

    raw = call_gemini(prompt)

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        log.error(f'Copy JSON parse error: {e}\nRaw output: {raw[:400]}')
        raise

    return data


# ── Image sourcing ─────────────────────────────────────────────────────────────

def fetch_pexels_images(category: str, count: int = 5) -> list:
    """
    Fetches images from Pexels. Falls back to the curated FALLBACK_IMAGES
    list if the API key is missing or the request fails.
    Returns a list of image URLs.
    """
    if not PEXELS_API_KEY:
        log.info('No Pexels key — using fallback images')
        return FALLBACK_IMAGES[:count]

    queries = PEXELS_QUERIES.get(category, PEXELS_QUERIES['default'])
    urls = []

    for query in queries:
        if len(urls) >= count:
            break
        try:
            resp = requests.get(
                'https://api.pexels.com/v1/search',
                headers={'Authorization': PEXELS_API_KEY},
                params={'query': query, 'per_page': 3, 'orientation': 'landscape'},
                timeout=6,
            )
            if resp.status_code == 200:
                photos = resp.json().get('photos', [])
                for p in photos:
                    url = p.get('src', {}).get('large2x') or p.get('src', {}).get('large')
                    if url and url not in urls:
                        urls.append(url)
        except Exception as e:
            log.warning(f'Pexels request failed for "{query}": {e}')
        time.sleep(0.3)  # stay under Pexels rate limit (200/hr)

    # Pad with fallbacks if we didn't get enough
    if len(urls) < count:
        for fb in FALLBACK_IMAGES:
            if fb not in urls:
                urls.append(fb)
            if len(urls) >= count:
                break

    return urls[:count]


# ── HTML template filling ──────────────────────────────────────────────────────

def js_escape(s: str) -> str:
    """Escape single quotes for JS string literals. HTML doesn't need this."""
    return s.replace('\\', '\\\\').replace("'", "\\'")


def build_programs_cards(programs: list) -> str:
    """Generates the HTML for the programs grid from the Gemini programs list."""
    icons = [
        # Heart
        '<path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z"/>',
        # Users
        '<path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/>',
        # Leaf
        '<path d="M2 22 16 8"/><path d="M16 8c-2.5-2.5-6-4-9-4 0 3 1.5 6.5 4 9l-1 1c-2.5-2.5-4-6-4-9 0-3 1-5.5 3-7.5C11.5 1 14 2 16 4c2-2 4.5-3 7-3-3 3-3 7-3 9l-1-1c1.5-2 2.5-5 2-7-2.5 0-5.5 1-7 3z"/>',
    ]
    cards = []
    for i, prog in enumerate(programs[:3]):
        icon = icons[i % len(icons)]
        title = prog.get('title', '')
        desc  = prog.get('description', '')
        cards.append(f"""    <div class="prog-card" role="listitem">
      <div class="prog-icon" aria-hidden="true">
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">{icon}</svg>
      </div>
      <h3>{title}</h3>
      <p>{desc}</p>
    </div>""")
    return '\n'.join(cards)


def build_footer_links(program_titles: list) -> str:
    links = []
    for title in program_titles[:4]:
        anchor = '#programs'
        links.append(f'<a href="{anchor}">{title}</a>')
    return '\n'.join(links)


def fill_template(org: dict, copy: dict, images: list) -> str:
    """
    Reads the template and replaces every {{SLOT}} with real content.
    Returns the complete HTML string ready to deploy.
    """
    with open(TEMPLATE_PATH, 'r', encoding='utf-8') as f:
        html = f.read()

    phone_raw = re.sub(r'\D', '', org.get('phone') or '')
    phone_fmt = org.get('phone') or 'Contact us'

    # Apostrophes in org names can break things — escape for the template
    # but NOT over-escape: HTML entities are fine in HTML context
    org_name = org['name'].replace("'", '&#39;')

    slots = {
        # Org data from DB
        'ORG_NAME':        org_name,
        'ORG_TYPE':        org.get('category', 'Nonprofit').title(),
        'CITY':            org.get('city', ''),
        'PHONE_NUMBER':    phone_fmt,
        'PHONE_NUMBER_RAW': phone_raw,
        'FULL_ADDRESS':    org.get('full_address', org.get('city', '')),
        'GOOGLE_MAPS_URL': org.get('google_maps_url', '#'),
        'YEAR':            str(datetime.now().year),

        # Attribution
        'MADE_BY_NAME':  MADE_BY_NAME,
        'MADE_BY_URL':   MADE_BY_URL,
        'MADE_BY_EMAIL': MADE_BY_EMAIL,

        # Gemini copy
        'META_DESCRIPTION':    copy.get('meta_description', ''),
        'HERO_HEADLINE':       copy.get('hero_headline', ''),
        'HERO_HEADLINE_ACCENT': copy.get('hero_headline_accent', ''),
        'HERO_SUBHEADLINE':    copy.get('hero_subheadline', ''),
        'PROGRAMS_HEADLINE':   copy.get('programs_headline', ''),
        'PROGRAMS_SUBHEADLINE': copy.get('programs_subheadline', ''),
        'PROGRAMS_CARDS':      build_programs_cards(copy.get('programs', [])),
        'ABOUT_HEADLINE':      copy.get('about_headline', ''),
        'ABOUT_PARAGRAPH_1':   copy.get('about_paragraph_1', ''),
        'ABOUT_PARAGRAPH_2':   copy.get('about_paragraph_2', ''),
        'GALLERY_HEADLINE':    copy.get('gallery_headline', ''),
        'GALLERY_SUBHEADLINE': copy.get('gallery_subheadline', ''),
        'GALLERY_LABEL_1':     (copy.get('gallery_labels') or [''] * 5)[0],
        'GALLERY_LABEL_2':     (copy.get('gallery_labels') or [''] * 5)[1],
        'GALLERY_LABEL_3':     (copy.get('gallery_labels') or [''] * 5)[2],
        'GALLERY_LABEL_4':     (copy.get('gallery_labels') or [''] * 5)[3],
        'GALLERY_LABEL_5':     (copy.get('gallery_labels') or [''] * 5)[4],
        'CTA_HEADLINE':        copy.get('cta_headline', ''),
        'CTA_SUB':             copy.get('cta_sub', ''),
        'FOOTER_TAGLINE':      copy.get('footer_tagline', ''),
        'FOOTER_PROGRAM_LINKS': build_footer_links(copy.get('footer_program_titles', [])),
        'DONATE_URL':          copy.get('donate_url', '#'),
        'VOLUNTEER_URL':       copy.get('volunteer_url', '#contact'),
        'FOUNDED_YEAR':        str(copy.get('founded_year', '')),

        # Stats (numbers only — the counter animation reads data-target)
        'PEOPLE_SERVED':   str(copy.get('people_served', 100)),
        'VOLUNTEER_COUNT': str(copy.get('volunteer_count', 20)),
        'YEARS_SERVING':   str(copy.get('years_serving', 5)),
        'PROGRAMS_COUNT':  str(copy.get('programs_count', 3)),

        # Images
        'HERO_IMAGE':    images[0] if images else FALLBACK_IMAGES[0],
        'GALLERY_IMAGE_1': images[0] if len(images) > 0 else FALLBACK_IMAGES[0],
        'GALLERY_IMAGE_2': images[1] if len(images) > 1 else FALLBACK_IMAGES[1],
        'GALLERY_IMAGE_3': images[2] if len(images) > 2 else FALLBACK_IMAGES[2],
        'GALLERY_IMAGE_4': images[3] if len(images) > 3 else FALLBACK_IMAGES[3],
        'GALLERY_IMAGE_5': images[4] if len(images) > 4 else FALLBACK_IMAGES[4],
    }

    for key, val in slots.items():
        html = html.replace(f'{{{{{key}}}}}', str(val))

    # Catch any slots we missed — makes debugging easy
    remaining = re.findall(r'{{[^}]+}}', html)
    if remaining:
        log.warning(f'Unfilled slots in template: {remaining}')

    return html


# ── GitHub API deployment ──────────────────────────────────────────────────────

def slugify(name: str) -> str:
    """
    Turns an org name into a valid GitHub repo name.
    "Seattle Food Pantry Network" → "giveback-seattle-food-pantry-network"
    """
    slug = re.sub(r'[^a-zA-Z0-9\s]', '', name).strip().lower()
    slug = re.sub(r'\s+', '-', slug)
    slug = re.sub(r'-+', '-', slug)
    return f'giveback-{slug[:50]}'  # cap at ~60 chars total


def gh_post(endpoint: str, payload: dict) -> dict:
    resp = requests.post(f'{GH_API}{endpoint}', headers=GH_HEADERS, json=payload, timeout=15)
    if not resp.ok:
        print('GITHUB ERROR:', resp.status_code, resp.text[:500])
    resp.raise_for_status()
    return resp.json()


def gh_put(endpoint: str, payload: dict) -> dict:
    resp = requests.put(f'{GH_API}{endpoint}', headers=GH_HEADERS, json=payload, timeout=15)
    if not resp.ok:
        print('GITHUB ERROR:', resp.status_code, resp.text[:500])
    resp.raise_for_status()
    return resp.json()


def gh_patch(endpoint: str, payload: dict) -> dict:
    resp = requests.patch(f'{GH_API}{endpoint}', headers=GH_HEADERS, json=payload, timeout=15)
    if not resp.ok:
        print('GITHUB ERROR:', resp.status_code, resp.text[:500])
    resp.raise_for_status()
    return resp.json()


def gh_get(endpoint: str) -> requests.Response:
    return requests.get(f'{GH_API}{endpoint}', headers=GH_HEADERS, timeout=15)


def create_github_repo(repo_name: str, org_name: str) -> dict:
    """
    Creates a new public GitHub repo under GH_ACTOR.
    Returns the repo data dict from the API.
    MLH GitHub track: uses the GitHub API directly, no CLI.
    """
    payload = {
        'name':        repo_name,
        'description': f'Website for {org_name} — built by GiveBack',
        'private':     False,
        'auto_init':   False,   # we push our own files, don't need a README
    }
    if GITHUB_ORG:
        return gh_post(f'/orgs/{GITHUB_ORG}/repos', payload)
    else:
        return gh_post('/user/repos', payload)


def push_large_file_to_repo(repo_name: str, path: str, content: str, message: str) -> None:
    """
    For files too large for the Contents API (observed cutoff is between
    48,000-50,000 base64 chars — roughly 36-37KB of raw UTF-8 content),
    use the Git Data API instead: create a blob, read the current tree,
    layer a new tree on top with our file added/updated, wrap it in a
    new commit, then fast-forward the branch ref to point at it.

    This is the same end result as push_file_to_repo() (file ends up
    committed on GITHUB_PAGES_BRANCH) but goes through the lower-level
    Git plumbing API, which has no equivalent payload-size restriction.
    """
    # 1. Get the current branch ref (latest commit SHA)
    ref_resp = gh_get(f'/repos/{GH_ACTOR}/{repo_name}/git/ref/heads/{GITHUB_PAGES_BRANCH}')
    ref_resp.raise_for_status()
    latest_commit_sha = ref_resp.json()['object']['sha']

    # 2. Get that commit to find its tree SHA
    commit_resp = requests.get(
        f'{GH_API}/repos/{GH_ACTOR}/{repo_name}/git/commits/{latest_commit_sha}',
        headers=GH_HEADERS, timeout=15
    )
    commit_resp.raise_for_status()
    base_tree_sha = commit_resp.json()['tree']['sha']

    # 3. Create a blob with our file content
    blob_resp = gh_post(f'/repos/{GH_ACTOR}/{repo_name}/git/blobs', {
        'content':  base64.b64encode(content.encode('utf-8')).decode('ascii'),
        'encoding': 'base64',
    })
    blob_sha = blob_resp['sha']

    # 4. Create a new tree with this file added/updated, layered on the
    #    existing tree so we don't disturb any other files in the repo
    tree_resp = gh_post(f'/repos/{GH_ACTOR}/{repo_name}/git/trees', {
        'base_tree': base_tree_sha,
        'tree': [{
            'path': path,
            'mode': '100644',
            'type': 'blob',
            'sha':  blob_sha,
        }],
    })
    new_tree_sha = tree_resp['sha']

    # 5. Create a new commit pointing at the new tree
    new_commit_resp = gh_post(f'/repos/{GH_ACTOR}/{repo_name}/git/commits', {
        'message': message,
        'tree':    new_tree_sha,
        'parents': [latest_commit_sha],
    })
    new_commit_sha = new_commit_resp['sha']

    # 6. Move the branch ref to point at the new commit
    gh_patch(f'/repos/{GH_ACTOR}/{repo_name}/git/refs/heads/{GITHUB_PAGES_BRANCH}', {
        'sha': new_commit_sha,
    })


def push_file_to_repo(repo_name: str, path: str, content: str, message: str) -> None:
    """
    Creates or updates a single file in the repo.

    Routes through the Contents API for small files (the normal, simple
    path) or the Git Data API for large files, since the Contents API
    starts returning a generic GitHub "Whoa there!" 400 once the base64
    payload crosses roughly 48,000-50,000 characters.
    """
    encoded_size = len(base64.b64encode(content.encode('utf-8')))
    if encoded_size >= CONTENTS_API_SAFE_LIMIT:
        log.info(f'  -> {path} is {encoded_size:,} base64 chars, using Git Data API')
        push_large_file_to_repo(repo_name, path, content, message)
        return

    # Check if the file exists so we can include the sha for updates
    check = gh_get(f'/repos/{GH_ACTOR}/{repo_name}/contents/{path}')
    payload = {
        'message': message,
        'content': base64.b64encode(content.encode('utf-8')).decode('ascii'),
        'branch':  GITHUB_PAGES_BRANCH,
    }
    if check.status_code == 200:
        payload['sha'] = check.json()['sha']  # required for updates

    gh_put(f'/repos/{GH_ACTOR}/{repo_name}/contents/{path}', payload)


def enable_github_pages(repo_name: str) -> str:
    """
    Activates GitHub Pages on the main branch via the Pages API.
    Returns the expected live URL (may take 30-60s to go live).
    """
    payload = {
        'source': {
            'branch': GITHUB_PAGES_BRANCH,
            'path':   '/',
        }
    }
    try:
        resp = requests.post(
            f'{GH_API}/repos/{GH_ACTOR}/{repo_name}/pages',
            headers=GH_HEADERS,
            json=payload,
            timeout=15,
        )
        # 201 = created, 409 = already enabled — both are fine
        if resp.status_code not in (201, 409):
            resp.raise_for_status()
        data = resp.json()
        return data.get('html_url') or f'https://{GH_ACTOR}.github.io/{repo_name}/'
    except Exception as e:
        log.warning(f'Pages enable response: {e} — URL will still work after propagation')
        return f'https://{GH_ACTOR}.github.io/{repo_name}/'


def deploy_to_github(repo_name: str, org_name: str, html: str) -> str:
    """
    Pushes built site into the GiveBack repo under built_websites/{slug}/.
    No new repo needed — GiveBack Pages serves everything.
    Returns the live GitHub Pages URL.
    """
    MAIN_REPO = 'GiveBack'
    folder = f'built_websites/{repo_name}'

    log.info(f'  -> Pushing index.html to {folder}/')
    push_file_to_repo(MAIN_REPO, f'{folder}/index.html', html,
                      f'Add site for {org_name}')

    favicon_path = os.path.join(os.path.dirname(TEMPLATE_PATH), 'favicon.svg')
    with open(favicon_path, 'r') as fav:
        push_file_to_repo(MAIN_REPO, f'{folder}/favicon.svg', fav.read(), 'Add favicon')

    readme = f"""# {org_name}

Website built by **GiveBack** — an automated pipeline that finds nonprofits
without a web presence and builds them a free, accessible website.

**Live site:** https://{GH_ACTOR}.github.io/GiveBack/{folder}/

---
*This site was generated and deployed automatically. The nonprofit can claim
ownership and customize it at any time.*
"""
    push_file_to_repo(MAIN_REPO, f'{folder}/README.md', readme, 'Add README')

    live_url = f'https://{GH_ACTOR}.github.io/GiveBack/{folder}/'
    return live_url

# ── Telegram notification ──────────────────────────────────────────────────────

def send_telegram(message: str) -> None:
    """
    Sends a notification to the review Telegram chat.
    Used to ping when a site is ready for human-in-the-loop approval.
    Non-fatal if it fails — the Streamlit UI is the real approval gate.
    """
    from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log.info('Telegram not configured — skipping notification')
        return
    try:
        requests.post(
            f'https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage',
            json={
                'chat_id':    TELEGRAM_CHAT_ID,
                'text':       message,
                'parse_mode': 'HTML',
            },
            timeout=6,
        )
    except Exception as e:
        log.warning(f'Telegram notification failed: {e}')


# ── Core build function (called by Streamlit UI) ───────────────────────────────

def build_site(org_id: int, dry_run: bool = False) -> dict:
    """
    Generates copy + HTML for a single org and saves the HTML to the DB.
    Does NOT deploy — the Streamlit UI calls deploy_site() after human approval.

    Returns a dict with 'html', 'copy', and 'org' keys for the UI to preview.
    """
    org = fetch_one('SELECT * FROM orgs WHERE id = ?', (org_id,))
    if not org:
        raise ValueError(f'Org {org_id} not found')

    log.info(f'Building site for: {org["name"]} ({org["city"]})')
    execute("UPDATE orgs SET pipeline_stage='building' WHERE id=?", (org_id,))

    # Generate all copy
    copy = generate_copy(org)
    log.info(f'  ✓ Copy generated ({len(str(copy))} chars)')

    # Get images
    images = fetch_pexels_images(org.get('category', 'default'), count=5)
    log.info(f'  ✓ {len(images)} images sourced')

    # Fill the template
    html = fill_template(org, copy, images)
    log.info(f'  ✓ Template filled ({len(html):,} chars)')

    # Save to DB and local file so the UI can preview it
    execute(
        "UPDATE orgs SET demo_html=?, demo_built_at=?, pipeline_stage='built' WHERE id=?",
        (html, datetime.now().isoformat(), org_id)
    )

    # Also write locally for easy inspection during dev
    safe_name = re.sub(r'[^a-z0-9]', '_', org['name'].lower())
    local_path = f'generated/{safe_name}.html'
    with open(local_path, 'w', encoding='utf-8') as f:
        f.write(html)
    log.info(f'  ✓ Saved locally to {local_path}')

    # Telegram ping so I know to open the Streamlit UI and review
    send_telegram(
        f'<b>GiveBack</b> — new site ready for review\n\n'
        f'<b>{org["name"]}</b>\n'
        f'{org.get("city", "")} · {org.get("category", "").title()}\n'
        f'{org.get("phone", "no phone")}\n\n'
        f'Open Streamlit to approve and deploy.'
    )

    return {'org': org, 'copy': copy, 'html': html, 'local_path': local_path}


def deploy_site(org_id: int) -> str:
    """
    Called by the Streamlit UI after the human clicks "Approve & Deploy".
    Pushes the already-generated HTML to GitHub and activates Pages.
    Returns the live URL.
    """
    org = fetch_one('SELECT * FROM orgs WHERE id = ?', (org_id,))
    if not org or not org.get('demo_html'):
        raise ValueError(f'Org {org_id} has no built HTML — run build_site() first')

    log.info(f'Deploying: {org["name"]}')
    execute("UPDATE orgs SET pipeline_stage='deploying' WHERE id=?", (org_id,))

    repo_name = slugify(org['name'])
    live_url  = deploy_to_github(repo_name, org['name'], org['demo_html'])

    execute(
        "UPDATE orgs SET demo_url=?, github_repo=?, pipeline_stage='deployed' WHERE id=?",
        (live_url, repo_name, org_id)
    )
    log.info(f'  ✓ Live at {live_url}')

    send_telegram(
        f'<b>GiveBack</b> — site deployed\n\n'
        f'<b>{org["name"]}</b>\n'
        f'<a href="{live_url}">{live_url}</a>'
    )

    return live_url


# ── Batch mode (for running outside the UI) ────────────────────────────────────

def run_batch(limit: int = 5, dry_run: bool = False) -> None:
    """
    Builds sites for the next N hot/warm orgs that haven't been built yet.
    Useful for pre-building a queue before the demo so the UI is snappy.
    """
    init_db()
    orgs = fetch_all(
        """SELECT * FROM orgs
           WHERE pipeline_stage = 'qualified'
             AND lead_tier IN ('hot', 'warm')
           ORDER BY lead_tier ASC, review_count DESC
           LIMIT ?""",
        (limit,)
    )
    if not orgs:
        log.info('No qualified orgs to build — run qualifier.py first.')
        return

    log.info(f'Batch building {len(orgs)} sites (dry_run={dry_run})')
    for i, org in enumerate(orgs, 1):
        log.info(f'[{i}/{len(orgs)}] {org["name"]}')
        try:
            build_site(org['id'], dry_run=dry_run)
            time.sleep(SLEEP_BETWEEN_LEADS)
        except Exception as e:
            log.error(f'Failed to build site for {org["name"]}: {e}')
            execute(
                "UPDATE orgs SET pipeline_stage='build_error', notes=? WHERE id=?",
                (str(e)[:300], org['id'])
            )


# ── CLI entry point ────────────────────────────────────────────────────────────

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='GiveBack site builder')
    parser.add_argument('--org-id',  type=int, help='Build a single org by DB id')
    parser.add_argument('--batch',   type=int, default=5, help='Build N orgs in batch mode (default: 5)')
    parser.add_argument('--dry-run', action='store_true', help='Generate HTML locally, skip GitHub deploy')
    args = parser.parse_args()

    init_db()

    if args.org_id:
        result = build_site(args.org_id, dry_run=args.dry_run)
        log.info(f'Built: {result["local_path"]}')
        if not args.dry_run:
            url = deploy_site(args.org_id)
            log.info(f'Live at: {url}')
    else:
        run_batch(limit=args.batch, dry_run=args.dry_run)
