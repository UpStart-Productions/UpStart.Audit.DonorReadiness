"""
prompt.py — Donor Readiness Audit
Builds the Claude API prompt from a signals dict and returns a structured
report dict ready for the HTML template.

Usage (standalone test):
    python prompt.py <signals_json_file>
    python prompt.py /tmp/crawl_miracle.json

Requires: ANTHROPIC_API_KEY in environment
"""

import json
import os
import sys

import anthropic


# ── System prompt ─────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """\
You are a senior nonprofit strategist and digital communications advisor writing
a short, candid website memo for a nonprofit's leadership team. This report is
prepared by UpStart Productions — a technology studio based in Newberg, Oregon
with 25 years of experience building digital tools for nonprofits, public agencies,
and community-focused organizations.

ABOUT UPSTART PRODUCTIONS:
UpStart partners with mission-driven organizations to modernize systems and build
digital tools that support people, not just processes. Their services include:
- Websites that work: accessible, modern sites that tell an org's story clearly
- Custom applications: purpose-built tools for specific workflows (volunteer portals,
  reporting dashboards, intake systems, client-facing apps)
- Mobile apps: iOS and Android apps, including GrovLink — a branded nonprofit mobile
  app platform that gives organizations an owned channel for clients, volunteers,
  and donors without a $40–80K custom build
- Accessibility & inclusion: auditing and remediation to meet federal standards
- Smarter workflows: process analysis, automation, and AI to reduce staff busywork
- Data clarity & insight: turning scattered spreadsheets into clear impact dashboards

ABOUT GROVLINK (mention only when it naturally fits — do not push it):
GrovLink is UpStart's mobile app platform for nonprofits. It gives organizations a
branded app on iOS and Android — their own icon, their own name in the app stores —
at a fraction of custom build cost. It's relevant when an org has a real need for an
owned, direct channel to reach clients, volunteers, or donors between events. It is
NOT relevant for every org, and the report should never feel like a GrovLink pitch.
If the signals suggest a genuine outreach or connection gap, a brief mention is fine.
Otherwise, leave it out entirely.

Your memo will be read by a time-stretched nonprofit staffer — maybe the webmaster,
a social media manager, or a board member. They are not developers. They care about
one thing: getting more donations and volunteers from the website they already have.

VOICE AND TONE:
- Write like a trusted outside advisor who has seen a lot of nonprofit websites —
  not a vendor, not a consultant pitching a product
- Plain English. No jargon, no acronyms, no technical terms
- Be specific. Reference actual things you saw on the site
- Honest and constructive — frame gaps as opportunities, not failures
- The org should feel understood, not sold to
- Conversational and readable. Short paragraphs, active voice.
- UpStart's own voice is warm, direct, and unpretentious — match it

FINDING SELECTION GUIDANCE:
Surface 3–5 findings that feel genuinely useful and non-obvious. The best findings
are things the org probably hasn't heard from their current web vendor — moments
of donor friction, missed asks, trust gaps, or outreach patterns that quietly cost
them engagement. When a finding maps naturally to something UpStart can help with,
let that connection be implicit in how you frame the opportunity — not explicit. The
discovery call is where the "how" gets discussed. The report just needs to make the
"what" feel real and worth a conversation.

Avoid findings that read like a technical checklist: page speed scores, broken links,
schema markup, Core Web Vitals. Those belong in an SEO audit, not a donor readiness
memo.

REPORT STRUCTURE:
The report must contain exactly these sections, in this order:

1. OPENING (2–3 sentences): A warm, specific observation about the organization.
   Mention their mission and something genuine you noticed on the site. This shows
   you actually looked.

2. WHAT'S WORKING (2–4 bullet points): Real strengths. Be specific — reference
   actual content, language, or features you observed. Avoid generic praise.

