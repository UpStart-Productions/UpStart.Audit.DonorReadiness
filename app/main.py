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
from email.utils import formataddr
from pathlib import Path
from urllib.parse import urlparse

# Local modules
from crawler import crawl
from companion import run_seo_audit, run_a11y_audit, crawl_seo_inline, crawl_a11y_inline
from prompt import generate_report
from renderer import render_pdf
from scorer import score as score_audit


# -- S3 job store --------------------------------------------------------------

AUDIT_BUCKET = os.environ.get('AUDIT_BUCKET', 'donor-readiness-audit-jobs')


def _normalize_domain(url: str) -> str:
    """Strip scheme, www., and trailing slash for use as S3 key prefix."""
    url = url.strip().lower()
    url = re.sub(r'^https?://', '', url)
    url = re.sub(r'^www\.', '', url)
    return url.rstrip('/')


def s3_write_status(domain_key: str, status: str, **extra) -> None:
    """Write {status, ...extra} to donor-readiness-audit-jobs/<domain>/status.json."""
    try:
        import boto3
        s3 = boto3.client('s3', region_name=os.environ.get('AWS_REGION', 'us-east-1'))
        s3.put_object(
            Bucket=AUDIT_BUCKET,
            Key=f'{domain_key}/status.json',
            Body=json.dumps({'status': status, **extra}),
            ContentType='application/json',
        )
    except Exception as e:
        print(f'[s3] status write failed ({status}): {e}', file=sys.stderr)


def _check_domain_reachable(url: str) -> str | None:
    """
    Resolve the domain in url. Returns None if reachable, or an error
    message string if DNS fails or the hostname is clearly invalid.
    """
    import socket
    try:
        parsed = urlparse(url if '://' in url else f'https://{url}')
        host = parsed.hostname or ''
        if not host or '.' not in host:
            return f'"{url}" doesn\'t look like a valid website address.'
        socket.getaddrinfo(host, None)
        return None
    except socket.gaierror:
        host = urlparse(url if '://' in url else f'https://{url}').hostname or url
        return (
            f'We couldn\'t find a website at {host}. '
            'Please double-check the URL and make sure the site is live, then try again.'
        )


def s3_write_report(domain_key: str, report: dict) -> None:
    """Write report JSON to S3."""
    try:
        import boto3
        s3 = boto3.client('s3', region_name=os.environ.get('AWS_REGION', 'us-east-1'))
        s3.put_object(
            Bucket=AUDIT_BUCKET,
            Key=f'{domain_key}/report.json',
            Body=json.dumps(report),
            ContentType='application/json',
        )
    except Exception as e:
        print(f'[s3] report write failed: {e}', file=sys.stderr)


def s3_write_pdf(domain_key: str, pdf_path: str) -> None:
    """Upload PDF to S3."""
    try:
        import boto3
        s3 = boto3.client('s3', region_name=os.environ.get('AWS_REGION', 'us-east-1'))
        with open(pdf_path, 'rb') as f:
            s3.put_object(
                Bucket=AUDIT_BUCKET,
                Key=f'{domain_key}/report.pdf',
                Body=f,
                ContentType='application/pdf',
            )
    except Exception as e:
        print(f'[s3] PDF write failed: {e}', file=sys.stderr)


def safe_filename(s: str) -> str:
    """Convert a string to a safe filename component."""
    return re.sub(r'[^\w\-.]', '_', s)


# ── Email ──────────────────────────────────────────────────────────────────────

FAVICON_URL   = 'https://heyupstart.com/favicon.png'
DARK_LOGO_URL = 'https://heyupstart.com/images/upstart-logo-dark.png'
BOOKING_URL   = 'https://heyupstart.com/chat'
UPSTART_COLOR = '#F5C400'   # UpStart gold/yellow
LINK_COLOR    = '#1a73e8'   # Blue for signature links


def _build_plain_text(org_name: str, first_name: str = '') -> str:
    greeting = f"Hi {first_name}," if first_name else "Hi,"
    return (
        f"{greeting}\n\n"
        f"Your Donor Engagement Snapshot for {org_name} is attached.\n\n"
        f"It's a quick read — a few things working well on the site, and some specific "
        f"gaps that are quietly costing you real engagement with donors and volunteers.\n\n"
        f"If anything in there raises a question, or you'd like to dig into any of it, "
        f"just reply here. I'm also happy to get on a call.\n\n"
        f"  {BOOKING_URL}\n\n"
        f"— Jeff\n"
        f"UpStart Productions\n"
        f"heyupstart.com"
    )


