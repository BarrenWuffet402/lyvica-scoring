"""Vision model call via OpenAI-compatible gateway for visual datedness scoring."""

from __future__ import annotations

import base64
import json
import logging
import re
from pathlib import Path
from typing import Optional

from ..config import settings

logger = logging.getLogger(__name__)

_VISION_PROMPT = (
    "Rate the visual datedness of this website homepage on a scale of 0 to 100, "
    "where 0 means completely modern/current design and 100 means extremely outdated. "
    "Consider: design trends, typography, layout, color usage, imagery style, UI patterns. "
    'Respond with ONLY a JSON object: {"score": <integer 0-100>, "reasoning": "<one sentence>"}.'
)


async def rate_visual_datedness(
    image_path: str,
    model: Optional[str] = None,
) -> dict:
    """
    Send a screenshot to a vision model and get a visual-datedness score.

    Returns:
        {score: int|None, reasoning: str|None, error: str|None}
    """
    result: dict = {"score": None, "reasoning": None, "error": None}

    # Check that a gateway API key is configured — vision is optional
    api_key = settings.gateway_api_key
    if not api_key:
        result["error"] = "GATEWAY_API_KEY not set — vision step skipped"
        logger.debug("GATEWAY_API_KEY not configured; skipping vision for %s", image_path)
        return result

    # Attempt to import openai SDK — it's listed as a required dep but guard anyway
    try:
        from openai import AsyncOpenAI  # type: ignore[import]
    except ImportError:
        result["error"] = "openai SDK not installed"
        logger.error("openai SDK not installed; cannot perform vision call")
        return result

    # Read and base64-encode the image
    try:
        image_data = Path(image_path).read_bytes()
        b64_image = base64.standard_b64encode(image_data).decode("utf-8")
    except FileNotFoundError:
        result["error"] = f"Image file not found: {image_path}"
        return result
    except OSError as exc:
        result["error"] = f"Cannot read image {image_path}: {exc}"
        return result

    vision_model = model or settings.vision_model
    base_url = settings.gateway_base_url

    client = AsyncOpenAI(api_key=api_key, base_url=base_url)

    try:
        response = await client.chat.completions.create(
            model=vision_model,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/png;base64,{b64_image}",
                                "detail": "low",
                            },
                        },
                        {"type": "text", "text": _VISION_PROMPT},
                    ],
                }
            ],
            max_tokens=150,
        )
        raw_text = response.choices[0].message.content or ""
    except Exception as exc:  # noqa: BLE001
        result["error"] = f"Vision API call failed: {exc}"
        logger.error("Vision API error for %s: %s", image_path, exc)
        return result

    # Parse JSON from response, handling markdown code fences
    json_str = raw_text.strip()
    # Strip ```json ... ``` fences if present
    json_str = re.sub(r"^```(?:json)?\s*", "", json_str, flags=re.MULTILINE)
    json_str = re.sub(r"\s*```$", "", json_str, flags=re.MULTILINE)

    try:
        parsed = json.loads(json_str)
        score = parsed.get("score")
        reasoning = parsed.get("reasoning")
        if score is not None:
            result["score"] = int(score)
        result["reasoning"] = reasoning
    except (json.JSONDecodeError, ValueError, TypeError) as exc:
        result["error"] = f"Could not parse vision response JSON: {exc}. Raw: {raw_text[:200]}"
        logger.warning("Vision response parse error for %s: %s", image_path, exc)

    return result
