"""
companion.py — Donor Readiness Audit
Runs optional SEO and accessibility audits as companion reports.

These are script-based (zero Claude API cost) and produce branded .docx files
alongside the main donor PDF. Summary stats are returned so the donor report
closing can tease the companion findings.

Script location is resolved in this order:
  1. COMPANION_SCRIPTS_DIR env var (explicit path to UpStart.Skills.Claude root)
  2. Sibling directory: ../../UpStart.Skills.Claude relative to this file
  3. Gracefully skipped if scripts not found

Node.js dependencies (playwright, @axe-core/playwright, docx) are resolved via:
  1. COMPANION_NODE_MODULES env var
  2. companion/node_modules/ next to this file (run: cd app/companion && npm install)
  3. Skills repo node_modules (if present)
"""

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional


# ── Path resolution ────────────────────────────────────────────────────────────

def _skills_root() -> Path:
    """Find the UpStart.Skills.Claude repo root."""
    env = os.environ.get('COMPANION_SCRIPTS_DIR')
    if env:
        return Path(env)
    # Default: ../../../UpStart.Skills.Claude relative to this file
    # app/ → UpStart.Audit.DonorReadiness/ → parent dir → UpStart.Skills.Claude
    return Path(__file__).parent.parent.parent / 'UpStart.Skills.Claude'


def _find_node_modules() -> Optional[Path]:
    """Find an installed node_modules directory for companion scripts."""
    # 1. Explicit env var
    env = os.environ.get('COMPANION_NODE_MODULES')
    if env:
        p = Path(env)
        if (p / 'docx').exists():
            return p

    # 2. Local companion/node_modules (preferred install location)
    local = Path(__file__).parent / 'companion' / 'node_modules'
    if (local / 'docx').exists():
        return local

    # 3. a11y-audit skill node_modules
    skills = _skills_root()
    a11y_nm = skills / 'skills' / 'a11y-audit' / 'node_modules'
    if (a11y_nm / 'docx').exists():
        return a11y_nm

    return None


# ── Stat extractors ────────────────────────────────────────────────────────────

def _summarize_seo(crawl_data: dict) -> dict:
    """Extract a concise stat summary from crawl_results.json."""
    pages = crawl_data.get('pages', [])
    # meta_description is None (not missing key) when absent — coerce safely
    missing_meta  = sum(1 for p in pages if not (p.get('meta_description') or '').strip())
    # h1 is a list; missing means empty list
    missing_h1    = sum(1 for p in pages if not p.get('h1'))
    # images have a 'status' field: "missing", "empty", "ok", "decorative"
    images_no_alt = sum(
        sum(1 for img in p.get('images', []) if img.get('status') in ('missing', 'empty'))
        for p in pages
    )
    # possible_js_rendering_gap is set per-page by seo_crawl.py's raw-HTML
    # heuristic (SPA framework markers + near-empty visible text). Surface a
    # count so the report can caveat findability findings for JS-heavy sites
    # that this scanner (no JS execution) can't fully see.
    js_gap_pages = sum(1 for p in pages if p.get('possible_js_rendering_gap'))

    return {
        'pages_crawled':                   len(pages),
        'missing_meta_description':        missing_meta,
        'missing_h1':                      missing_h1,
        'images_missing_alt':               images_no_alt,
        # sitemap_status/robots_status are raw HTTP status codes (int) from
        # seo_crawl.fetch_status() — compare against 200, not the string
        # 'found' (that comparison was always False, regardless of site).
        'has_sitemap':                      crawl_data.get('sitemap_status') == 200,
        'has_robots':                       crawl_data.get('robots_status')  == 200,
        'possible_js_rendering_gap_pages':  js_gap_pages,
    }


def _summarize_a11y(axe_data: dict) -> dict:
    """Extract a concise stat summary from axe_results.json."""
    pages = axe_data.get('pages', [])
    all_violations = [v for p in pages for v in p.get('violations', [])]
    critical  = sum(1 for v in all_violations if v.get('impact') == 'critical')
    serious   = sum(1 for v in all_violations if v.get('impact') == 'serious')
    moderate  = sum(1 for v in all_violations if v.get('impact') == 'moderate')
    unique_ids = len({v.get('id') for v in all_violations})
    return {
        'pages_crawled':     len(pages),
        'total_violations':  len(all_violations),
        'critical':          critical,
        'serious':           serious,
        'moderate':          moderate,
        'unique_issue_types': unique_ids,
    }