def _build_html(org_name: str, first_name: str = '') -> str:
    greeting = f"Hi {first_name}," if first_name else "Hi,"
    return f"""\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1.0">
  <title>Your Donor Engagement Snapshot</title>
</head>
<body style="margin:0;padding:0;background-color:#f4f4f4;font-family:Arial,Helvetica,sans-serif;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
         style="background-color:#f4f4f4;">
    <tr>
      <td align="center" style="padding:32px 16px;">

        <!-- Card -->
        <table role="presentation" width="560" cellpadding="0" cellspacing="0"
               style="max-width:560px;width:100%;background:#ffffff;border-radius:6px;
                      box-shadow:0 1px 4px rgba(0,0,0,0.08);">

          <!-- Header: purple gradient tagline -->
          <tr>
            <td style="background:linear-gradient(135deg,#7B6BB5 0%,#5A4A99 100%);
                       border-radius:6px 6px 0 0;padding:14px 36px;">
              <span style="font-family:Arial,Helvetica,sans-serif;font-size:13px;
                           font-weight:700;color:#ffffff;letter-spacing:0.01em;">
                Technology that serves your mission
              </span>
            </td>
          </tr>

          <!-- Logo lockup on white -->
          <tr>
            <td style="padding:20px 36px 8px;background:#ffffff;">
              <img src="{DARK_LOGO_URL}" alt="UpStart Productions" height="52"
                   style="display:block;height:52px;border:0;">
            </td>
          </tr>

          <!-- Body -->
          <tr>
            <td style="padding:36px 36px 8px;">
              <p style="margin:0 0 20px;font-size:16px;line-height:1.65;color:#1a1a1a;">
                {greeting}
              </p>
              <p style="margin:0 0 20px;font-size:16px;line-height:1.65;color:#1a1a1a;">
                Your <strong>Donor Engagement Snapshot</strong> for
                <strong>{org_name}</strong> is attached.
              </p>
              <p style="margin:0 0 20px;font-size:16px;line-height:1.65;color:#1a1a1a;">
                It's a quick read — a few things working well on the site, and some specific
                gaps that are quietly costing you real engagement with donors and volunteers.
              </p>
              <p style="margin:0 0 32px;font-size:16px;line-height:1.65;color:#1a1a1a;">
                If anything raises a question or you'd like to dig into any of it, just
                reply here. I'm also happy to get on a call.
              </p>

              <!-- CTA button -->
              <table role="presentation" cellpadding="0" cellspacing="0">
                <tr>
                  <td style="background:{UPSTART_COLOR};border-radius:4px;">
                    <a href="{BOOKING_URL}"
                       style="display:block;padding:13px 26px;font-size:15px;font-weight:700;
                              color:#1a1a1a;text-decoration:none;letter-spacing:0.01em;">
                      Book a Discovery Chat &rarr;
                    </a>
                  </td>
                </tr>
              </table>
            </td>
          </tr>

          <!-- Divider -->
          <tr>
            <td style="padding:32px 36px 0;">
              <hr style="border:none;border-top:1px solid #e8e8e8;margin:0;">
            </td>
          </tr>

          <!-- Signature -->
          <tr>
            <td style="padding:24px 36px 32px;">
              <table role="presentation" cellpadding="0" cellspacing="0">
                <tr>
                  <td style="padding-right:18px;border-right:2px solid #e0e0e0;
                             vertical-align:middle;">
                    <img src="{FAVICON_URL}" alt="up" height="44"
                         style="display:block;height:44px;border:0;">
                  </td>
                  <td style="padding-left:18px;vertical-align:middle;">
                    <p style="margin:0;font-size:15px;font-weight:700;color:#1a1a1a;">
                      Jeff Denton
                    </p>
                    <p style="margin:3px 0 0;font-size:13px;color:#555555;">
                      Founder, UpStart Productions LLC
                    </p>
                    <p style="margin:3px 0 0;font-size:13px;color:#555555;font-style:italic;">
                      Technology that serves your mission.
                    </p>
                    <p style="margin:5px 0 0;font-size:13px;">
                      <a href="https://heyupstart.com"
                         style="color:{LINK_COLOR};text-decoration:none;">
                        heyupstart.com</a>
                      &nbsp;&nbsp;|&nbsp;&nbsp;
                      <a href="mailto:jeff@heyupstart.com"
                         style="color:{LINK_COLOR};text-decoration:none;">
                        jeff@heyupstart.com</a>
                    </p>
                  </td>
                </tr>
              </table>
            </td>
          </tr>

        </table>
        <!-- /Card -->

      </td>
    </tr>
  </table>
</body>
</html>"""


