"""
crawler.py — Donor Readiness Audit
Crawls a nonprofit homepage + key internal pages and returns a structured
signals JSON for use in the Claude report prompt.

Usage:
    python crawler.py <url>
    python crawler.py https://example-nonprofit.org

Output: signals JSON printed to stdout (pipe to file or capture in main.py)
"""

import json
import re
import sys
import time
from urllib.parse import urljoin, urlparse

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout


# ── Helpers ──────────────────────────────────────────────────────────────────

DONATE_PATTERNS = re.compile(
    r'\b(donate|give|support us|make a gift|contribute|gift)\b', re.I
)
VOLUNTEER_PATTERNS = re.compile(
    r'\b(volunteer|get involved|join us|help out)\b', re.I
)
EMAIL_PATTERNS = re.compile(
    r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}'
)
PHONE_PATTERNS = re.compile(
    r'(\+?1[\s.\-]?)?\(?\d{3}\)?[\s.\-]?\d{3}[\s.\-]?\d{4}'
)
ADDRESS_PATTERNS = re.compile(
    r'\d{1,5}\s+\w[\w\s,\.]{5,40}(Street|St|Avenue|Ave|Road|Rd|Blvd|Boulevard|Drive|Dr|Lane|Ln|Way|Place|Pl)',
    re.I
)
IMPACT_PATTERNS = re.compile(
    r'(\$[\d,]+(?:\.\d+)?(?:K|M|B)?|\d[\d,]*\+?\s*(?:families|people|students|children|lives|meals|hours|volunteers|donors|organizations|communities|years))',
    re.I
)
CHARITY_BADGE_PATTERNS = re.compile(
    r'(charity.?navigator|guidestar|candid|bbb.?wise|give\.org|greatnonprofits)',
    re.I
)
PAYMENT_PROCESSOR_PATTERNS = re.compile(
    r'(classy\.org|bloomerang|donorbox|paypal\.com|stripe\.com|qgiv|networkforgood|razoo|fundly|mightycause|salesforce\.org)',
    re.I
)


def normalize_url(url: str) -> str:
    if not url.startswith(('http://', 'https://')):
        url = 'https://' + url
    return url.rstrip('/')


def same_domain(url: str, base: str) -> bool:
    return urlparse(url).netloc == urlparse(base).netloc


def find_priority_links(page, base_url: str) -> dict:
    """
    Scan all hrefs on page and return the best candidate URL for each
    category: donate, volunteer, about.
    """
    links = page.eval_on_selector_all(
        'a[href]',
        'els => els.map(e => ({href: e.href, text: e.innerText.trim()}))'
    )

    found = {'donate': None, 'volunteer': None, 'about': None}

    donate_path = re.compile(r'/(donate|give|support|gift|contribution)', re.I)
    volunteer_path = re.compile(r'/(volunteer|get.?involved|join)', re.I)
    about_path = re.compile(r'/(about|who.?we.?are|our.?story|team|staff)', re.I)

    for link in links:
        href = link.get('href', '')
        text = link.get('text', '')
        if not href or not same_domain(href, base_url):
            continue
        if not found['donate'] and (donate_path.search(href) or DONATE_PATTERNS.search(text)):
            found['donate'] = href
        if not found['volunteer'] and (volunteer_path.search(href) or VOLUNTEER_PATTERNS.search(text)):
            found['volunteer'] = href
        if not found['about'] and about_path.search(href):
            found['about'] = href

    return found


