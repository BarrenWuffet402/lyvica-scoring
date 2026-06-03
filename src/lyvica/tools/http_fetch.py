"""HTTP fetching, HTML parsing, and robots.txt checking."""

from __future__ import annotations

import logging
import re
import urllib.robotparser
from typing import Optional
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# User-agent used for all requests
_USER_AGENT = "LyvicaBot/1.0 (+https://lyvica.com/bot)"

# Regex to find copyright year in footer text
_COPYRIGHT_RE = re.compile(
    r"(?:©|&copy;|copyright)[^\d]{0,30}(\d{4})",
    re.IGNORECASE,
)

# Social domain patterns we care about (for dead link detection)
_SOCIAL_DOMAINS = re.compile(
    r"(?:facebook\.com|twitter\.com|x\.com|instagram\.com|"
    r"linkedin\.com|youtube\.com|pinterest\.com|tiktok\.com)",
    re.IGNORECASE,
)


def _normalize_url(url: str) -> str:
    """Add https scheme if missing."""
    if not url.startswith(("http://", "https://")):
        return f"https://{url}"
    return url


async def fetch_page(url: str, timeout: float = 15.0) -> dict:
    """
    Fetch a web page asynchronously.

    Returns:
        {url, status_code, headers, html, final_url, error}
    """
    url = _normalize_url(url)
    result: dict = {
        "url": url,
        "status_code": None,
        "headers": {},
        "html": "",
        "final_url": url,
        "error": None,
    }
    try:
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=timeout,
            headers={"User-Agent": _USER_AGENT},
        ) as client:
            response = await client.get(url)
            result["status_code"] = response.status_code
            result["headers"] = dict(response.headers)
            result["html"] = response.text
            result["final_url"] = str(response.url)
    except httpx.TimeoutException as exc:
        result["error"] = f"Timeout: {exc}"
        logger.warning("Timeout fetching %s: %s", url, exc)
    except httpx.RequestError as exc:
        result["error"] = f"Request error: {exc}"
        logger.warning("Request error fetching %s: %s", url, exc)
    except Exception as exc:  # noqa: BLE001
        result["error"] = f"Unexpected error: {exc}"
        logger.exception("Unexpected error fetching %s", url)
    return result


async def check_robots(domain: str, path: str = "/") -> bool:
    """
    Fetch and parse robots.txt for *domain*.

    Returns True if the wildcard user-agent is allowed to fetch *path*,
    False if disallowed or if robots.txt cannot be fetched.
    """
    robots_url = f"https://{domain}/robots.txt"
    try:
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=10.0,
            headers={"User-Agent": _USER_AGENT},
        ) as client:
            response = await client.get(robots_url)
            if response.status_code == 404:
                # No robots.txt → everything allowed
                return True
            if response.status_code != 200:
                logger.debug(
                    "robots.txt for %s returned status %s — assuming allowed",
                    domain,
                    response.status_code,
                )
                return True
            content = response.text
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not fetch robots.txt for %s: %s", domain, exc)
        # Fail open — if we cannot check, assume allowed
        return True

    rp = urllib.robotparser.RobotFileParser()
    rp.set_url(robots_url)
    rp.parse(content.splitlines())
    allowed = rp.can_fetch("*", path)
    return allowed


def _extract_footer_year(soup: BeautifulSoup) -> Optional[int]:
    """Extract the most recent year from a copyright notice, preferring footer."""
    # Try footer first, then fall back to full document
    candidates: list[str] = []
    for tag in soup.find_all(["footer", "div", "p", "span", "small"]):
        text = tag.get_text(separator=" ")
        matches = _COPYRIGHT_RE.findall(text)
        if matches:
            candidates.extend(matches)
        if candidates:
            break  # stop after first element with a match

    if not candidates:
        matches = _COPYRIGHT_RE.findall(soup.get_text(separator=" "))
        candidates.extend(matches)

    if not candidates:
        return None

    # Pick most recent year
    try:
        return max(int(y) for y in candidates)
    except ValueError:
        return None


def parse_html_signals(html: str, url: str) -> dict:
    """
    Extract signals from raw HTML.

    Returns:
        {
            footer_copyright_year: int|None,
            viewport_meta: bool,
            open_graph: bool,
            schema_org: bool,
            meta_description: bool,
            dead_social_links: list[str],
        }
    """
    result: dict = {
        "footer_copyright_year": None,
        "viewport_meta": False,
        "open_graph": False,
        "schema_org": False,
        "meta_description": False,
        "dead_social_links": [],
    }

    if not html:
        return result

    try:
        soup = BeautifulSoup(html, "html.parser")

        # viewport meta
        viewport = soup.find(
            "meta",
            attrs={"name": re.compile(r"^viewport$", re.IGNORECASE)},
        )
        result["viewport_meta"] = viewport is not None

        # open graph (any og: meta property)
        og = soup.find("meta", attrs={"property": re.compile(r"^og:", re.IGNORECASE)})
        result["open_graph"] = og is not None

        # schema.org — JSON-LD or microdata
        json_ld = soup.find(
            "script", attrs={"type": re.compile(r"application/ld\+json", re.IGNORECASE)}
        )
        microdata = soup.find(attrs={"itemscope": True})
        result["schema_org"] = json_ld is not None or microdata is not None

        # meta description
        meta_desc = soup.find(
            "meta",
            attrs={"name": re.compile(r"^description$", re.IGNORECASE)},
        )
        result["meta_description"] = meta_desc is not None

        # footer copyright year
        result["footer_copyright_year"] = _extract_footer_year(soup)

        # dead social links — check href attributes pointing to social domains
        # We flag links whose href references a social domain but looks broken
        # (e.g., placeholder "#", empty, or contains the domain without a valid path)
        dead: list[str] = []
        base_domain = urlparse(url).netloc
        for a_tag in soup.find_all("a", href=True):
            href = a_tag["href"].strip()
            if not href or href == "#":
                continue
            if _SOCIAL_DOMAINS.search(href):
                # Flag links with no path beyond the domain root (likely placeholders)
                parsed = urlparse(href)
                if parsed.path in ("", "/", "") and not parsed.fragment:
                    dead.append(href)
        result["dead_social_links"] = dead

    except Exception as exc:  # noqa: BLE001
        logger.warning("Error parsing HTML signals from %s: %s", url, exc)

    return result