def send_report_email(to_email: str, org_name: str, pdf_path: str, first_name: str = '') -> None:
    """Send the PDF report to the recipient via SES (HTML + plain-text fallback)."""
    import boto3

    from_email   = os.environ.get('FROM_EMAIL', 'hello@heyupstart.com')
    from_name    = os.environ.get('FROM_NAME', 'UpStart Productions')
    region       = os.environ.get('AWS_REGION', 'us-east-1')

    # Outer envelope: multipart/mixed  (body alternatives + attachment)
    outer = MIMEMultipart('mixed')
    outer['Subject'] = f'Your Donor Engagement Snapshot — {org_name}'
    outer['From']    = formataddr((from_name, from_email))
    outer['To']      = to_email

    # Inner: multipart/alternative so clients pick the best version they support
    alt = MIMEMultipart('alternative')
    alt.attach(MIMEText(_build_plain_text(org_name, first_name), 'plain', 'utf-8'))
    alt.attach(MIMEText(_build_html(org_name, first_name),       'html',  'utf-8'))
    outer.attach(alt)

    # PDF attachment
    with open(pdf_path, 'rb') as f:
        pdf_data = f.read()

    attachment = MIMEApplication(pdf_data, _subtype='pdf')
    safe_org   = safe_filename(org_name)
    attachment.add_header(
        'Content-Disposition', 'attachment',
        filename=f'{safe_org}_Donor_Engagement_Snapshot.pdf'
    )
    outer.attach(attachment)

    ses = boto3.client('ses', region_name=region)
    ses.send_raw_email(
        Source=formataddr((from_name, from_email)),
        Destinations=[to_email],
        RawMessage={'Data': outer.as_string()},
    )
    print(f'[email] Report sent to {to_email}', file=sys.stderr)


# ── UBO Pipeline Integration ───────────────────────────────────────────────────

# Maps audit report upstart_service labels → UBO serviceInterests values
_SERVICE_LABEL_MAP = {
    'GrovLink — Nonprofit Mobile App': 'GrovLink',
    'Websites That Work': 'Website',
    'Data Clarity & Insight': 'Consulting',
    'Custom App Development': 'Custom App',
    'Tech Assessment': 'Tech Assessment',
}


def _derive_service_interests(report: dict) -> list[str]:
    """Extract unique, normalized service interest labels from audit findings."""
    seen = set()
    result = []
    for finding in report.get('findings', []):
        raw_label = finding.get('upstart_service', {}).get('label', '')
        normalized = _SERVICE_LABEL_MAP.get(raw_label, raw_label)
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result


def post_to_ubo_pipeline(
    report: dict,
    domain_key: str,
    email: str,
    first_name: str = '',
    last_name: str = '',
    role: str = '',
    ran_at: str = '',
) -> None:
    """
    POST the completed audit to UBO's /leads/ingest endpoint.
    Creates a DISCOVERY-stage lead pre-populated from the audit data.
    Fires and forgets — errors are logged but do not interrupt the audit.
    """
    import urllib.request

    ubo_api_url = os.environ.get('UBO_API_URL', '').rstrip('/')
    api_key     = os.environ.get('UBO_API_KEY', '')

    if not ubo_api_url or not api_key:
        print('[ubo] UBO_API_URL or UBO_API_KEY not set — skipping pipeline ingest.',
              file=sys.stderr)
        return

    service_interests = _derive_service_interests(report)
    pdf_s3_key = f'{domain_key}/report.pdf'

    # Generate a presigned URL (1 year) so the link is clickable from UBO
    pdf_url = pdf_s3_key  # fallback to raw key if presign fails
    try:
        import boto3
        s3 = boto3.client('s3', region_name=os.environ.get('AWS_REGION', 'us-east-1'))
        pdf_url = s3.generate_presigned_url(
            'get_object',
            Params={'Bucket': AUDIT_BUCKET, 'Key': pdf_s3_key},
            ExpiresIn=60 * 60 * 24 * 365,  # 1 year
        )
    except Exception as e:
        print(f'[ubo] Could not generate presigned URL (non-fatal): {e}', file=sys.stderr)

    payload = {
        'organization':    report.get('org_name', ''),
        'website':         f'https://{domain_key}',
        'auditReportUrl':  pdf_url,
        'email':             email or None,
        'firstName':         first_name or None,
        'lastName':          last_name or None,
        'role':              role or None,
        'serviceInterests':  service_interests,
        'auditDate':         ran_at,
    }

    # Strip None values so the DTO's @IsOptional() validators stay clean
    payload = {k: v for k, v in payload.items() if v is not None}

    try:
        body = json.dumps(payload).encode('utf-8')
        req = urllib.request.Request(
            f'{ubo_api_url}/leads/ingest',
            data=body,
            headers={
                'Content-Type': 'application/json',
                'x-api-key':    api_key,
            },
            method='POST',
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())
            if result.get('duplicate'):
                print(f'[ubo] Duplicate lead — org already in pipeline: '
                      f'{result.get("organization")} (id={result.get("leadId")})',
                      file=sys.stderr)
            else:
                print(f'[ubo] Lead created in pipeline: '
                      f'{result.get("organization")} (id={result.get("leadId")})',
                      file=sys.stderr)
    except Exception as e:
        print(f'[ubo] Pipeline ingest failed (non-fatal): {e}', file=sys.stderr)


