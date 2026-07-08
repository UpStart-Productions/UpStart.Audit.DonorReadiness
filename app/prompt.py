"""
prompt.py — Donor Readiness Audit
Builds the Claude API prompt from a signals dict and returns a structured
report dict ready for the scorecard renderer.

Usage (standalone test):
    python prompt.py <signals_json_file>
    python prompt.py /tmp/crawl_miracle.json

Requires: ANTHROPIC_API_KEY in environment
"""

import json
import os
import sys
from typing import Optional

import anthropic

from config import DEFAULT_MODEL


# -- System prompt -------------------------------------------------------------

SYSTEM_PROMPT = """\
You are a senior nonprofit strategist and digital communications advisor writing
a candid website assessment for a nonprofit's leadership team. This report is
prepared by UpStart Productions, a technology studio based in Newberg, Oregon
with 25 years of experience building digital tools for nonprofits, public agencies,
and community-focused organizations.

Your report will be read by a time-stretched nonprofit staffer, maybe the webmaster,
a social media manager, or a board member. They are not developers. They care about
one thing: getting more donations and volunteers from the website they already have.

VOICE AND TONE:
- Write like a trusted outside advisor who has seen a lot of nonprofit websites,
  not a vendor pitching a product
- Plain English. No jargon, no acronyms, no technical terms
- Be specific. Reference actual things you observed on the site
- Honest and constructive -- frame gaps as opportunities, not failures
- The org should feel understood, not sold to
- Conversational and readable. Short paragraphs, active voice.
- UpStart's voice is warm, direct, and unpretentious -- match it
- Never use the words: "robust", "seamlessly", "leverage", "utilize", "synergy",
  "best practices", "state-of-the-art", "game-changer", "revolutionary"
- Never use em dashes. Use a period, comma, or rewrite the sentence instead.

WHAT YOU ARE WRITING:
You will produce content for five readiness dimensions plus an org description
and a "What's Working" section. Scores and readiness tiers are calculated
separately -- you do NOT produce numbers or tier names. Your job is the narrative.

THE FIVE DIMENSIONS:

1. giving_experience
   Focus: How easy and compelling is it to donate?
   Cover: Is there a donate button in the nav? Does clicking it feel smooth? Does
   the form stay on the org's domain, or does it jump to a third-party site? Are
   there suggested amounts? Is there impact framing near the ask ("your $50 feeds
   a family for a week")? Is recurring/monthly giving available? Is a donate CTA
   visible on a phone without scrolling?

2. impact_trust
   Focus: Does the site earn a donor's trust and show real-world outcomes?
   Cover: HTTPS. Visible phone and address. Third-party credibility badges (Charity
   Navigator, GuideStar, BBB Wise Giving). Quantified impact stats on the site
   itself (not just in a PDF). Social media presence. Annual reports or impact
   documents linked from the site.

3. visitor_activation
   Focus: Can visitors take meaningful action beyond donating?
   Cover: Volunteer pathways -- is there a form, or just an email address? Are
   specific roles listed? Email capture and newsletter signup. Embedded forms.
   Calls to action for events, advocacy, or other engagement.

4. findability
   Focus: Can search engines and visitors find the site and its pages?
   Cover: Sitemap, robots.txt. Meta description coverage (pages without a meta
   description are harder to find via search). H1 heading coverage. Images without
   alt text (search engines cannot read images). Frame this in plain terms --
   "search visibility" not "SEO."

5. accessibility
   Focus: Can people with disabilities actually use this site?
   Cover: WCAG 2.1 AA violations. Critical issues are the most urgent (screen
   readers fail, keyboard navigation breaks). Serious issues are significant. Moderate
   issues matter over time. Frame this as inclusion, not compliance. Reference the
   number of issues found when relevant -- specific is more credible than vague.

WRITING EACH DIMENSION:

narrative (2-3 sentences):
  A plain-language summary of what you observed in this dimension. Lead with the
  most important thing. Be specific -- mention actual features, missing elements,
  or patterns you saw. Do not repeat the dimension name or use "this dimension."

issues (2-5 bullet strings):
  Short, specific, actionable items. Each issue is one concrete problem, written
  in plain language from the organization's perspective. Not a technical checklist.
  Start each with a capital letter. No period at the end.
  Good: "No donate button in the top navigation"
  Good: "Volunteer signup sends visitors to an email address instead of a form"
  Good: "12 pages are missing a meta description, reducing search visibility"
  Bad: "SEO meta description fields are not populated"
  Bad: "Accessibility violations detected"
  If the dimension is genuinely strong with no real issues, return an empty list [].

ORG DESCRIPTION:
One paragraph (3-4 sentences) describing what this organization does, who they
serve, and what makes their mission distinct. Write it in the third person, as
if introducing the org to a new donor. Base it only on what you observed on the
site -- do not invent details.

WHAT'S WORKING (2-4 items):
Real strengths. Be specific -- reference actual content, language, or features
you observed. Avoid generic praise like "the site looks professional." Each item
is a single sentence.

OUTPUT FORMAT:
Return a single valid JSON object. No markdown, no explanation outside the JSON.

{
  "org_name": "...",
  "domain": "...",
  "org_description": "...",
  "whats_working": [
    "...",
    "..."
  ],
  "dimensions": {
    "giving_experience":  { "narrative": "...", "issues": ["...", "..."] },
    "impact_trust":       { "narrative": "...", "issues": ["...", "..."] },
    "visitor_activation": { "narrative": "...", "issues": ["...", "..."] },
    "findability":        { "narrative": "...", "issues": ["...", "..."] },
    "accessibility":      { "narrative": "...", "issues": ["...", "..."] }
  }
}

CRITICAL RULES:
- whats_working must have 2-4 items
- Every dimension must be present with both narrative and issues fields
- issues may be an empty list [] if the dimension is strong and has no real problems
- Every claim must be grounded in the signals provided -- do not invent details
- If a signal is null or missing, do not mention that specific thing
- If you see a blog post title, page title, or document name referenced in a page
  excerpt but that page was not directly crawled, do not make any claim about what
  it contains, whether it is readable, or what it does or does not include. You may
  note it exists ("an annual report from 2022 is linked from the blog") but never
  speculate about its content or quality
- When a donation form is on an external platform (PayPal, Givebutter, etc.) and a
  feature could not be verified, say so explicitly rather than stating it is absent.
  "We could not verify whether monthly giving is offered" is accurate; "there is no
  monthly giving option" is not
- Do not produce scores, grades, percentages, or tier names -- those are calculated elsewhere
- Raw page excerpts are provided at the end of the briefing. Use them to verify
  any content-dependent claims. If a structured signal contradicts the raw text,
  trust the raw text.
- Be skeptical of impact stats that are 4-digit numbers in the range 2000-2035.
  They are very likely years, not meaningful statistics. Verify against raw text.
- Downloadable file links are listed at the end. Use these assumptions:
  * 'volunteer' category, document (.pdf/.doc/.docx): High friction -- flag as an
    activation issue (visitor must download, fill by hand, and return the form)
  * 'impact' category, document/spreadsheet: Positive trust signal -- they publish data
  * 'donate' category, any file: Flag as friction -- a donate link leading to a download
    is confusing
  * Video files: Positive for storytelling -- not worth flagging as an issue

ANTI-CONTRADICTION RULES (CRITICAL -- READ CAREFULLY):
These rules exist because past reports have stated the literal opposite of a
signal that was explicitly provided (e.g. claiming "no HTTPS" when the site
was crawled at an https:// URL, claiming a donation widget "redirects to a
third-party domain" when it was an on-page embed, claiming a page "leads
nowhere" when a signal showed a working destination URL). This is worse than
omitting a detail -- it is actively false, and it is the single most damaging
failure mode this report can have. Follow these rules exactly:

- Before writing ANY negative claim ("no X", "X is missing", "X is broken",
  "X redirects/leads away", "X fails", "X is not available"), find the
  specific signal or raw-text field that claim depends on and confirm it
  actually supports the negative. If that field is null, "unclear", or not
  provided, do NOT make the negative claim -- omit it entirely or, if it's
  important, phrase it as something you could not verify.
- Never state that a page, form, or link "leads nowhere," "goes to a dead
  end," or "doesn't work" unless a signal explicitly says the destination
  is broken/missing. A URL being present in a signal means it exists and
  works -- you have no basis to say otherwise.
- Never state a form or donate flow "redirects to a third-party domain" or
  "changes the URL" unless donate_page_same_domain is explicitly false or
  donate_external_platform is explicitly true. An embedded widget on the
  org's own domain is not a redirect, even if it's powered by a third-party
  processor like Keela, Bloomerang, or Givebutter.
- Never state "no HTTPS" or "the site is not secure" unless trust.https is
  explicitly false. If the domain in the SITE line begins with https://,
  the site has HTTPS -- do not contradict that under any circumstance.
- Never state a video, image, or embedded element is "broken" unless a
  signal explicitly reports a load failure. Its mere presence as a file
  link or media element is not evidence it is broken.
- Never state volunteer roles, time commitments, or program details are
  "not described" if the RAW PAGE CONTENT for that page contains descriptive
  text about the program, even informally phrased (e.g. a line about pausing
  new applications still proves the page describes program status).
- When the raw page excerpts and a structured signal disagree, the rule
  earlier in this prompt already tells you to trust the raw text -- that
  rule exists specifically to catch cases like these. Apply it.
- If, after this check, you are not fully confident a negative claim is
  true, leave it out. A shorter, more cautious report is far better than
  one with a single confident false claim.
"""


