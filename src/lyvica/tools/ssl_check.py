"""HTTPS/TLS certificate and mixed-content checking."""

from __future__ import annotations

import logging
import re
import socket
import ssl
from datetime import datetime, timezone

import httpx

logger = logging.getLogger(__name__)

# Patterns for mixed-content detection: http:// in src/href attributes
_MIXED_CONTENT_RE = re.compile(
    r"""(?:src|href|action|data-src)\s*=\s*['"]http://([^'">\s]+)['"]""",
    re.IGNORECASE,
)

# Common external asset CDNs / ad networks that signal mixed content
_ASSET_PATTERNS = re.compile(
    r"\.(?:js|css|png|jpg|jpeg|gif|webp|woff2?|ttf|svg|ico)(?:\?[^'\"]*)?$",
    re.IGNORECASE,
)


def _check_cert_socket(domain: str) -> dict:
    """
    Low-level TLS check using ssl + socket.

    Returns {https: bool, cert_valid: bool, cert_expiry: str|None, error: str|None}
    """
    ctx = ssl.create_default_context()
    result = {
        "https": False,
        "cert_valid": False,
        "cert_expiry": None,
        "error": None,
    }
    try:
        with socket.create_connection((domain, 443), timeout=10) as sock:
            with ctx.wrap_socket(sock, server_hostname=domain) as ssock:
                result["https"] = True
                result["cert_valid"] = True
                cert = ssock.getpeercert()
                not_after = cert.get("notAfter")
                if not_after:
                    # Format: "Jan  1 00:00:00 2099 GMT"
                    expiry = datetime.strptime(not_after, "%b %d %H:%M:%S %Y %Z").replace(
                        tzinfo=timezone.utc
                    )
                    result["cert_expiry"] = expiry.isoformat()
    except ssl.SSLCertVerificationError as exc:
        result["https"] = True  # TLS exists but cert is invalid
        result["cert_valid"] = False
        result["error"] = f"Cert verification failed: {exc}"
        logger.debug("SSL cert invalid for %s: %s", domain, exc)
    except ssl.SSLError as exc:
        result["https"] = False
        result["error"] = f"SSL error: {exc}"
        logger.debug("SSL error for %s: %s", domain, exc)
    except (socket.timeout, ConnectionRefusedError, OSError) as exc:
        result["https"] = False
        result["error"] = f"Connection error: {exc}"
        logger.debug("Cannot connect to port 443 for %s: %s", domain, exc)
    return result


def _detect_mixed_content(html: str, domain: str) -> bool:
    """
    Scan HTML for http:// resource references.

    Considers references to the same domain or common asset file extensions
    as mixed content indicators.
    """
    matches = _MIXED_CONTENT_RE.findall(html)
    for match_url in matches:
        # Flag if it points to same domain
        if domain.lower() in match_url.lower():
            return True
        # Flag if it looks like a static asset (script, style, image, font)
        if _ASSET_PATTERNS.search(match_url):
            return True
    return False


async def check_ssl(domain: str) -> dict:
    """
    Check HTTPS availability, certificate validity, and mixed content.

    Returns:
        {https: bool, cert_valid: bool, cert_expiry: str|None, mixed_content: bool, error: str|None}
    """
    result: dict = {
        "https": False,
        "cert_valid": False,
        "cert_expiry": None,
        "mixed_content": False,
        "error": None,
    }

    # TLS socket check
    cert_result = _check_cert_socket(domain)
    result.update(cert_result)

    if not result["https"]:
        return result

    # Fetch homepage over HTTPS and scan for mixed content
    try:
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=15.0,
            headers={"User-Agent": "LyvicaBot/1.0"},
            verify=False,  # We already checked cert above; don't block on bad certs here
        ) as client:
            response = await client.get(f"https://{domain}/")
            html = response.text
            result["mixed_content"] = _detect_mixed_content(html, domain)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not fetch HTTPS homepage for mixed-content check on %s: %s", domain, exc)
        # Don't override error if already set
        if not result["error"]:
            result["error"] = f"Mixed content check failed: {exc}"

    return result
