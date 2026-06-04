"""Pydantic v2 models for Lyvica scoring agent."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, field_validator, model_validator


# ---------------------------------------------------------------------------
# Job request models
# ---------------------------------------------------------------------------


class CompanySize(BaseModel):
    min_employees: Optional[int] = None
    max_employees: Optional[int] = None


class ICP(BaseModel):
    technologies: list[str] = []
    countries: list[str] = []
    industries: list[str] = []
    company_size: Optional[CompanySize] = None
    traffic_tier: Optional[str] = None


class Limits(BaseModel):
    max_candidates: int = 500
    min_score_to_include: int = 60


class Config(BaseModel):
    model: Optional[str] = None
    vision_model: Optional[str] = None
    cost_ceiling_usd: Optional[float] = None
    concurrency: int = 8


class JobRequest(BaseModel):
    job_id: str
    icp: ICP = ICP()
    seed_domains: list[str] = []
    limits: Limits = Limits()
    config: Config = Config()

    @model_validator(mode="after")
    def require_technologies_or_seed_domains(self) -> "JobRequest":
        if not self.icp.technologies and not self.seed_domains:
            raise ValueError(
                "At least one of icp.technologies or seed_domains must be non-empty."
            )
        return self


# ---------------------------------------------------------------------------
# Lead / output models
# ---------------------------------------------------------------------------


class Subscores(BaseModel):
    mobile: Optional[float] = None
    visual_datedness: Optional[float] = None
    tech_obsolescence: Optional[float] = None
    performance: Optional[float] = None
    security: Optional[float] = None
    content_freshness: Optional[float] = None
    seo_hygiene: Optional[float] = None


class Evidence(BaseModel):
    detected_technologies: Optional[list[str]] = None
    cms: Optional[str] = None
    cms_version: Optional[str] = None
    https: Optional[bool] = None
    mixed_content: Optional[bool] = None
    viewport_meta: Optional[bool] = None
    pagespeed_mobile: Optional[float] = None
    pagespeed_performance: Optional[float] = None
    last_significant_change: Optional[str] = None
    footer_copyright_year: Optional[int] = None
    open_graph: Optional[bool] = None
    schema_org: Optional[bool] = None
    screenshot_ref: Optional[str] = None


class Contact(BaseModel):
    name: Optional[str] = None
    role: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    source: Optional[str] = None


class Compliance(BaseModel):
    source: Literal["builtwith", "seed"]
    data_collected_at: datetime
    robots_respected: bool


class Lead(BaseModel):
    domain: str
    url: str
    company_name: Optional[str] = None
    rebuild_opportunity_score: Optional[float] = None
    tier: Optional[Literal["hot", "warm", "cold"]] = None
    status: Literal["qualified", "needs_review", "disqualified"]
    confidence: float
    subscores: Subscores
    evidence: Evidence
    pitch_angles: list[str]
    summary: str = ""
    contact: Optional[Contact] = None
    compliance: Compliance


class Summary(BaseModel):
    candidates_sourced: int
    candidates_evaluated: int
    qualified: int
    needs_review: int
    disqualified: int
    notes: str


class QualifiedLeadList(BaseModel):
    job_id: str
    generated_at: datetime
    summary: Summary
    leads: list[Lead]

    def to_json(self) -> dict:
        """Serialize to dict with leads sorted by rebuild_opportunity_score descending."""
        data = self.model_dump(mode="json")
        data["leads"] = sorted(
            data["leads"],
            key=lambda lead: lead.get("rebuild_opportunity_score") or 0,
            reverse=True,
        )
        return data
