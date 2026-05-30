"""
scorer.py — Donor Readiness Audit
Deterministic, Claude-free scoring of audit signals into five dimension
scores and an overall score. All logic is explicit point math — no
heuristics, no LLM involvement.

Usage:
    from scorer import score
    result = score(signals, companion_stats)

Returns:
    {
        "dimensions": {
            "giving_experience": {"score": 72, "tier": "Established"},
            "impact_trust":      {"score": 45, "tier": "Developing"},
            "visitor_activation":{"score": 28, "tier": "Developing"},
            "findability":       {"score": 61, "tier": "Established"},
            "accessibility":     {"score": 38, "tier": "Developing"},
        },
        "overall": {"score": 49, "tier": "Developing"},
    }

Tier thresholds:
    0–25   → Emerging
    26–50  → Developing
    51–75  → Established
    76–100 → Optimized
"""


# ── Tier assignment ────────────────────────────────────────────────────────────

def tier(score: int) -> str:
    """Convert a 0–100 score to a named readiness tier."""
    if score <= 25:
        return "Emerging"
    elif score <= 50:
        return "Developing"
    elif score <= 75:
        return "Established"
    else:
        return "Optimized"


# ── Dimension 1: Giving Experience ────────────────────────────────────────────
# How easy and compelling is it to donate?
#
# Signal source: signals['donate_page'], signals['pages']['homepage'],
#                signals['mobile'], signals['trust']
#
# Max 100 points:
#   20 — donate page or link exists
#   15 — donate button/link is in the nav
#   20 — recognized payment processor detected
#   15 — recurring / monthly giving option offered
#   10 — suggested donation amounts shown
#   10 — impact framing present on the donate page
#   10 — donate CTA visible above the fold on mobile

def score_giving_experience(signals: dict) -> int:
    donate = signals.get("donate_page", {})
    homepage = signals.get("pages", {}).get("homepage", {})
    mobile = signals.get("mobile", {})

    pts = 0

    # 20 pts — donate page / link exists
    donate_found = (
        donate.get("donate_page_found") is not False      # explicit False means not found
        and donate                                         # non-empty dict
        and donate.get("donate_processor") is not None    # processor key present
    )
    if donate_found:
        pts += 20

    # 15 pts — donate in nav
    if homepage.get("donate_in_nav"):
        pts += 15

    # 20 pts — recognized payment processor
    processor = (donate.get("donate_processor") or "").lower()
    known_processors = {
        "classy", "bloomerang", "donorbox", "paypal", "stripe", "qgiv",
        "networkforgood", "razoo", "fundly", "mightycause", "givebutter",
        "donately", "every.org", "double.giving", "salesforce",
    }
    if any(p in processor for p in known_processors):
        pts += 20

    # 15 pts — recurring giving
    if donate.get("has_recurring_giving"):
        pts += 15

    # 10 pts — suggested amounts
    if donate.get("has_suggested_amounts"):
        pts += 10

    # 10 pts — impact framing on donate page
    if donate.get("has_impact_framing_on_donate_page"):
        pts += 10

    # 10 pts — mobile CTA visible above the fold
    if mobile.get("donate_cta_above_fold"):
        pts += 10

    return min(100, pts)


# ── Dimension 2: Impact & Trust ───────────────────────────────────────────────
# Does the site demonstrate credibility and show real-world outcomes?
#
# Signal source: signals['trust'], signals['https'], signals['file_links']
#
# Max 100 points:
#   15 — HTTPS
#   10 — phone number present on site
#   10 — physical address present on site
#   20 — charity watchdog badge (Charity Navigator, Candid/GuideStar, BBB Wise)
#   25 — impact statistics (quantified outcomes)
#        0 stats = 0, 1–2 = 10, 3–5 = 18, 6+ = 25
#   10 — impact / annual report PDF linked from the site
#   10 — social media presence

