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
import sys
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

# Windows' default console encoding (cp1252) can't print the ✓ characters
# used throughout the log messages below, which was spamming a
# UnicodeEncodeError on every single log.info() call. Reconfiguring to
# UTF-8 fixes it at the source instead of stripping the ✓ everywhere.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, 'reconfigure'):
        _stream.reconfigure(encoding='utf-8', errors='replace')

# ── Setup ─────────────────────────────────────────────────────────────────────
os.makedirs('logs', exist_ok=True)
os.makedirs('generated', exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('logs/builder.log', mode='a', encoding='utf-8'),
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

# Contents API rejects requests once the request body gets too large
# (observed cutoff is roughly 46,000-50,000 raw bytes). This turned out
# to affect the Git Data API blob endpoint equally — not just Contents
# API — so files over this size route through the chunked-commit
# workaround (push_large_file_to_repo) instead of any single big request.
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


def generate_translations(copy: dict) -> dict:
    """
    Translates the AI-written, user-facing copy fields into Spanish,
    Vietnamese, and Somali. Returns {'es': {...}, 'vi': {...}, 'so': {...}},
    each shaped like a subset of `copy` (same keys, same array lengths).

    Each language is its OWN Gemini call rather than one combined call —
    this keeps each payload small (avoids the JSON-truncation risk flagged
    when this was scoped as a single 4-language call) and means a failure
    in one language degrades gracefully to English for just that language
    instead of taking down the whole build.

    Only translates fields that actually render as visible text on the
    page (hero, about, programs, gallery captions, CTA, footer tagline).
    Numbers, URLs, and founded_year are language-independent and skipped.
    """
    text_fields = {
        'hero_headline':        copy.get('hero_headline', ''),
        'hero_headline_accent': copy.get('hero_headline_accent', ''),
        'hero_subheadline':     copy.get('hero_subheadline', ''),
        'programs_headline':    copy.get('programs_headline', ''),
        'programs_subheadline': copy.get('programs_subheadline', ''),
        'programs':             copy.get('programs', []),
        'about_headline':       copy.get('about_headline', ''),
        'about_paragraph_1':    copy.get('about_paragraph_1', ''),
        'about_paragraph_2':    copy.get('about_paragraph_2', ''),
        'gallery_headline':     copy.get('gallery_headline', ''),
        'gallery_subheadline':  copy.get('gallery_subheadline', ''),
        'gallery_labels':       copy.get('gallery_labels', []),
        'cta_headline':         copy.get('cta_headline', ''),
        'cta_sub':              copy.get('cta_sub', ''),
        'footer_tagline':       copy.get('footer_tagline', ''),
    }

    lang_names = {'es': 'Spanish', 'vi': 'Vietnamese', 'so': 'Somali'}
    translations = {}

    for lang_code, lang_name in lang_names.items():
        prompt = f"""Translate the following nonprofit website copy into {lang_name}.
Keep the same tone: warm, plain-spoken, specific. Not corporate, not stiff.
Keep the exact same JSON structure — same keys, same array lengths, same
field order. Do not add, remove, or rename any field. Do not translate
numbers.

{json.dumps(text_fields, ensure_ascii=False, indent=2)}

Respond ONLY with the translated JSON object — no markdown, no preamble, no trailing commas."""

        try:
            raw = call_gemini(prompt)
            translations[lang_code] = json.loads(raw)
            log.info(f'  ✓ {lang_name} translation OK')
        except json.JSONDecodeError as e:
            log.error(f'{lang_name} translation JSON parse error: {e} — falling back to English for this language')
            translations[lang_code] = text_fields
        except Exception as e:
            log.error(f'{lang_name} translation call failed: {e} — falling back to English for this language')
            translations[lang_code] = text_fields
        time.sleep(0.5)  # stay polite to Gemini between back-to-back calls

    return translations


# ── Image sourcing ─────────────────────────────────────────────────────────────

def fetch_pexels_images(category: str, count: int = 8) -> list:
    """
    Fetches images from Pexels. Falls back to the curated FALLBACK_IMAGES
    list if the API key is missing or the request fails.
    Returns a list of image URLs.

    count defaults to 8: 1 hero + 2 about-section + 5 gallery slots, each
    needing a UNIQUE image (see fill_template()'s _img() helper below).
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


def _img(images: list, idx: int) -> str:
    """
    Returns a unique image for a given slot index. Pulls from the sourced
    `images` list first; if that list is shorter than needed, falls back
    to FALLBACK_IMAGES, wrapping with modulo so it never throws an
    IndexError even if FALLBACK_IMAGES is shorter than expected.

    This is the fix for the hero/about/gallery image-repeat bug: every
    slot now gets its own index instead of multiple slots reading
    images[0].
    """
    if idx < len(images):
        return images[idx]
    return FALLBACK_IMAGES[idx % len(FALLBACK_IMAGES)]


def build_programs_cards(programs: list) -> str:
    """
    Generates the HTML for the programs grid from the Gemini programs list.
    h3/p carry data-i18n-ai="programs_{i}_title"/"programs_{i}_desc" so the
    language switcher can swap these in along with the rest of the
    AI-generated copy — see _build_ai_i18n_lang() in fill_template().
    """
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
      <h3 data-i18n-ai="programs_{i}_title">{title}</h3>
      <p data-i18n-ai="programs_{i}_desc">{desc}</p>
    </div>""")
    return '\n'.join(cards)


