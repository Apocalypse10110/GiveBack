"""
ui.py — GiveBack Dashboard
Run with: streamlit run ui.py

Design principle: use Streamlit native components wherever possible.
Custom HTML only for badge pills — nothing else. This way the built-in
dark/light toggle in the three-dot menu works perfectly.
"""

import time
import re
import base64
import streamlit as st

from db import init_db, fetch_all, fetch_one, execute
import scraper
import qualifier
import builder
from config import GITHUB_ORG, GITHUB_USERNAME

# ── Must be first Streamlit call ──────────────────────────────────────────────
st.set_page_config(
    page_title='GiveBack',
    page_icon='💚',
    layout='wide',
    initial_sidebar_state='collapsed',
    menu_items={
        'About': 'GiveBack — automated nonprofit web pipeline. github.com/Apocalypse10110/GiveBack'
    }
)

# ── CSS — minimal, works with dark theme ─────────────────────────────────────
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');
html, body, [class*="css"] { font-family:'Inter',-apple-system,sans-serif !important; }

/* Header banner — sits on top of the dark bg naturally */
.gb-banner {
  background: linear-gradient(135deg, #0B1D3A 0%, #0A2E28 100%);
  border: 1px solid rgba(13,124,110,.25);
  border-radius: 12px;
  padding: 22px 26px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 20px;
}
.gb-banner-left  { display:flex; align-items:center; gap:14px; }
.gb-banner-logo  {
  width:42px; height:42px;
  background: rgba(13,124,110,.2);
  border: 1.5px solid rgba(13,124,110,.45);
  border-radius:10px;
  display:flex; align-items:center; justify-content:center; flex-shrink:0;
}
.gb-banner-title { font-size:1.25rem; font-weight:800; color:#fff; letter-spacing:-.3px; margin:0; }
.gb-banner-sub   { font-size:.72rem; color:rgba(255,255,255,.38); margin:0; margin-top:2px; }
.gb-banner-right { display:flex; align-items:center; gap:8px; }
.gb-chip {
  background: rgba(255,255,255,.06);
  border: 1px solid rgba(255,255,255,.09);
  border-radius:8px; padding:8px 14px; text-align:center; min-width:60px;
}
.gb-chip-num   { font-size:1.15rem; font-weight:800; color:#fff; line-height:1; display:block; }
.gb-chip-label { font-size:.58rem; color:rgba(255,255,255,.38); text-transform:uppercase; letter-spacing:.8px; display:block; margin-top:2px; }
.gb-chip.hi .gb-chip-num { color:#4DD9C8; }

/* Badge pills — explicit colors so they read on dark bg */
.pill { display:inline-block; padding:2px 9px; border-radius:99px; font-size:.68rem; font-weight:600; line-height:1.6; }
.pill-teal   { background:rgba(13,124,110,.25);  color:#4DD9C8; }
.pill-blue   { background:rgba(59,130,246,.2);   color:#93C5FD; }
.pill-amber  { background:rgba(217,119,6,.2);    color:#FCD34D; }
.pill-red    { background:rgba(239,68,68,.2);    color:#FCA5A5; }
.pill-purple { background:rgba(139,92,246,.2);   color:#C4B5FD; }
.pill-gray   { background:rgba(100,116,139,.2);  color:#94A3B8; }

/* Divider line — lighter on dark */
hr { border-color: rgba(255,255,255,.08) !important; }

iframe { border-radius:8px !important; border:1px solid rgba(255,255,255,.08) !important; }
</style>
""", unsafe_allow_html=True)

# ── Init ──────────────────────────────────────────────────────────────────────# ── Init ──────────────────────────────────────────────────────────────────────
init_db()
GH_ACTOR = GITHUB_ORG if GITHUB_ORG else GITHUB_USERNAME

# ── Theme note ───────────────────────────────────────────────────────────────
# Light/dark toggle is in the three-dot menu top right → Settings → Theme
# Default is dark (set in .streamlit/config.toml)

# ── Counts ────────────────────────────────────────────────────────────────────
counts     = {r['pipeline_stage']: r['c'] for r in fetch_all(
    'SELECT pipeline_stage, COUNT(*) as c FROM orgs GROUP BY pipeline_stage'
)}
total      = sum(counts.values())
qualified  = counts.get('qualified', 0)
built      = counts.get('built', 0)
deployed   = counts.get('deployed', 0)

# ── Header banner with inline stats ──────────────────────────────────────────
st.markdown(f"""
<div class="gb-banner">
  <div class="gb-banner-left">
    <div class="gb-banner-logo">
      <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="#0D7C6E" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
        <path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z"/>
      </svg>
    </div>
    <div>
      <p class="gb-banner-title">GiveBack</p>
      <p class="gb-banner-sub">Find nonprofits without websites &nbsp;&middot;&nbsp; Build them a free site &nbsp;&middot;&nbsp; Deploy instantly</p>
    </div>
  </div>
  <div class="gb-banner-right">
    <div class="gb-stat-chip">
      <span class="gb-stat-chip-num">{total}</span>
      <span class="gb-stat-chip-label">Found</span>
    </div>
    <div class="gb-stat-chip">
      <span class="gb-stat-chip-num">{qualified}</span>
      <span class="gb-stat-chip-label">Qualified</span>
    </div>
    <div class="gb-stat-chip hi">
      <span class="gb-stat-chip-num">{built}</span>
      <span class="gb-stat-chip-label">To Review</span>
    </div>
    <div class="gb-stat-chip">
      <span class="gb-stat-chip-num">{deployed}</span>
      <span class="gb-stat-chip-label">Live</span>
    </div>
  </div>
</div>
""", unsafe_allow_html=True)

# ── Tabs ──────────────────────────────────────────────────────────────────────
tab_pipeline, tab_review, tab_deployed, tab_stats = st.tabs([
    "Run Pipeline",
    f"Review Queue  ({built})",
    "Deployed Sites",
    "Stats",
])


# ══════════════════════════════════════════════════════════════════════════════
# TAB 1 — Run Pipeline
# ══════════════════════════════════════════════════════════════════════════════
with tab_pipeline:
    st.markdown("### Pipeline steps")
    st.caption("Run each step in order. Scraper feeds Qualifier, Qualifier feeds Builder.")
    st.markdown("")

    # Step 1
    with st.expander("Step 1 — Discover nonprofits without websites", expanded=True):
        st.caption(
            "Searches Google Maps via Outscraper for community orgs across all "
            "cities and query types in config.py. Filters out any result that already "
            "has a website. Results land in the DB as `scraped`."
        )
        col_a, col_b = st.columns([3, 1])
        with col_a:
            scrape_city = st.text_input(
                "One-off city (optional — leave blank to run all cities)",
                placeholder="e.g. Olympia WA",
                key="scrape_city",
            )
        with col_b:
            st.markdown("<div style='height:28px'></div>", unsafe_allow_html=True)
            run_scraper = st.button("Run Scraper", type="primary", use_container_width=True)

        if run_scraper:
            import config as cfg
            cities = list(cfg.TARGET_CITIES)
            if scrape_city.strip():
                cities = [scrape_city.strip()] + cities
            original = cfg.TARGET_CITIES
            cfg.TARGET_CITIES = cities
            with st.spinner("Scraping — this takes a few minutes..."):
                t = __import__('threading').Thread(target=scraper.run)
                t.start()
                while t.is_alive():
                    time.sleep(0.5)
                t.join()
            cfg.TARGET_CITIES = original
            st.success("Done. Refresh to see updated counts.")

    # Step 2
    with st.expander("Step 2 — Qualify scraped orgs"):
        st.caption(
            "Reads all `scraped` orgs and assigns hot / warm / cold tier based on "
            "phone presence and review count. Rejects junk entries."
        )
        if st.button("Run Qualifier", type="primary"):
            with st.spinner("Qualifying..."):
                qualifier.run()
            st.success("Done.")

    # Step 3
    with st.expander("Step 3 — Build sites for qualified orgs"):
        st.caption(
            "Calls Gemini to write copy, fills the HTML template, sources images "
            "from Pexels, and saves to the DB. A Telegram ping is sent when each "
            "site is ready. Nothing goes to GitHub until you approve it."
        )
        col_c, col_d = st.columns([3, 1])
        with col_c:
            build_limit = st.slider("How many sites to build", 1, 20, 3)
        with col_d:
            st.markdown("<div style='height:28px'></div>", unsafe_allow_html=True)
            run_builder = st.button("Build Sites", type="primary", use_container_width=True)

        if run_builder:
            orgs_to_build = fetch_all(
                """SELECT * FROM orgs
                   WHERE pipeline_stage = 'qualified'
                     AND lead_tier IN ('hot','warm')
                   ORDER BY lead_tier ASC, review_count DESC
                   LIMIT ?""",
                (build_limit,)
            )
            if not orgs_to_build:
                st.warning("No qualified orgs to build. Run the qualifier first.")
            else:
                bar  = st.progress(0)
                stat = st.empty()
                errs = []
                for i, org in enumerate(orgs_to_build):
                    stat.caption(f"Building {org['name']} ({i+1}/{len(orgs_to_build)})...")
                    try:
                        builder.build_site(org['id'])
                    except Exception as e:
                        errs.append(f"{org['name']}: {e}")
                    bar.progress((i + 1) / len(orgs_to_build))
                stat.empty()
                if errs:
                    st.warning(f"Finished with {len(errs)} error(s):\n" + "\n".join(f"• {e}" for e in errs))
                else:
                    st.success(f"{len(orgs_to_build)} sites ready. Open the Review Queue tab.")


# ══════════════════════════════════════════════════════════════════════════════
# TAB 2 — Review Queue
# ══════════════════════════════════════════════════════════════════════════════
with tab_review:
    st.markdown("### Review queue")
    st.caption("Check the copy and preview before anything goes live. Nothing deploys without your approval.")

    built_orgs = fetch_all(
        "SELECT * FROM orgs WHERE pipeline_stage='built' ORDER BY demo_built_at DESC"
    )

    if not built_orgs:
        st.info("No sites in the queue. Build some in the Pipeline tab.")
    else:
        names = {f"{o['name']}  —  {o['city']}": o['id'] for o in built_orgs}
        selected_label = st.selectbox(
            f"{len(built_orgs)} site(s) waiting",
            options=list(names.keys()),
        )
        selected_id  = names[selected_label]
        org = fetch_one("SELECT * FROM orgs WHERE id=?", (selected_id,))
        st.markdown("")

        # Org info row
        tier  = org.get('lead_tier','warm')
        stage = org.get('pipeline_stage','built')
        tier_colors  = {'hot':'red','warm':'amber','cold':'blue'}
        stage_colors = {'built':'amber','deploying':'purple','deployed':'teal','rejected':'red','scraped':'gray','qualified':'blue'}
        tc = tier_colors.get(tier,'gray')
        sc = stage_colors.get(stage,'gray')

        col_info, col_actions = st.columns([2, 1])
        with col_info:
            st.markdown(f"**{org['name']}**")
            st.caption(
                f"{org.get('category','').title()}  ·  "
                f"{org.get('city','')}  ·  "
                f"{org.get('phone','No phone')}  ·  "
                f"{org.get('review_count',0)} Google reviews"
            )
            cols = st.columns(3)
            cols[0].markdown(
                f'<span class="pill pill-{tc}">{tier.upper()}</span>',
                unsafe_allow_html=True
            )
            cols[1].markdown(
                f'<span class="pill pill-{sc}">{stage}</span>',
                unsafe_allow_html=True
            )
            if org.get('google_maps_url'):
                cols[2].markdown(f"[View on Maps]({org['google_maps_url']})")

        st.divider()

        # Two columns: copy on left, preview on right
        left, right = st.columns([1, 1], gap="large")

        with left:
            st.markdown("**Generated copy**")
            html_content = org.get('demo_html','')
            if html_content:
                h1  = re.search(r'class="hero-h1">(.*?)<em', html_content, re.S)
                sub = re.search(r'class="hero-sub">(.*?)</p', html_content, re.S)
                ab  = re.search(r'<p>{{ABOUT_PARAGRAPH_1}}</p>|about-text.*?<p>(.*?)</p>', html_content, re.S)

                hero_h   = h1.group(1).strip()  if h1  else "—"
                hero_sub = sub.group(1).strip()  if sub else "—"

                st.markdown("**Hero headline**")
                st.info(hero_h)
                st.markdown("**Subheadline**")
                st.info(hero_sub)

                built_at = org.get('demo_built_at','')
                if built_at:
                    st.caption(f"Built {built_at[:16]}")
            else:
                st.warning("No HTML found — try rebuilding.")

        with right:
            st.markdown("**Live preview**")
            if html_content:
                b64 = base64.b64encode(html_content.encode()).decode()
                st.markdown(
                    f'<iframe src="data:text/html;base64,{b64}" width="100%" height="520" '
                    f'title="Preview — {org["name"]}"></iframe>',
                    unsafe_allow_html=True,
                )

        st.markdown("")
        st.markdown("---")
        btn1, btn2, _ = st.columns([1, 1, 3])

        if btn1.button("Approve & Deploy", type="primary", use_container_width=True):
            with st.spinner(f"Deploying {org['name']} to GitHub Pages..."):
                try:
                    url = builder.deploy_site(selected_id)
                    st.success(f"Live at: {url}")
                    st.markdown(f"**[Open site]({url})**")
                    st.balloons()
                    time.sleep(1)
                    st.rerun()
                except Exception as e:
                    st.error(f"Deploy failed: {e}")
                    execute(
                        "UPDATE orgs SET pipeline_stage='build_error',notes=? WHERE id=?",
                        (str(e)[:300], selected_id)
                    )

        if btn2.button("Reject", use_container_width=True):
            execute(
                "UPDATE orgs SET pipeline_stage='rejected',notes='Rejected via UI' WHERE id=?",
                (selected_id,)
            )
            st.warning("Rejected.")
            time.sleep(0.4)
            st.rerun()


# ══════════════════════════════════════════════════════════════════════════════
# TAB 3 — Deployed Sites
# ══════════════════════════════════════════════════════════════════════════════
with tab_deployed:
    st.markdown("### Live sites")

    deployed_orgs = fetch_all(
        "SELECT * FROM orgs WHERE pipeline_stage='deployed' ORDER BY rowid DESC"
    )

    if not deployed_orgs:
        st.info("No sites deployed yet.")
    else:
        st.caption(f"{len(deployed_orgs)} site(s) live on GitHub Pages.")
        st.markdown("")

        for org in deployed_orgs:
            with st.container():
                a, b, c = st.columns([3, 3, 1])
                a.markdown(f"**{org['name']}**")
                a.caption(f"{org.get('city','')}  ·  {org.get('category','').title()}")
                url  = org.get('demo_url','')
                repo = org.get('github_repo','')
                if url:
                    b.markdown(f"[{url}]({url})")
                if repo:
                    b.caption(f"github.com/{GH_ACTOR}/{repo}")
                tier = org.get('lead_tier','')
                tc   = {'hot':'red','warm':'amber','cold':'blue'}.get(tier,'gray')
                c.markdown(
                    f'<span class="pill pill-{tc}">{tier.upper()}</span>',
                    unsafe_allow_html=True
                )
                st.divider()


# ══════════════════════════════════════════════════════════════════════════════
# TAB 4 — Stats
# ══════════════════════════════════════════════════════════════════════════════
with tab_stats:
    st.markdown("### Pipeline overview")
    st.markdown("")

    all_stages  = fetch_all('SELECT pipeline_stage, COUNT(*) as c FROM orgs GROUP BY pipeline_stage ORDER BY c DESC')
    all_tiers   = fetch_all('SELECT lead_tier, COUNT(*) as c FROM orgs WHERE lead_tier IS NOT NULL GROUP BY lead_tier ORDER BY c DESC')
    city_counts = fetch_all('SELECT city, COUNT(*) as c FROM orgs GROUP BY city ORDER BY c DESC LIMIT 10')
    cat_counts  = fetch_all('SELECT category, COUNT(*) as c FROM orgs GROUP BY category ORDER BY c DESC LIMIT 8')

    s1, s2 = st.columns(2)

    with s1:
        st.markdown("**By stage**")
        for row in all_stages:
            stage = row['pipeline_stage'] or 'unknown'
            pct   = int(row['c'] / max(total,1) * 100)
            sc    = {'deployed':'teal','built':'amber','qualified':'blue','scraped':'gray','rejected':'red'}.get(stage,'gray')
            col_l, col_b, col_r = st.columns([2, 5, 1])
            col_l.markdown(f'<span class="pill pill-{sc}">{stage}</span>', unsafe_allow_html=True)
            col_b.progress(pct / 100)
            col_r.markdown(f"**{row['c']}**")

        st.markdown("")
        st.markdown("**By tier**")
        for row in all_tiers:
            tier  = row['lead_tier'] or 'unknown'
            ttl   = sum(r['c'] for r in all_tiers)
            pct   = int(row['c'] / max(ttl,1) * 100)
            tc    = {'hot':'red','warm':'amber','cold':'blue'}.get(tier,'gray')
            col_l, col_b, col_r = st.columns([2, 5, 1])
            col_l.markdown(f'<span class="pill pill-{tc}">{tier.upper()}</span>', unsafe_allow_html=True)
            col_b.progress(pct / 100)
            col_r.markdown(f"**{row['c']}**")

    with s2:
        st.markdown("**Top cities**")
        if city_counts:
            city_total = sum(r['c'] for r in city_counts)
            for row in city_counts:
                pct = int(row['c'] / max(city_total,1) * 100)
                col_l, col_b, col_r = st.columns([2, 5, 1])
                col_l.caption(row['city'])
                col_b.progress(pct / 100)
                col_r.markdown(f"**{row['c']}**")
        else:
            st.caption("No data yet.")

        st.markdown("")
        st.markdown("**By org type**")
        if cat_counts:
            cat_total = sum(r['c'] for r in cat_counts)
            for row in cat_counts:
                pct = int(row['c'] / max(cat_total,1) * 100)
                col_l, col_b, col_r = st.columns([2, 5, 1])
                col_l.caption(row['category'] or 'other')
                col_b.progress(pct / 100)
                col_r.markdown(f"**{row['c']}**")

    st.divider()
    st.markdown("**Recent scrape runs**")
    runs = fetch_all('SELECT * FROM scrape_runs ORDER BY rowid DESC LIMIT 15')
    if runs:
        rows = []
        for r in runs:
            rows.append({
                "Status":  "Success" if r['status'] == 'success' else "Error",
                "Query":   (r.get('query') or '')[:35],
                "City":    r.get('city',''),
                "New":     r.get('new_records', 0),
                "Ran at":  str(r.get('ran_at',''))[:16],
            })
        st.dataframe(rows, use_container_width=True, hide_index=True)
    else:
        st.caption("No scrape runs yet.")

    with st.expander("About GiveBack"):
        st.markdown("""
GiveBack finds community organizations without a web presence, generates a free
accessible website using AI, and deploys it to GitHub Pages automatically.

| Sponsor track | Implementation |
|---|---|
| **MLH GitHub** | GitHub REST API — creates repo, pushes files, activates Pages. Zero CLI. |
| **Fidelity DEI** | WCAG ARIA labels, semantic HTML5, 4-language switcher (EN / ES / VI / SO) |
| **USAII** | This dashboard — nothing deploys without operator approval |
| **Hack3 / Xylem** | Search queries include watershed, river cleanup, water access nonprofits |

**Order:** Scraper → Qualifier → Builder → Review → Deploy
        """)