def score_impact_trust(signals: dict) -> int:
    trust = signals.get("trust", {})
    file_links = signals.get("file_links", [])

    pts = 0

    # 15 pts — HTTPS
    if signals.get("https") or trust.get("https"):
        pts += 15

    # 10 pts — phone number
    if trust.get("has_phone"):
        pts += 10

    # 10 pts — physical address
    if trust.get("has_address"):
        pts += 10

    # 20 pts — charity watchdog badge
    if trust.get("has_charity_badge"):
        pts += 20

    # 25 pts — impact statistics (tiered)
    impact_count = len(trust.get("all_impact_stats") or [])
    if impact_count >= 6:
        pts += 25
    elif impact_count >= 3:
        pts += 18
    elif impact_count >= 1:
        pts += 10

    # 10 pts — impact/annual report PDF linked
    has_impact_pdf = any(
        fl.get("category") == "impact" and fl.get("file_type") == "document"
        for fl in file_links
    )
    if has_impact_pdf:
        pts += 10

    # 10 pts — social media links
    if trust.get("social_links"):
        pts += 10

    return min(100, pts)


# ── Dimension 3: Visitor Activation ──────────────────────────────────────────
# Can visitors take meaningful action beyond donating?
#
# Signal source: signals['volunteer_page'], signals['trust'],
#                signals['pages']['homepage']
#
# Max 100 points:
#   25 — volunteer sign-up mechanism
#        none_found = 0, email_only = 10, form/platform = 25
#   15 — specific volunteer roles described
#   20 — email capture form on site
#   15 — newsletter sign-up link or mention
#   10 — embedded third-party form present
#   15 — CTAs (donate or volunteer) visible in body/buttons

def score_visitor_activation(signals: dict) -> int:
    volunteer = signals.get("volunteer_page", {})
    trust = signals.get("trust", {})
    homepage = signals.get("pages", {}).get("homepage", {})

    pts = 0

    # 25 pts — volunteer sign-up quality
    signup_type = volunteer.get("volunteer_signup_type", "none_found")
    if signup_type == "form":
        pts += 25
    elif signup_type == "email_only":
        pts += 10

    # 15 pts — specific roles listed
    if volunteer.get("has_specific_volunteer_roles"):
        pts += 15

    # 20 pts — email capture form (any page)
    if trust.get("has_email_capture"):
        pts += 20

    # 15 pts — newsletter link or mention
    if trust.get("has_newsletter_link"):
        pts += 15

    # 10 pts — embedded form (iframes / Bloomerang widget / etc.)
    if homepage.get("has_embedded_form"):
        pts += 10

    # 15 pts — CTAs visible on homepage
    if homepage.get("cta_texts"):
        pts += 15

    return min(100, pts)


# ── Dimension 4: Findability ──────────────────────────────────────────────────
# Can search engines and visitors actually find the site and its pages?
#
# Signal source: companion_stats['seo']
#
# Max 100 points:
#   20 — XML sitemap present
#   10 — robots.txt present
#   35 — meta description coverage  (35 × covered_ratio)
#   20 — H1 coverage                (20 × covered_ratio)
#   15 — image alt text             15 if 0 missing, else max(0, 15 − missing×2)
#
# If no SEO data available: returns 50 (neutral / unknown).

def score_findability(seo: dict) -> int:
    if not seo:
        return 50

    pts = 0
    pages = seo.get("pages_crawled", 0)

    # 20 pts — sitemap
    if seo.get("has_sitemap"):
        pts += 20

    # 10 pts — robots.txt
    if seo.get("has_robots"):
        pts += 10

    # 35 pts — meta description coverage
    if pages > 0:
        missing_meta = seo.get("missing_meta_description", 0)
        covered = max(0, pages - missing_meta)
        pts += round(35 * covered / pages)
    else:
        pts += 17  # no data → half credit

    # 20 pts — H1 coverage
    if pages > 0:
        missing_h1 = seo.get("missing_h1", 0)
        covered_h1 = max(0, pages - missing_h1)
        pts += round(20 * covered_h1 / pages)
    else:
        pts += 10

    # 15 pts — image alt text
    missing_alt = seo.get("images_missing_alt", 0)
    if missing_alt == 0:
        pts += 15
    else:
        pts += max(0, 15 - missing_alt * 2)

    return min(100, pts)