def send_notification_email(
    url: str,
    to_email: str,
    first_name: str = '',
    last_name: str = '',
    role: str = '',
    org_name: str = '',
    ran_at: str = '',
) -> None:
    """
    Send a plain-text CRM notification to jeff@heyupstart.com when an audit completes.
    Formatted for easy copy-paste into Notion.
    """
    import boto3

    notify_to  = os.environ.get('NOTIFY_EMAIL', 'jeff@heyupstart.com')
    from_email = os.environ.get('FROM_EMAIL', 'hello@heyupstart.com')
    from_name  = os.environ.get('FROM_NAME', 'UpStart Productions')
    region     = os.environ.get('AWS_REGION', 'us-east-1')

    full_name  = (first_name + ' ' + last_name).strip() or '(not provided)'
    role_str   = role or '(not provided)'
    org_str    = org_name or '(not detected)'
    divider    = '-' * 36

    body = '\n'.join([
        'New Donor Readiness Audit',
        divider,
        'Website:    ' + url,
        'Org:        ' + org_str,
        'Name:       ' + full_name,
        'Role:       ' + role_str,
        'Sent to:    ' + to_email,
        'Date/Time:  ' + ran_at,
        '',
    ])

    ses = boto3.client('ses', region_name=region)
    ses.send_email(
        Source=formataddr((from_name, from_email)),
        Destination={'ToAddresses': [notify_to]},
        Message={
            'Subject': {'Data': f'Audit completed — {url}', 'Charset': 'UTF-8'},
            'Body':    {'Text': {'Data': body, 'Charset': 'UTF-8'}},
        },
    )
    print(f'[email] Notification sent to {notify_to}', file=sys.stderr)


# ── Core pipeline ──────────────────────────────────────────────────────────────

def run_audit(url: str, output_dir: str = '/tmp', on_status=None) -> dict:
    """
    Core audit pipeline: crawl → score → generate → render PDF.
    Returns a dict with keys: pdf_path, report, signals, domain_key.

    on_status: optional callable(status_str) called between pipeline stages.
               Used by lambda_handler to write progress to S3.
    """
    def _status(s):
        if on_status:
            on_status(s)

    domain = urlparse(url if '://' in url else 'https://' + url).netloc or url
    stem = safe_filename(domain)
    domain_key = _normalize_domain(url)

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    pdf_path = str(output_path / f'{stem}_donor_readiness.pdf')
    model = os.environ.get('AUDIT_MODEL', 'claude-sonnet-4-6')

    print(f'[audit] Starting: {url}  model={model}', file=sys.stderr)

    _status('crawling')
    print('[1/3] Crawling (parallel: main + SEO + a11y)...', file=sys.stderr)
    t0 = time.time()
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=3) as pool:
        f_main = pool.submit(crawl, url)
        f_seo  = pool.submit(crawl_seo_inline, url)
        f_a11y = pool.submit(crawl_a11y_inline, url)
        signals      = f_main.result()
        seo_summary  = f_seo.result()
        a11y_summary = f_a11y.result()
    print(f'[1/3] All crawls done in {time.time()-t0:.1f}s', file=sys.stderr)

    companion_stats = {}
    if seo_summary:
        companion_stats['seo'] = seo_summary
    if a11y_summary:
        companion_stats['a11y'] = a11y_summary

    _status('analyzing')
    print('[1b/3] Scoring...', file=sys.stderr)
    scores = score_audit(signals, companion_stats if companion_stats else None)

    print('[2/3] Generating report...', file=sys.stderr)
    _status('generating')
    t1 = time.time()
    report = generate_report(
        signals,
        model=model,
        companion_stats=companion_stats if companion_stats else None,
    )
    print(f'[2/3] Done in {time.time()-t1:.1f}s', file=sys.stderr)

    # Merge scores into report
    report['scores'] = scores

    print('[3/3] Rendering PDF...', file=sys.stderr)
    t2 = time.time()
    final_path = render_pdf(report, pdf_path)
    print(f'[3/3] Done in {time.time()-t2:.1f}s', file=sys.stderr)

    print(f'[audit] Complete. PDF: {final_path}', file=sys.stderr)
    return {'pdf_path': final_path, 'report': report, 'signals': signals, 'domain_key': domain_key}