# ── Runners ────────────────────────────────────────────────────────────────────

def run_seo_audit(url: str, output_dir: str) -> Optional[dict]:
    """
    Run the SEO crawl + report generator.

    Returns a dict:
      {
        'report_path': str | None,   # path to the .docx, or None if generation failed
        'summary':     dict,         # stat summary for prompt injection
      }
    Returns None if scripts are not found.
    """
    skills = _skills_root()
    crawl_script  = skills / 'skills' / 'seo-audit' / 'scripts' / 'crawl_site.py'
    report_script = skills / 'skills' / 'seo-audit' / 'scripts' / 'generate_report.js'

    if not crawl_script.exists():
        print(f'[companion:seo] crawl_site.py not found at {crawl_script} — skipping', file=sys.stderr)
        return None

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    seo_dir   = out / 'seo_work'
    seo_dir.mkdir(exist_ok=True)
    crawl_json = seo_dir / 'crawl_results.json'

    # ── Step 1: crawl ──
    print('[companion:seo] Crawling...', file=sys.stderr)
    result = subprocess.run(
        [sys.executable, str(crawl_script), url, str(seo_dir)],
        capture_output=True, text=True
    )
    if result.returncode != 0 or not crawl_json.exists():
        print(f'[companion:seo] Crawl failed (rc={result.returncode}): {result.stderr[-300:]}', file=sys.stderr)
        return {'report_path': None, 'summary': {}}

    with open(crawl_json) as f:
        crawl_data = json.load(f)
    summary = _summarize_seo(crawl_data)
    print(f'[companion:seo] Crawled {summary["pages_crawled"]} pages', file=sys.stderr)

    # ── Step 2: generate report ──
    if not report_script.exists():
        print('[companion:seo] generate_report.js not found — skipping docx', file=sys.stderr)
        return {'report_path': None, 'summary': summary}

    domain = url.replace('https://', '').replace('http://', '').split('/')[0]
    docx_path = out / f'{domain}_seo_audit.docx'

    node_modules = _find_node_modules()
    env = dict(os.environ)
    if node_modules:
        env['NODE_PATH'] = str(node_modules)

    r2 = subprocess.run(
        ['node', str(report_script), str(crawl_json), str(docx_path)],
        capture_output=True, text=True, env=env
    )
    if r2.returncode != 0:
        print(f'[companion:seo] Report generation failed: {r2.stderr[-300:]}', file=sys.stderr)
        return {'report_path': None, 'summary': summary}

    print(f'[companion:seo] Report saved: {docx_path}', file=sys.stderr)
    return {
        'report_path': str(docx_path) if docx_path.exists() else None,
        'summary':     summary,
    }


def run_a11y_audit(url: str, output_dir: str) -> Optional[dict]:
    """
    Run the a11y crawl + report generator.

    Returns a dict:
      {
        'report_path': str | None,
        'summary':     dict,
      }
    Returns None if scripts are not found.
    """
    skills = _skills_root()
    crawl_script  = skills / 'skills' / 'a11y-audit' / 'scripts' / 'crawl_a11y.js'
    report_script = skills / 'skills' / 'a11y-audit' / 'scripts' / 'generate_report.js'

    if not crawl_script.exists():
        print(f'[companion:a11y] crawl_a11y.js not found at {crawl_script} — skipping', file=sys.stderr)
        return None

    node_modules = _find_node_modules()
    if node_modules is None or not (node_modules / 'playwright').exists():
        print('[companion:a11y] playwright not installed — skipping. '
              'Run: cd app/companion && npm install && npx playwright install chromium',
              file=sys.stderr)
        return None

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    a11y_dir  = out / 'a11y_work'
    a11y_dir.mkdir(exist_ok=True)
    axe_json  = a11y_dir / 'axe_results.json'

    env = dict(os.environ)
    env['NODE_PATH'] = str(node_modules)
    # Playwright browser path — respect existing env or use node_modules location
    if 'PLAYWRIGHT_BROWSERS_PATH' not in env:
        browsers_path = node_modules.parent / 'browsers'
        if browsers_path.exists():
            env['PLAYWRIGHT_BROWSERS_PATH'] = str(browsers_path)

    # ── Step 1: crawl ──
    print('[companion:a11y] Crawling with axe-core...', file=sys.stderr)
    result = subprocess.run(
        ['node', str(crawl_script), url, str(a11y_dir)],
        capture_output=True, text=True, env=env
    )
    if result.returncode != 0 or not axe_json.exists():
        print(f'[companion:a11y] Crawl failed (rc={result.returncode}): {result.stderr[-300:]}', file=sys.stderr)
        return {'report_path': None, 'summary': {}}

    with open(axe_json) as f:
        axe_data = json.load(f)
    summary = _summarize_a11y(axe_data)
    print(f'[companion:a11y] {summary["total_violations"]} violations across {summary["pages_crawled"]} pages', file=sys.stderr)

    # ── Step 2: generate report ──
    if not report_script.exists():
        print('[companion:a11y] generate_report.js not found — skipping docx', file=sys.stderr)
        return {'report_path': None, 'summary': summary}

    domain = url.replace('https://', '').replace('http://', '').split('/')[0]
    docx_path = out / f'{domain}_a11y_audit.docx'

    r2 = subprocess.run(
        ['node', str(report_script), str(axe_json), str(docx_path)],
        capture_output=True, text=True, env=env
    )
    if r2.returncode != 0:
        print(f'[companion:a11y] Report generation failed: {r2.stderr[-300:]}', file=sys.stderr)
        return {'report_path': None, 'summary': summary}

    print(f'[companion:a11y] Report saved: {docx_path}', file=sys.stderr)
    return {
        'report_path': str(docx_path) if docx_path.exists() else None,
        'summary':     summary,
    }