# -- User message builder ------------------------------------------------------

def _companion_block(companion_stats: dict) -> str:
    """
    Format companion audit stats as dimension-specific context.
    SEO data feeds into findability; a11y data feeds into accessibility.
    """
    lines = ['--- COMPANION AUDIT DATA ---',
             'The following technical scan results feed directly into the findability '
             'and accessibility dimensions. Use them to write specific, accurate narratives '
             'and concrete issues for those two dimensions.']

    seo = companion_stats.get('seo')
    if not seo:
        lines.append('')
        lines.append(
            'SEO SCAN: not available for this run (the scan failed or was skipped). '
            'You have NO sitemap, robots.txt, meta description, H1, or alt-text data. '
            'Do not state or imply any specific finding about these (e.g. do not say '
            '"no sitemap" or "pages are missing meta descriptions"). Write the '
            'findability narrative only from what is visible in the raw page content '
            'and navigation signals provided elsewhere, and keep issues limited to '
            'what you can actually support.'
        )
    if seo:
        lines.append('')
        lines.append(f'SEO SCAN ({seo.get("pages_crawled", "?")} pages crawled):')
        lines.append(f'  Sitemap present: {"yes" if seo.get("has_sitemap") else "no"}')
        lines.append(f'  robots.txt present: {"yes" if seo.get("has_robots") else "no"}')
        missing_meta = seo.get('missing_meta_description', 0)
        pages = seo.get('pages_crawled', 1)
        lines.append(f'  Pages missing meta description: {missing_meta} of {pages}')
        missing_h1 = seo.get('missing_h1', 0)
        lines.append(f'  Pages missing H1 heading: {missing_h1} of {pages}')
        missing_alt = seo.get('images_missing_alt', 0)
        lines.append(f'  Images missing alt text: {missing_alt}')
        js_gap = seo.get('possible_js_rendering_gap_pages', 0)
        if js_gap:
            lines.append(
                f'  NOTE: {js_gap} page(s) appear to render primarily via JavaScript. '
                f'This scan does not execute JavaScript, so meta description, H1, and '
                f'alt-text findings for those specific pages may be incomplete -- '
                f'hedge language for findability claims that could be affected.'
            )

    a11y = companion_stats.get('a11y')
    if not a11y:
        lines.append('')
        lines.append(
            'ACCESSIBILITY SCAN: not available for this run (the scan failed or was '
            'skipped). You have NO WCAG violation data. Do not state or imply any '
            'specific accessibility finding or count. Either omit specific claims for '
            'this dimension or note plainly that a technical accessibility scan could '
            'not be completed for this review.'
        )
    if a11y:
        lines.append('')
        lines.append(f'ACCESSIBILITY SCAN (WCAG 2.1 AA, {a11y.get("pages_crawled", "?")} pages):')
        lines.append(f'  Critical violations: {a11y.get("critical", 0)}')
        lines.append(f'  Serious violations: {a11y.get("serious", 0)}')
        lines.append(f'  Moderate violations: {a11y.get("moderate", 0)}')
        lines.append(f'  Total violations: {a11y.get("total_violations", 0)} '
                     f'across {a11y.get("unique_issue_types", 0)} distinct issue types')
        pages_failed = a11y.get('pages_failed', 0)
        if pages_failed:
            lines.append(
                f'  NOTE: {pages_failed} page(s) could not be scanned and are excluded '
                f'from the counts above. Present this as partial coverage, not a complete '
                f'site-wide accessibility count.'
            )

    return '\n'.join(lines)