def extract_page_signals(page, url: str, role: str) -> dict:
    """Extract signals from an already-loaded page."""
    sig = {'url': url, 'role': role, 'loaded': True}

    # ── Basic metadata ──
    sig['title'] = page.title()
    sig['meta_description'] = page.eval_on_selector_all(
        'meta[name="description"]',
        'els => els.map(e => e.content)'
    )
    sig['meta_description'] = sig['meta_description'][0] if sig['meta_description'] else None

    # ── Headings ──
    sig['h1'] = page.eval_on_selector_all('h1', 'els => els.map(e => e.innerText.trim())')
    sig['h2s'] = page.eval_on_selector_all('h2', 'els => els.map(e => e.innerText.trim())')[:6]

    # ── Body text (first 600 chars, no tags) ──
    body_text = page.eval_on_selector_all(
        'p, li',
        'els => els.map(e => e.innerText.trim()).filter(t => t.length > 20)'
    )
    sig['body_preview'] = ' '.join(body_text)[:800] if body_text else ''

    # ── Impact stats ──
    full_text = page.inner_text('body') if page.query_selector('body') else ''
    sig['impact_stats'] = list(set(IMPACT_PATTERNS.findall(full_text)))[:8]

    # ── Contact / trust signals ──
    sig['has_phone'] = bool(PHONE_PATTERNS.search(full_text))
    sig['has_address'] = bool(ADDRESS_PATTERNS.search(full_text))
    sig['has_charity_badge'] = bool(CHARITY_BADGE_PATTERNS.search(full_text))
    sig['charity_badge_detail'] = (
        CHARITY_BADGE_PATTERNS.findall(full_text)[0]
        if CHARITY_BADGE_PATTERNS.search(full_text) else None
    )

    # ── Email capture ──
    email_inputs = page.eval_on_selector_all(
        'input[type="email"], input[placeholder*="email" i], input[name*="email" i]',
        'els => els.length'
    )
    sig['has_email_capture'] = email_inputs > 0

    # ── Social links ──
    social_hrefs = page.eval_on_selector_all(
        'a[href*="facebook.com"], a[href*="twitter.com"], a[href*="instagram.com"], '
        'a[href*="linkedin.com"], a[href*="youtube.com"], a[href*="tiktok.com"]',
        'els => els.map(e => e.href)'
    )
    sig['social_links'] = list(set(social_hrefs))

    # ── Nav donate button ──
    nav_links = page.eval_on_selector_all(
        'nav a, header a',
        'els => els.map(e => ({href: e.href, text: e.innerText.trim()}))'
    )
    sig['nav_links'] = [l for l in nav_links if l.get('text')]
    sig['donate_in_nav'] = any(
        DONATE_PATTERNS.search(l.get('text', '')) for l in nav_links
    )
    sig['donate_nav_text'] = next(
        (l['text'] for l in nav_links if DONATE_PATTERNS.search(l.get('text', ''))),
        None
    )

    # ── CTA buttons visible in viewport ──
    cta_buttons = page.eval_on_selector_all(
        'a, button',
        '''els => els
            .map(e => ({text: e.innerText.trim(), tag: e.tagName}))
            .filter(e => e.text.length > 0 && e.text.length < 40)'''
    )
    sig['cta_texts'] = [b['text'] for b in cta_buttons if DONATE_PATTERNS.search(b['text']) or VOLUNTEER_PATTERNS.search(b['text'])][:6]

    return sig


def check_mobile_donate_cta(page, url: str) -> bool:
    """Re-render at 390px and check if a donate CTA is visible without scrolling."""
    page.set_viewport_size({'width': 390, 'height': 844})
    try:
        page.goto(url, wait_until='domcontentloaded', timeout=20000)
        page.wait_for_timeout(1500)
    except PlaywrightTimeout:
        return False

    visible = page.eval_on_selector_all(
        'a, button',
        '''els => els.filter(e => {
            const r = e.getBoundingClientRect();
            const text = e.innerText.trim().toLowerCase();
            return r.top >= 0 && r.bottom <= window.innerHeight &&
                   (text.includes("donat") || text.includes("give") || text.includes("support"));
        }).length'''
    )
    return visible > 0


def crawl_donate_page(page, url: str) -> dict:
    """Extra signals specific to the donation page."""
    sig = {}
    try:
        page.goto(url, wait_until='domcontentloaded', timeout=20000)
        page.wait_for_timeout(1500)
    except PlaywrightTimeout:
        return {'donate_page_loaded': False}

    actual_url = page.url
    sig['donate_page_url'] = actual_url
    sig['donate_page_same_domain'] = same_domain(actual_url, url)

    page_src = page.content()
    processor_match = PAYMENT_PROCESSOR_PATTERNS.search(page_src)
    sig['donate_processor'] = processor_match.group(0) if processor_match else 'unknown'

    full_text = page.inner_text('body') if page.query_selector('body') else ''
    sig['has_recurring_giving'] = bool(
        re.search(r'\b(monthly|recurring|sustaining|regular)\b', full_text, re.I)
    )

    amount_buttons = page.eval_on_selector_all(
        'button, label, [class*="amount"], [class*="preset"]',
        'els => els.map(e => e.innerText.trim()).filter(t => /^\$\d+/.test(t))'
    )
    sig['suggested_amounts'] = amount_buttons[:6]
    sig['has_suggested_amounts'] = len(amount_buttons) > 0
    sig['has_impact_framing_on_donate_page'] = bool(IMPACT_PATTERNS.search(full_text))

    return sig


def crawl_volunteer_page(page, url: str) -> dict:
    """Extra signals specific to the volunteer page."""
    sig = {}
    try:
        page.goto(url, wait_until='domcontentloaded', timeout=20000)
        page.wait_for_timeout(1500)
    except PlaywrightTimeout:
        return {'volunteer_page_loaded': False}

    full_text = page.inner_text('body') if page.query_selector('body') else ''
    sig['volunteer_page_url'] = page.url

    forms = page.eval_on_selector_all('form', 'els => els.length')
    email_inputs = page.eval_on_selector_all(
        'input[type="email"], input[type="text"]', 'els => els.length'
    )
    bare_email = EMAIL_PATTERNS.search(full_text)

    if forms > 0 or email_inputs >= 2:
        sig['volunteer_signup_type'] = 'form'
    elif bare_email:
        sig['volunteer_signup_type'] = 'email_only'
    else:
        sig['volunteer_signup_type'] = 'none_found'

    role_indicators = re.findall(
        r'\b(tutor|mentor|driver|cook|admin|coordinator|photographer|translator|board|committee)\b',
        full_text, re.I
    )
    sig['volunteer_roles_listed'] = list(set(role_indicators))
    sig['has_specific_volunteer_roles'] = len(role_indicators) > 0

    return sig