# ── Inline a11y crawl (Lambda-safe, Python Playwright + injected axe-core) ─────

def crawl_a11y_inline(url: str, max_pages: int = 10) -> Optional[dict]:
    """
    Run WCAG 2.1 AA checks using Python Playwright + axe-core injected as an
    init script (bypasses CSP). Uses the same Chromium already in the container.
    Bundled axe.min.js must sit alongside companion.py in app/.

    Pages are prioritized the same way the main donor-readiness crawler
    prioritizes pages (donate, volunteer, about, impact first, discovered from
    the homepage's own links) so a fixed page budget covers the pages that
    matter most rather than whichever links happen to appear first in the
    homepage's raw DOM order. No time-budget cutoff — intentionally lets a
    scan run long rather than stop early on a slow page, per project decision.
    """
    axe_js = Path(__file__).parent / 'axe.min.js'
    if not axe_js.exists():
        print('[companion:a11y] axe.min.js not found — skipping', file=sys.stderr)
        return None

    try:
        from urllib.parse import urlparse
        from playwright.sync_api import sync_playwright
        # Reuse the main crawler's category regex so page selection here
        # matches the same donate/volunteer/about/impact priority order.
        from crawler import DONATE_URL, DONATE_PATTERNS, VOLUNTEER_URL, VOLUNTEER_PATTERNS, ABOUT_URL, IMPACT_URL

        axe_source = axe_js.read_text(encoding='utf-8')

        start_url = url.rstrip('/')
        if not start_url.startswith('http'):
            start_url = 'https://' + start_url

        def link_priority(href: str, text: str) -> int:
            """Lower number = higher priority. Mirrors crawler.py's category order."""
            if DONATE_URL.search(href) or DONATE_PATTERNS.search(text):
                return 0
            if VOLUNTEER_URL.search(href) or VOLUNTEER_PATTERNS.search(text):
                return 1
            if ABOUT_URL.search(href) or re.search(r'\b(about|mission|team|board|staff)\b', text, re.I):
                return 2
            if IMPACT_URL.search(href) or re.search(r'\b(impact|outcomes|results|report)\b', text, re.I):
                return 3
            return 4

        page_results = []
        failed_urls  = []

        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=True,
                args=['--no-sandbox', '--disable-dev-shm-usage',
                      '--disable-gpu', '--single-process'],
            )
            # add_init_script runs before page scripts → not blocked by CSP
            ctx = browser.new_context()
            ctx.add_init_script(axe_source)
            pg = ctx.new_page()

            def scan_page(target_url: str) -> None:
                try:
                    pg.goto(target_url, wait_until='networkidle', timeout=30000)
                    pg.wait_for_timeout(300)

                    violations = pg.evaluate("""
                        async () => {
                            const r = await axe.run({
                                runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa'] }
                            });
                            return r.violations.map(v => ({
                                id:     v.id,
                                impact: v.impact,
                                nodes:  v.nodes.length,
                            }));
                        }
                    """)
                    page_results.append({'url': target_url, 'violations': violations})
                except Exception as page_err:
                    failed_urls.append(target_url)
                    print(f'[companion:a11y] {target_url}: {page_err}', file=sys.stderr)

            # ── Homepage first ──
            scan_page(start_url)

            # ── Discover links from the homepage, then prioritize the rest ──
            try:
                links = pg.evaluate("""
                    () => Array.from(document.querySelectorAll('a[href]'))
                        .map(a => ({
                            href: a.href.split('#')[0].replace(/\\/$/, ''),
                            text: (a.textContent || '').trim()
                        }))
                        .filter(l => l.href.startsWith(window.location.origin))
                """)
            except Exception as e:
                print(f'[companion:a11y] Link discovery failed: {e}', file=sys.stderr)
                links = []

            seen = {start_url}
            candidates = []
            for link in links:
                href = link.get('href')
                text = link.get('text', '')
                if not href or href in seen:
                    continue
                seen.add(href)
                candidates.append((link_priority(href, text), href))

            candidates.sort(key=lambda c: c[0])

            for _, href in candidates:
                if len(page_results) + len(failed_urls) >= max_pages:
                    break
                scan_page(href)

            browser.close()

        all_violations = [v for pr in page_results for v in pr.get('violations', [])]
        summary = {
            'pages_crawled':      len(page_results),
            'pages_failed':       len(failed_urls),
            'total_violations':   len(all_violations),
            'critical':           sum(1 for v in all_violations if v.get('impact') == 'critical'),
            'serious':            sum(1 for v in all_violations if v.get('impact') == 'serious'),
            'moderate':           sum(1 for v in all_violations if v.get('impact') == 'moderate'),
            'unique_issue_types': len({v.get('id') for v in all_violations}),
        }
        print(
            f'[companion:a11y] {summary["pages_crawled"]} pages scanned '
            f'({summary["pages_failed"]} failed) — '
            f'{summary["critical"]} critical, {summary["serious"]} serious violations',
            file=sys.stderr,
        )
        return summary

    except Exception as e:
        print(f'[companion:a11y] Inline crawl failed: {e}', file=sys.stderr)
        return None