# ── Lambda handler ─────────────────────────────────────────────────────────────

CORS_HEADERS = {
    'Access-Control-Allow-Origin':  'https://heyupstart.com',
    'Access-Control-Allow-Headers': 'Content-Type',
    'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
}


def _s3_read_status(domain_key: str) -> dict | None:
    """Read status.json from S3. Returns None if not found."""
    try:
        import boto3
        s3 = boto3.client('s3', region_name=os.environ.get('AWS_REGION', 'us-east-1'))
        obj = s3.get_object(Bucket=AUDIT_BUCKET, Key=f'{domain_key}/status.json')
        return json.loads(obj['Body'].read())
    except Exception:
        return None


def _s3_read_report(domain_key: str) -> dict | None:
    """Read report.json from S3. Returns None if not found."""
    try:
        import boto3
        s3 = boto3.client('s3', region_name=os.environ.get('AWS_REGION', 'us-east-1'))
        obj = s3.get_object(Bucket=AUDIT_BUCKET, Key=f'{domain_key}/report.json')
        return json.loads(obj['Body'].read())
    except Exception:
        return None


def _handle_status_request(event) -> dict:
    """
    GET /status?domain=<domain>
    Returns { status, report? } — report is included when status == 'complete'.
    """
    params = event.get('queryStringParameters') or {}
    raw_domain = (params.get('domain') or '').strip()
    if not raw_domain:
        return {
            'statusCode': 400,
            'headers': CORS_HEADERS,
            'body': json.dumps({'error': 'domain parameter is required'}),
        }

    domain_key = _normalize_domain(raw_domain)
    status_data = _s3_read_status(domain_key)

    if status_data is None:
        return {
            'statusCode': 404,
            'headers': CORS_HEADERS,
            'body': json.dumps({'status': 'pending'}),
        }

    status = status_data.get('status', 'pending')

    if status == 'complete':
        report = _s3_read_report(domain_key)
        return {
            'statusCode': 200,
            'headers': CORS_HEADERS,
            'body': json.dumps({'status': 'complete', 'report': report}),
        }

    payload: dict = {'status': status}
    if 'message' in status_data:
        payload['message'] = status_data['message']
    return {
        'statusCode': 200,
        'headers': CORS_HEADERS,
        'body': json.dumps(payload),
    }