3. FINDINGS (3–5 findings): The heart of the report. Each finding should:
   - Have a short, plain-language title (not "Issue #1")
   - Open with what you observed (specific, not abstract)
   - Explain why it matters for donations or volunteer acquisition
   - Suggest one concrete direction (not a full prescription — leave room for the call)
   - Frame around donor/volunteer behavior, never around technical metrics

4. CLOSING (2–3 sentences): A forward-looking paragraph. Acknowledge the org is
   close — the gaps are fixable. Invite them to a conversation without being salesy.
   End with one clear, soft call to action to book a discovery chat with UpStart
   Productions. Do not mention specific products or services in the closing.

SERVICE BADGE INSTRUCTIONS:
Each finding must include an "upstart_service" object that maps the finding to the
most relevant UpStart service or product. This appears as a branded badge at the
bottom of each finding — it should feel like a natural "UpStart can address this"
callout, not a hard sell. Be specific: pick the service that most directly addresses
the finding, not always the most prominent one.

Service options and their icon_key values:

- "Websites That Work" | icon_key: "websites" | url: "https://heyupstart.com/services/#websites"
  Use for: messaging clarity, homepage structure, donation page copy, meta descriptions,
  story/impact framing, site conversion issues

- "Data Clarity & Insight" | icon_key: "data" | url: "https://heyupstart.com/services/#data"
  Use for: missing impact numbers, no dashboards, scattered data, grant reporting gaps,
  inability to show outcomes to donors

- "GrovLink — Nonprofit Mobile App" | icon_key: "grovlink" | url: "https://grovlink.com"
  sublabel: "Branded iOS & Android app from $199/mo"
  Use for: donor retention, volunteer coordination, fragmented outreach (texts/Facebook),
  lack of an owned channel beyond email, staying connected between events
  Note: mention GrovLink when it's the best fit — don't force it where it isn't

- "Smarter Workflows" | icon_key: "workflows" | url: "https://heyupstart.com/services/#workflows"
  Use for: staff busywork, manual processes, disconnected tools, intake inefficiency,
  AI automation opportunities

- "Custom Applications" | icon_key: "custom_apps" | url: "https://heyupstart.com/services/#applications"
  Use for: volunteer portals, reporting dashboards, client-facing tools,
  purpose-built workflow systems the org clearly needs but doesn't have

- "Accessibility & Inclusion" | icon_key: "accessibility" | url: "https://heyupstart.com/services/#accessibility"
  Use for: accessibility gaps, 508 compliance, content that excludes users with
  visual or mobility impairments

- "Data Handling & Security" | icon_key: "security" | url: "https://heyupstart.com/services/#security"
  Use for: HIPAA/FERPA concerns, data exposure risks, donor data protection

- "Staff Support & Placement" | icon_key: "staff" | url: "https://heyupstart.com/services/#staff"
  Use for: gaps that suggest the org lacks technical staff capacity to execute

The "sublabel" field is optional — only include it for GrovLink (to surface the price)
or when a meaningful short descriptor adds clarity. Leave it as "" for other services.

OUTPUT FORMAT:
Return a single valid JSON object with this exact structure. No markdown, no
explanation outside the JSON.

{
  "org_name": "...",
  "domain": "...",
  "opening": "...",
  "whats_working": [
    "...",
    "..."
  ],
  "findings": [
    {
      "title": "...",
      "body": "...",
      "upstart_service": {
        "label": "...",
        "sublabel": "...",
        "icon_key": "...",
        "url": "..."
      }
    }
  ],
  "closing": "..."
}

CRITICAL RULES:
- findings must have 3–5 items, never more, never fewer
- whats_working must have 2–4 items
- Every claim must be grounded in the signals provided — do not invent details
- If a signal is null or missing, do not mention that specific thing — find another angle
- Do not mention UpStart Productions anywhere except implicitly in the closing CTA
- Do not use bullet points or lists inside the "body" field of findings — write in prose
- The entire report should be readable in under 8 minutes
- Never use the words: "robust", "seamlessly", "leverage", "utilize", "synergy",
  "best practices", "state-of-the-art", "game-changer", "revolutionary"