# ── Inline SEO crawl (Lambda-safe, no subprocess or Node required) ─────────────

def crawl_seo_inline(url: str) -> Optional[dict]:
    """
    Run the SEO crawl in-process using seo_crawl.py's functions directly.
    Works in Lambda (pure Python + curl). Returns summary stats or None on failure.
    """
    try:
        import urllib.parse
        import seo_crawl  # bundled alongside companion.py in app/

        base_url = url.rstrip('/')
        if not base_url.startswith('http'):
            base_url = 'https://' + base_url
        parsed = urllib.parse.urlparse(base_url)
        base_domain = parsed.netloc

        robots_url    = base_url + '/robots.txt'
        robots_status = seo_crawl.fetch_status(robots_url)

        pages, sitemap_status, _ = seo_crawl.discover_pages(base_url, base_domain)
        pages = sorted(pages)[:20]

        all_page_data = []
        for page in pages:
            html = seo_crawl.fetch(base_url + page)
            if not html:
                continue
            page_data = seo_crawl.extract_page_data(html, base_url + page, base_domain)
            page_data['path'] = page
            all_page_data.append(page_data)

        crawl_data = {
            'base_url':       base_url,
            'sitemap_status': sitemap_status,
            'robots_status':  robots_status,
            'pages':          all_page_data,
        }
        summary = _summarize_seo(crawl_data)
        print(
            f'[companion:seo] {summary["pages_crawled"]} pages — '
            f'{summary["missing_meta_description"]} missing meta, '
            f'{summary["images_missing_alt"]} images without alt',
            file=sys.stderr,
        )
        return summary

    except Exception as e:
        print(f'[companion:seo] Inline crawl failed: {e}', file=sys.stderr)
        return None


# ── Setup helper ───────────────────────────────────────────────────────────────

def print_setup_instructions():
    """Print one-time setup instructions for node dependencies."""
    companion_dir = Path(__file__).parent / 'companion'
    print(f"""
[companion] To enable SEO and a11y report generation, install Node dependencies:

  mkdir -p {companion_dir}
  cp {_skills_root()}/skills/a11y-audit/package.json {companion_dir}/package.json
  cd {companion_dir} && npm install
  npx playwright install chromium

Or set COMPANION_NODE_MODULES to an existing node_modules directory.
""", file=sys.stderr)