def build_footer_links(program_titles: list) -> str:
    links = []
    for title in program_titles[:4]:
        anchor = '#programs'
        links.append(f'<a href="{anchor}">{title}</a>')
    return '\n'.join(links)


def _build_ai_i18n_lang(lang_copy: dict) -> dict:
    """
    Flattens one language's copy dict (English `copy`, or one entry from
    `translations`) into the flat key structure the page's JS reads —
    matching the data-i18n-ai="KEY" attributes in template/index.html.
    Missing fields degrade to empty string rather than raising, so a
    partially-translated language still renders instead of breaking.
    """
    flat = {
        'hero_headline':        lang_copy.get('hero_headline', ''),
        'hero_headline_accent': lang_copy.get('hero_headline_accent', ''),
        'hero_subheadline':     lang_copy.get('hero_subheadline', ''),
        'programs_headline':    lang_copy.get('programs_headline', ''),
        'programs_subheadline': lang_copy.get('programs_subheadline', ''),
        'about_headline':       lang_copy.get('about_headline', ''),
        'about_paragraph_1':    lang_copy.get('about_paragraph_1', ''),
        'about_paragraph_2':    lang_copy.get('about_paragraph_2', ''),
        'gallery_headline':     lang_copy.get('gallery_headline', ''),
        'gallery_subheadline':  lang_copy.get('gallery_subheadline', ''),
        'cta_headline':         lang_copy.get('cta_headline', ''),
        'cta_sub':              lang_copy.get('cta_sub', ''),
        'footer_tagline':       lang_copy.get('footer_tagline', ''),
    }
    programs = lang_copy.get('programs', [])
    for i in range(3):
        prog = programs[i] if i < len(programs) else {}
        flat[f'programs_{i}_title'] = prog.get('title', '')
        flat[f'programs_{i}_desc']  = prog.get('description', '')
    labels = lang_copy.get('gallery_labels', [])
    for i in range(5):
        flat[f'gallery_label_{i + 1}'] = labels[i] if i < len(labels) else ''
    return flat