# ── Main crawl orchestration ──────────────────────────────────────────────────

def crawl(start_url: str) -> dict:
    start_url = normalize_url(start_url)
    parsed = urlparse(start_url)
    domain = parsed.netloc

    signals = {
        'domain': domain,
        'start_url': start_url,
        'crawled_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'https': start_url.startswith('https://'),
        'pages': {},
        'donate_page': {},
        'volunteer_page': {},
        'navigation': {},
        'mobile': {},
        'trust': {},
    }

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={'width': 1280, 'height': 800},
            user_agent=(
                'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/120.0.0.0 Safari/537.36'
            )
        )
        page = context.new_page()

        print(f'[crawl] Loading homepage: {start_url}', file=sys.stderr)
        t0 = time.time()
        try:
            page.goto(start_url, wait_until='domcontentloaded', timeout=25000)
            page.wait_for_timeout(2000)
        except PlaywrightTimeout:
            print('[crawl] Homepage timed out', file=sys.stderr)
            browser.close()
            return signals

        signals['page_load_ms'] = int((time.time() - t0) * 1000)
        homepage_sig = extract_page_signals(page, start_url, 'homepage')
        signals['pages']['homepage'] = homepage_sig

        priority_links = find_priority_links(page, start_url)
        signals['navigation']['priority_links'] = priority_links
        print(f'[crawl] Found links: {priority_links}', file=sys.stderr)

        donate_url = priority_links.get('donate')
        is_modal = donate_url and (
            donate_url.endswith('/#') or donate_url.endswith('#') or
            urlparse(donate_url).fragment != '' and urlparse(donate_url).path in ('', '/')
        )
        if donate_url and not is_modal:
            print(f'[crawl] Loading donate page: {donate_url}', file=sys.stderr)
            signals['donate_page'] = crawl_donate_page(page, donate_url)
            signals['donate_page']['clicks_from_homepage'] = 1
            signals['donate_page']['donate_type'] = 'page'
        elif is_modal:
            print(f'[crawl] Donate opens as modal/overlay on homepage', file=sys.stderr)
            page_src = page.content()
            processor_match = PAYMENT_PROCESSOR_PATTERNS.search(page_src)
            signals['donate_page'] = {
                'donate_type': 'modal_or_overlay',
                'donate_processor': processor_match.group(0) if processor_match else 'unknown',
                'donate_page_same_domain': True,
                'clicks_from_homepage': 1,
                'has_recurring_giving': None,
                'has_suggested_amounts': None,
                'has_impact_framing_on_donate_page': None,
                'note': 'Donation form opens in a modal/lightbox — processor detected from page source',
            }
        else:
            signals['donate_page'] = {'clicks_from_homepage': None, 'donate_page_found': False}
            print('[crawl] No donate page found', file=sys.stderr)

        volunteer_url = priority_links.get('volunteer')
        if volunteer_url:
            print(f'[crawl] Loading volunteer page: {volunteer_url}', file=sys.stderr)
            signals['volunteer_page'] = crawl_volunteer_page(page, volunteer_url)
        else:
            signals['volunteer_page']['volunteer_page_found'] = False
            print('[crawl] No volunteer page found', file=sys.stderr)

        about_url = priority_links.get('about')
        if about_url:
            print(f'[crawl] Loading about page: {about_url}', file=sys.stderr)
            try:
                page.goto(about_url, wait_until='domcontentloaded', timeout=20000)
                page.wait_for_timeout(1500)
                about_sig = extract_page_signals(page, about_url, 'about')
                signals['pages']['about'] = about_sig
            except PlaywrightTimeout:
                print('[crawl] About page timed out', file=sys.stderr)

        print('[crawl] Checking mobile CTA visibility', file=sys.stderr)
        mobile_page = context.new_page()
        signals['mobile']['donate_cta_above_fold'] = check_mobile_donate_cta(
            mobile_page, start_url
        )
        mobile_page.close()

        browser.close()

    hp = signals['pages'].get('homepage', {})
    about = signals['pages'].get('about', {})

    signals['trust'] = {
        'https': signals['https'],
        'has_phone': hp.get('has_phone') or about.get('has_phone', False),
        'has_address': hp.get('has_address') or about.get('has_address', False),
        'has_charity_badge': hp.get('has_charity_badge') or about.get('has_charity_badge', False),
        'charity_badge_detail': hp.get('charity_badge_detail') or about.get('charity_badge_detail'),
        'has_email_capture': hp.get('has_email_capture', False),
        'social_links': hp.get('social_links', []),
    }

    return signals


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print('Usage: python crawler.py <url>', file=sys.stderr)
        sys.exit(1)

    url = sys.argv[1]
    result = crawl(url)
    print(json.dumps(result, indent=2))
