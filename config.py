"""
config.py — GiveBack Pipeline
All settings live here. Actual secrets go in .env — this file just reads them.
"""

import os
from dotenv import load_dotenv

load_dotenv()

# ── API Keys ─────────────────────────────────────────────────────────────────
OUTSCRAPER_API_KEY  = os.environ.get('OUTSCRAPER_API_KEY', '')
GEMINI_API_KEY      = os.environ.get('GEMINI_API_KEY', '')
GITHUB_TOKEN        = os.environ.get('GITHUB_TOKEN', '')          # repo scope
TELEGRAM_BOT_TOKEN  = os.environ.get('TELEGRAM_BOT_TOKEN', '')
TELEGRAM_CHAT_ID    = os.environ.get('TELEGRAM_CHAT_ID', '')      # new chat for this project
PEXELS_API_KEY      = os.environ.get('PEXELS_API_KEY', '')        # free at pexels.com/api

# ── GitHub config ─────────────────────────────────────────────────────────────
GITHUB_USERNAME     = os.environ.get('GITHUB_USERNAME', '')       # your GH username
GITHUB_ORG          = os.environ.get('GITHUB_ORG', '')            # optional org, else uses username
GITHUB_PAGES_BRANCH = 'main'

# ── Gemini ────────────────────────────────────────────────────────────────────
GEMINI_MODEL        = 'gemini-2.5-flash-lite'                 # free tier: ~1000 RPD / 15 RPM
GEMINI_MAX_RETRIES  = 3

# ── Pipeline behavior ─────────────────────────────────────────────────────────
RESULTS_PER_SEARCH  = 20    # how many results to pull per Outscraper query
SCRAPE_COOLDOWN_DAYS = 30   # don't re-scrape the same search within this window
SLEEP_BETWEEN_LEADS  = 1.5  # seconds between Gemini calls — stays under rate limit

# ── Database ──────────────────────────────────────────────────────────────────
# SQLite so anyone can clone and run this without any DB setup
DB_PATH = os.path.join(os.path.dirname(__file__), 'giveback.db')

# ── Search targets ────────────────────────────────────────────────────────────
# These are the search strings sent to Outscraper. Each one targets a real
# category of community org that tends to have no web presence.
#
# Hack3/Xylem track: water/environment queries are included alongside food/shelter
# so the project visibly addresses environmental and community infrastructure.

SEARCH_QUERIES = [
    # food access
    "food pantry",
    "food bank",
    "community fridge",
    "free meals nonprofit",
    "soup kitchen",

    # housing & social services
    "homeless shelter nonprofit",
    "transitional housing nonprofit",
    "family resource center",

    # environment & water — Xylem sponsor track
    "watershed conservation nonprofit",
    "water access nonprofit",
    "river cleanup organization",
    "environmental nonprofit",
    "community garden nonprofit",

    # animals
    "animal shelter nonprofit",
    "pet rescue nonprofit",

    # youth & community
    "after school program nonprofit",
    "community center nonprofit",
    "youth mentorship nonprofit",
]

# Cities to search. PNW-first since that's our base, but a wider list makes
# the demo look more scalable when presenting.
TARGET_CITIES = [
    "Seattle WA",
    "Tacoma WA",
    "Bellevue WA",
    "Everett WA",
    "Spokane WA",
    "Portland OR",
    "Eugene OR",
]

# ── Qualifier thresholds ──────────────────────────────────────────────────────
# We can't screenshot these orgs (they have no site), so qualification is
# purely based on data quality from the Maps listing.
MIN_REVIEWS_FOR_HOT  = 5    # >= 5 reviews = people know about them, real org
MIN_REVIEWS_FOR_WARM = 1    # at least 1 review = confirmed active

# ── Templates ─────────────────────────────────────────────────────────────────
# All generated sites use a single unified nonprofit template.
# No per-category branching needed — the AI fills in the mission/programs copy.
TEMPLATE_PATH = os.path.join(os.path.dirname(__file__), 'template', 'index.html')

# Supported languages for the i18n toggle (Fidelity DEI track)
# Each code maps to a display label shown in the language switcher
I18N_LANGUAGES = {
    'en': 'English',
    'es': 'Español',
    'vi': 'Tiếng Việt',
    'so': 'Soomaali',
}

# ── Pexels image queries by org type ─────────────────────────────────────────
# Searched at build time. Falls back to FALLBACK_IMAGES if API is unavailable.
PEXELS_QUERIES = {
    'food pantry':          ['food pantry volunteers', 'community food bank', 'food donation volunteers'],
    'food bank':            ['food bank volunteers', 'community food distribution', 'food drive'],
    'homeless shelter':     ['community shelter volunteers', 'social services helping', 'community support'],
    'animal shelter':       ['animal shelter volunteers', 'pet rescue dogs cats', 'shelter animals'],
    'environmental':        ['river cleanup volunteers', 'community garden', 'environmental cleanup'],
    'youth':                ['youth mentorship community', 'after school program kids', 'community youth'],
    'default':              ['community volunteers helping', 'nonprofit volunteers', 'community service'],
}

# Fallback Pexels images — confirmed correct subjects, used if API key is missing
# Fallback Pexels images — confirmed correct subjects, used if API key is missing
FALLBACK_IMAGES = [
    'https://images.pexels.com/photos/6647037/pexels-photo-6647037.jpeg?auto=compress&cs=tinysrgb&w=1600',  # food bank volunteers
    'https://images.pexels.com/photos/6647119/pexels-photo-6647119.jpeg?auto=compress&cs=tinysrgb&w=1600',  # community meal
    'https://images.pexels.com/photos/3593865/pexels-photo-3593865.jpeg?auto=compress&cs=tinysrgb&w=1600',  # volunteer group
    'https://images.pexels.com/photos/6646918/pexels-photo-6646918.jpeg?auto=compress&cs=tinysrgb&w=1600',  # donation boxes
    'https://images.pexels.com/photos/6647038/pexels-photo-6647038.jpeg?auto=compress&cs=tinysrgb&w=1600',  # volunteers sorting
    'https://images.pexels.com/photos/6646866/pexels-photo-6646866.jpeg?auto=compress&cs=tinysrgb&w=1600',  # community help
    'https://images.pexels.com/photos/6646924/pexels-photo-6646924.jpeg?auto=compress&cs=tinysrgb&w=1600',  # volunteers giving donations
    'https://images.pexels.com/photos/6994833/pexels-photo-6994833.jpeg?auto=compress&cs=tinysrgb&w=1600',  # volunteers sorting donated clothes
]
