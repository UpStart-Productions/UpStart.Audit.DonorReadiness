"""
crawler.py — Donor Readiness Audit (v2)
Crawls a nonprofit website comprehensively and returns a structured
signals JSON for use in the Claude report prompt.

Strategy (3 phases):
  1. Homepage + full nav discovery  — collect every internal link,
     including JS-rendered dropdowns, via hover-triggering and
     textContent (not innerText, which returns "" for hidden elements).
  2. Page selection                 — categorize links by type and pick
     the 12 most signal-rich pages to visit.
  3. Crawl + consolidate           — visit each selected page, run
     general + targeted extraction, merge trust/impact signals across all.

Usage:
    python crawler.py <url>
    python crawler.py https://example-nonprofit.org

Output: signals JSON printed to stdout
"""

import json
import re
import sys
import time
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout


# ── Regex patterns ────────────────────────────────────────────────────────────

DONATE_PATTERNS = re.compile(
    r'\b(donate|give|support us|make a gift|contribute|gift|ways to give)\b', re.I
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
    r'(\$[\d,]+(?:\.\d+)?(?:K|M|B)?'
    r'|\d[\d,]*\+?\s*(?:families|people|students|children|lives|meals|hours|'
    r'volunteers|donors|organizations|communities|years|homes|beds|clients|'
    r'referrals|households|rooms|individuals|veterans|seniors))',
    re.I
)
CHARITY_BADGE_PATTERNS = re.compile(
    r'(charity.?navigator|guidestar|candid|bbb.?wise|give\.org|greatnonprofits)',
    re.I
)
PAYMENT_PROCESSOR_PATTERNS = re.compile(
    r'(classy\.org|bloomerang|donorbox|paypal\.com|stripe\.com|qgiv|'
    r'networkforgood|razoo|fundly|mightycause|salesforce\.org|'
    r'double\.giving|every\.org|givebutter|donately|mightycause)',
    re.I
)
VOLUNTEER_PLATFORM_PATTERNS = re.compile(
    r'(galaxydigital|volunteermatch|volunteerlocal|initlive|cervistech|'
    r'volunteer\.gov|volgistics|volunteerspot|signupgenius)',
    re.I
)

# URL path patterns for link categorization
DONATE_URL    = re.compile(r'/(donate|give|ways.?to.?give|gift|support|contribut|fund|campaign)', re.I)
VOLUNTEER_URL = re.compile(r'/(volunteer|get.?involved|join|help)', re.I)
ABOUT_URL     = re.compile(r'/(about|who.?we|our.?story|foundation|mission|values|team|board|staff|history|leadership)', re.I)
IMPACT_URL    = re.compile(r'/(impact|outcome|result|annual.?report|hope.?report|report|financials?|data|numbers|stories)', re.I)
NEWSLETTER_URL = re.compile(r'/(newsletter|sign.?up|subscribe|email.?list)', re.I)
CONTACT_URL   = re.compile(r'/(contact|reach|connect|location)', re.I)