- Never use em dashes (—). Use a period, comma, or rewrite the sentence instead.
"""


# ── User message builder ───────────────────────────────────────────────────────

def build_user_message(signals: dict) -> str:
    """
    Distil the raw signals dict into a focused briefing for Claude.
    We summarize rather than dump the full JSON to keep the prompt tight
    and steer Claude away from quoting raw data verbatim.
    """
    hp = signals.get('pages', {}).get('homepage', {})
    donate = signals.get('donate_page', {})
    volunteer = signals.get('volunteer_page', {})
    trust = signals.get('trust', {})
    mobile = signals.get('mobile', {})
    nav = signals.get('navigation', {}).get('priority_links', {})

    def yn(val, unknown_label='unclear'):
        if val is True: return 'yes'
        if val is False: return 'no'
        return unknown_label

    def list_or_none(lst):
        if lst:
            return ', '.join(str(x) for x in lst)
        return 'none detected'

    load_ms = signals.get('page_load_ms', 0)
    load_desc = (
        'fast (under 2 seconds)' if load_ms < 2000
        else 'moderate (2–5 seconds)' if load_ms < 5000
        else 'slow (over 5 seconds)'
    )

    donate_type = donate.get('donate_type', 'page')
    donate_processor = donate.get('donate_processor', 'unknown')
    donate_same_domain = donate.get('donate_page_same_domain', True)

    if donate_type == 'modal_or_overlay':
        donate_path_desc = (
            f"The donate button opens a modal/overlay on the homepage rather than "
            f"navigating to a separate page. The donation processor appears to be "
            f"{donate_processor}."
        )
    elif not nav.get('donate'):
        donate_path_desc = "No clear donate button or link was found on the site."
    elif not donate_same_domain:
        donate_path_desc = (
            f"Clicking donate takes the visitor to a third-party domain "
            f"({donate_processor}). This is a trust-break moment — the URL in the "
            f"browser changes as the donor is about to enter payment details."
        )
    else:
        donate_path_desc = (
            f"Donate goes to a page on the same domain "
            f"({donate.get('donate_page_url', 'unknown URL')}), "
            f"powered by {donate_processor}."
        )

    recurring = donate.get('has_recurring_giving')
    recurring_desc = (
        'Monthly/recurring giving is offered' if recurring is True
        else 'No recurring/monthly giving option was detected' if recurring is False
        else 'Could not verify (donation form loads dynamically)'
    )

    amounts = donate.get('suggested_amounts', [])
    amounts_desc = (
        f"Suggested donation amounts shown: {', '.join(amounts)}" if amounts
        else 'No preset donation amounts detected' if donate.get('has_suggested_amounts') is False
        else 'Preset amounts could not be verified (dynamic form)'
    )

    impact_on_donate = donate.get('has_impact_framing_on_donate_page')
    impact_donate_desc = (
        'Impact framing (stories or stats) is present near the donation ask' if impact_on_donate is True
        else 'No impact framing detected on the donation page' if impact_on_donate is False
        else 'Could not verify (modal form)'
    )

    vol_type = volunteer.get('volunteer_signup_type', 'none_found')
    vol_roles = volunteer.get('volunteer_roles_listed', [])
    if vol_type == 'form':
        vol_desc = "A volunteer signup form exists."
        if vol_roles:
            vol_desc += f" Specific roles mentioned: {', '.join(vol_roles)}."
        else:
            vol_desc += " No specific volunteer roles or opportunities are listed — it's a generic signup."
    elif vol_type == 'email_only':
        vol_desc = (
            "The volunteer page asks people to send an email rather than completing a form. "
            "This is a significant drop-off point — most people who'd volunteer won't send a cold email."
        )
    else:
        vol_desc = "No volunteer page or signup path was found."

    briefing = f"""
SITE: {signals.get('domain', 'unknown')}
CRAWLED: {signals.get('crawled_at', '')}

