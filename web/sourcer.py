"""Google Places API sourcing for business websites."""

from __future__ import annotations

import logging
from typing import Optional
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)

_PLACES_URL = "https://places.googleapis.com/v1/places:searchText"
_FIELDS = ",".join([
    "places.id",
    "places.displayName",
    "places.formattedAddress",
    "places.websiteUri",
    "places.rating",
    "places.userRatingCount",
])

# Industries grouped by sector — ordered within each group by typical
# website-staleness (highest yield targets first).
INDUSTRY_GROUPS: list[tuple[str, list[tuple[str, str]]]] = [
    ("🔧 Home Services", [
        ("Plumber",              "plumber"),
        ("HVAC Contractor",      "hvac contractor"),
        ("Electrician",          "electrician"),
        ("Roofer",               "roofing contractor"),
        ("General Contractor",   "general contractor"),
        ("Pest Control",         "pest control company"),
        ("Locksmith",            "locksmith"),
        ("Landscaper",           "landscaping company"),
        ("Painter",              "painting contractor"),
        ("Flooring Company",     "flooring company"),
    ]),
    ("🚗 Automotive", [
        ("Auto Repair Shop",     "auto repair shop"),
        ("Auto Body Shop",       "auto body shop"),
        ("Tire Shop",            "tire shop"),
    ]),
    ("🏥 Health & Wellness", [
        ("Dentist",              "dental clinic"),
        ("Chiropractor",         "chiropractor"),
        ("Optometrist",          "optometrist"),
        ("Physical Therapist",   "physical therapy clinic"),
        ("Veterinarian",         "veterinary clinic"),
    ]),
    ("⚖️ Professional Services", [
        ("Accountant",           "accounting firm"),
        ("Insurance Agent",      "insurance agency"),
        ("Law Firm",             "law firm"),
        ("Real Estate Agency",   "real estate agency"),
    ]),
    ("💅 Personal Services", [
        ("Hair Salon",           "hair salon"),
        ("Barbershop",           "barbershop"),
        ("Nail Salon",           "nail salon"),
        ("Day Spa",              "day spa"),
        ("Gym / Fitness",        "gym fitness center"),
    ]),
    ("🍕 Food & Hospitality", [
        ("Restaurant",           "restaurant"),
        ("Bakery",               "bakery"),
        ("Catering Company",     "catering company"),
    ]),
]

# Flat lookup: display label → search query
INDUSTRY_MAP: dict[str, str] = {
    label: query
    for _, items in INDUSTRY_GROUPS
    for label, query in items
}


def _bare_domain(url: str) -> Optional[str]:
    """Strip scheme/path from a URL and return the bare domain."""
    try:
        netloc = urlparse(url).netloc.lower()
        if netloc.startswith("www."):
            netloc = netloc[4:]
        return netloc or None
    except Exception:
        return None


async def search_businesses(
    city: str,
    industry_query: str,
    api_key: str,
    max_results: int = 40,
) -> list[dict]:
    """
    Query Google Places Text Search (v1) for businesses.

    Returns a list of dicts:
        {name, address, website, domain, rating, reviews, has_website}
    """
    results: list[dict] = []
    page_token: Optional[str] = None
    query = f"{industry_query} in {city}"

    async with httpx.AsyncClient(timeout=15.0) as client:
        while len(results) < max_results:
            payload: dict = {
                "textQuery": query,
                "maxResultCount": min(20, max_results - len(results)),
            }
            if page_token:
                payload["pageToken"] = page_token

            headers = {
                "X-Goog-Api-Key": api_key,
                "X-Goog-FieldMask": _FIELDS,
                "Content-Type": "application/json",
            }

            try:
                resp = await client.post(_PLACES_URL, json=payload, headers=headers)
                resp.raise_for_status()
                data = resp.json()
            except httpx.HTTPStatusError as exc:
                logger.error("Places API error %s: %s", exc.response.status_code, exc.response.text[:300])
                break
            except Exception as exc:
                logger.error("Places API request failed: %s", exc)
                break

            for place in data.get("places", []):
                website = place.get("websiteUri")
                domain = _bare_domain(website) if website else None
                results.append({
                    "name":        place.get("displayName", {}).get("text", ""),
                    "address":     place.get("formattedAddress", ""),
                    "website":     website,
                    "domain":      domain,
                    "rating":      place.get("rating"),
                    "reviews":     place.get("userRatingCount", 0),
                    "has_website": bool(domain),
                })

            page_token = data.get("nextPageToken")
            if not page_token or not data.get("places"):
                break

    return results[:max_results]
