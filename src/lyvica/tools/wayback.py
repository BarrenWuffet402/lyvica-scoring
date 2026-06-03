"""Wayback Machine CDX API wrapper for detecting last significant page change."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import httpx

logger = logging.getLogger(__name__)

_CDX_URL = "http://web.archive.org/cdx/search/cdx"


async def get_last_change(domain: str) -> dict:
    """
    Query the Wayback Machine CDX API for the most recent snapshot of *domain*.

    Returns:
        {last_significant_change: str|None (ISO-8601), error: str|None}
    """
    result: dict = {
        "last_significant_change": None,
        "error": None,
    }

    today = datetime.now(tz=timezone.utc).strftime("%Y%m%d")

    params = {
        "url": domain,
        "output": "json",
        "limit": "1",
        "fl": "timestamp,statuscode",
        "filter": "statuscode:200",
        "from": "20150101",
        "to": today,
        "collapse": "digest",
        "fastLatest": "true",
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(_CDX_URL, params=params)
            response.raise_for_status()
            data = response.json()
    except httpx.HTTPStatusError as exc:
        result["error"] = f"HTTP {exc.response.status_code}: {exc.response.text[:200]}"
        logger.error("Wayback CDX error for %s: %s", domain, result["error"])
        return result
    except httpx.TimeoutException as exc:
        result["error"] = f"Timeout: {exc}"
        logger.warning("Wayback CDX timeout for %s", domain)
        return result
    except httpx.RequestError as exc:
        result["error"] = f"Request error: {exc}"
        logger.error("Wayback CDX request error for %s: %s", domain, exc)
        return result
    except Exception as exc:  # noqa: BLE001
        result["error"] = f"Unexpected error: {exc}"
        logger.exception("Unexpected Wayback CDX error for %s", domain)
        return result

    # Response format: [["timestamp", "statuscode"], ["20231015120000", "200"], ...]
    # First row is the header row.
    try:
        rows = data if isinstance(data, list) else []
        # Skip header row if present
        data_rows = [r for r in rows if r and r[0] != "timestamp"]
        if not data_rows:
            logger.debug("No Wayback snapshots found for %s", domain)
            return result

        # fastLatest returns the most recent match
        timestamp_str = data_rows[-1][0]
        # Parse YYYYMMDDHHMMSS
        dt = datetime.strptime(timestamp_str, "%Y%m%d%H%M%S").replace(
            tzinfo=timezone.utc
        )
        result["last_significant_change"] = dt.isoformat()
    except (IndexError, ValueError, TypeError) as exc:
        logger.warning("Could not parse Wayback CDX response for %s: %s", domain, exc)

    return result
