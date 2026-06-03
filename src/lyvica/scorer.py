"""Fixed rebuild-opportunity scoring logic for Lyvica."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Optional

from .models import Subscores

# ---------------------------------------------------------------------------
# Technology obsolescence lookup
# ---------------------------------------------------------------------------

# (pattern, score) — evaluated in order; first match wins.
# Patterns may be regex strings or plain lowercase substrings.
_TECH_RULES: list[tuple[str, float, bool]] = [
    # (pattern, score, is_regex)
    (r"flash",                           100.0, True),
    (r"jquery/1\.",                      100.0, True),
    (r"table[\s_-]?layout",             100.0, True),
    (r"<font",                           100.0, True),  # font-tag HTML signals
    (r"jquery/2\.",                       80.0, True),
    (r"php/5\.",                          80.0, True),
    (r"php 5\.",                          80.0, True),
    # WordPress major version <= 4
    (r"wordpress/[1-4]\b",               80.0, True),
    (r"wordpress [1-4]\b",               80.0, True),
    # Moderately old (60)
    (r"jquery/3\.",                       60.0, True),
    (r"php/7\.",                          60.0, True),
    (r"php 7\.",                          60.0, True),
    (r"drupal/7",                         60.0, True),
    (r"joomla/3",                         60.0, True),
    (r"bootstrap/3",                      60.0, True),
    (r"mootools",                         60.0, True),
    (r"prototype\.js",                    60.0, True),
    (r"scriptaculous",                    60.0, True),
    (r"dojo/1\.[0-7]",                   60.0, True),
    (r"silverlight",                      80.0, True),
    (r"activex",                          80.0, True),
    (r"java applet",                      80.0, True),
]

# Plain lowercase substring lookup for fast checks (no regex needed)
_PLAIN_OLD_TECH: dict[str, float] = {
    "flash":          100.0,
    "table layout":   100.0,
    "silverlight":     80.0,
    "activex":         80.0,
    "java applet":     80.0,
    "mootools":        60.0,
    "scriptaculous":   60.0,
}

# Modern stacks that should score 0
_MODERN_STACKS: set[str] = {
    "react", "next.js", "nextjs", "vue.js", "vuejs", "nuxt", "svelte",
    "astro", "remix", "angular", "gatsby", "vite", "webpack 5",
    "tailwind", "tailwindcss", "bootstrap/5", "bootstrap 5",
    "php/8", "php 8", "laravel", "symfony",
    "wordpress/6", "wordpress 6", "wordpress/5", "wordpress 5",
    "shopify", "webflow", "framer",
}


def compute_tech_obsolescence(
    technologies: list[str],
    cms: Optional[str],
    cms_version: Optional[str],
) -> float:
    """Return 0–100 obsolescence score based on detected technologies."""
    if not technologies and cms is None:
        return 0.0

    # Build a combined searchable corpus (lowercase)
    corpus_parts: list[str] = [t.lower() for t in (technologies or [])]
    if cms:
        cms_str = cms.lower()
        if cms_version:
            corpus_parts.append(f"{cms_str}/{cms_version.lower()}")
            corpus_parts.append(f"{cms_str} {cms_version.lower()}")
        else:
            corpus_parts.append(cms_str)

    # Check modern stacks first — if any modern tech found, cap at 0
    for part in corpus_parts:
        for modern in _MODERN_STACKS:
            if modern in part:
                return 0.0

    max_score = 0.0

    for part in corpus_parts:
        # Plain-substring checks
        for keyword, score in _PLAIN_OLD_TECH.items():
            if keyword in part:
                max_score = max(max_score, score)

        # Regex checks
        for pattern, score, is_regex in _TECH_RULES:
            if is_regex:
                if re.search(pattern, part, re.IGNORECASE):
                    max_score = max(max_score, score)

    return max_score


# ---------------------------------------------------------------------------
# Content freshness
# ---------------------------------------------------------------------------

# Breakpoints: (years_old, score)
_FRESHNESS_BREAKPOINTS: list[tuple[float, float]] = [
    (0.0,  0.0),
    (1.0,  0.0),
    (2.0, 40.0),
    (3.0, 70.0),
    (4.0, 100.0),
]


def _interpolate(x: float, breakpoints: list[tuple[float, float]]) -> float:
    """Linear interpolation across a list of (x, y) breakpoints."""
    if x <= breakpoints[0][0]:
        return breakpoints[0][1]
    if x >= breakpoints[-1][0]:
        return breakpoints[-1][1]
    for i in range(len(breakpoints) - 1):
        x0, y0 = breakpoints[i]
        x1, y1 = breakpoints[i + 1]
        if x0 <= x <= x1:
            t = (x - x0) / (x1 - x0)
            return y0 + t * (y1 - y0)
    return breakpoints[-1][1]


def compute_content_freshness(
    last_change: Optional[datetime],
    footer_year: Optional[int],
) -> float:
    """Return 0–100 staleness score. Higher = more stale."""
    now = datetime.now(tz=timezone.utc)

    if last_change is not None:
        # Ensure timezone-aware comparison
        if last_change.tzinfo is None:
            last_change = last_change.replace(tzinfo=timezone.utc)
        years_old = (now - last_change).days / 365.25
    elif footer_year is not None:
        years_old = max(0.0, now.year - footer_year)
    else:
        # No signal available
        return 0.0

    return round(_interpolate(years_old, _FRESHNESS_BREAKPOINTS), 2)


# ---------------------------------------------------------------------------
# SEO hygiene
# ---------------------------------------------------------------------------


def compute_seo_hygiene(
    open_graph: Optional[bool],
    meta_description: Optional[bool],
    schema_org: Optional[bool],
) -> float:
    """Return 0–100 SEO-deficiency score. Higher = worse hygiene."""
    score = 0.0
    for signal in (open_graph, meta_description, schema_org):
        if not signal:  # False or None both count as missing
            score += 34.0
    return min(100.0, round(score, 2))


# ---------------------------------------------------------------------------
# Main scoring function
# ---------------------------------------------------------------------------

_WEIGHTS: dict[str, float] = {
    "mobile":           0.20,
    "visual_datedness": 0.20,
    "tech_obsolescence":0.20,
    "performance":      0.15,
    "security":         0.10,
    "content_freshness":0.10,
    "seo_hygiene":      0.05,
}

_TOTAL = 7  # total subscore fields


def score_lead(subscores: Subscores) -> tuple[float, str, float]:
    """
    Compute rebuild_opportunity_score, tier, and confidence from subscores.

    Returns:
        (score, tier, confidence)
        - score: 0–100 float
        - tier: "hot" | "warm" | "cold"
        - confidence: 0.0–1.0 float
    """
    values: dict[str, Optional[float]] = {
        "mobile":           subscores.mobile,
        "visual_datedness": subscores.visual_datedness,
        "tech_obsolescence":subscores.tech_obsolescence,
        "performance":      subscores.performance,
        "security":         subscores.security,
        "content_freshness":subscores.content_freshness,
        "seo_hygiene":      subscores.seo_hygiene,
    }

    null_count = sum(1 for v in values.values() if v is None)
    non_null = _TOTAL - null_count

    # Weighted sum — treat None as 0
    weighted_sum = sum(
        _WEIGHTS[k] * (v if v is not None else 0.0)
        for k, v in values.items()
    )
    score = round(weighted_sum)

    # Confidence
    if null_count >= 2:
        confidence = 0.4 + (non_null / _TOTAL) * 0.4
    else:
        confidence = 0.6 + (non_null / _TOTAL) * 0.4

    confidence = round(min(1.0, max(0.0, confidence)), 4)

    # Tiering
    if score >= 70:
        tier = "hot"
    elif score >= 50:
        tier = "warm"
    else:
        tier = "cold"

    # Cap tier at "warm" when >= 2 null subscores
    if null_count >= 2 and tier == "hot":
        tier = "warm"

    return float(score), tier, confidence
