#!/usr/bin/env python3
"""
crawl_site.py — UpStart Productions SEO Audit Crawler
Fetches all discoverable pages for a given domain and extracts SEO data.

Usage:
    python crawl_site.py https://example.com output_dir/
"""

import sys
import re
import json
import subprocess
import urllib.parse
from pathlib import Path

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
HEADERS = ["-H", "Accept: text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
           "-H", "Accept-Language: en-US,en;q=0.5"]

def fetch(url, timeout=20):
    try:
        result = subprocess.run(
            ["curl", "-s", "-L", "--max-time", str(timeout), "-A", UA] + HEADERS + [url],
            capture_output=True, text=True, timeout=timeout + 5
        )
        return result.stdout
    except Exception as e:
        return ""

def fetch_status(url):
    try:
        result = subprocess.run(
            ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", "-L", "--max-time", "10", "-A", UA] + HEADERS + [url],
            capture_output=True, text=True, timeout=15
        )
        return int(result.stdout.strip() or "0")
    except:
        return 0

def extract_links(html, base_domain):
    """Extract internal links from HTML."""
    hrefs = re.findall(r'href=["\']([^"\']+)["\']', html)
    links = set()
    for h in hrefs:
        if h.startswith('/') and not h.startswith('//'):
            links.add(h.rstrip('/') or '/')
        elif base_domain in h:
            parsed = urllib.parse.urlparse(h)
            path = parsed.path.rstrip('/') or '/'
            links.add(path)
    # Filter out assets
    links = {l for l in links if not re.search(r'\.(css|js|png|jpg|jpeg|svg|gif|ico|webmanifest|woff|woff2|ttf|pdf)$', l, re.I)}
    return links

def extract_page_data(html, url, base_domain):
    """Extract all SEO-relevant data from a page's HTML."""
    data = {"url": url}

    # Title
    m = re.search(r'<title[^>]*>(.*?)</title>', html, re.DOTALL | re.IGNORECASE)
    data["title"] = re.sub(r'<[^>]+>', '', m.group(1)).strip() if m else None

    # Meta description
    m = re.search(r'<meta\s+name=["\']description["\']\s+content=["\']([^"\']*)["\']', html, re.IGNORECASE)
    if not m:
        m = re.search(r'<meta\s+content=["\']([^"\']*)["\'][^>]+name=["\']description["\']', html, re.IGNORECASE)
    data["meta_description"] = m.group(1).strip() if m else None

    # Canonical
    m = re.search(r'<link\s+rel=["\']canonical["\']\s+href=["\']([^"\']*)["\']', html, re.IGNORECASE)
    if not m:
        m = re.search(r'<link\s+href=["\']([^"\']*)["\'][^>]+rel=["\']canonical["\']', html, re.IGNORECASE)
    data["canonical"] = m.group(1).strip() if m else None

    # OG tags
    og = {}
    for prop, val in re.findall(r'<meta\s+property=["\']og:([^"\']+)["\']\s+content=["\']([^"\']*)["\']', html, re.IGNORECASE):
        og[prop] = val
    for prop, val in re.findall(r'<meta\s+content=["\']([^"\']*)["\'][^>]+property=["\']og:([^"\']+)["\']', html, re.IGNORECASE):
        og[val] = prop
    data["og"] = og

    # Twitter tags
    tw = {}
    for name, val in re.findall(r'<meta\s+name=["\']twitter:([^"\']+)["\']\s+content=["\']([^"\']*)["\']', html, re.IGNORECASE):
        tw[name] = val
    data["twitter"] = tw

    # JSON-LD schemas
    schemas = re.findall(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.DOTALL | re.IGNORECASE)
    parsed_schemas = []
    for s in schemas:
        try:
            parsed_schemas.append(json.loads(s.strip()))
        except:
            parsed_schemas.append({"_raw": s.strip()[:200]})
    data["schema"] = parsed_schemas

    # H1 and H2 headings
    h1s = [re.sub(r'<[^>]+>', '', t).strip() for _, t in re.findall(r'<(h1)[^>]*>(.*?)</h1>', html, re.DOTALL | re.IGNORECASE)]
    h2s = [re.sub(r'<[^>]+>', '', t).strip() for _, t in re.findall(r'<(h2)[^>]*>(.*?)</h2>', html, re.DOTALL | re.IGNORECASE)]
    data["h1"] = [h[:200] for h in h1s]
    data["h2"] = [h[:200] for h in h2s]

    # Images — alt text audit
    imgs = []
    for attrs in re.findall(r'<img([^>]*)>', html, re.IGNORECASE):
        src_m = re.search(r'src=["\']([^"\']+)', attrs)
        alt_m = re.search(r'alt=["\']([^"\']*)', attrs)
        src = src_m.group(1) if src_m else None
        if not src or re.search(r'(tracking|pixel|analytics|ct\.pinterest|facebook\.com/tr)', src or '', re.I):
            continue  # Skip tracking pixels
        alt = alt_m.group(1) if alt_m else None
        # Decorative SVGs with empty alt are intentional — flag only truly missing alts
        if src and src.endswith('.svg') and alt == '':
            status = "decorative"
        elif alt is None:
            status = "missing"
        elif alt.strip() == '':
            status = "empty"
        else:
            status = "ok"
        imgs.append({"src": src[:120], "alt": (alt or '')[:150], "status": status})
    data["images"] = imgs

    # Internal body links (excluding nav/header/footer)
    body_m = re.search(r'<body[^>]*>(.*)</body>', html, re.DOTALL | re.IGNORECASE)
    body = body_m.group(1) if body_m else html
    body_clean = re.sub(r'<(nav|header)[^>]*>.*?</\1>', '', body, flags=re.DOTALL | re.IGNORECASE)
    body_clean = re.sub(r'<footer[^>]*>.*?</footer>', '', body_clean, flags=re.DOTALL | re.IGNORECASE)
    int_links = []
    for href, link_text in re.findall(r'<a[^>]+href=["\']([^"\'#][^"\']*)["\'][^>]*>(.*?)</a>', body_clean, re.DOTALL | re.IGNORECASE):
        if href.startswith('/') or base_domain in href:
            clean_text = re.sub(r'<[^>]+>', '', link_text).strip()[:80]
            if clean_text:
                int_links.append({"href": href[:100], "text": clean_text})
    data["internal_body_links"] = int_links

    # robots meta
    m = re.search(r'<meta\s+name=["\']robots["\']\s+content=["\']([^"\']*)["\']', html, re.IGNORECASE)
    data["robots_meta"] = m.group(1) if m else None

    return data

