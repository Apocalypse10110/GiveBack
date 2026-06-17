"""
ui.py — GiveBack Dashboard
==========================
The human-in-the-loop approval gate. Run with:
    streamlit run ui.py

Four tabs:
  1. Run Pipeline   — trigger scraper / qualifier / builder with live progress
  2. Review Queue   — approve or reject built sites before they go to GitHub
  3. Deployed       — all live sites with links
  4. Stats          — quick numbers on the pipeline

The actual build and deploy logic lives in builder.py — this file just
imports and calls it so there's one source of truth for the heavy lifting.
"""

import time
import threading
import queue
import json
import streamlit as st
from datetime import datetime

from db import init_db, fetch_all, fetch_one, execute
import scraper
import qualifier
import builder

# ── Page config — must be the very first Streamlit call ───────────────────────
st.set_page_config(
    page_title='GiveBack Dashboard',
    page_icon='G',
    layout='wide',
    initial_sidebar_state='collapsed',
)

# ── Custom CSS — matches the site template palette exactly ────────────────────
# Sora for headings, dark navy background for the sidebar and metric cards,
# teal accent for CTAs. Matches the template palette exactly.
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Sora:wght@400;600;700;800&family=DM+Sans:wght@300;400;500;600&display=swap');

html, body, [class*="css"] {
  font-family: 'DM Sans', sans-serif !important;
}
h1, h2, h3, h4 {
  font-family: 'Sora', sans-serif !important;
}