def fill_template(org: dict, copy: dict, images: list, translations: dict) -> str:
    """
    Reads the template and replaces every {{SLOT}} with real content.
    Returns the complete HTML string ready to deploy.

    `translations` is the dict returned by generate_translations() —
    {'es': {...}, 'vi': {...}, 'so': {...}} — used to build the AI_I18N
    JS object that the page's language switcher reads at runtime so
    AI-generated content (not just static UI chrome) actually translates.
    """
    with open(TEMPLATE_PATH, 'r', encoding='utf-8') as f:
        html = f.read()

    phone_raw = re.sub(r'\D', '', org.get('phone') or '')
    phone_fmt = org.get('phone') or 'Contact us'

    # Apostrophes in org names can break things — escape for the template
    # but NOT over-escape: HTML entities are fine in HTML context
    org_name = org['name'].replace("'", '&#39;')

    # Build the AI_I18N object the page's JS reads to swap AI-generated
    # content (hero, about, programs, gallery captions, CTA, footer
    # tagline) when the language switcher is clicked. json.dumps() is
    # used (not manual string concat) so apostrophes/quotes in the
    # AI-written copy can never break the embedded script — and the
    # </ escape below prevents a stray "</script>" substring in any
    # language's text from prematurely closing the <script> tag.
    ai_i18n = {
        'en': _build_ai_i18n_lang(copy),
        'es': _build_ai_i18n_lang(translations.get('es', {})),
        'vi': _build_ai_i18n_lang(translations.get('vi', {})),
        'so': _build_ai_i18n_lang(translations.get('so', {})),
    }
    ai_i18n_json = json.dumps(ai_i18n, ensure_ascii=False).replace('</', '<\\/')

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

        # AI multilingual content (consumed by the page's applyLang() JS).
        # Embedded inline rather than as a separate file — now that
        # large files route through the chunked-commit workaround, total
        # page size no longer matters, so there's no upside to the extra
        # moving parts a separate fetched file added.
        'AI_I18N_JSON': ai_i18n_json,

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

        # Images — each slot gets its OWN unique index (0-7) so hero,
        # both about-section photos, and all 5 gallery images are
        # guaranteed distinct. See _img() above for the fallback/wrap
        # behavior. NOTE: template/index.html must use {{ABOUT_IMAGE_A}}
        # and {{ABOUT_IMAGE_B}} for the about-section <img> tags (not
        # {{GALLERY_IMAGE_1}}/{{GALLERY_IMAGE_2}}) or they'll render
        # the same photo as the gallery grid regardless of this fix.
        'HERO_IMAGE':      _img(images, 0),
        'ABOUT_IMAGE_A':   _img(images, 1),
        'ABOUT_IMAGE_B':   _img(images, 2),
        'GALLERY_IMAGE_1': _img(images, 3),
        'GALLERY_IMAGE_2': _img(images, 4),
        'GALLERY_IMAGE_3': _img(images, 5),
        'GALLERY_IMAGE_4': _img(images, 6),
        'GALLERY_IMAGE_5': _img(images, 7),
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


def push_small_file(repo_name: str, path: str, content: str, message: str) -> None:
    """
    The plain, single-request Contents API push. Only safe for content
    confirmed to encode under CONTENTS_API_SAFE_LIMIT — callers should
    go through push_file_to_repo(), which checks size and routes here
    or to the chunked path automatically.
    """
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


def chunk_text(content: str, max_chars: int = 38000) -> list:
    """
    Splits a string into pieces that are each safely under max_chars when
    UTF-8 encoded — small enough to clear CONTENTS_API_SAFE_LIMIT with
    headroom. Splits on the encoded bytes (not the raw character count)
    so a chunk boundary can never land in the middle of a multi-byte
    UTF-8 character.
    """
    encoded = content.encode('utf-8')
    chunks = []
    start = 0
    while start < len(encoded):
        end = min(start + max_chars, len(encoded))
        # Back off until we're not splitting a multi-byte UTF-8 sequence
        while end < len(encoded) and (encoded[end] & 0xC0) == 0x80:
            end -= 1
        chunks.append(encoded[start:end].decode('utf-8'))
        start = end
    return chunks


def push_large_file_to_repo(repo_name: str, path: str, content: str, message: str) -> None:
    """
    KNOWN GITHUB BUG / QUIRK (discovered during GiveBack development,
    June 2026): the GitHub Contents API — AND the Git Data API blob
    endpoint — both reject requests with a generic, non-JSON "Whoa
    there!" 400 error page once the request body crosses roughly
    46,000-50,000 raw bytes. This is NOT GitHub's documented size limit
    (their docs say up to ~100MB via Git Data API, ~1MB via Contents
    API). It affects every GitHub REST write endpoint we tried — Git
    Data API blob creation included, confirmed the hard way today —
    so it looks like an edge/WAF-layer rule, not an API-layer one.

    Workaround: since plain Contents API pushes are confirmed reliable
    under ~38-40KB, large files get split into multiple small commits,
    stored as numbered chunk files (index.part0.html.txt, index.part1...)
    alongside a tiny real index.html that fetches and reassembles them
    client-side on load. Every individual API call stays inside the
    known-safe zone regardless of the total page size.
    """
    chunks = chunk_text(content, max_chars=38000)
    log.info(f'  -> {path} is large ({len(content):,} chars), splitting into {len(chunks)} chunk(s)')

    base, ext = os.path.splitext(path)
    chunk_paths = []
    for i, chunk in enumerate(chunks):
        chunk_path = f'{base}.part{i}{ext}.txt'
        push_small_file(repo_name, chunk_path, chunk, f'{message} (part {i+1}/{len(chunks)})')
        chunk_paths.append(os.path.basename(chunk_path))

    # The real index.html is a tiny loader that fetches each chunk (as
    # plain text, same-origin, no CORS issues on GitHub Pages) and
    # writes the reassembled page in place. Judges and visitors see a
    # normal, fully-rendered page — the splitting is invisible.
    loader_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Loading…</title>
