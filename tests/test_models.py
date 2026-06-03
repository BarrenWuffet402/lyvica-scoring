"""Tests for models.py — validation, serialization, sorting."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from lyvica.models import (
    Compliance,
    Config,
    Evidence,
    ICP,
    JobRequest,
    Lead,
    Limits,
    QualifiedLeadList,
    Subscores,
    Summary,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# JobRequest validation
# ---------------------------------------------------------------------------


class TestJobRequestValidator:
    def test_raises_when_both_empty(self):
        """Validator raises if icp.technologies and seed_domains are both empty."""
        with pytest.raises(ValueError, match="seed_domains"):
            JobRequest(
                job_id="test",
                icp=ICP(technologies=[], countries=[], industries=[]),
                seed_domains=[],
            )

    def test_valid_with_technologies(self):
        """Valid when icp.technologies is non-empty."""
        req = JobRequest(
            job_id="test-001",
            icp=ICP(technologies=["wordpress"]),
        )
        assert req.job_id == "test-001"
        assert "wordpress" in req.icp.technologies

    def test_valid_with_seed_domains(self):
        """Valid when seed_domains is non-empty."""
        req = JobRequest(
            job_id="test-002",
            seed_domains=["example.com"],
        )
        assert "example.com" in req.seed_domains

    def test_valid_with_both(self):
        """Valid when both technologies and seed_domains are provided."""
        req = JobRequest(
            job_id="test-003",
            icp=ICP(technologies=["shopify"]),
            seed_domains=["mystore.com"],
        )
        assert req is not None

    def test_default_limits(self):
        """Default limits are applied."""
        req = JobRequest(
            job_id="test-defaults",
            seed_domains=["example.com"],
        )
        assert req.limits.max_candidates == 500
        assert req.limits.min_score_to_include == 60

    def test_default_config(self):
        """Default config values are applied."""
        req = JobRequest(
            job_id="test-config",
            seed_domains=["example.com"],
        )
        assert req.config.concurrency == 8

    def test_fixture_file_loads(self):
        """Sample fixture file parses without errors."""
        fixture = json.loads((FIXTURES_DIR / "sample_job_request.json").read_text())
        req = JobRequest(**fixture)
        assert req.job_id == "test-001"
        assert req.icp.technologies == ["wordpress"]
        assert req.limits.max_candidates == 50
        assert req.config.concurrency == 4


# ---------------------------------------------------------------------------
# QualifiedLeadList.to_json() sorting
# ---------------------------------------------------------------------------


def _make_lead(domain: str, score: float | None, status: str = "qualified") -> Lead:
    """Helper to build a minimal Lead."""
    return Lead(
        domain=domain,
        url=f"https://{domain}",
        rebuild_opportunity_score=score,
        tier="hot" if (score or 0) >= 70 else "warm",
        status=status,
        confidence=0.8,
        subscores=Subscores(),
        evidence=Evidence(),
        pitch_angles=[],
        compliance=Compliance(
            source="seed",
            data_collected_at=datetime.now(tz=timezone.utc),
            robots_respected=True,
        ),
    )


def _make_qll(leads: list[Lead]) -> QualifiedLeadList:
    return QualifiedLeadList(
        job_id="sort-test",
        generated_at=datetime.now(tz=timezone.utc),
        summary=Summary(
            candidates_sourced=len(leads),
            candidates_evaluated=len(leads),
            qualified=len(leads),
            needs_review=0,
            disqualified=0,
            notes="",
        ),
        leads=leads,
    )


class TestQualifiedLeadListSorting:
    def test_to_json_sorts_descending(self):
        """to_json() returns leads sorted by rebuild_opportunity_score descending."""
        leads = [
            _make_lead("low.com", 30.0),
            _make_lead("high.com", 85.0),
            _make_lead("mid.com", 55.0),
        ]
        qll = _make_qll(leads)
        data = qll.to_json()
        scores = [l["rebuild_opportunity_score"] for l in data["leads"]]
        assert scores == sorted(scores, reverse=True)
        assert scores[0] == 85.0
        assert scores[-1] == 30.0

    def test_to_json_none_scores_sorted_last(self):
        """Leads with None score sort after those with scores."""
        leads = [
            _make_lead("none.com", None, status="disqualified"),
            _make_lead("high.com", 80.0),
            _make_lead("mid.com", 40.0),
        ]
        qll = _make_qll(leads)
        data = qll.to_json()
        scores = [l["rebuild_opportunity_score"] for l in data["leads"]]
        # None treated as 0 for sorting → last
        assert scores[0] == 80.0
        assert scores[1] == 40.0
        assert scores[2] is None

    def test_to_json_single_lead(self):
        """to_json() works with a single lead."""
        leads = [_make_lead("only.com", 72.0)]
        qll = _make_qll(leads)
        data = qll.to_json()
        assert len(data["leads"]) == 1
        assert data["leads"][0]["rebuild_opportunity_score"] == 72.0

    def test_to_json_empty_leads(self):
        """to_json() works with no leads."""
        qll = _make_qll([])
        data = qll.to_json()
        assert data["leads"] == []

    def test_to_json_includes_job_id(self):
        """to_json() preserves job_id."""
        qll = _make_qll([])
        data = qll.to_json()
        assert data["job_id"] == "sort-test"

    def test_to_json_is_json_serialisable(self):
        """Output of to_json() can be passed to json.dumps without error."""
        leads = [_make_lead("a.com", 75.0), _make_lead("b.com", 45.0)]
        qll = _make_qll(leads)
        data = qll.to_json()
        serialised = json.dumps(data)
        assert isinstance(serialised, str)

    def test_to_json_stable_sort_equal_scores(self):
        """Leads with equal scores are included (no crash or dedup)."""
        leads = [
            _make_lead("first.com", 60.0),
            _make_lead("second.com", 60.0),
        ]
        qll = _make_qll(leads)
        data = qll.to_json()
        assert len(data["leads"]) == 2

    def test_model_does_not_sort_in_place(self):
        """
        The original QualifiedLeadList.leads are not mutated by to_json().
        """
        leads = [
            _make_lead("low.com", 20.0),
            _make_lead("high.com", 90.0),
        ]
        qll = _make_qll(leads)
        original_order = [l.domain for l in qll.leads]
        qll.to_json()
        # Model's own leads list should remain unchanged
        assert [l.domain for l in qll.leads] == original_order
