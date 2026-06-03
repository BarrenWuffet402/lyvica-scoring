"""Main async orchestrator for Lyvica scoring agent."""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlparse

from .config import settings
from .models import (
    Compliance,
    Evidence,
    JobRequest,
    Lead,
    QualifiedLeadList,
    Subscores,
    Summary,
)
from .scorer import (
    compute_content_freshness,
    compute_seo_hygiene,
    compute_tech_obsolescence,
    score_lead,
)
from .tools.builtwith import domain_lookup, list_domains
from .tools.http_fetch import check_robots, fetch_page, parse_html_signals
from .tools.pagespeed import run_psi
from .tools.screenshot import capture_screenshot
from .tools.ssl_check import check_ssl
from .tools.vision import rate_visual_datedness
from .tools.wayback import get_last_change

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Pitch angle rules (deterministic, signal-based)
# ---------------------------------------------------------------------------

_PITCH_RULES: list[tuple[str, object]] = [
    # (message, condition_fn)  — condition_fn receives (score_value: float|None)
]


def _pitch(score: Optional[float], threshold: float, msg: str) -> Optional[str]:
    if score is not None and score > threshold:
        return msg
    return None


def _generate_pitch_angles(
    evidence: Evidence,
    subscores: Subscores,
) -> list[str]:
    """
    Generate deterministic, rule-based pitch angles from scored signals.

    These are never LLM-generated.
    """
    angles: list[str] = []

    angle = _pitch(
        subscores.mobile,
        70,
        "Not mobile-responsive — losing mobile search traffic under Google's mobile-first indexing",
    )
    if angle:
        angles.append(angle)

    # Security: score of 100 means no HTTPS at all
    if subscores.security is not None and subscores.security >= 100:
        angles.append(
            "No HTTPS — browsers display security warnings that damage trust and conversions"
        )

    angle = _pitch(
        subscores.visual_datedness,
        70,
        "Visually dated design signals an untrustworthy or inactive business to visitors",
    )
    if angle:
        angles.append(angle)

    angle = _pitch(
        subscores.performance,
        70,
        "Slow page load speed — every 1-second delay reduces conversions by ~7%",
    )
    if angle:
        angles.append(angle)

    angle = _pitch(
        subscores.tech_obsolescence,
        80,
        "Built on obsolete technology that is a security risk and maintenance burden",
    )
    if angle:
        angles.append(angle)

    angle = _pitch(
        subscores.content_freshness,
        70,
        "Content appears stale — last significant update over 3 years ago",
    )
    if angle:
        angles.append(angle)

    angle = _pitch(
        subscores.seo_hygiene,
        66,
        "Missing critical SEO metadata — invisible to search engines for key queries",
    )
    if angle:
        angles.append(angle)

    return angles


# ---------------------------------------------------------------------------
# Domain normalization helpers
# ---------------------------------------------------------------------------


def _to_domain(raw: str) -> str:
    """Strip scheme and path from a URL/domain string, return bare domain."""
    raw = raw.strip()
    if not raw.startswith(("http://", "https://")):
        raw = f"https://{raw}"
    parsed = urlparse(raw)
    return parsed.netloc.lower().lstrip("www.") or raw


def _to_url(domain: str) -> str:
    """Build a canonical https URL for a domain."""
    if domain.startswith(("http://", "https://")):
        return domain
    return f"https://{domain}"


# ---------------------------------------------------------------------------
# Score converters from PSI / tool outputs
# ---------------------------------------------------------------------------


def _psi_to_mobile_subscore(mobile_score: Optional[float]) -> Optional[float]:
    """
    Convert PSI accessibility score (0–100) to a mobile-friendliness *problem* score (0–100).
    Low accessibility score → high problem score.
    """
    if mobile_score is None:
        return None
    return round(100.0 - mobile_score, 2)


def _psi_to_performance_subscore(perf_score: Optional[float]) -> Optional[float]:
    """Convert PSI performance score to a performance-problem score (inverted)."""
    if perf_score is None:
        return None
    return round(100.0 - perf_score, 2)


def _ssl_to_security_subscore(
    https: bool,
    cert_valid: bool,
    mixed_content: bool,
) -> float:
    """Map SSL/TLS signals to a security-problem score (0–100)."""
    if not https:
        return 100.0
    if not cert_valid:
        return 80.0
    if mixed_content:
        return 40.0
    return 0.0


