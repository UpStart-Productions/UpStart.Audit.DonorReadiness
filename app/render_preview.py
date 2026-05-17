"""
render_preview.py — Fast template iteration
Renders template.html + sample_report.json → preview_artifact.html
Open preview_artifact.html in your browser, then just refresh after each run.

Usage:
    python3 render_preview.py
"""
import json
import time
from jinja2 import Environment, FileSystemLoader
from pathlib import Path

HERE = Path(__file__).parent

with open(HERE / "sample_report.json") as f:
    report = json.load(f)

context = {
    'org_name': report.get('org_name', 'Your Organization'),
    'domain': report.get('domain', report.get('_meta', {}).get('domain', '')),
    'opening': report.get('opening', ''),
    'whats_working': report.get('whats_working', []),
    'findings': report.get('findings', []),
    'closing': report.get('closing', ''),
    'report_date': time.strftime('%-d %B %Y'),
}

env = Environment(loader=FileSystemLoader(str(HERE)))
html = env.get_template('template.html').render(**context)

out = HERE / 'preview_artifact.html'
out.write_text(html)
print(f"✓  preview_artifact.html updated")