# ── Dimension 5: Accessibility ────────────────────────────────────────────────
# Can people with disabilities use the site?
#
# Signal source: companion_stats['a11y']
#
# Starts at 100 and deducts for violations:
#   critical violations: −15 each (capped at −45)
#   serious violations:  −8 each  (capped at −24)
#   moderate violations: −3 each  (capped at −15)
#   unique issue types:  −2 each  (capped at −16)
#
# If no a11y data available: returns 50 (neutral / unknown).

def score_accessibility(a11y: dict) -> int:
    if not a11y:
        return 50

    critical = a11y.get("critical", 0)
    serious  = a11y.get("serious", 0)
    moderate = a11y.get("moderate", 0)
    unique   = a11y.get("unique_issue_types", 0)

    penalty = (
        min(45, critical * 15)
        + min(24, serious  *  8)
        + min(15, moderate *  3)
        + min(16, unique   *  2)
    )
    return max(0, 100 - penalty)


# ── Public API ─────────────────────────────────────────────────────────────────

def score(signals: dict, companion_stats: dict | None = None) -> dict:
    """
    Score a completed audit.

    Args:
        signals:         Output of crawler.crawl()
        companion_stats: Optional dict with 'seo' and/or 'a11y' sub-dicts
                         from companion crawls. Pass None or {} if unavailable.

    Returns:
        Dict with 'dimensions' (per-dimension score + tier) and 'overall'
        (average score + tier). Scores are integers 0–100.
    """
    cs = companion_stats or {}

    dim_scores = {
        "giving_experience":  score_giving_experience(signals),
        "impact_trust":       score_impact_trust(signals),
        "visitor_activation": score_visitor_activation(signals),
        "findability":        score_findability(cs.get("seo")),
        "accessibility":      score_accessibility(cs.get("a11y")),
    }

    overall_score = round(sum(dim_scores.values()) / len(dim_scores))

    return {
        "dimensions": {
            name: {"score": s, "tier": tier(s)}
            for name, s in dim_scores.items()
        },
        "overall": {
            "score": overall_score,
            "tier":  tier(overall_score),
        },
    }


# ── CLI (smoke test) ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    import json, sys

    if len(sys.argv) < 2:
        # Quick sanity check with minimal fake signals
        fake_signals = {
            "https": True,
            "donate_page": {
                "donate_processor": "donorbox",
                "has_recurring_giving": True,
                "has_suggested_amounts": True,
                "has_impact_framing_on_donate_page": False,
            },
            "pages": {
                "homepage": {
                    "donate_in_nav": True,
                    "has_email_capture": True,
                    "has_newsletter_link": False,
                    "has_embedded_form": False,
                    "cta_texts": ["Donate", "Volunteer"],
                }
            },
            "mobile": {"donate_cta_above_fold": True},
            "trust": {
                "https": True,
                "has_phone": True,
                "has_address": False,
                "has_charity_badge": False,
                "all_impact_stats": ["500 families", "$120,000 raised", "3 years"],
                "has_email_capture": True,
                "has_newsletter_link": False,
                "social_links": ["https://facebook.com/example"],
            },
            "volunteer_page": {
                "volunteer_signup_type": "form",
                "has_specific_volunteer_roles": True,
            },
            "file_links": [],
        }
        fake_companion = {
            "seo": {
                "pages_crawled": 10,
                "missing_meta_description": 3,
                "missing_h1": 1,
                "images_missing_alt": 4,
                "has_sitemap": True,
                "has_robots": True,
            },
            "a11y": {
                "critical": 1,
                "serious": 3,
                "moderate": 5,
                "unique_issue_types": 4,
            },
        }
        result = score(fake_signals, fake_companion)
        print(json.dumps(result, indent=2))
    else:
        # Accept a signals JSON file as argument
        with open(sys.argv[1]) as f:
            data = json.load(f)
        companion = json.loads(sys.argv[2]) if len(sys.argv) > 2 else None
        result = score(data, companion)
        print(json.dumps(result, indent=2))