# ---------------------------------------------------------------------------
# Main agent class
# ---------------------------------------------------------------------------


class LyvicaAgent:
    """Orchestrates the full Lyvica scoring pipeline for a JobRequest."""

    def __init__(self, runtime_config: dict | None = None) -> None:
        self._runtime_config = runtime_config or {}
        # Allow runtime override of screenshot dir
        self._screenshot_dir = self._runtime_config.get(
            "screenshot_dir", settings.screenshot_dir
        )
        log_level = self._runtime_config.get("log_level", settings.log_level)
        logging.basicConfig(
            level=getattr(logging, log_level.upper(), logging.INFO),
            format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        )

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def run_job(self, job_request: JobRequest) -> QualifiedLeadList:
        """Execute the full scoring pipeline and return a QualifiedLeadList."""
        logger.info("Starting job %s", job_request.job_id)
        started_at = datetime.now(tz=timezone.utc)

        # 1. Source candidates
        candidates = await self._source_candidates(job_request)
        candidates_sourced = len(candidates)
        logger.info("Sourced %d candidates for job %s", candidates_sourced, job_request.job_id)

        # Respect max_candidates limit
        candidates = candidates[: job_request.limits.max_candidates]
        candidates_evaluated = len(candidates)

        # 2. Evaluate domains in parallel with concurrency control
        sem = asyncio.Semaphore(job_request.config.concurrency)

        async def _bounded_evaluate(domain: str) -> Lead:
            async with sem:
                return await self._evaluate_domain(domain, job_request)

        leads: list[Lead] = list(
            await asyncio.gather(
                *[_bounded_evaluate(d) for d in candidates],
                return_exceptions=False,
            )
        )

        # 3. Filter by minimum score
        min_score = job_request.limits.min_score_to_include
        included_leads: list[Lead] = []
        for lead in leads:
            score = lead.rebuild_opportunity_score
            if score is None or score < min_score:
                if lead.status != "disqualified":
                    lead.status = "disqualified"
            included_leads.append(lead)

        # 4. Build summary
        qualified = sum(1 for l in included_leads if l.status == "qualified")
        needs_review = sum(1 for l in included_leads if l.status == "needs_review")
        disqualified = sum(1 for l in included_leads if l.status == "disqualified")

        summary = Summary(
            candidates_sourced=candidates_sourced,
            candidates_evaluated=candidates_evaluated,
            qualified=qualified,
            needs_review=needs_review,
            disqualified=disqualified,
            notes=(
                f"Job completed at {datetime.now(tz=timezone.utc).isoformat()}. "
                f"Concurrency={job_request.config.concurrency}."
            ),
        )

        result = QualifiedLeadList(
            job_id=job_request.job_id,
            generated_at=started_at,
            summary=summary,
            leads=sorted(
                included_leads,
                key=lambda l: l.rebuild_opportunity_score or 0,
                reverse=True,
            ),
        )
        logger.info(
            "Job %s complete: %d qualified, %d needs_review, %d disqualified",
            job_request.job_id,
            qualified,
            needs_review,
            disqualified,
        )
        return result

    # ------------------------------------------------------------------
    # Sourcing
    # ------------------------------------------------------------------

    async def _source_candidates(self, job: JobRequest) -> list[str]:
        """Combine BuiltWith-sourced domains with seed domains, deduped."""
        domains: list[str] = []

        # BuiltWith sourcing
        if job.icp.technologies:
            bw_domains = await list_domains(
                technologies=job.icp.technologies,
                countries=job.icp.countries,
                industries=job.icp.industries,
                limit=job.limits.max_candidates,
            )
            domains.extend(bw_domains)

        # Seed domains
        for raw in job.seed_domains:
            domains.append(_to_domain(raw))

        # Deduplicate preserving order
        seen: set[str] = set()
        unique: list[str] = []
        for d in domains:
            key = d.lower()
            if key not in seen:
                seen.add(key)
                unique.append(d)

        return unique

    # ------------------------------------------------------------------
    # Per-domain evaluation
    # ------------------------------------------------------------------

    async def _evaluate_domain(self, domain: str, job: JobRequest) -> Lead:
        """
        Run the full evaluation pipeline for a single domain.

        Any unhandled exception results in a disqualified lead rather than
        crashing the entire job.
        """
        # Determine sourcing origin for compliance
        is_seed = domain in [_to_domain(s) for s in job.seed_domains]
        source_label = "seed" if is_seed else "builtwith"

        try:
            return await self._evaluate_domain_inner(domain, source_label, job)
        except Exception as exc:  # noqa: BLE001
            logger.error("Unhandled error evaluating domain %s: %s", domain, exc, exc_info=True)
            return self._disqualified_lead(
                domain=domain,
                source=source_label,
                reason=f"Evaluation failed: {exc}",
            )

    async def _evaluate_domain_inner(
        self,
        domain: str,
        source: str,
        job: JobRequest,
    ) -> Lead:
        url = _to_url(domain)
        now = datetime.now(tz=timezone.utc)

        # ── Step 1: robots.txt ─────────────────────────────────────────
        robots_allowed = await check_robots(domain)
        if not robots_allowed:
            logger.info("robots.txt disallows scraping %s — skipping", domain)
            return self._disqualified_lead(
                domain=domain,
                source=source,
                reason="robots.txt disallows crawling",
                robots_respected=True,
            )

        # ── Step 2: Parallel tool calls ────────────────────────────────
        (
            page_result,
            psi_result,
            ssl_result,
            wayback_result,
        ) = await asyncio.gather(
            fetch_page(url),
            run_psi(url),
            check_ssl(domain),
            get_last_change(domain),
            return_exceptions=True,
        )

        # Treat any exception from gather as an empty result with error
        def _safe(result: object, default: dict) -> dict:
            if isinstance(result, Exception):
                default["error"] = str(result)
                return default
            return result  # type: ignore[return-value]

        page_result = _safe(page_result, {"html": "", "final_url": url, "error": None, "status_code": None, "headers": {}})
        psi_result = _safe(psi_result, {"mobile_score": None, "performance_score": None, "fcp": None, "lcp": None, "cls": None, "tbt": None, "error": None})
        ssl_result = _safe(ssl_result, {"https": False, "cert_valid": False, "cert_expiry": None, "mixed_content": False, "error": None})
        wayback_result = _safe(wayback_result, {"last_significant_change": None, "error": None})

        # ── Step 3: Parse HTML signals ─────────────────────────────────
        html = page_result.get("html", "") or ""
        final_url = page_result.get("final_url", url) or url
        html_signals = parse_html_signals(html, final_url) if html else {}

        # ── Step 4: Technology detection ───────────────────────────────
        # Uses BuiltWith API when key is set; free HTML detector otherwise.
        # Pre-fetched html/headers are passed in to avoid a second request.
        bw_result: dict = {"technologies": [], "cms": None, "cms_version": None, "error": None}
        try:
            bw_result = await domain_lookup(
                domain,
                html=html,
                headers=page_result.get("headers") or {},
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Technology lookup failed for %s: %s", domain, exc)

        # ── Step 5: Screenshot (optional) ─────────────────────────────
        screenshot_result: dict = {"path": None, "error": None}
        try:
            screenshot_result = await capture_screenshot(
                final_url, output_dir=self._screenshot_dir
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("Screenshot skipped for %s: %s", domain, exc)

        # ── Step 6: Vision (optional — only if screenshot succeeded) ───
        visual_score: Optional[float] = None
        vision_model = job.config.vision_model  # may be None → falls back to settings
        if screenshot_result.get("path") and settings.gateway_api_key:
            try:
                vision_result = await rate_visual_datedness(
                    screenshot_result["path"],
                    model=vision_model,
                )
                if vision_result.get("score") is not None:
                    visual_score = float(vision_result["score"])
            except Exception as exc:  # noqa: BLE001
                logger.debug("Vision call skipped for %s: %s", domain, exc)

        # ── Step 7: Assemble Evidence ──────────────────────────────────
        last_change_iso: Optional[str] = wayback_result.get("last_significant_change")
        footer_year: Optional[int] = html_signals.get("footer_copyright_year")

        evidence = Evidence(
            detected_technologies=bw_result.get("technologies") or [],
            cms=bw_result.get("cms"),
            cms_version=bw_result.get("cms_version"),
            https=ssl_result.get("https"),
            mixed_content=ssl_result.get("mixed_content"),
            viewport_meta=html_signals.get("viewport_meta"),
            pagespeed_mobile=psi_result.get("mobile_score"),
            pagespeed_performance=psi_result.get("performance_score"),
            last_significant_change=last_change_iso,
            footer_copyright_year=footer_year,
            open_graph=html_signals.get("open_graph"),
            schema_org=html_signals.get("schema_org"),
            screenshot_ref=screenshot_result.get("path"),
        )

        # ── Step 8: Compute Subscores ──────────────────────────────────
        # Parse last_change datetime for content_freshness
        last_change_dt: Optional[datetime] = None
        if last_change_iso:
            try:
                last_change_dt = datetime.fromisoformat(last_change_iso)
            except ValueError:
                pass

        tech_obs = compute_tech_obsolescence(
            technologies=bw_result.get("technologies") or [],
            cms=bw_result.get("cms"),
            cms_version=bw_result.get("cms_version"),
        )

        content_fresh = compute_content_freshness(
            last_change=last_change_dt,
            footer_year=footer_year,
        )

        seo = compute_seo_hygiene(
            open_graph=html_signals.get("open_graph"),
            meta_description=html_signals.get("meta_description"),
            schema_org=html_signals.get("schema_org"),
        )

        subscores = Subscores(
            mobile=_psi_to_mobile_subscore(psi_result.get("mobile_score")),
            visual_datedness=visual_score,
            tech_obsolescence=tech_obs if tech_obs > 0 else None,
            performance=_psi_to_performance_subscore(psi_result.get("performance_score")),
            security=_ssl_to_security_subscore(
                https=ssl_result.get("https", False),
                cert_valid=ssl_result.get("cert_valid", False),
                mixed_content=ssl_result.get("mixed_content", False),
            ),
            content_freshness=content_fresh if content_fresh > 0 else None,
            seo_hygiene=seo if seo > 0 else None,
        )

        # ── Step 9: Score and tier ─────────────────────────────────────
        score, tier, confidence = score_lead(subscores)

        # ── Step 10: Pitch angles ──────────────────────────────────────
        pitch_angles = _generate_pitch_angles(evidence, subscores)

        # ── Step 11: Determine status ──────────────────────────────────
        # If we have no HTML and no PSI data, flag for review
        has_data = bool(html or psi_result.get("performance_score") is not None)
        if not has_data:
            status = "needs_review"
        elif confidence >= 0.7:
            status = "qualified"
        else:
            status = "needs_review"

        compliance = Compliance(
            source=source,  # type: ignore[arg-type]
            data_collected_at=now,
            robots_respected=True,
        )

        # Try to extract company name from HTML title
        company_name = _extract_company_name(html, domain)

        return Lead(
            domain=domain,
            url=final_url,
            company_name=company_name,
            rebuild_opportunity_score=score,
            tier=tier,
            status=status,
            confidence=confidence,
            subscores=subscores,
            evidence=evidence,
            pitch_angles=pitch_angles,
            contact=None,
            compliance=compliance,
        )

    # ------------------------------------------------------------------
    # Pitch angles — kept here for context, logic moved to module level
    # ------------------------------------------------------------------

    async def _generate_pitch_angles(
        self,
        domain: str,
        evidence: Evidence,
        subscores: Subscores,
    ) -> list[str]:
        """Rule-based pitch angles (no LLM)."""
        return _generate_pitch_angles(evidence, subscores)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _disqualified_lead(
        self,
        domain: str,
        source: str,
        reason: str,
        robots_respected: bool = False,
    ) -> Lead:
        """Build a disqualified Lead with minimal data."""
        now = datetime.now(tz=timezone.utc)
        return Lead(
            domain=domain,
            url=_to_url(domain),
            company_name=None,
            rebuild_opportunity_score=None,
            tier=None,
            status="disqualified",
            confidence=0.0,
            subscores=Subscores(),
            evidence=Evidence(),
            pitch_angles=[],
            contact=None,
            compliance=Compliance(
                source=source,  # type: ignore[arg-type]
                data_collected_at=now,
                robots_respected=robots_respected,
            ),
        )


def _extract_company_name(html: str, domain: str) -> Optional[str]:
    """Best-effort company name extraction from <title> tag."""
    if not html:
        return None
    match = re.search(r"<title[^>]*>([^<]{1,120})</title>", html, re.IGNORECASE)
    if match:
        title = match.group(1).strip()
        # Strip common suffixes like " | Home" or " - Official Site"
        title = re.sub(r"\s*[\|–—-]\s*.*$", "", title).strip()
        if title:
            return title[:120]
    # Fallback: capitalise domain root
    root = domain.split(".")[0].replace("-", " ").title()
    return root or None