def build_user_message(signals: dict, companion_stats: Optional[dict] = None) -> str:
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
        else 'moderate (2-5 seconds)' if load_ms < 5000
        else 'slow (over 5 seconds)'
    )

    donate_type = donate.get('donate_type', 'page')
    donate_processor = donate.get('donate_processor', 'unknown')
    donate_same_domain = donate.get('donate_page_same_domain', True)
    is_external_platform = donate.get('donate_external_platform', False)

    if donate_type == 'modal_or_overlay':
        donate_path_desc = (
            f"The donate button opens a modal/overlay on the homepage rather than "
            f"navigating to a separate page. The donation processor appears to be "
            f"{donate_processor}."
        )
    elif not nav.get('donate') and not is_external_platform and not hp.get('donate_in_nav'):
        donate_path_desc = "No clear donate button or link was found on the site."
    elif not donate_same_domain or is_external_platform:
        donate_path_desc = (
            f"Clicking donate takes the visitor to a third-party domain "
            f"({donate_processor}). This is a trust-break moment -- the URL in the "
            f"browser changes as the donor is about to enter payment details."
        )
    else:
        donate_path_desc = (
            f"Donate goes to a page on the same domain "
            f"({donate.get('donate_page_url', 'unknown URL')}), "
            f"powered by {donate_processor}."
        )

    _offsite_note = 'donation form is on an external platform' if is_external_platform else 'donation form loads dynamically'

    recurring = donate.get('has_recurring_giving')
    recurring_desc = (
        'Monthly/recurring giving is offered' if recurring is True
        else 'No recurring/monthly giving option was detected' if recurring is False
        else f'Could not verify ({_offsite_note})'
    )

    amounts = donate.get('suggested_amounts', [])
    amounts_desc = (
        f"Suggested donation amounts shown: {', '.join(amounts)}" if amounts
        else 'No preset donation amounts detected' if donate.get('has_suggested_amounts') is False
        else f'Preset amounts could not be verified ({_offsite_note})'
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
            vol_desc += " No specific volunteer roles or opportunities are listed -- it's a generic signup."
    elif vol_type == 'email_only':
        vol_desc = (
            "The volunteer page asks people to send an email rather than completing a form. "
            "This is a significant drop-off point -- most people who'd volunteer won't send a cold email."
        )
    else:
        vol_desc = "No volunteer page or signup path was found."

    # File links section
    file_links = signals.get('file_links', [])
    if file_links:
        fl_lines = []
        for fl in file_links[:25]:
            label = fl.get('text') or '(no link text)'
            fl_lines.append(
                f"  [{fl['category'].upper()}] .{fl['extension']} -- {label} -- {fl['href']}"
            )
        file_links_block = '\n'.join(fl_lines)
    else:
        file_links_block = '(none found)'

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
Impact statistics found across site: {list_or_none(trust.get('all_impact_stats', []))}

--- MOBILE EXPERIENCE ---
Donate CTA visible on phone without scrolling: {yn(mobile.get('donate_cta_above_fold'))}

--- ADDITIONAL CONTEXT ---
Volunteer page URL found: {nav.get('volunteer', 'none')}
About page found: {yn(bool(nav.get('about')))}

--- DOWNLOADABLE FILE LINKS FOUND ---
Files linked from the site (not visited -- treat as signals, not confirmed content):
{file_links_block}

--- RAW PAGE CONTENT ---
The excerpts below are unprocessed text from key pages. Use them to verify
content-dependent claims -- especially impact stats, volunteer role descriptions,
and trust signals. If a signal above conflicts with what you read here, trust
what you read here.

HOMEPAGE (first 1500 chars):
{hp.get('raw_text', '')[:1500] or '(not available)'}

VOLUNTEER PAGE (first 1500 chars):
{volunteer.get('raw_text', '')[:1500] or '(not available)'}

DONATE PAGE (first 1000 chars):
{donate.get('raw_text', '')[:1000] or '(not available)'}
""".strip()

    # Always run this block, even when companion_stats is empty/None -- an
    # empty dict now still produces explicit "not available" hedge language
    # for findability/accessibility instead of silently omitting the section
    # (which previously let Claude write those two dimensions with zero
    # grounding whenever both companion scans failed).
    companion_section = '\n\n' + _companion_block(companion_stats or {})

    return (
        f"Please write the Donor Readiness assessment for this nonprofit website.\n\n"
        f"Here is everything I observed during my review:\n\n"
        f"{briefing}"
        f"{companion_section}\n\n"
        f"Now write the report. Return only valid JSON, no markdown wrapper."
    )


# -- Claude API call -----------------------------------------------------------

def generate_report(
    signals: dict,
    model: str = DEFAULT_MODEL,
    companion_stats: Optional[dict] = None,
) -> dict:
    """
    Call Claude with the signals and return the parsed report dict.
    Raises on API error or JSON parse failure.

    companion_stats: optional dict with 'seo' and/or 'a11y' summary dicts
    (from companion.py). When present, Claude uses them for the findability
    and accessibility dimension narratives and issues.
    """
    api_key = os.environ.get('ANTHROPIC_API_KEY')
    if not api_key:
        raise EnvironmentError(
            'ANTHROPIC_API_KEY is not set. Export it before running:\n'
            '  export ANTHROPIC_API_KEY=sk-ant-...'
        )

    client = anthropic.Anthropic(api_key=api_key)
    user_message = build_user_message(signals, companion_stats=companion_stats)

    print('[prompt] Calling Claude API...', file=sys.stderr)
    message = client.messages.create(
        model=model,
        max_tokens=3000,
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


# -- Entry point ---------------------------------------------------------------

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print('Usage: python prompt.py <signals_json_file>', file=sys.stderr)
        sys.exit(1)

    with open(sys.argv[1]) as f:
        signals = json.load(f)

    report = generate_report(signals)
    print(json.dumps(report, indent=2))