def discover_pages(base_url, base_domain):
    """Discover all pages: sitemap first, then link crawl from homepage."""
    pages = set()
    sitemap_status = None

    # 1. Try sitemap
    sitemap_url = base_url.rstrip('/') + '/sitemap.xml'
    sitemap_html = fetch(sitemap_url)
    sitemap_status = fetch_status(sitemap_url)

    if sitemap_status == 200 and '<url>' in sitemap_html.lower():
        locs = re.findall(r'<loc>(.*?)</loc>', sitemap_html)
        for loc in locs:
            parsed = urllib.parse.urlparse(loc.strip())
            path = parsed.path.rstrip('/') or '/'
            if not re.search(r'\.(xml|css|js|png|jpg|jpeg|gif|svg|pdf)$', path, re.I):
                pages.add(path)
        print(f"  Sitemap: found {len(pages)} URLs", flush=True)
    else:
        print(f"  Sitemap: HTTP {sitemap_status} — falling back to link crawl", flush=True)

    # 2. Link crawl from homepage (always do this to catch any unlisted pages)
    homepage_html = fetch(base_url)
    found_links = extract_links(homepage_html, base_domain)
    pages.add('/')
    pages.update(found_links)

    # 3. Second-level crawl for pages found on homepage
    for page in list(found_links):
        if page == '/':
            continue
        page_html = fetch(base_url.rstrip('/') + page)
        if page_html:
            deeper = extract_links(page_html, base_domain)
            pages.update(deeper)

    # Filter out obvious non-content paths
    skip_patterns = re.compile(r'(/cdn-cgi/|/__/|/wp-json/|/feed/|\.xml$|\.json$)', re.I)
    pages = {p for p in pages if not skip_patterns.search(p)}

    return sorted(pages), sitemap_status, sitemap_url

def main():
    if len(sys.argv) < 3:
        print("Usage: python crawl_site.py <url> <output_dir>")
        sys.exit(1)

    base_url = sys.argv[1].rstrip('/')
    output_dir = Path(sys.argv[2])
    output_dir.mkdir(parents=True, exist_ok=True)

    parsed = urllib.parse.urlparse(base_url)
    base_domain = parsed.netloc

    print(f"\nCrawling: {base_url}", flush=True)
    print("=" * 60, flush=True)

    # robots.txt
    robots_url = base_url + '/robots.txt'
    robots_content = fetch(robots_url)
    robots_status = fetch_status(robots_url)

    # Discover pages
    print("\nDiscovering pages...", flush=True)
    pages, sitemap_status, sitemap_url = discover_pages(base_url, base_domain)
    # Limit to top-level pages (max 20) for large sites
    pages = sorted(pages)[:20]
    print(f"  Total pages to audit: {len(pages)} (limited to 20)", flush=True)

    # Audit each page
    print("\nAuditing pages...", flush=True)
    all_page_data = []
    for page in pages:
        url = base_url + page
        print(f"  {page}", flush=True)
        html = fetch(url)
        if not html:
            print(f"    WARNING: No content fetched", flush=True)
            continue
        page_data = extract_page_data(html, url, base_domain)
        page_data["path"] = page
        all_page_data.append(page_data)

    # Save results
    results = {
        "base_url": base_url,
        "base_domain": base_domain,
        "sitemap_url": sitemap_url,
        "sitemap_status": sitemap_status,
        "robots_status": robots_status,
        "robots_content": robots_content,
        "pages": all_page_data
    }

    out_file = output_dir / "crawl_results.json"
    with open(out_file, 'w') as f:
        json.dump(results, f, indent=2)

    print(f"\nCrawl complete. Results saved to: {out_file}", flush=True)
    print(f"Pages audited: {len(all_page_data)}", flush=True)

if __name__ == '__main__':
    main()