def lambda_handler(event, context):
    """
    AWS Lambda entrypoint.

    GET  ?domain=<domain>  → return audit status / report from S3
    POST {url, email, ...} → run audit pipeline
    """
    # HTTP API v2 puts method at requestContext.http.method
    # REST API v1 puts it at httpMethod — support both
    method = (
        event.get('httpMethod')
        or event.get('requestContext', {}).get('http', {}).get('method', 'POST')
    ).upper()

    # OPTIONS preflight
    if method == 'OPTIONS':
        return {'statusCode': 200, 'headers': CORS_HEADERS, 'body': ''}

    # Status endpoint
    if method == 'GET':
        return _handle_status_request(event)

    t_start = time.time()
    url = ''
    try:
        # API Gateway passes the body as a JSON string; direct invocations pass a dict
        body = event
        if 'body' in event:
            body = json.loads(event['body'] or '{}')

        url        = (body.get('url')        or '').strip()
        email      = (body.get('email')      or '').strip()
        first_name = (body.get('firstName')  or '').strip()
        last_name  = (body.get('lastName')   or '').strip()
        role       = (body.get('role')       or '').strip()

        if not url:
            return {'statusCode': 400, 'body': json.dumps({'error': 'url is required'})}
        if not email:
            return {'statusCode': 400, 'body': json.dumps({'error': 'email is required'})}

        domain_key = _normalize_domain(url)

        # DNS pre-check — fail fast before spending time crawling
        dns_error = _check_domain_reachable(url)
        if dns_error:
            print(json.dumps({'event': 'AUDIT_INVALID_DOMAIN', 'url': url, 'reason': dns_error}))
            s3_write_status(domain_key, 'error', message=dns_error)
            return {
                'statusCode': 200,
                'headers': CORS_HEADERS,
                'body': json.dumps({'message': dns_error}),
            }

        ran_at = time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())
        print(json.dumps({'event': 'AUDIT_STARTED', 'url': url, 'email': email,
                          'firstName': first_name, 'lastName': last_name, 'role': role}))

        s3_write_status(domain_key, 'crawling')

        result   = run_audit(
            url,
            output_dir='/tmp',
            on_status=lambda s: s3_write_status(domain_key, s),
        )
        org_name = result['report'].get('org_name', 'Your Organization')

        # Write report + PDF to S3, then mark complete
        s3_write_report(domain_key, result['report'])
        s3_write_pdf(domain_key, result['pdf_path'])
        s3_write_status(domain_key, 'complete')

        send_report_email(email, org_name, result['pdf_path'], first_name=first_name)
        send_notification_email(
            url=url, to_email=email,
            first_name=first_name, last_name=last_name,
            role=role, org_name=org_name, ran_at=ran_at,
        )
        post_to_ubo_pipeline(
            report=result['report'],
            domain_key=result['domain_key'],
            email=email,
            first_name=first_name,
            last_name=last_name,
            role=role,
            ran_at=ran_at,
        )

        elapsed = round(time.time() - t_start, 1)
        print(json.dumps({'event': 'AUDIT_COMPLETE', 'url': url, 'org': org_name, 'duration_s': elapsed}))

        return {
            'statusCode': 200,
            'headers': CORS_HEADERS,
            'body': json.dumps({'message': f'Report for {org_name} sent to {email}'}),
        }

    except Exception as e:
        elapsed = round(time.time() - t_start, 1)
        print(json.dumps({'event': 'AUDIT_FAILED', 'url': url, 'error': str(e), 'duration_s': elapsed}))
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

    # ── Companion audits: crawl phase ──────────────────────────────────────────
    # Run both crawls now (before Claude) so summary stats enrich the closing.
    # Report (.docx) generation happens after the main PDF.
    companion_stats = {}
    skip_companions = os.environ.get('SKIP_COMPANION_AUDITS', '').lower() in ('1', 'true', 'yes')

    if not skip_companions:
        print(f'\n[1b/3] SEO companion crawl...', file=sys.stderr)
        tc0 = time.time()
        seo_result = run_seo_audit(url, str(reports_dir))
        if seo_result:
            companion_stats['seo'] = seo_result.get('summary', {})
            print(f'       Done in {time.time()-tc0:.1f}s', file=sys.stderr)

        print(f'\n[1c/3] Accessibility companion crawl...', file=sys.stderr)
        tc1 = time.time()
        a11y_result = run_a11y_audit(url, str(reports_dir))
        if a11y_result:
            companion_stats['a11y'] = a11y_result.get('summary', {})
            print(f'       Done in {time.time()-tc1:.1f}s', file=sys.stderr)
    else:
        seo_result  = None
        a11y_result = None
        print(f'\n[1b/3] Companion audits skipped (SKIP_COMPANION_AUDITS=1)', file=sys.stderr)

    print(f'\n[2/3] Generating report ({model})...', file=sys.stderr)
    t1 = time.time()
    report = generate_report(
        signals,
        model=model,
        companion_stats=companion_stats if companion_stats else None,
    )
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

    # ── Companion audits: report generation phase ───────────────────────────────
    companion_paths = []
    if seo_result and seo_result.get('report_path'):
        companion_paths.append(('SEO audit', seo_result['report_path']))
    if a11y_result and a11y_result.get('report_path'):
        companion_paths.append(('A11y audit', a11y_result['report_path']))

    print(f'\n{"="*60}', file=sys.stderr)
    print(f'  ✓  PDF ready: {final_path}', file=sys.stderr)
    for label, cp in companion_paths:
        print(f'  ✓  {label}: {cp}', file=sys.stderr)
    print(f'     Total time: {time.time()-t0:.1f}s', file=sys.stderr)
    print(f'{"="*60}\n', file=sys.stderr)

    print(final_path)
    for _, cp in companion_paths:
        print(cp)


if __name__ == '__main__':
    main()
