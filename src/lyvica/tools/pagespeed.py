"""Google PageSpeed Insights API v5 (Lighthouse) wrapper."""

from __future__ import annotations

import logging

import httpx

from ..config import settings

logger = logging.getLogger(__name__)

_PSI_URL = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"


async def run_psi(url: str) -> dict:
    """
    Run PageSpeed Insights for *url* using mobile strategy.

    Returns:
        {
            mobile_score: float|None,       # accessibility score * 100
            performance_score: float|None,   # performance score * 100
            fcp: float|None,                 # First Contentful Paint (ms)
            lcp: float|None,                 # Largest Contentful Paint (ms)
            cls: float|None,                 # Cumulative Layout Shift (raw)
            tbt: float|None,                 # Total Blocking Time (ms)
            error: str|None,
        }
    """
    result: dict = {
        "has_viewport": None,       # bool — viewport meta tag present (mobile-friendliness)
        "performance_score": None,
        "fcp": None,
        "lcp": None,
        "cls": None,
        "tbt": None,
        "error": None,
    }

    params: dict = {"url": url, "strategy": "mobile"}
    api_key = settings.pagespeed_api_key
    if api_key:
        params["key"] = api_key

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.get(_PSI_URL, params=params)
            response.raise_for_status()
            data = response.json()
    except httpx.HTTPStatusError as exc:
        result["error"] = f"HTTP {exc.response.status_code}: {exc.response.text[:300]}"
        logger.error("PSI API error for %s: %s", url, result["error"])
        return result
    except httpx.TimeoutException as exc:
        result["error"] = f"Timeout: {exc}"
        logger.warning("PSI API timeout for %s", url)
        return result
    except httpx.RequestError as exc:
        result["error"] = f"Request error: {exc}"
        logger.error("PSI API request error for %s: %s", url, exc)
        return result
    except Exception as exc:  # noqa: BLE001
        result["error"] = f"Unexpected error: {exc}"
        logger.exception("Unexpected PSI error for %s", url)
        return result

    try:
        lighthouse = data.get("lighthouseResult", {})
        categories = lighthouse.get("categories", {})
        audits = lighthouse.get("audits", {})

        perf = categories.get("performance", {}).get("score")
        if perf is not None:
            result["performance_score"] = round(perf * 100)

        # viewport audit: score 1 = has viewport meta tag, 0 = missing
        viewport_score = audits.get("viewport", {}).get("score")
        if viewport_score is not None:
            result["has_viewport"] = viewport_score == 1

        # Core Web Vitals from audits
        def _ms(audit_key: str) -> float | None:
            audit = audits.get(audit_key, {})
            numeric = audit.get("numericValue")
            return round(numeric, 2) if numeric is not None else None

        result["fcp"] = _ms("first-contentful-paint")
        result["lcp"] = _ms("largest-contentful-paint")
        result["tbt"] = _ms("total-blocking-time")

        cls_val = audits.get("cumulative-layout-shift", {}).get("numericValue")
        result["cls"] = round(cls_val, 4) if cls_val is not None else None

    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not parse PSI response for %s: %s", url, exc)

    return result
