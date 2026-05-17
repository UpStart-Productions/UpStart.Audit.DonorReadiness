"""
main.py — Donor Readiness Audit (local end-to-end runner)
Crawls a URL, generates a report via Claude, and renders a PDF.

Usage:
    python main.py <url> [output.pdf]

    python main.py https://www.miraclefoundation.org
    python main.py https://example-nonprofit.org ./reports/example_report.pdf

Environment:
    ANTHROPIC_API_KEY   required
    AUDIT_MODEL         optional, default: claude-opus-4-6

Output:
    PDF written to ./reports/<domain>_donor_readiness.pdf (or specified path)
    Intermediate signals and report JSON written to ./reports/ for debugging
"""

import json
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

# Local modules
from crawler import crawl
from prompt import generate_report
from renderer import render_pdf


def safe_filename(s: str) -> str:
    """Convert a string to a safe filename component."""
    return re.sub(r'[^\w\-.]', '_', s)


def main():
    if len(sys.argv) < 2:
        print('Usage: python main.py <url> [output.pdf]', file=sys.stderr)
        sys.exit(1)

    url = sys.argv[1]
    domain = urlparse(url if '://' in url else 'https://' + url).netloc or url
    timestamp = time.strftime('%Y%m%d_%H%M%S')

    # ── Output directory ──
    reports_dir = Path(__file__).parent / 'reports'
    reports_dir.mkdir(exist_ok=True)

    stem = safe_filename(domain)
    signals_path = reports_dir / f'{stem}_{timestamp}_signals.json'
    report_path  = reports_dir / f'{stem}_{timestamp}_report.json'

    if len(sys.argv) > 2:
        pdf_path = sys.argv[2]
    else:
        pdf_path = str(reports_dir / f'{stem}_donor_readiness.pdf')

    model = os.environ.get('AUDIT_MODEL', 'claude-opus-4-6')

    print(f'\n{"="*60}', file=sys.stderr)
    print(f'  Donor Readiness Audit', file=sys.stderr)
    print(f'  URL:   {url}', file=sys.stderr)
    print(f'  Model: {model}', file=sys.stderr)
    print(f'{"="*60}\n', file=sys.stderr)

    # ── Step 1: Crawl ──
    print('[1/3] Crawling site...', file=sys.stderr)
    t0 = time.time()
    signals = crawl(url)
    print(f'      Done in {time.time()-t0:.1f}s', file=sys.stderr)

    with open(signals_path, 'w') as f:
        json.dump(signals, f, indent=2)
    print(f'      Signals saved: {signals_path}', file=sys.stderr)

    # ── Step 2: Generate report via Claude ──
    print(f'\n[2/3] Generating report ({model})...', file=sys.stderr)
    t1 = time.time()
    report = generate_report(signals, model=model)
    print(f'      Done in {time.time()-t1:.1f}s  '
          f'({report["_meta"]["input_tokens"]} in / {report["_meta"]["output_tokens"]} out)',
          file=sys.stderr)

    with open(report_path, 'w') as f:
        json.dump(report, f, indent=2)
    print(f'      Report JSON saved: {report_path}', file=sys.stderr)

    # ── Step 3: Render PDF ──
    print(f'\n[3/3] Rendering PDF...', file=sys.stderr)
    t2 = time.time()
    final_path = render_pdf(report, pdf_path)
    print(f'      Done in {time.time()-t2:.1f}s', file=sys.stderr)

    print(f'\n{"="*60}', file=sys.stderr)
    print(f'  ✓  PDF ready: {final_path}', file=sys.stderr)
    print(f'     Total time: {time.time()-t0:.1f}s', file=sys.stderr)
    print(f'{"="*60}\n', file=sys.stderr)

    # Emit PDF path to stdout for scripting
    print(final_path)


if __name__ == '__main__':
    main()