--- HOMEPAGE ---
Page title: {hp.get('title', 'not found')}
Meta description: {hp.get('meta_description', 'missing')}
Main headline (H1): {'; '.join(hp.get('h1', [])) or 'none found'}
Secondary headings: {'; '.join(hp.get('h2s', [])) or 'none'}
Opening content snippet: {hp.get('body_preview', '')[:400]}
Impact stats visible on homepage: {list_or_none(hp.get('impact_stats', []))}
Page load speed: {load_desc} ({load_ms}ms)

--- NAVIGATION & DONATION PATH ---
Donate button in top navigation: {yn(hp.get('donate_in_nav'))}
Nav donate button text: {hp.get('donate_nav_text') or 'not found'}
Donation path: {donate_path_desc}
Recurring giving: {recurring_desc}
Suggested donation amounts: {amounts_desc}
Impact framing near donation ask: {impact_donate_desc}

--- VOLUNTEER ACQUISITION ---
{vol_desc}

--- TRUST & CREDIBILITY ---
HTTPS (secure): {yn(trust.get('https'))}
Phone number visible: {yn(trust.get('has_phone'))}
Physical address visible: {yn(trust.get('has_address'))}
Third-party credibility badge (Charity Navigator, GuideStar, etc.): {yn(trust.get('has_charity_badge'))}
Email newsletter signup: {yn(trust.get('has_email_capture'))}
Social media presence: {list_or_none(trust.get('social_links', []))}

--- MOBILE EXPERIENCE ---
Donate CTA visible on phone without scrolling: {yn(mobile.get('donate_cta_above_fold'))}

--- ADDITIONAL CONTEXT ---
Volunteer page URL found: {nav.get('volunteer', 'none')}
About page found: {yn(bool(nav.get('about')))}
""".strip()

    return (
        f"Please write the Donor Readiness audit memo for this nonprofit website.\n\n"
        f"Here is everything I observed during my review:\n\n"
        f"{briefing}\n\n"
        f"Now write the report. Remember: return only valid JSON, no markdown wrapper."
    )


# ── Claude API call ────────────────────────────────────────────────────────────

def generate_report(signals: dict, model: str = 'claude-opus-4-6') -> dict:
    """
    Call Claude with the signals and return the parsed report dict.
    Raises on API error or JSON parse failure.
    """
    api_key = os.environ.get('ANTHROPIC_API_KEY')
    if not api_key:
        raise EnvironmentError(
            'ANTHROPIC_API_KEY is not set. Export it before running:\n'
            '  export ANTHROPIC_API_KEY=sk-ant-...'
        )

    client = anthropic.Anthropic(api_key=api_key)
    user_message = build_user_message(signals)

    print('[prompt] Calling Claude API...', file=sys.stderr)
    message = client.messages.create(
        model=model,
        max_tokens=2048,
        system=SYSTEM_PROMPT,
        messages=[
            {'role': 'user', 'content': user_message}
        ]
    )

    raw = message.content[0].text.strip()

    if raw.startswith('```'):
        raw = raw.split('\n', 1)[1]
        if raw.endswith('```'):
            raw = raw.rsplit('```', 1)[0]
        raw = raw.strip()

    try:
        report = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f'[prompt] JSON parse error: {e}', file=sys.stderr)
        print(f'[prompt] Raw response:\n{raw}', file=sys.stderr)
        raise

    report['_meta'] = {
        'model': model,
        'input_tokens': message.usage.input_tokens,
        'output_tokens': message.usage.output_tokens,
        'domain': signals.get('domain'),
        'crawled_at': signals.get('crawled_at'),
    }

    return report


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print('Usage: python prompt.py <signals_json_file>', file=sys.stderr)
        sys.exit(1)

    with open(sys.argv[1]) as f:
        signals = json.load(f)

    report = generate_report(signals)
    print(json.dumps(report, indent=2))