<style>html,body{{margin:0;background:#0B1D3A;height:100%}}</style>
</head>
<body>
<script>
(async function() {{
  const parts = {json.dumps(chunk_paths)};
  let html = '';
  for (const p of parts) {{
    const res = await fetch(p);
    html += await res.text();
  }}
  document.open();
  document.write(html);
  document.close();
}})();
</script>
</body>
</html>"""
    push_small_file(repo_name, path, loader_html, message)


def push_file_to_repo(repo_name: str, path: str, content: str, message: str) -> None:
    """
    Creates or updates a single file in the repo.

    Routes through the plain Contents API for small files, or the
    chunked-commit workaround for large files — see the comment in
    push_large_file_to_repo() for why this split exists.
    """
    encoded_size = len(base64.b64encode(content.encode('utf-8')))
    if encoded_size >= CONTENTS_API_SAFE_LIMIT:
        push_large_file_to_repo(repo_name, path, content, message)
        return

    push_small_file(repo_name, path, content, message)


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


def wait_for_pages_live(url: str, timeout: int = 45, interval: int = 3) -> bool:
    """
    Polls a freshly-deployed GitHub Pages URL until it stops 404ing.
    GitHub Pages typically takes 10-30 seconds to propagate a fresh
    push, so opening the link immediately after deploy_site() returns
    often shows a 404 even though the deploy itself succeeded.

    Returns True once the page is live, or False if it's still not up
    after `timeout` seconds (caller can decide what to tell the user
    in that case — the link will still go live shortly after).
    """
    elapsed = 0
    while elapsed < timeout:
        try:
            resp = requests.get(url, timeout=10)
            if resp.status_code == 200:
                return True
        except requests.RequestException:
            pass  # transient DNS/connection hiccups during propagation are normal
        time.sleep(interval)
        elapsed += interval
    return False

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

    # Translate the AI-written copy into ES/VI/SO — separate Gemini call
    # per language so a malformed response in one doesn't break the others
    translations = generate_translations(copy)
    log.info(f'  ✓ Translations generated (ES/VI/SO)')

    # Get images — 8 needed: 1 hero + 2 about + 5 gallery, each slot unique
    images = fetch_pexels_images(org.get('category', 'default'), count=8)
    log.info(f'  ✓ {len(images)} images sourced')

    # Fill the template
    html = fill_template(org, copy, images, translations)
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

    Waits for GitHub Pages to actually finish propagating before
    returning, so the URL handed back is ready to open immediately
    instead of 404ing for the first 10-20 seconds after a fresh push.
    """
    org = fetch_one('SELECT * FROM orgs WHERE id = ?', (org_id,))
    if not org or not org.get('demo_html'):
        raise ValueError(f'Org {org_id} has no built HTML — run build_site() first')

    log.info(f'Deploying: {org["name"]}')
    execute("UPDATE orgs SET pipeline_stage='deploying' WHERE id=?", (org_id,))

    repo_name = slugify(org['name'])
    live_url  = deploy_to_github(repo_name, org['name'], org['demo_html'])

    log.info('  -> Waiting for GitHub Pages to finish propagating...')
    is_live = wait_for_pages_live(live_url)
    if is_live:
        log.info('  ✓ Confirmed live')
    else:
        log.warning('  ⚠ Still propagating after 45s — link will work shortly')

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
