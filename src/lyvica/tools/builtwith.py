"""BuiltWith Lists API and Domain API wrappers.

When BUILTWITH_API_KEY is not set, domain_lookup falls back to a free
HTML/header-based technology detector so the pipeline runs without any
paid subscription.
"""

from __future__ import annotations

import logging
import re
from typing import Optional

import httpx

from ..config import settings

logger = logging.getLogger(__name__)

_LISTS_URL = "https://api.builtwith.com/lists/v2/api.json"
_DOMAIN_URL = "https://api.builtwith.com/v21/api.json"

# ---------------------------------------------------------------------------
# CMS keyword set (shared by both the API parser and the free detector)
# ---------------------------------------------------------------------------

_CMS_KEYWORDS = {
    "wordpress", "drupal", "joomla", "shopify", "squarespace",
    "wix", "webflow", "ghost", "magento", "typo3", "prestashop",
    "opencart", "craft cms", "umbraco", "sitecore", "kentico",
    "contentful", "strapi", "sanity",
}

# ---------------------------------------------------------------------------
# Free HTML/header technology detector
# ---------------------------------------------------------------------------

# Each entry: (signal_name, regex_pattern, field)
# field = "technologies" item to append; CMS entries are identified separately.
_HTML_PATTERNS: list[tuple[str, str]] = [
    # CMS fingerprints
    ("WordPress",         r"wp-(?:content|includes|json)[/\"']"),
    ("Drupal",            r"(?:Drupal\.settings|drupal\.js|/sites/(?:all|default)/)"),
    ("Joomla",            r"/components/com_[a-z]"),
    ("Squarespace",       r"static\d*\.squarespace\.com"),
    ("Shopify",           r"cdn\.shopify\.com"),
    ("Webflow",           r"webflow\.com"),
    ("Wix",               r"static\.wixstatic\.com"),
    ("Ghost",             r"/ghost/api/|ghost\.io"),
    ("Magento",           r"Mage\.Cookies|skin/frontend/"),
    # JS libraries
    ("jQuery/1",          r"jquery[.-]1\.\d+"),
    ("jQuery/2",          r"jquery[.-]2\.\d+"),
    ("jQuery/3",          r"jquery[.-]3\.\d+"),
    ("Bootstrap/3",       r"bootstrap[.-]3\.\d+"),
    ("Bootstrap/4",       r"bootstrap[.-]4\.\d+"),
    ("Bootstrap/5",       r"bootstrap[.-]5\.\d+"),
    ("React",             r'(?:react\.development\.js|react\.production\.min\.js|"__NEXT_DATA__")'),
    ("Vue.js",            r'(?:vue\.min\.js|vue\.esm\.js|"__vue_app__")'),
    ("Angular",           r'(?:angular\.min\.js|ng-version=|ng2-app)'),
    ("Svelte",            r'__svelte|svelte-'),
    # Dead tech
    ("Flash",             r'\.swf["\' ]|<object[^>]+\.swf|<embed[^>]+\.swf'),
    ("Silverlight",       r'Silverlight\.js|application/x-silverlight'),
    ("MooTools",          r'mootools[.-]core'),
    ("Prototype.js",      r'prototype\.js'),
]

# Generator meta tag: <meta name="generator" content="WordPress 5.9.3">
_GENERATOR_RE = re.compile(
    r'<meta[^>]+name=["\']generator["\'][^>]+content=["\']([^"\']+)["\']',
    re.IGNORECASE,
)

# X-Powered-By: PHP/7.4.33
_PHP_HEADER_RE = re.compile(r"php[/ ](\d+\.\d+)", re.IGNORECASE)


def detect_technologies_from_html(html: str, headers: dict) -> dict:
    """
    Free tech-stack detector based on pre-fetched HTML and response headers.

    Returns the same shape as the BuiltWith API parser:
        {technologies: list[str], cms: str|None, cms_version: str|None, error: None}
    """
    techs: list[str] = []
    cms_name: Optional[str] = None
    cms_ver: Optional[str] = None

    # ── Generator meta tag ────────────────────────────────────────────────
    if html:
        gen_match = _GENERATOR_RE.search(html)
        if gen_match:
            gen_value = gen_match.group(1).strip()
            # e.g. "WordPress 5.9.3"
            parts = gen_value.split(" ", 1)
            tech_name = parts[0]
            tech_version = parts[1] if len(parts) > 1 else None
            tag = f"{tech_name}/{tech_version}" if tech_version else tech_name
            techs.append(tag)
            name_lower = tech_name.lower()
            for kw in _CMS_KEYWORDS:
                if kw in name_lower:
                    cms_name = tech_name
                    cms_ver = tech_version
                    break

        # ── HTML pattern matching ─────────────────────────────────────────
        for tech_label, pattern in _HTML_PATTERNS:
            if re.search(pattern, html, re.IGNORECASE):
                if tech_label not in techs:
                    techs.append(tech_label)
                    # Mark as CMS if applicable and no CMS found yet
                    if cms_name is None:
                        for kw in _CMS_KEYWORDS:
                            if kw in tech_label.lower():
                                cms_name = tech_label
                                break

    # ── HTTP headers ──────────────────────────────────────────────────────
    powered_by = headers.get("x-powered-by", "") or headers.get("X-Powered-By", "")
    if powered_by:
        techs.append(powered_by)
        php_match = _PHP_HEADER_RE.search(powered_by)
        if php_match:
            techs.append(f"PHP/{php_match.group(1)}")

    server = headers.get("server", "") or headers.get("Server", "")
    if server:
        techs.append(f"Server/{server}")

    # Wix publishes a version header
    wix_ver = headers.get("x-wix-published-version") or headers.get("X-Wix-Published-Version")
    if wix_ver:
        techs.append(f"Wix/{wix_ver}")
        if cms_name is None:
            cms_name = "Wix"
            cms_ver = wix_ver

    return {
        "technologies": list(dict.fromkeys(techs)),  # dedupe preserving order
        "cms": cms_name,
        "cms_version": cms_ver,
        "error": None,
    }


