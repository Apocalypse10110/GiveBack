# GiveBack 💚

**Automatically finds nonprofits without a website and builds them one — for free.**

GiveBack is a solo-built pipeline that searches Google Maps for community organizations
(food pantries, shelters, environmental nonprofits, youth programs) that have no web
presence, generates a fully accessible website using AI-written copy, and deploys it
instantly to GitHub Pages — at zero cost to the organization.

---

## How it works

```
Scraper → Qualifier → Builder → Human Review → GitHub Pages
```

1. **Scraper** — searches Google Maps via Outscraper, filters for orgs with no `site` field
2. **Qualifier** — scores each org by tier (hot / warm / cold) based on phone + review count
3. **Builder** — Gemini generates all website copy; HTML template is filled and saved to DB
4. **Review** — Streamlit dashboard shows generated copy + live preview; nothing deploys without human approval (USAII Human-in-the-Loop track)
5. **Deploy** — GitHub REST API creates a public repo, pushes `index.html`, activates Pages

---

## Sponsor tracks

| Track | How it's addressed |
|---|---|
| **MLH GitHub** | Pure GitHub REST API deployment — `POST /repos`, push files via Contents API, `POST /pages`. Zero CLI. |
| **Fidelity DEI/Accessibility** | Template uses semantic HTML5, explicit ARIA labels, `prefers-contrast` media query, and a 4-language switcher: English, Español, Tiếng Việt, Soomaali |
| **USAII Responsible AI** | Human-in-the-loop Streamlit dashboard — operator reviews copy and live preview before any site goes live |
| **Hack3 / Xylem** | Search queries include watershed conservation, river cleanup, water access, and environmental nonprofits |

---

## Setup

```bash
git clone https://github.com/YOUR_USERNAME/giveback
cd giveback
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in your API keys
```

**Required keys** (see `.env.example`):
- `OUTSCRAPER_API_KEY` — [outscraper.com](https://outscraper.com)
- `GEMINI_API_KEY` — free at [aistudio.google.com](https://aistudio.google.com)
- `GITHUB_TOKEN` — Personal Access Token with `repo` scope
- `GITHUB_USERNAME` — your GitHub username
- `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` — for review notifications

**Optional:**
- `PEXELS_API_KEY` — free at [pexels.com/api](https://www.pexels.com/api/); falls back to curated images if not set

---

## Running

```bash
# Option A: Full pipeline via the dashboard
streamlit run ui.py

# Option B: Run steps manually
python3 scraper.py
python3 qualifier.py
python3 builder.py --batch 5 --dry-run   # generates HTML, skips GitHub
python3 builder.py --org-id 3            # build + deploy a specific org
```

---

## Project structure

```
giveback/
├── config.py          # all settings and search targets
├── db.py              # SQLite helpers
├── scraper.py         # Phase 1: Google Maps discovery
├── qualifier.py       # Phase 2: tier assignment
├── builder.py         # Phase 3: Gemini copy + GitHub deploy
├── ui.py              # Streamlit dashboard
├── template/
│   └── index.html     # accessible, i18n-ready nonprofit template
├── requirements.txt
└── .env.example
```

---

Built for MLH Global Hack Week, USAII Global AI Hackathon, and Hack3 Summer Edition · June 2026
