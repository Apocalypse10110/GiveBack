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
@import url('https://fonts.googleapis.com/css2?family=Sora:wght@400;600;700;800&family=DM+Sans:wght@400;500;600&display=swap');

:root {
  --accent:   #0D7C6E;
  --dark:     #0B1D3A;
  --dark2:    #0F2347;
  --dim:      rgba(255,255,255,.08);
}

html, body, [class*="css"] { font-family: 'DM Sans', sans-serif !important; }
h1, h2, h3, h4 { font-family: 'Sora', sans-serif !important; letter-spacing: -.3px; }

/* Header — full-width dark strip so it reads on any Streamlit theme */
.gb-header-wrap {
  background: var(--dark);
  margin: -1rem -1rem 24px -1rem;
  padding: 16px 28px;
  border-bottom: 1px solid var(--dim);
}
.gb-header { display:flex; align-items:center; gap:14px; }
.gb-logo { width:32px; height:32px; background:var(--accent); border-radius:4px; display:flex; align-items:center; justify-content:center; flex-shrink:0; }
.gb-title { font-family:'Sora',sans-serif !important; font-size:1.05rem; font-weight:800; color:#ffffff; letter-spacing:-.2px; margin:0; line-height:1.2; }
.gb-sub { font-size:.72rem; color:rgba(255,255,255,.38); margin:0; }
.gb-pill { background:rgba(13,124,110,.18); border:1px solid rgba(13,124,110,.3); color:#4DD9C8; font-family:'Sora',sans-serif; font-size:.63rem; font-weight:700; padding:3px 8px; border-radius:3px; letter-spacing:.7px; text-transform:uppercase; margin-left:auto; }

/* Metrics — flat table layout, no hero-number cliche */
.metrics-row { display:grid; grid-template-columns:repeat(4,1fr); gap:1px; background:var(--dim); border:1px solid var(--dim); border-radius:5px; overflow:hidden; margin-bottom:22px; }
.metric-cell { background:rgba(11,29,58,.55); padding:14px 18px; display:flex; align-items:center; gap:12px; }
.metric-val { font-family:'Sora',sans-serif; font-size:1.5rem; font-weight:800; color:#fff; line-height:1; min-width:24px; }
.metric-label { font-size:.66rem; font-weight:700; color:rgba(255,255,255,.32); text-transform:uppercase; letter-spacing:1px; display:block; }
.metric-desc  { font-size:.74rem; color:rgba(255,255,255,.52); }
.metric-cell.hi .metric-val { color:#4DD9C8; }

/* Badges */
.tier-hot  { background:#FEF2F2; color:#991B1B; border:1px solid #FECACA; padding:2px 8px; border-radius:3px; font-size:.7rem; font-weight:700; }
.tier-warm { background:#FFFBEB; color:#92400E; border:1px solid #FDE68A; padding:2px 8px; border-radius:3px; font-size:.7rem; font-weight:700; }
.tier-cold { background:#F0F9FF; color:#075985; border:1px solid #BAE6FD; padding:2px 8px; border-radius:3px; font-size:.7rem; font-weight:700; }

/* Org card */
.org-card { border:1px solid #E2E8F0; border-radius:4px; padding:18px 22px; margin-bottom:14px; background:#fff; }
.org-card-name { font-family:'Sora',sans-serif; font-size:.96rem; font-weight:700; color:#0F1E32; margin-bottom:3px; }
.org-card-meta { font-size:.78rem; color:#64748B; margin-bottom:12px; }

/* Stage pills */
.stage-pill { display:inline-block; padding:2px 8px; border-radius:3px; font-size:.67rem; font-weight:700; text-transform:uppercase; letter-spacing:.5px; }
.stage-scraped   { background:#F1F5F9; color:#475569; }
.stage-qualified { background:#EFF6FF; color:#1D4ED8; }
.stage-built     { background:#FEF9C3; color:#854D0E; }
.stage-deploying { background:#FDF4FF; color:#7E22CE; }
.stage-deployed  { background:#F0FDF4; color:#166534; }
.stage-rejected  { background:#FEF2F2; color:#991B1B; }
.stage-error     { background:#FEF2F2; color:#991B1B; }

/* Misc */
.stButton button { font-family:'Sora',sans-serif !important; font-weight:700 !important; border-radius:4px !important; }
.block-container { padding-top:0 !important; }
iframe { border:1px solid #CBD5E1 !important; border-radius:4px; }
</style>
""", unsafe_allow_html=True)


# ── Init ──────────────────────────────────────────────────────────────────────
init_db()

# ── Header ─────────────────────────────────────────────────────────────────────
st.markdown("""
<div class="gb-header-wrap">
  <div class="gb-header">
    <div class="gb-logo">
      <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z"/>
      </svg>
    </div>
    <div>
      <p class="gb-title">GiveBack</p>
      <p class="gb-sub">Nonprofit web pipeline &nbsp;&middot;&nbsp; Human-in-the-loop review</p>
    </div>
    <span class="gb-pill">Internal</span>
  </div>
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

st.markdown(f"""
<div class="metrics-row">
  <div class="metric-cell">
    <div class="metric-val">{total}</div>
    <div class="metric-info">
      <span class="metric-label">Orgs Found</span>
      <span class="metric-desc">Total in database</span>
    </div>
  </div>
  <div class="metric-cell">
    <div class="metric-val">{qualified}</div>
    <div class="metric-info">
      <span class="metric-label">Ready to Build</span>
      <span class="metric-desc">Qualified, awaiting builder</span>
    </div>
  </div>
  <div class="metric-cell hi">
    <div class="metric-val">{needs_review}</div>
    <div class="metric-info">
      <span class="metric-label">Awaiting Review</span>
      <span class="metric-desc">Needs your approval</span>
    </div>
  </div>
  <div class="metric-cell">
    <div class="metric-val">{deployed}</div>
    <div class="metric-info">
      <span class="metric-label">Sites Live</span>
      <span class="metric-desc">Deployed to GitHub Pages</span>
    </div>
  </div>
</div>
""", unsafe_allow_html=True)

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