class ConfigurationError(Exception):
    """Raised when a required configuration value is missing."""


async def list_domains(
    technologies: list[str],
    countries: list[str],
    industries: list[str],
    limit: int = 500,
) -> list[str]:
    """
    Query the BuiltWith Lists API to discover domains by technology/country/industry.

    Returns a list of domain strings. Returns [] on any error.
    """
    api_key = settings.builtwith_api_key
    if not api_key:
        logger.warning(
            "BUILTWITH_API_KEY not set — skipping BuiltWith sourcing."
        )
        return []

    if not technologies:
        logger.debug("No technologies specified for BuiltWith list query.")
        return []

    params: dict = {
        "KEY": api_key,
        "TECH": ",".join(technologies),
        "LIMIT": str(limit),
    }
    if countries:
        params["COUNTRY"] = ",".join(countries)
    # NOTE: The BuiltWith Lists API uses a NICHE parameter for industry filtering,
    # but the exact taxonomy varies. Pass as NICHE if provided.
    if industries:
        params["NICHE"] = ",".join(industries)

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(_LISTS_URL, params=params)
            response.raise_for_status()
            data = response.json()
    except httpx.HTTPStatusError as exc:
        logger.error(
            "BuiltWith Lists API HTTP error %s: %s",
            exc.response.status_code,
            exc.response.text[:500],
        )
        return []
    except httpx.RequestError as exc:
        logger.error("BuiltWith Lists API request error: %s", exc)
        return []
    except Exception as exc:  # noqa: BLE001
        logger.exception("Unexpected error calling BuiltWith Lists API: %s", exc)
        return []

    # Parse response — expected: {"Results": [{"Domain": "example.com", ...}, ...]}
    domains: list[str] = []
    try:
        results = data.get("Results") or data.get("results") or []
        for item in results:
            domain = (
                item.get("Domain")
                or item.get("domain")
                or item.get("Url")
                or item.get("url")
            )
            if domain:
                # Strip protocol if present
                domain = domain.replace("https://", "").replace("http://", "").rstrip("/")
                domains.append(domain)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not parse BuiltWith Lists API response: %s", exc)

    logger.info("BuiltWith returned %d domains.", len(domains))
    return domains


async def domain_lookup(
    domain: str,
    html: str = "",
    headers: Optional[dict] = None,
) -> dict:
    """
    Return technology details for a domain.

    If BUILTWITH_API_KEY is set, calls the BuiltWith Domain API.
    Otherwise falls back to free HTML/header detection using the pre-fetched
    *html* and *headers* passed in from the agent (no second HTTP request).

    Returns:
        {technologies: list[str], cms: str|None, cms_version: str|None, error: str|None}
    """
    api_key = settings.builtwith_api_key

    if not api_key:
        logger.debug(
            "BUILTWITH_API_KEY not set — using free HTML detector for %s", domain
        )
        return detect_technologies_from_html(html, headers or {})

    params = {"KEY": api_key, "LOOKUP": domain}
    result: dict = {
        "technologies": [],
        "cms": None,
        "cms_version": None,
        "error": None,
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(_DOMAIN_URL, params=params)
            response.raise_for_status()
            data = response.json()
    except httpx.HTTPStatusError as exc:
        result["error"] = f"HTTP {exc.response.status_code}: {exc.response.text[:200]}"
        logger.error("BuiltWith Domain API error for %s: %s", domain, result["error"])
        return result
    except httpx.RequestError as exc:
        result["error"] = f"Request error: {exc}"
        logger.error("BuiltWith Domain API request error for %s: %s", domain, exc)
        return result
    except Exception as exc:  # noqa: BLE001
        result["error"] = f"Unexpected error: {exc}"
        logger.exception("Unexpected error in BuiltWith domain lookup for %s", domain)
        return result

    # Parse: Results[0].Paths[0].Technologies
    try:
        paths = data.get("Results", [{}])[0].get("Paths", [{}])
        techs: list[str] = []
        cms_name: Optional[str] = None
        cms_ver: Optional[str] = None

        for path in paths:
            for tech in path.get("Technologies", []):
                name = tech.get("Name") or ""
                version = tech.get("Version") or None
                tag_full = f"{name}/{version}" if version else name
                techs.append(tag_full)

                name_lower = name.lower()
                for cms_kw in _CMS_KEYWORDS:
                    if cms_kw in name_lower and cms_name is None:
                        cms_name = name
                        cms_ver = version
                        break

        result["technologies"] = techs
        result["cms"] = cms_name
        result["cms_version"] = cms_ver
    except (IndexError, KeyError, TypeError) as exc:
        logger.warning("Could not parse BuiltWith domain response for %s: %s", domain, exc)

    return result