# Downloadable file extensions — links matching this are collected as signals
# rather than navigated to (Playwright raises "Download is starting" on file URLs)
FILE_EXT_PATTERN = re.compile(
    r'\.(pdf|doc|docx|xls|xlsx|ppt|pptx|zip|mov|mp4|mp3|wav|avi|wmv|'
    r'jpg|jpeg|png|gif|svg|csv|json|xml)$',
    re.I
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def normalize_url(url: str) -> str:
    if not url.startswith(('http://', 'https://')):
        url = 'https://' + url
    return url.rstrip('/')


def same_domain(url: str, base: str) -> bool:
    try:
        return urlparse(url).netloc == urlparse(base).netloc
    except Exception:
        return False


# ── Phase 1: Full nav discovery ───────────────────────────────────────────────

def discover_all_links(page, base_url: str) -> list:
    """
    Collect every internal link on the page, including those inside
    JS-rendered dropdown menus.

    Key fix over v1: uses textContent (not innerText) when reading link
    text. innerText returns "" for elements with display:none — exactly
    what Squarespace/Wix/Webflow dropdown items look like before a hover
    event expands them. textContent always returns the raw text content
    regardless of visibility.
    """
    # Trigger dropdown menus by hovering over nav parent items.
    # This forces Squarespace/similar builders to reveal hidden sub-links.
    try:
        nav_parents = page.query_selector_all(
            'nav li, header li, [class*="nav"] li, [class*="menu"] li, '
            '[class*="Nav"] li, [class*="Menu"] li'
        )
        for item in nav_parents[:40]:
            try:
                item.hover(timeout=400)
                page.wait_for_timeout(120)
            except Exception:
                pass
    except Exception:
        pass

    # Small extra wait for any hover-triggered animations to settle
    page.wait_for_timeout(500)

    # Collect ALL anchor elements using textContent for reliable text on
    # hidden/collapsed nav items
    raw_links = page.eval_on_selector_all(
        'a[href]',
        '''els => els.map(e => ({
            href: e.href,
            text: (e.textContent || e.getAttribute("aria-label") || e.getAttribute("title") || "").trim(),
            inNav: !!(e.closest("nav") || e.closest("header") || e.closest("[role=navigation]"))
        }))'''
    )

    seen = set()
    links = []
    file_links = []
    for link in raw_links:
        href = link.get('href', '')
        if not href:
            continue
        # Skip non-http schemes
        if href.startswith(('mailto:', 'tel:', 'javascript:', 'data:')):
            continue
        # Only same-domain links
        if not same_domain(href, base_url):
            continue
        # Collect downloadable file links separately — don't navigate to them
        # (Playwright raises "Download is starting" when hitting file URLs directly)
        if FILE_EXT_PATTERN.search(href):
            if href not in seen:
                seen.add(href)
                file_links.append({
                    'href': href,
                    'text': link.get('text', ''),
                })
            continue
        # Skip fragment-only links (modal triggers, anchor scrolls)
        parsed = urlparse(href)
        if (not parsed.path or parsed.path == '/') and parsed.fragment:
            continue
        # Normalize: strip fragment and trailing slash
        normalized = href.split('#')[0].rstrip('/')
        if not normalized:
            continue
        # Skip homepage itself
        if normalized == base_url.rstrip('/'):
            continue
        if normalized in seen:
            continue
        seen.add(normalized)
        links.append({
            'href': normalized,
            'text': link.get('text', ''),
            'in_nav': link.get('inNav', False),
        })

    return links, file_links


# ── Phase 2: Categorize and select pages ─────────────────────────────────────

def categorize_links(links: list) -> dict:
    """Group discovered links by inferred category."""
    categories = {
        'donate':     [],
        'volunteer':  [],
        'about':      [],
        'impact':     [],
        'newsletter': [],
        'contact':    [],
        'other':      [],
    }

    for link in links:
        href = link['href']
        text = link['text']

        if DONATE_URL.search(href) or DONATE_PATTERNS.search(text):
            categories['donate'].append(link)
        elif VOLUNTEER_URL.search(href) or VOLUNTEER_PATTERNS.search(text):
            categories['volunteer'].append(link)
        elif ABOUT_URL.search(href) or re.search(
                r'\b(about|mission|team|board|staff|history|foundation|leadership)\b', text, re.I):
            categories['about'].append(link)
        elif IMPACT_URL.search(href) or re.search(
                r'\b(impact|outcomes|results|report|financials|data|stories|numbers)\b', text, re.I):
            categories['impact'].append(link)
        elif NEWSLETTER_URL.search(href) or re.search(
                r'\b(newsletter|sign.?up|subscribe)\b', text, re.I):
            categories['newsletter'].append(link)
        elif CONTACT_URL.search(href) or re.search(
                r'\b(contact|reach us|get in touch|location)\b', text, re.I):
            categories['contact'].append(link)
        else:
            categories['other'].append(link)

    return categories


def categorize_file_links(file_links: list) -> list:
    """
    Annotate each collected file link with extension, file_type, and category.
    Returns a list of dicts suitable for inclusion in signals['file_links'].
    """
    type_map = {
        'pdf': 'document', 'doc': 'document', 'docx': 'document',
        'xls': 'spreadsheet', 'xlsx': 'spreadsheet', 'csv': 'spreadsheet',
        'ppt': 'presentation', 'pptx': 'presentation',
        'jpg': 'image', 'jpeg': 'image', 'png': 'image', 'gif': 'image', 'svg': 'image',
        'mp4': 'video', 'mov': 'video', 'avi': 'video', 'wmv': 'video',
        'mp3': 'audio', 'wav': 'audio',
        'zip': 'archive',
    }

    categorized = []
    for link in file_links:
        href = link['href']
        text = link.get('text', '') or ''

        ext_match = FILE_EXT_PATTERN.search(href)
        ext = ext_match.group(1).lower() if ext_match else ''
        file_type = type_map.get(ext, 'other')

        # Categorize by URL path and link text (same signals as categorize_links)
        if DONATE_URL.search(href) or DONATE_PATTERNS.search(text):
            category = 'donate'
        elif (VOLUNTEER_URL.search(href) or VOLUNTEER_PATTERNS.search(text)
              or re.search(r'(application|apply)', href, re.I)
              or re.search(r'\b(application|apply)\b', text, re.I)):
            category = 'volunteer'
        elif (IMPACT_URL.search(href) or re.search(
                r'\b(impact|outcomes|results|report|financials|data|stories|numbers|annual)\b',
                text, re.I)):
            category = 'impact'
        elif ABOUT_URL.search(href) or re.search(
                r'\b(about|mission|team|board|staff|history|foundation|leadership)\b', text, re.I):
            category = 'about'
        elif NEWSLETTER_URL.search(href) or re.search(
                r'\b(newsletter|sign.?up|subscribe)\b', text, re.I):
            category = 'newsletter'
        else:
            category = 'other'

        categorized.append({
            'href':      href,
            'text':      text,
            'extension': ext,
            'file_type': file_type,
            'category':  category,
        })

    return categorized


def select_pages_to_crawl(categories: dict, max_pages: int = 11) -> list:
    """
    Select up to max_pages pages to visit, in priority order.
    Nav-linked pages (in_nav=True) get a slight boost within each category.
    """
    # Sort each category: nav links first, then others
    def nav_first(links):
        return sorted(links, key=lambda l: (0 if l.get('in_nav') else 1))

    # (category, max from this category)
    priority = [
        ('donate',     2),
        ('volunteer',  2),
        ('about',      3),
        ('impact',     2),
        ('newsletter', 1),
        ('contact',    1),
        ('other',      1),
    ]

    selected = []
    seen = set()

    for category, limit in priority:
        for link in nav_first(categories.get(category, []))[:limit]:
            href = link['href']
            if href not in seen and len(selected) < max_pages:
                selected.append({
                    'href':     href,
                    'category': category,
                    'text':     link['text'],
                })
                seen.add(href)

    return selected


# ── Phase 3: Per-page extraction ──────────────────────────────────────────────

def load_page(page, url: str, timeout: int = 20000) -> bool:
    """Navigate to url. Returns True on success, False on timeout."""
    try:
        page.goto(url, wait_until='domcontentloaded', timeout=timeout)
        page.wait_for_timeout(2000)
        return True
    except PlaywrightTimeout:
        print(f'[crawl] Timeout loading {url}', file=sys.stderr)
        return False
    except Exception as e:
        # Catches "Download is starting" and other non-timeout navigation errors
        print(f'[crawl] Skipping {url} — {e}', file=sys.stderr)
        return False


def extract_page_signals(page, url: str, role: str) -> dict:
    """General signal extraction — run on every visited page."""
    sig = {'url': url, 'role': role, 'loaded': True}

    sig['title'] = page.title()
    sig['meta_description'] = page.eval_on_selector_all(
        'meta[name="description"]', 'els => els.map(e => e.content)'
    )
    sig['meta_description'] = sig['meta_description'][0] if sig['meta_description'] else None

    sig['h1']  = page.eval_on_selector_all('h1', 'els => els.map(e => e.innerText.trim())')
    sig['h2s'] = page.eval_on_selector_all('h2', 'els => els.map(e => e.innerText.trim())')[:8]

    body_text = page.eval_on_selector_all(
        'p, li',
        'els => els.map(e => e.innerText.trim()).filter(t => t.length > 20)'
    )
    sig['body_preview'] = ' '.join(body_text)[:1000] if body_text else ''

    full_text = page.inner_text('body') if page.query_selector('body') else ''
    # Store cleaned raw text so Claude can verify content-dependent claims
    sig['raw_text'] = ' '.join(full_text.split())[:2000]
    sig['impact_stats'] = list(set(IMPACT_PATTERNS.findall(full_text)))[:10]

    # Trust signals
    sig['has_phone']         = bool(PHONE_PATTERNS.search(full_text))
    sig['has_address']       = bool(ADDRESS_PATTERNS.search(full_text))
    sig['has_charity_badge'] = bool(CHARITY_BADGE_PATTERNS.search(full_text))
    sig['charity_badge_detail'] = (
        CHARITY_BADGE_PATTERNS.findall(full_text)[0]
        if CHARITY_BADGE_PATTERNS.search(full_text) else None
    )

    # Email capture — check input fields AND links to signup pages
    email_inputs = page.eval_on_selector_all(
        'input[type="email"], input[placeholder*="email" i], input[name*="email" i]',
        'els => els.length'
    )
    newsletter_links = page.eval_on_selector_all(
        'a[href]',
        '''els => els.filter(e =>
            /newsletter|sign.?up|subscribe/i.test(e.href) ||
            /newsletter|sign.?up|subscribe/i.test(e.textContent)
        ).length'''
    )
    sig['has_email_capture']   = email_inputs > 0
    sig['has_newsletter_link'] = newsletter_links > 0

    # Detect third-party embedded forms (iframes, known embed scripts)
    page_src = page.content()
    sig['has_embedded_form'] = bool(re.search(
        r'<iframe|bloomerang|formstack|typeform|jotform|cognito|wufoo|gravity.?form',
        page_src, re.I
    ))

    social_hrefs = page.eval_on_selector_all(
        'a[href*="facebook.com"], a[href*="twitter.com"], a[href*="instagram.com"], '
        'a[href*="linkedin.com"], a[href*="youtube.com"], a[href*="tiktok.com"]',
        'els => els.map(e => e.href)'
    )
    sig['social_links'] = list(set(social_hrefs))

    # Nav-level donate detection (used by prompt.py)
    nav_links = page.eval_on_selector_all(
        'nav a, header a',
        '''els => els.map(e => ({
            href: e.href,
            text: (e.textContent || e.innerText || "").trim()
        }))'''
    )
    sig['nav_links']       = [l for l in nav_links if l.get('text')]
    sig['donate_in_nav']   = any(DONATE_PATTERNS.search(l.get('text', '')) for l in nav_links)
    sig['donate_nav_text'] = next(
        (l['text'] for l in nav_links if DONATE_PATTERNS.search(l.get('text', ''))), None
    )
    # Capture the actual href of the donate nav link (may be external)
    sig['donate_nav_href'] = next(
        (l['href'] for l in nav_links if DONATE_PATTERNS.search(l.get('text', ''))), None
    )

    cta_buttons = page.eval_on_selector_all(
        'a, button',
        '''els => els
            .map(e => ({text: (e.innerText || "").trim()}))
            .filter(e => e.text.length > 0 && e.text.length < 40)'''
    )
    sig['cta_texts'] = [
        b['text'] for b in cta_buttons
        if DONATE_PATTERNS.search(b['text']) or VOLUNTEER_PATTERNS.search(b['text'])
    ][:8]

    return sig


def extract_donate_signals(page, original_url: str) -> dict:
    """Targeted extraction for donation pages."""
    sig = {}
    actual_url = page.url
    sig['donate_page_url']         = actual_url
    sig['donate_page_same_domain'] = same_domain(actual_url, original_url)

    # Detect if donation stayed on-page (modal) vs navigated
    parsed = urlparse(actual_url)
    is_modal = parsed.fragment != '' and parsed.path in ('', '/')
    sig['donate_type'] = 'modal_or_overlay' if is_modal else 'page'

    page_src = page.content()
    processor_match = PAYMENT_PROCESSOR_PATTERNS.search(page_src)
    sig['donate_processor'] = processor_match.group(0) if processor_match else 'unknown'

    full_text = page.inner_text('body') if page.query_selector('body') else ''
    sig['has_recurring_giving'] = bool(
        re.search(r'\b(monthly|recurring|sustaining|regular)\b', full_text, re.I)
    )

    amount_buttons = page.eval_on_selector_all(
        'button, label, [class*="amount"], [class*="preset"]',
        'els => els.map(e => e.innerText.trim()).filter(t => /^\\$\\d+/.test(t))'
    )
    sig['suggested_amounts']               = amount_buttons[:6]
    sig['has_suggested_amounts']           = len(amount_buttons) > 0
    sig['has_impact_framing_on_donate_page'] = bool(IMPACT_PATTERNS.search(full_text))
    sig['clicks_from_homepage']            = 1

    return sig


def extract_volunteer_signals(page, url: str) -> dict:
    """Targeted extraction for volunteer pages."""
    sig = {}
    sig['volunteer_page_url'] = page.url

    full_text = page.inner_text('body') if page.query_selector('body') else ''
    page_src  = page.content()

    # Form detection — check inputs, iframes, and known volunteer platforms.
    # An iframe or known platform embed counts as a "form" even if no
    # native <input> elements are present in the DOM.
    forms         = page.eval_on_selector_all('form', 'els => els.length')
    email_inputs  = page.eval_on_selector_all(
        'input[type="email"], input[type="text"]', 'els => els.length'
    )
    bare_email    = EMAIL_PATTERNS.search(full_text)
    has_platform  = bool(VOLUNTEER_PLATFORM_PATTERNS.search(page_src))
    has_iframe    = bool(re.search(r'<iframe', page_src, re.I))

    if forms > 0 or email_inputs >= 2 or has_platform or has_iframe:
        sig['volunteer_signup_type'] = 'form'
        if has_platform:
            m = VOLUNTEER_PLATFORM_PATTERNS.search(page_src)
            sig['volunteer_platform'] = m.group(0) if m else None
    elif bare_email:
        sig['volunteer_signup_type'] = 'email_only'
        sig['volunteer_contact_email'] = bare_email.group(0)
    else:
        sig['volunteer_signup_type'] = 'none_found'

    role_indicators = re.findall(
        r'\b(tutor|mentor|driver|cook|admin|coordinator|photographer|'
        r'translator|board|committee|loader|sorter|delivery|warehouse|'
        r'packer|driver|mover|greeter|cashier|receptionist)\b',
        full_text, re.I
    )
    sig['volunteer_roles_listed']     = list(set(role_indicators))
    sig['has_specific_volunteer_roles'] = len(role_indicators) > 0

    # Capture raw list item text so Claude can read role descriptions directly.
    # This catches cases where roles are described in prose lists but don't
    # match the specific role-name regex patterns above.
    list_items = page.eval_on_selector_all(
        'li',
        'els => els.map(e => e.innerText.trim()).filter(t => t.length > 5 && t.length < 300)'
    )
    sig['volunteer_list_content'] = list_items[:30]

    return sig


def crawl_page(page, url: str, category: str) -> dict:
    """Load a page and run general + category-specific extraction."""
    print(f'[crawl] {category:12s} → {url}', file=sys.stderr)

    if not load_page(page, url):
        return {'url': url, 'category': category, 'loaded': False}

    sig = extract_page_signals(page, url, category)

    if category == 'donate':
        sig.update(extract_donate_signals(page, url))
    elif category == 'volunteer':
        sig.update(extract_volunteer_signals(page, url))

    return sig


# ── Mobile CTA check ──────────────────────────────────────────────────────────

def check_mobile_donate_cta(page, url: str) -> bool:
    """Re-render at 390px and check if a donate CTA is visible above the fold."""
    page.set_viewport_size({'width': 390, 'height': 844})
    try:
        page.goto(url, wait_until='domcontentloaded', timeout=20000)
        page.wait_for_timeout(2000)
    except PlaywrightTimeout:
        return False

    visible = page.eval_on_selector_all(
        'a, button',
        '''els => els.filter(e => {
            const r = e.getBoundingClientRect();
            const text = (e.innerText || "").trim().toLowerCase();
            return r.top >= 0 && r.bottom <= window.innerHeight &&
                   (text.includes("donat") || text.includes("give") || text.includes("support"));
        }).length'''
    )
    return visible > 0


# ── Signal consolidation ──────────────────────────────────────────────────────

def consolidate_signals(signals: dict, all_page_sigs: list) -> dict:
    """Merge trust and impact signals across all visited pages."""
    trust = {
        'https':               signals['https'],
        'has_phone':           False,
        'has_address':         False,
        'has_charity_badge':   False,
        'charity_badge_detail': None,
        'has_email_capture':   False,
        'has_newsletter_link': False,
        'social_links':        [],
        'all_impact_stats':    [],
    }

    all_impact = []
    for sig in all_page_sigs:
        if not sig.get('loaded', True):
            continue
        trust['has_phone']         = trust['has_phone']         or sig.get('has_phone', False)
        trust['has_address']       = trust['has_address']       or sig.get('has_address', False)
        trust['has_charity_badge'] = trust['has_charity_badge'] or sig.get('has_charity_badge', False)
        if not trust['charity_badge_detail']:
            trust['charity_badge_detail'] = sig.get('charity_badge_detail')
        trust['has_email_capture']   = trust['has_email_capture']   or sig.get('has_email_capture', False)
        trust['has_newsletter_link'] = trust['has_newsletter_link'] or sig.get('has_newsletter_link', False)
        if sig.get('social_links'):
            trust['social_links'] = list(set(trust['social_links'] + sig['social_links']))
        if sig.get('impact_stats'):
            all_impact.extend(sig['impact_stats'])

    trust['all_impact_stats'] = list(set(all_impact))[:15]
    return trust


# ── Main crawl orchestration ──────────────────────────────────────────────────

def crawl(start_url: str) -> dict:
    start_url = normalize_url(start_url)
    domain    = urlparse(start_url).netloc

    signals = {
        'domain':         domain,
        'start_url':      start_url,
        'crawled_at':     time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'https':          start_url.startswith('https://'),
        'pages':          {},
        'donate_page':    {},
        'volunteer_page': {},
        'navigation':     {},
        'mobile':         {},
        'trust':          {},
        'file_links':     [],
    }

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--single-process",
                "--no-zygote",
            ]
        )
        context = browser.new_context(
            viewport={'width': 1280, 'height': 800},
            user_agent=(
                'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/120.0.0.0 Safari/537.36'
            )
        )
        page = context.new_page()

        # ── Phase 1: Homepage ──────────────────────────────────────────────
        print(f'[crawl] Loading homepage: {start_url}', file=sys.stderr)
        t0 = time.time()

        # networkidle waits for JS-rendered nav to settle.
        # Fall back to domcontentloaded + extended wait for sites with
        # persistent connections (chat widgets, long-polling, etc.)
        try:
            page.goto(start_url, wait_until='networkidle', timeout=30000)
        except PlaywrightTimeout:
            print('[crawl] networkidle timed out — falling back to domcontentloaded', file=sys.stderr)
            try:
                page.goto(start_url, wait_until='domcontentloaded', timeout=25000)
                page.wait_for_timeout(4000)
            except PlaywrightTimeout:
                print('[crawl] Homepage failed to load', file=sys.stderr)
                browser.close()
                return signals

        # Extra wait specifically for nav links to populate
        try:
            page.wait_for_selector('nav a, header a', timeout=5000)
        except PlaywrightTimeout:
            pass

        signals['page_load_ms'] = int((time.time() - t0) * 1000)
        print(f'[crawl] Homepage loaded in {signals["page_load_ms"]}ms', file=sys.stderr)

        homepage_sig = extract_page_signals(page, start_url, 'homepage')
        signals['pages']['homepage'] = homepage_sig

        # Store homepage source for modal detection fallback
        homepage_src = page.content()

        # ── Full nav discovery ─────────────────────────────────────────────
        all_links, raw_file_links = discover_all_links(page, start_url)
        categories    = categorize_links(all_links)
        signals['file_links'] = categorize_file_links(raw_file_links)
        pages_to_crawl = select_pages_to_crawl(categories, max_pages=11)

        print(
            f'[crawl] Discovered {len(all_links)} internal links, '
            f'{len(raw_file_links)} file links — '
            + ', '.join(f'{k}:{len(v)}' for k, v in categories.items() if v),
            file=sys.stderr
        )
        print(f'[crawl] Selected {len(pages_to_crawl)} pages to crawl', file=sys.stderr)

        # Build priority_links for prompt.py backward compatibility
        priority_links = {
            'donate':    categories['donate'][0]['href']    if categories['donate']    else None,
            'volunteer': categories['volunteer'][0]['href'] if categories['volunteer'] else None,
            'about':     categories['about'][0]['href']     if categories['about']     else None,
        }
        signals['navigation']['priority_links']  = priority_links
        signals['navigation']['all_discovered']  = {
            k: [l['href'] for l in v] for k, v in categories.items()
        }
        signals['navigation']['pages_crawled'] = []

        # ── External donate page handling ──────────────────────────────────
        # If the donate nav link goes to an external platform (PayPal, Givebutter,
        # etc.), discover_all_links() will have filtered it out. Visit it directly
        # so we can extract real signals (recurring giving, preset amounts, etc.)
        # rather than leaving donate_page empty and reporting false negatives.
        external_donate_href = homepage_sig.get('donate_nav_href')
        if (not categories['donate']
                and external_donate_href
                and not same_domain(external_donate_href, start_url)):
            print(f'[crawl] donate      → {external_donate_href} (external platform)', file=sys.stderr)
            if load_page(page, external_donate_href):
                ext_donate_sig = extract_page_signals(page, page.url, 'donate')
                ext_donate_sig.update(extract_donate_signals(page, start_url))
                ext_donate_sig['donate_external_platform'] = True
                signals['donate_page'] = ext_donate_sig
                signals['navigation']['pages_crawled'].append(
                    {'url': external_donate_href, 'category': 'donate_external', 'loaded': True}
                )

        # ── Phase 2: Crawl selected pages ─────────────────────────────────
        all_page_sigs = [homepage_sig]

        for page_info in pages_to_crawl:
            url      = page_info['href']
            category = page_info['category']

            page_sig = crawl_page(page, url, category)
            all_page_sigs.append(page_sig)
            signals['navigation']['pages_crawled'].append(
                {'url': url, 'category': category, 'loaded': page_sig.get('loaded', True)}
            )

            # Populate legacy signal keys for prompt.py compatibility
            if category == 'donate' and not signals['donate_page']:
                signals['donate_page'] = page_sig
            elif category == 'volunteer' and not signals['volunteer_page']:
                signals['volunteer_page'] = page_sig
            elif category == 'about' and 'about' not in signals['pages']:
                signals['pages']['about'] = page_sig
            elif category == 'impact' and 'impact' not in signals['pages']:
                signals['pages']['impact'] = page_sig

        # ── Modal donate fallback ──────────────────────────────────────────
        # If no donate page was found (donate link was a homepage modal/overlay),
        # detect the payment processor from the homepage source.
        if not signals['donate_page'] and priority_links.get('donate') is None:
            processor_match = PAYMENT_PROCESSOR_PATTERNS.search(homepage_src)
            signals['donate_page'] = {
                'donate_type':              'modal_or_overlay',
                'donate_processor':         processor_match.group(0) if processor_match else 'unknown',
                'donate_page_same_domain':  True,
                'clicks_from_homepage':     1,
                'has_recurring_giving':     None,
                'has_suggested_amounts':    None,
                'has_impact_framing_on_donate_page': None,
                'note': 'Donation form opens in a modal/lightbox on the homepage',
            }

        # Ensure missing pages are explicitly marked for prompt.py
        if not signals['donate_page']:
            signals['donate_page'] = {'donate_page_found': False, 'clicks_from_homepage': None}
        if not signals['volunteer_page']:
            signals['volunteer_page'] = {'volunteer_page_found': False}

        # ── Phase 3: Mobile CTA check ──────────────────────────────────────
        print('[crawl] Checking mobile donate CTA visibility', file=sys.stderr)
        mobile_page = context.new_page()
        signals['mobile']['donate_cta_above_fold'] = check_mobile_donate_cta(
            mobile_page, start_url
        )
        mobile_page.close()

        browser.close()

    # ── Phase 4: Consolidate signals across all visited pages ──────────────
    signals['trust'] = consolidate_signals(signals, all_page_sigs)

    total = len(all_page_sigs)
    print(f'[crawl] Complete. {total} pages crawled, '
          f'{len(signals["trust"]["all_impact_stats"])} impact stats found.',
          file=sys.stderr)

    return signals


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print('Usage: python crawler.py <url>', file=sys.stderr)
        sys.exit(1)

    url = sys.argv[1]
    result = crawl(url)
    print(json.dumps(result, indent=2))
