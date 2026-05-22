"""
renderer.py — Donor Readiness Audit
Renders a report dict to a PDF file using Jinja2 + Playwright.

Usage (standalone test):
    python renderer.py <report_json_file> [output.pdf]
    python renderer.py /tmp/report_miracle.json /tmp/miracle_report.pdf
"""

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from jinja2 import Environment, FileSystemLoader
from playwright.sync_api import sync_playwright


TEMPLATE_DIR = Path(__file__).parent
TEMPLATE_FILE = 'template.html'


def render_pdf(report: dict, output_path: str) -> str:
    """
    Render the report dict to a PDF at output_path.
    Returns the absolute path to the written PDF.
    """
    context = {
        'org_name': report.get('org_name', 'Your Organization'),
        'domain': report.get('domain', report.get('_meta', {}).get('domain', '')),
        # 'scores': report.get('scores', None),  # SCORING DISABLED — uncomment to re-enable
        'opening': report.get('opening', ''),
        'whats_working': report.get('whats_working', []),
        'findings': report.get('findings', []),
        'closing': report.get('closing', ''),
        'report_date': time.strftime('%-d %B %Y'),
    }

    env = Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)))
    template = env.get_template(TEMPLATE_FILE)
    html_content = template.render(**context)

    with tempfile.NamedTemporaryFile(
        mode='w', suffix='.html', delete=False, encoding='utf-8'
    ) as f:
        f.write(html_content)
        tmp_html = f.name

    print(f'[renderer] HTML written to {tmp_html}', file=sys.stderr)

    abs_output = os.path.abspath(output_path)

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                '--no-sandbox',
                '--disable-dev-shm-usage',
                '--disable-gpu',
                '--single-process',
            ]
        )
        page = browser.new_page()
        page.goto(f'file://{tmp_html}', wait_until='networkidle')
        page.wait_for_timeout(500)

        footer_template = """
        <div style="
            -webkit-print-color-adjust: exact;
            print-color-adjust: exact;
            background: #1A1828;
            width: 100%;
            height: 36px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding: 0 0.5in;
            box-sizing: border-box;
            font-family: Arial, Helvetica, sans-serif;
        ">
            <span style="font-size:9pt; font-weight:300; color:rgba(255,255,255,0.75); letter-spacing:0.02em;">
                Technology that serves your mission.
            </span>
            <span style="font-size:9pt; font-weight:700; color:#ffffff; letter-spacing:0.1em; text-transform:uppercase;">
                HEYUPSTART.COM
            </span>
        </div>
        """

        page.pdf(
            path=abs_output,
            format='Letter',
            print_background=True,
            prefer_css_page_size=False,
            display_header_footer=True,
            header_template='<span></span>',
            footer_template=footer_template,
            margin={
                'top': '0.5in',
                'right': '0.5in',
                'bottom': '0.5in',
                'left': '0.5in',
            }
        )
        browser.close()

    os.unlink(tmp_html)
    print(f'[renderer] PDF written to {abs_output}', file=sys.stderr)
    return abs_output


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print('Usage: python renderer.py <report_json_file> [output.pdf]', file=sys.stderr)
        sys.exit(1)

    report_file = sys.argv[1]
    output_file = sys.argv[2] if len(sys.argv) > 2 else '/tmp/donor_readiness_report.pdf'

    with open(report_file) as f:
        report = json.load(f)

    path = render_pdf(report, output_file)
    print(f'PDF saved: {path}')

    # Auto-open on macOS
    if sys.platform == 'darwin':
        subprocess.run(['open', path])