/* ── Layout ────────────────────────────────────────────────────── */
.block-container { padding-top: 0 !important; padding-left: 2rem !important; padding-right: 2rem !important; }
iframe { border: 1px solid #E2E8F0 !important; border-radius: 8px; }

/* ── Header ────────────────────────────────────────────────────── */
.gb-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 20px 0 18px;
  border-bottom: 1px solid #E8ECF0;
  margin-bottom: 28px;
}
.gb-header-left { display:flex; align-items:center; gap:12px; }
.gb-logo {
  width: 36px; height: 36px;
  background: #0D7C6E;
  border-radius: 8px;
  display: flex; align-items: center; justify-content: center;
  flex-shrink: 0;
  box-shadow: 0 2px 8px rgba(13,124,110,.25);
}
.gb-title {
  font-family: 'Sora', sans-serif !important;
  font-size: 1.1rem; font-weight: 800;
  color: #0F1E32; letter-spacing: -.3px; margin: 0;
}
.gb-sub { font-size: .72rem; color: #94A3B8; margin: 0; }
.gb-badge {
  background: #F0FDF9; border: 1px solid #99E6DC;
  color: #0A6358; font-size: .65rem; font-weight: 700;
  padding: 3px 10px; border-radius: 20px;
  letter-spacing: .6px; text-transform: uppercase;
  font-family: 'Sora', sans-serif;
}

/* ── Metric cards ──────────────────────────────────────────────── */
.metric-card {
  border-radius: 12px;
  padding: 20px 22px;
  margin-bottom: 4px;
}
.metric-card.teal   { background: #F0FDF9; border: 1px solid #99E6DC; }
.metric-card.blue   { background: #EFF6FF; border: 1px solid #BFDBFE; }
.metric-card.amber  { background: #FFFBEB; border: 1px solid #FDE68A; }
.metric-card.purple { background: #FAF5FF; border: 1px solid #DDD6FE; }
.metric-icon {
  width: 32px; height: 32px; border-radius: 8px;
  display: flex; align-items: center; justify-content: center;
  margin-bottom: 14px;
}
.metric-card.teal   .metric-icon { background: #CCFBF1; }
.metric-card.blue   .metric-icon { background: #DBEAFE; }
.metric-card.amber  .metric-icon { background: #FEF3C7; }
.metric-card.purple .metric-icon { background: #EDE9FE; }
.metric-num {
  font-family: 'Sora', sans-serif;
  font-size: 2rem; font-weight: 800; line-height: 1; margin-bottom: 4px;
}
.metric-card.teal   .metric-num { color: #0D7C6E; }
.metric-card.blue   .metric-num { color: #1D4ED8; }
.metric-card.amber  .metric-num { color: #D97706; }
.metric-card.purple .metric-num { color: #7C3AED; }
.metric-label {
  font-size: .72rem; font-weight: 600; color: #64748B;
  text-transform: uppercase; letter-spacing: .8px;
}

/* ── Section headers ───────────────────────────────────────────── */
.section-label {
  font-family: 'Sora', sans-serif;
  font-size: .68rem; font-weight: 700; color: #94A3B8;
  text-transform: uppercase; letter-spacing: 1.5px;
  margin-bottom: 12px; display: block;
}

/* ── Tier badges ───────────────────────────────────────────────── */
.tier-hot  { background:#FEF2F2; color:#991B1B; border:1px solid #FECACA; padding:3px 9px; border-radius:20px; font-size:.68rem; font-weight:700; }
.tier-warm { background:#FFFBEB; color:#92400E; border:1px solid #FDE68A; padding:3px 9px; border-radius:20px; font-size:.68rem; font-weight:700; }
.tier-cold { background:#F0F9FF; color:#075985; border:1px solid #BAE6FD; padding:3px 9px; border-radius:20px; font-size:.68rem; font-weight:700; }

/* ── Stage pills ───────────────────────────────────────────────── */
.stage-pill { display:inline-block; padding:3px 9px; border-radius:20px; font-size:.67rem; font-weight:700; text-transform:uppercase; letter-spacing:.4px; }
.stage-scraped   { background:#F1F5F9; color:#64748B; }
.stage-qualified { background:#EFF6FF; color:#1D4ED8; }
.stage-built     { background:#FEF9C3; color:#854D0E; }
.stage-deploying { background:#FAF5FF; color:#7C3AED; }
.stage-deployed  { background:#F0FDF9; color:#0A6358; }
.stage-rejected  { background:#FEF2F2; color:#991B1B; }
.stage-error     { background:#FEF2F2; color:#991B1B; }

/* ── Org review card ───────────────────────────────────────────── */
.org-card {
  border: 1px solid #E2E8F0; border-radius: 10px;
  padding: 18px 20px; margin-bottom: 12px; background: #fff;
}
.org-card-name { font-family:'Sora',sans-serif; font-size:.96rem; font-weight:700; color:#0F1E32; margin-bottom:3px; }
.org-card-meta { font-size:.78rem; color:#64748B; margin-bottom:12px; }

/* ── Progress bars ─────────────────────────────────────────────── */
.prog-row { display:flex; align-items:center; gap:10px; margin-bottom:8px; }
.prog-label { font-size:.78rem; font-weight:600; color:#0F1E32; min-width:100px; }
.prog-bar-wrap { flex:1; background:#F1F5F9; border-radius:4px; height:7px; }
.prog-bar { height:7px; border-radius:4px; background:#0D7C6E; }
.prog-count { font-size:.78rem; font-weight:700; color:#0F1E32; min-width:24px; text-align:right; }

/* ── Buttons ───────────────────────────────────────────────────── */
.stButton > button {
  font-family: 'Sora', sans-serif !important;
  font-weight: 700 !important;
  border-radius: 8px !important;
  letter-spacing: .1px !important;
}

/* ── Expanders ─────────────────────────────────────────────────── */
.streamlit-expanderHeader {
  font-family: 'Sora', sans-serif !important;
  font-weight: 600 !important;
  font-size: .88rem !important;
}

/* ── Dark mode support ─────────────────────────────────────────── */
@media (prefers-color-scheme: dark) {
  .gb-title { color: #F1F5F9 !important; }
  .metric-card.teal   { background: rgba(13,124,110,.12) !important; }
  .metric-card.blue   { background: rgba(29,78,216,.12) !important; }
  .metric-card.amber  { background: rgba(217,119,6,.12) !important; }
  .metric-card.purple { background: rgba(124,58,237,.12) !important; }
  .org-card { background: rgba(255,255,255,.04) !important; border-color: rgba(255,255,255,.08) !important; }
  .org-card-name { color: #F1F5F9 !important; }
}
</style>
""", unsafe_allow_html=True)


# ── Init ──────────────────────────────────────────────────────────────────────
init_db()

# ── Header ─────────────────────────────────────────────────────────────────────
st.markdown("""
<div class="gb-header">
  <div class="gb-header-left">
    <div class="gb-logo">
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z"/>
      </svg>
    </div>
    <div>
      <p class="gb-title">GiveBack</p>
      <p class="gb-sub">Nonprofit web pipeline &nbsp;&middot;&nbsp; Human-in-the-loop review</p>
    </div>
  </div>
  <span class="gb-badge">Operator Dashboard</span>
</div>
""", unsafe_allow_html=True)

# ── Top-level metrics ──────────────────────────────────────────────────────────
counts = {r['pipeline_stage']: r['c'] for r in fetch_all(
    'SELECT pipeline_stage, COUNT(*) as c FROM orgs GROUP BY pipeline_stage'
)}

total      = sum(counts.values())
qualified  = counts.get('qualified', 0)
built      = counts.get('built', 0)
deployed   = counts.get('deployed', 0)
needs_review = built  # sites waiting for human approval

m1, m2, m3, m4 = st.columns(4)
m1.markdown(f"""
<div class="metric-card teal">
  <div class="metric-icon">
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#0D7C6E" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
  </div>
  <div class="metric-num">{total}</div>
  <div class="metric-label">Orgs Found</div>
</div>""", unsafe_allow_html=True)
m2.markdown(f"""
<div class="metric-card blue">
  <div class="metric-icon">
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#1D4ED8" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/></svg>
  </div>
  <div class="metric-num">{qualified}</div>
  <div class="metric-label">Ready to Build</div>
</div>""", unsafe_allow_html=True)
m3.markdown(f"""
<div class="metric-card amber">
  <div class="metric-icon">
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#D97706" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>
  </div>
  <div class="metric-num">{needs_review}</div>
  <div class="metric-label">Awaiting Review</div>
</div>""", unsafe_allow_html=True)
m4.markdown(f"""
<div class="metric-card purple">
  <div class="metric-icon">
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#7C3AED" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>
  </div>
  <div class="metric-num">{deployed}</div>
  <div class="metric-label">Sites Live</div>
</div>""", unsafe_allow_html=True)

# ── Tabs ───────────────────────────────────────────────────────────────────────
tab_pipeline, tab_review, tab_deployed, tab_stats = st.tabs([
    'Run Pipeline',
    f'Review Queue ({needs_review})',
    'Deployed Sites',
    'Stats',
])


# ══════════════════════════════════════════════════════════════════════════════
# TAB 1 — Run Pipeline
# ══════════════════════════════════════════════════════════════════════════════
with tab_pipeline:
    st.markdown('### Run a pipeline step')
    st.caption('Each step feeds the next. Run them top to bottom on first use.')

    st.markdown('<div style="height:8px"></div>', unsafe_allow_html=True)

    # ── Step 1: Scraper ───────────────────────────────────────────────────────
    with st.expander('**Step 1 — Scrape Google Maps for nonprofits without websites**', expanded=True):
        st.caption(
            'Searches Outscraper across all city × query combinations in config.py. '
            'Filters out any result that already has a website. New orgs go into the DB as `scraped`.'
        )
        col_a, col_b = st.columns([2, 1])
        with col_a:
            scrape_city = st.text_input(
                'Add a one-off city (optional)',
                placeholder='e.g. Olympia WA',
                key='scrape_city',
            )
        with col_b:
            st.markdown('<div style="height:28px"></div>', unsafe_allow_html=True)
            run_scraper = st.button('Run Scraper', type='primary', key='btn_scraper')

        if run_scraper:
            log_box = st.empty()
            log_lines = []

            def scrape_with_log():
                # Temporarily patch the cities list if a one-off city was given
                import config
                cities = list(config.TARGET_CITIES)
                if scrape_city.strip():
                    cities = [scrape_city.strip()] + cities
                original = config.TARGET_CITIES
                config.TARGET_CITIES = cities
                try:
                    scraper.run()
                finally:
                    config.TARGET_CITIES = original

            with st.spinner('Scraper running — this can take a few minutes...'):
                # Run in thread so Streamlit doesn't block
                t = threading.Thread(target=scrape_with_log)
                t.start()
                while t.is_alive():
                    time.sleep(0.5)
                t.join()

            st.success('Scraper finished. Refresh the page to see updated counts.')

    # ── Step 2: Qualifier ─────────────────────────────────────────────────────
    with st.expander('**Step 2 — Qualify scraped orgs**'):
        st.caption(
            'Reads all `scraped` orgs and assigns hot / warm / cold tier based on '
            'phone presence and review count. Rejects junk entries.'
        )
        run_qualifier = st.button('Run Qualifier', type='primary', key='btn_qualifier')
        if run_qualifier:
            with st.spinner('Qualifying...'):
                qualifier.run()
            st.success('Qualifier finished. Check the Stats tab for tier breakdown.')

    # ── Step 3: Builder ───────────────────────────────────────────────────────
    with st.expander('**Step 3 — Build sites for qualified orgs**'):
        st.caption(
            'Calls Gemini to generate copy, fills the HTML template, sources images from '
            'Pexels, and saves the result to the DB. Sites appear in the Review Queue tab. '
            'A Telegram notification is sent when each one is ready.'
        )
        col_c, col_d = st.columns([2, 1])
        with col_c:
            build_limit = st.slider('How many sites to build', 1, 20, 5, key='build_limit')
        with col_d:
            st.markdown('<div style="height:28px"></div>', unsafe_allow_html=True)
            run_builder = st.button('Build Sites', type='primary', key='btn_builder')

        if run_builder:
            progress  = st.progress(0)
            status_el = st.empty()
            orgs_to_build = fetch_all(
                """SELECT * FROM orgs
                   WHERE pipeline_stage = 'qualified'
                     AND lead_tier IN ('hot','warm')
                   ORDER BY lead_tier ASC, review_count DESC
                   LIMIT ?""",
                (build_limit,)
            )
            if not orgs_to_build:
                st.warning('No qualified orgs to build. Run the qualifier first.')
            else:
                errors = []
                for i, org in enumerate(orgs_to_build):
                    status_el.markdown(
                        f'Building **{org["name"]}** ({i+1}/{len(orgs_to_build)})...'
                    )
                    try:
                        builder.build_site(org['id'])
                    except Exception as e:
                        errors.append(f'{org["name"]}: {e}')
                    progress.progress((i + 1) / len(orgs_to_build))
                    time.sleep(0.2)

                status_el.empty()
                if errors:
                    st.warning(f'Finished with {len(errors)} error(s):')
                    for err in errors:
                        st.caption(f'• {err}')
                else:
                    st.success(
                        f'{len(orgs_to_build)} sites built and ready for review. '
                        'Check the Review Queue tab.'
                    )


# ══════════════════════════════════════════════════════════════════════════════
# TAB 2 — Review Queue  (USAII Human-in-the-Loop track)
# ══════════════════════════════════════════════════════════════════════════════
with tab_review:
    st.markdown('### Sites waiting for approval')
    st.caption(
        'Review the generated copy and live preview below. '
        'Click **Approve & Deploy** to push to GitHub Pages, or **Reject** to skip.'
    )

    built_orgs = fetch_all(
        """SELECT * FROM orgs
           WHERE pipeline_stage = 'built'
           ORDER BY demo_built_at DESC"""
    )

    if not built_orgs:
        st.info('No sites in the queue right now. Run the builder in the Pipeline tab.')
    else:
        # Org selector — pick which one to review
        org_options = {f'{o["name"]} — {o["city"]}': o['id'] for o in built_orgs}
        selected_label = st.selectbox(
            f'{len(built_orgs)} site(s) waiting for review',
            options=list(org_options.keys()),
            key='review_select',
        )
        selected_id  = org_options[selected_label]
        selected_org = fetch_one('SELECT * FROM orgs WHERE id = ?', (selected_id,))

        st.markdown('<div style="height:12px"></div>', unsafe_allow_html=True)

        # ── Org info strip ────────────────────────────────────────────────────
        tier = selected_org.get('lead_tier', 'warm')
        tier_html = f'<span class="tier-{tier}">{tier.upper()}</span>'
        st.markdown(f"""
        <div class="org-card">
          <div class="org-card-name">{selected_org['name']} {tier_html}</div>
          <div class="org-card-meta">
            {selected_org.get('category','').title()} &nbsp;·&nbsp;
            {selected_org.get('city','')} &nbsp;·&nbsp;
            {selected_org.get('phone','No phone')} &nbsp;·&nbsp;
            {selected_org.get('review_count',0)} Google reviews
          </div>
          <a href="{selected_org.get('google_maps_url','#')}" target="_blank"
             style="font-size:.78rem;color:#0D7C6E;font-weight:600;">
            View on Google Maps ↗
          </a>
        </div>
        """, unsafe_allow_html=True)

        # ── Two-column layout: copy on left, preview on right ─────────────────
        left, right = st.columns([1, 1], gap='large')

        with left:
            st.markdown('#### Generated copy')

            # Show parsed copy fields if we can pull them from the HTML
            # The DB doesn't store copy separately, but we can parse key bits
            # from the HTML for the reviewer's quick scan
            html_content = selected_org.get('demo_html', '')

            def extract_slot(html: str, tag: str) -> str:
                """Pull the text content that was filled into a specific element."""
                import re
                # Works for h1, h2, p — good enough for a review UI
                m = re.search(rf'<{tag}[^>]*class="[^"]*hero-h1[^"]*"[^>]*>(.*?)<', html, re.S)
                return m.group(1).strip() if m else ''

            # Pull headline from the filled HTML
            import re
            h1_match = re.search(r'class="hero-h1">(.*?)<em', html_content, re.S)
            hero_h   = h1_match.group(1).strip() if h1_match else '—'
            sub_match = re.search(r'class="hero-sub">(.*?)</p', html_content, re.S)
            hero_sub  = sub_match.group(1).strip() if sub_match else '—'
            about_match = re.search(r'class="about-text reveal-right".*?<p>(.*?)</p>', html_content, re.S)
            about_p   = about_match.group(1).strip() if about_match else '—'

            st.markdown('**Hero headline**')
            st.info(hero_h or '(could not parse)')
            st.markdown('**Hero subheadline**')
            st.info(hero_sub or '(could not parse)')
            st.markdown('**About paragraph**')
            st.info(about_p[:400] + '...' if len(about_p) > 400 else about_p or '(could not parse)')

            built_at = selected_org.get('demo_built_at', '')
            if built_at:
                st.caption(f'Built at {built_at[:16]}')

        with right:
            st.markdown('#### Live preview')
            # Render the HTML directly in an iframe — this is the actual site
            if html_content:
                # Encode as base64 so Streamlit can render it in a sandboxed iframe
                encoded = html_content.encode('utf-8')
                import base64 as b64
                b64_html = b64.b64encode(encoded).decode()
                iframe_src = f'data:text/html;base64,{b64_html}'
                st.markdown(
                    f'<iframe src="{iframe_src}" width="100%" height="540" '
                    f'style="border:1px solid #E2E8F0;border-radius:4px;" '
                    f'title="Preview of {selected_org["name"]} website"></iframe>',
                    unsafe_allow_html=True,
                )
            else:
                st.warning('No HTML found for this org. Try rebuilding.')

        # ── Approval buttons ───────────────────────────────────────────────────
        st.markdown('<div style="height:16px"></div>', unsafe_allow_html=True)
        st.markdown('---')
        st.markdown(
            '**Ready to publish?** Approving will create a GitHub repo and activate '
            'GitHub Pages. The site will be live within ~60 seconds.'
        )

        btn_col1, btn_col2, btn_col3 = st.columns([1, 1, 3])

        with btn_col1:
            approve = st.button(
                '✅ Approve & Deploy',
                type='primary',
                key=f'approve_{selected_id}',
                use_container_width=True,
            )

        with btn_col2:
            reject = st.button(
                '❌ Reject',
                key=f'reject_{selected_id}',
                use_container_width=True,
            )

        if approve:
            with st.spinner(f'Deploying {selected_org["name"]} to GitHub Pages...'):
                try:
                    live_url = builder.deploy_site(selected_id)
                    st.success(f'Deployed! Live at: {live_url}')
                    st.markdown(f'**[Open site ↗]({live_url})**')
                    st.balloons()
                    time.sleep(1)
                    st.rerun()
                except Exception as e:
                    st.error(f'Deploy failed: {e}')
                    execute(
                        "UPDATE orgs SET pipeline_stage='build_error', notes=? WHERE id=?",
                        (str(e)[:300], selected_id)
                    )

        if reject:
            execute(
                "UPDATE orgs SET pipeline_stage='rejected', notes='Rejected via UI' WHERE id=?",
                (selected_id,)
            )
            st.warning(f'{selected_org["name"]} rejected and removed from queue.')
            time.sleep(0.5)
            st.rerun()


# ══════════════════════════════════════════════════════════════════════════════
# TAB 3 — Deployed Sites
# ══════════════════════════════════════════════════════════════════════════════
with tab_deployed:
    st.markdown('### Live sites')

    deployed_orgs = fetch_all(
        """SELECT * FROM orgs
           WHERE pipeline_stage = 'deployed'
           ORDER BY rowid DESC"""
    )

    if not deployed_orgs:
        st.info('No sites deployed yet. Approve a site in the Review Queue tab.')
    else:
        st.caption(f'{len(deployed_orgs)} site(s) currently live on GitHub Pages.')
        for org in deployed_orgs:
            with st.container():
                d1, d2, d3 = st.columns([3, 2, 1])
                with d1:
                    st.markdown(f'**{org["name"]}**')
                    st.caption(f'{org.get("city","")} · {org.get("category","").title()}')
                with d2:
                    url = org.get('demo_url', '')
                    if url:
                        st.markdown(f'[{url}]({url})')
                    repo = org.get('github_repo', '')
                    if repo:
                        from config import GITHUB_ORG, GITHUB_USERNAME
                        gh_actor = GITHUB_ORG if GITHUB_ORG else GITHUB_USERNAME
                        st.caption(f'Repo: github.com/{gh_actor}/{repo}')
                with d3:
                    tier = org.get('lead_tier', '')
                    st.markdown(
                        f'<span class="tier-{tier}">{tier.upper()}</span>',
                        unsafe_allow_html=True
                    )
                st.markdown('<hr style="margin:8px 0;border-color:#E2E8F0">', unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════════════
# TAB 4 — Stats
# ══════════════════════════════════════════════════════════════════════════════
with tab_stats:
    st.markdown('### Pipeline overview')

    all_stages = fetch_all(
        'SELECT pipeline_stage, COUNT(*) as c FROM orgs GROUP BY pipeline_stage ORDER BY c DESC'
    )
    all_tiers = fetch_all(
        'SELECT lead_tier, COUNT(*) as c FROM orgs WHERE lead_tier IS NOT NULL GROUP BY lead_tier ORDER BY c DESC'
    )
    city_counts = fetch_all(
        'SELECT city, COUNT(*) as c FROM orgs GROUP BY city ORDER BY c DESC LIMIT 10'
    )
    cat_counts = fetch_all(
        'SELECT category, COUNT(*) as c FROM orgs GROUP BY category ORDER BY c DESC LIMIT 8'
    )

    s1, s2 = st.columns(2)

    with s1:
        st.markdown('**By pipeline stage**')
        if all_stages:
            for row in all_stages:
                stage = row['pipeline_stage'] or 'unknown'
                pill_class = f'stage-{stage.replace(" ","_").replace("-","_")}'
                bar_pct = int((row['c'] / max(total, 1)) * 100)
                st.markdown(
                    f'<div style="display:flex;align-items:center;gap:10px;margin-bottom:7px;">'
                    f'<span class="stage-pill {pill_class}">{stage}</span>'
                    f'<div style="flex:1;background:#F1F5F9;border-radius:2px;height:6px;">'
                    f'<div style="width:{bar_pct}%;background:#0D7C6E;height:6px;border-radius:2px;"></div>'
                    f'</div>'
                    f'<span style="font-size:.8rem;font-weight:600;color:#0F1E32;min-width:24px;text-align:right;">{row["c"]}</span>'
                    f'</div>',
                    unsafe_allow_html=True
                )
        else:
            st.caption('No data yet — run the scraper first.')

        st.markdown('<div style="height:18px"></div>', unsafe_allow_html=True)
        st.markdown('**By lead tier**')
        if all_tiers:
            for row in all_tiers:
                tier = row['lead_tier'] or 'unknown'
                pill_class = f'tier-{tier}'
                tier_total = sum(r['c'] for r in all_tiers)
                bar_pct = int((row['c'] / max(tier_total, 1)) * 100)
                st.markdown(
                    f'<div style="display:flex;align-items:center;gap:10px;margin-bottom:7px;">'
                    f'<span class="{pill_class}" style="min-width:48px;text-align:center;">{tier.upper()}</span>'
                    f'<div style="flex:1;background:#F1F5F9;border-radius:2px;height:6px;">'
                    f'<div style="width:{bar_pct}%;background:#0D7C6E;height:6px;border-radius:2px;"></div>'
                    f'</div>'
                    f'<span style="font-size:.8rem;font-weight:600;color:#0F1E32;min-width:24px;text-align:right;">{row["c"]}</span>'
                    f'</div>',
                    unsafe_allow_html=True
                )

    with s2:
        st.markdown('**Top cities**')
        if city_counts:
            city_total = sum(r['c'] for r in city_counts)
            for row in city_counts:
                bar_pct = int((row['c'] / max(city_total, 1)) * 100)
                st.markdown(
                    f'<div style="display:flex;align-items:center;gap:10px;margin-bottom:7px;">'
                    f'<span style="font-size:.8rem;font-weight:600;color:#0F1E32;min-width:120px;">{row["city"]}</span>'
                    f'<div style="flex:1;background:#F1F5F9;border-radius:2px;height:6px;">'
                    f'<div style="width:{bar_pct}%;background:#0D7C6E;height:6px;border-radius:2px;"></div>'
                    f'</div>'
                    f'<span style="font-size:.8rem;font-weight:600;color:#0F1E32;min-width:24px;text-align:right;">{row["c"]}</span>'
                    f'</div>',
                    unsafe_allow_html=True
                )
        else:
            st.caption('No city data yet.')

        st.markdown('<div style="height:18px"></div>', unsafe_allow_html=True)
        st.markdown('**By org type**')
        if cat_counts:
            cat_total = sum(r['c'] for r in cat_counts)
            for row in cat_counts:
                bar_pct = int((row['c'] / max(cat_total, 1)) * 100)
                st.markdown(
                    f'<div style="display:flex;align-items:center;gap:10px;margin-bottom:7px;">'
                    f'<span style="font-size:.8rem;font-weight:600;color:#0F1E32;min-width:120px;">{row["category"] or "other"}</span>'
                    f'<div style="flex:1;background:#F1F5F9;border-radius:2px;height:6px;">'
                    f'<div style="width:{bar_pct}%;background:#0D7C6E;height:6px;border-radius:2px;"></div>'
                    f'</div>'
                    f'<span style="font-size:.8rem;font-weight:600;color:#0F1E32;min-width:24px;text-align:right;">{row["c"]}</span>'
                    f'</div>',
                    unsafe_allow_html=True
                )

    # ── Recent scrape runs ─────────────────────────────────────────────────────
    st.markdown('<div style="height:20px"></div>', unsafe_allow_html=True)
    st.markdown('**Recent scrape runs**')
    runs = fetch_all(
        'SELECT * FROM scrape_runs ORDER BY rowid DESC LIMIT 20'
    )
    if runs:
        for run in runs:
            status_color = '#166534' if run['status'] == 'success' else '#991B1B'
            run_icon = '✓' if run['status'] == 'success' else '✗'
            st.markdown(
                f'<div style="display:flex;align-items:center;gap:12px;padding:7px 0;'
                f'border-bottom:1px solid #F1F5F9;font-size:.8rem;">'
                f'<span style="color:{status_color};font-weight:700;min-width:14px;">{run_icon}</span>'
                f'<span style="font-weight:600;min-width:220px">{run.get("query","")[:30]}</span>'
                f'<span style="color:#64748B;">{run.get("city","")}</span>'
                f'<span style="margin-left:auto;color:#64748B;">{run.get("new_records",0)} new</span>'
                f'<span style="color:#94A3B8;min-width:140px;text-align:right">{str(run.get("ran_at",""))[:16]}</span>'
                f'</div>',
                unsafe_allow_html=True
            )
    else:
        st.caption('No scrape runs yet.')

    # ── Quick readme for judges ────────────────────────────────────────────────
    with st.expander('About this pipeline'):
        st.markdown("""
**GiveBack** finds nonprofits and community organizations that don't have a website,
builds them a free, accessible site using AI-generated copy, and deploys it instantly
to GitHub Pages — no cost, no hosting fees.

**How each sponsor track is addressed:**

| Track | Implementation |
|---|---|
| MLH GitHub | GitHub REST API creates repo, pushes files, activates Pages — zero CLI |
| Fidelity DEI | Template has WCAG-compliant ARIA labels, semantic HTML5, and a 4-language switcher (EN/ES/VI/SO) |
| USAII Human-in-the-Loop | This dashboard — nothing deploys without a human clicking Approve |
| Hack3 / Xylem | Search queries include water, watershed, river cleanup, and environmental nonprofits |

**Pipeline order:** Scraper → Qualifier → Builder → Review (here) → Deploy
        """)
