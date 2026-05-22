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
    missing_meta   = sum(1 for p in pages if not p.get('meta_description', '').strip())
    missing_h1     = sum(1 for p in pages if not p.get('h1', []))
    images_no_alt  = sum(
        sum(1 for img in p.get('images', []) if not img.get('alt', '').strip())
        for p in pages
    )
    return {
        'pages_crawled':          len(pages),
        'missing_meta_description': missing_meta,
        'missing_h1':             missing_h1,
        'images_missing_alt':     images_no_alt,
        'has_sitemap':            crawl_data.get('sitemap_status', '') == 'found',
        'has_robots':             crawl_data.get('robots_status', '')  == 'found',
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
