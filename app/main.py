"""
main.py — Donor Readiness Audit
Crawls a URL, generates a report via Claude, and renders a PDF.

Supports two execution modes:
  - Lambda: invoked by AWS Lambda with event {url, email}
  - CLI:    python main.py <url> [output.pdf]

Environment:
    ANTHROPIC_API_KEY   required
    AUDIT_MODEL         optional, default: claude-opus-4-6
    FROM_EMAIL          optional, default: audits@heyupstart.com
    AWS_REGION          optional, default: us-east-1
"""

import json
import os
import re
import sys
import time
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from urllib.parse import urlparse

# Local modules
from crawler import crawl
from prompt import generate_report
from renderer import render_pdf


def safe_filename(s: str) -> str:
    """Convert a string to a safe filename component."""
    return re.sub(r'[^\w\-.]', '_', s)


# ── Email ──────────────────────────────────────────────────────────────────────

def send_report_email(to_email: str, org_name: str, pdf_path: str) -> None:
    """Send the PDF report to the visitor via SES."""
    import boto3

    from_email = os.environ.get('FROM_EMAIL', 'audits@heyupstart.com')
    region = os.environ.get('AWS_REGION', 'us-east-1')

    msg = MIMEMultipart()
    msg['Subject'] = f'Your Donor Readiness Audit — {org_name}'
    msg['From'] = from_email
    msg['To'] = to_email

    body_text = (
        f"Hi,\n\n"
        f"Your Donor Readiness Audit for {org_name} is attached.\n\n"
        f"It's a quick read — a few things working in your favor, and a handful of specific gaps "
        f"that are costing you real engagement.\n\n"
        f"If you'd like to talk through any of it, just reply here or book time at heyupstart.com.\n\n"
        f"— Jeff\n"
        f"UpStart Productions\n"
        f"heyupstart.com"
    )
    msg.attach(MIMEText(body_text, 'plain'))

    with open(pdf_path, 'rb') as f:
        pdf_data = f.read()

    attachment = MIMEApplication(pdf_data, _subtype='pdf')
    safe_org = safe_filename(org_name)
    attachment.add_header(
        'Content-Disposition', 'attachment',
        filename=f'{safe_org}_Donor_Readiness_Audit.pdf'
    )
    msg.attach(attachment)

    ses = boto3.client('ses', region_name=region)
    ses.send_raw_email(
        Source=from_email,
        Destinations=[to_email],
        RawMessage={'Data': msg.as_string()},
    )
    print(f'[email] Report sent to {to_email}', file=sys.stderr)


# ── Core pipeline ──────────────────────────────────────────────────────────────

def run_audit(url: str, output_dir: str = '/tmp') -> dict:
    """
    Core audit pipeline: crawl → generate → render PDF.
    Returns a dict with keys: pdf_path, report, signals.
    """
    domain = urlparse(url if '://' in url else 'https://' + url).netloc or url
    stem = safe_filename(domain)

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    pdf_path = str(output_path / f'{stem}_donor_readiness.pdf')
    model = os.environ.get('AUDIT_MODEL', 'claude-opus-4-6')

    print(f'[audit] Starting: {url}  model={model}', file=sys.stderr)

    print('[1/3] Crawling...', file=sys.stderr)
    t0 = time.time()
    signals = crawl(url)
    print(f'[1/3] Done in {time.time()-t0:.1f}s', file=sys.stderr)

    print('[2/3] Generating report...', file=sys.stderr)
    t1 = time.time()
    report = generate_report(signals, model=model)
    print(f'[2/3] Done in {time.time()-t1:.1f}s', file=sys.stderr)

    print('[3/3] Rendering PDF...', file=sys.stderr)
    t2 = time.time()
    final_path = render_pdf(report, pdf_path)
    print(f'[3/3] Done in {time.time()-t2:.1f}s', file=sys.stderr)

    print(f'[audit] Complete. PDF: {final_path}', file=sys.stderr)
    return {'pdf_path': final_path, 'report': report, 'signals': signals}


# ── Lambda handler ─────────────────────────────────────────────────────────────

def lambda_handler(event, context):
    """
    AWS Lambda entrypoint.
    Expects event body: { "url": "...", "email": "..." }
    """
    try:
        # API Gateway passes the body as a JSON string; direct invocations pass a dict
        body = event
        if 'body' in event:
            body = json.loads(event['body'] or '{}')

        url   = (body.get('url')   or '').strip()
        email = (body.get('email') or '').strip()

        if not url:
            return {'statusCode': 400, 'body': json.dumps({'error': 'url is required'})}
        if not email:
            return {'statusCode': 400, 'body': json.dumps({'error': 'email is required'})}

        result   = run_audit(url, output_dir='/tmp')
        org_name = result['report'].get('org_name', 'Your Organization')

        send_report_email(email, org_name, result['pdf_path'])

        return {
            'statusCode': 200,
            'body': json.dumps({'message': f'Report for {org_name} sent to {email}'}),
        }

    except Exception as e:
        print(f'[lambda_handler] ERROR: {e}', file=sys.stderr)
        raise  # Let Lambda log the full traceback


# ── CLI entry point ────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print('Usage: python main.py <url> [output.pdf]', file=sys.stderr)
        sys.exit(1)

    url = sys.argv[1]
    domain = urlparse(url if '://' in url else 'https://' + url).netloc or url
    timestamp = time.strftime('%Y%m%d_%H%M%S')

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

    print('[1/3] Crawling site...', file=sys.stderr)
    t0 = time.time()
    signals = crawl(url)
    print(f'      Done in {time.time()-t0:.1f}s', file=sys.stderr)

    with open(signals_path, 'w') as f:
        json.dump(signals, f, indent=2)
    print(f'      Signals saved: {signals_path}', file=sys.stderr)

    print(f'\n[2/3] Generating report ({model})...', file=sys.stderr)
    t1 = time.time()
    report = generate_report(signals, model=model)
    print(f'      Done in {time.time()-t1:.1f}s  '
          f'({report["_meta"]["input_tokens"]} in / {report["_meta"]["output_tokens"]} out)',
          file=sys.stderr)

    with open(report_path, 'w') as f:
        json.dump(report, f, indent=2)
    print(f'      Report JSON saved: {report_path}', file=sys.stderr)

    print(f'\n[3/3] Rendering PDF...', file=sys.stderr)
    t2 = time.time()
    final_path = render_pdf(report, pdf_path)
    print(f'      Done in {time.time()-t2:.1f}s', file=sys.stderr)

    print(f'\n{"="*60}', file=sys.stderr)
    print(f'  ✓  PDF ready: {final_path}', file=sys.stderr)
    print(f'     Total time: {time.time()-t0:.1f}s', file=sys.stderr)
    print(f'{"="*60}\n', file=sys.stderr)

    print(final_path)


if __name__ == '__main__':
    main()
