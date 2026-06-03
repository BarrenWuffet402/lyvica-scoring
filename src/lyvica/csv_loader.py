"""Load a domain list from a CSV file and build a JobRequest."""

from __future__ import annotations

import csv
import logging
import re
import uuid
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from .models import JobRequest

logger = logging.getLogger(__name__)

# Column names we'll accept (checked case-insensitively, first match wins)
_DOMAIN_COLUMNS = ("domain", "url", "website", "site", "homepage", "link")


def _extract_domain(raw: str) -> Optional[str]:
    """Return a bare domain from a URL or domain string. Returns None if unparseable."""
    raw = raw.strip()
    if not raw:
        return None
    if not raw.startswith(("http://", "https://")):
        raw = f"https://{raw}"
    try:
        netloc = urlparse(raw).netloc.lower()
    except Exception:
        return None
    # Strip leading www.
    netloc = re.sub(r"^www\.", "", netloc)
    return netloc or None


def load_domains_from_csv(path: str, column: Optional[str] = None) -> list[str]:
    """
    Read a CSV file and return a deduplicated list of bare domain strings.

    Column selection order:
    1. *column* argument if supplied
    2. First header that matches one of: domain, url, website, site, homepage, link
    3. The first column if no match is found
    """
    file = Path(path)
    if not file.exists():
        raise FileNotFoundError(f"CSV file not found: {path}")

    domains: list[str] = []
    seen: set[str] = set()

    with file.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)

        if reader.fieldnames is None:
            # No headers — treat as single-column file
            fh.seek(0)
            plain_reader = csv.reader(fh)
            for row in plain_reader:
                if not row:
                    continue
                d = _extract_domain(row[0])
                if d and d not in seen:
                    seen.add(d)
                    domains.append(d)
            return domains

        # Resolve the target column name
        headers_lower = {h.lower(): h for h in reader.fieldnames}
        target_col: Optional[str] = None

        if column:
            target_col = headers_lower.get(column.lower())
            if target_col is None:
                raise ValueError(
                    f"Column '{column}' not found in CSV. "
                    f"Available columns: {list(reader.fieldnames)}"
                )
        else:
            for candidate in _DOMAIN_COLUMNS:
                if candidate in headers_lower:
                    target_col = headers_lower[candidate]
                    break
            if target_col is None:
                # Fall back to the first column
                target_col = reader.fieldnames[0]
                logger.warning(
                    "No recognized domain column found; using first column '%s'",
                    target_col,
                )

        logger.info("Reading domains from column '%s' in %s", target_col, path)

        for row in reader:
            raw = row.get(target_col, "").strip()
            d = _extract_domain(raw)
            if d and d not in seen:
                seen.add(d)
                domains.append(d)

    logger.info("Loaded %d unique domains from %s", len(domains), path)
    return domains


def job_from_csv(
    path: str,
    job_id: Optional[str] = None,
    column: Optional[str] = None,
    max_candidates: int = 500,
    min_score_to_include: int = 60,
    concurrency: int = 8,
) -> JobRequest:
    """
    Build a JobRequest from a CSV file of domains.

    All domains are loaded as seed_domains; no BuiltWith sourcing is used.

    Args:
        path: Path to the CSV file.
        job_id: Optional job identifier; a UUID is generated if omitted.
        column: Column name containing the domain/URL values.
        max_candidates: Cap on how many domains to evaluate.
        min_score_to_include: Minimum rebuild_opportunity_score to include a lead.
        concurrency: Number of domains to evaluate in parallel.
    """
    domains = load_domains_from_csv(path, column=column)
    if not domains:
        raise ValueError(f"No domains found in {path}")

    return JobRequest.model_validate({
        "job_id": job_id or str(uuid.uuid4()),
        "icp": {"technologies": []},
        "seed_domains": domains,
        "limits": {
            "max_candidates": max_candidates,
            "min_score_to_include": min_score_to_include,
        },
        "config": {"concurrency": concurrency},
    })
