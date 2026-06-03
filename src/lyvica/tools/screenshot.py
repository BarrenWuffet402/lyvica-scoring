"""Playwright headless screenshot capture."""

from __future__ import annotations

import logging
import os
import re
import time
from pathlib import Path

logger = logging.getLogger(__name__)

_SLUG_RE = re.compile(r"[^a-zA-Z0-9_-]")


def _domain_slug(url: str) -> str:
    """Convert a URL/domain to a filesystem-safe slug."""
    slug = url.replace("https://", "").replace("http://", "").rstrip("/")
    return _SLUG_RE.sub("_", slug)[:80]


async def capture_screenshot(
    url: str,
    output_dir: str = "/tmp/lyvica_screenshots",
) -> dict:
    """
    Capture a full-page screenshot of *url* using Playwright (headless Chromium).

    Returns:
        {path: str|None, error: str|None}
    """
    result: dict = {"path": None, "error": None}

    # Attempt to import playwright — it's optional
    try:
        from playwright.async_api import async_playwright  # type: ignore[import]
    except ImportError:
        result["error"] = "playwright is not installed — screenshot skipped"
        logger.debug("playwright not installed; skipping screenshot for %s", url)
        return result

    # Ensure output directory exists
    try:
        Path(output_dir).mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        result["error"] = f"Cannot create screenshot dir {output_dir!r}: {exc}"
        return result

    slug = _domain_slug(url)
    timestamp = int(time.time())
    filename = f"{slug}_{timestamp}.png"
    output_path = os.path.join(output_dir, filename)

    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            context = await browser.new_context(
                viewport={"width": 1280, "height": 800},
                user_agent="LyvicaBot/1.0 (+https://lyvica.com/bot)",
            )
            page = await context.new_page()
            try:
                await page.goto(url, wait_until="networkidle", timeout=30_000)
            except Exception:  # noqa: BLE001
                # Fall back to domcontentloaded if networkidle times out
                try:
                    await page.goto(url, wait_until="domcontentloaded", timeout=20_000)
                except Exception as inner_exc:
                    raise inner_exc from None

            await page.screenshot(path=output_path, full_page=False)
            await browser.close()

        result["path"] = output_path
        logger.info("Screenshot saved: %s", output_path)
    except Exception as exc:  # noqa: BLE001
        result["error"] = f"Screenshot failed for {url}: {exc}"
        logger.warning("Screenshot failed for %s: %s", url, exc)

    return result
