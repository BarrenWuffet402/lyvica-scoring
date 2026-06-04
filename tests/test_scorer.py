"""Tests for scorer.py — scoring logic, tier thresholds, content freshness, SEO hygiene."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from lyvica.models import Subscores
from lyvica.scorer import (
    compute_content_freshness,
    compute_seo_hygiene,
    compute_tech_obsolescence,
    score_lead,
)


# ---------------------------------------------------------------------------
# score_lead — basic cases
# ---------------------------------------------------------------------------


class TestScoreLead:
    def test_all_subscores_present_hot(self):
        """All subscores at maximum → hot tier, high confidence."""
        subscores = Subscores(
            mobile=100.0,
            visual_datedness=100.0,
            tech_obsolescence=100.0,
            performance=100.0,
            security=100.0,
            content_freshness=100.0,
            seo_hygiene=100.0,
        )
        score, tier, confidence = score_lead(subscores)
        assert score == 100.0
        assert tier == "hot"
        # All 7 non-null → confidence = 0.6 + 1.0*0.4 = 1.0
        assert confidence == pytest.approx(1.0)

    def test_all_subscores_present_warm(self):
        """Subscores producing a warm score (50–69)."""
        subscores = Subscores(
            mobile=50.0,
            visual_datedness=50.0,
            tech_obsolescence=50.0,
            performance=50.0,
            security=50.0,
            content_freshness=50.0,
            seo_hygiene=50.0,
        )
        score, tier, confidence = score_lead(subscores)
        assert score == 50.0
        assert tier == "warm"
        assert confidence == pytest.approx(1.0)

    def test_all_subscores_present_cold(self):
        """All subscores at zero → cold tier."""
        subscores = Subscores(
            mobile=0.0,
            visual_datedness=0.0,
            tech_obsolescence=0.0,
            performance=0.0,
            security=0.0,
            content_freshness=0.0,
            seo_hygiene=0.0,
        )
        score, tier, confidence = score_lead(subscores)
        assert score == 0.0
        assert tier == "cold"
        assert confidence == pytest.approx(1.0)

    def test_weighted_formula(self):
        """Verify weighted formula with known values."""
        subscores = Subscores(
            mobile=80.0,         # 0.20 * 80 = 16
            visual_datedness=60.0,  # 0.20 * 60 = 12
            tech_obsolescence=50.0, # 0.20 * 50 = 10
            performance=40.0,    # 0.15 * 40 = 6
            security=20.0,       # 0.10 * 20 = 2
            content_freshness=30.0, # 0.10 * 30 = 3
            seo_hygiene=20.0,    # 0.05 * 20 = 1
        )
        # Expected: 16+12+10+6+2+3+1 = 50
        score, tier, confidence = score_lead(subscores)
        assert score == 50.0
        assert tier == "warm"

    def test_two_null_subscores_caps_tier(self):
        """Two null subscores → tier capped at warm even if normalized score >= 70."""
        subscores = Subscores(
            mobile=100.0,
            visual_datedness=100.0,
            tech_obsolescence=100.0,
            performance=100.0,
            security=100.0,
            content_freshness=None,
            seo_hygiene=None,
        )
        score, tier, confidence = score_lead(subscores)
        # Normalized: all measured at 100 → score = 100
        assert score >= 70
        assert tier == "warm"
        # confidence = measured_weight = 0.20+0.20+0.20+0.15+0.10 = 0.85
        assert confidence == pytest.approx(0.85, abs=0.001)

    def test_two_null_subscores_confidence_formula(self):
        """Confidence equals the sum of weights of measured subscores."""
        subscores = Subscores(
            mobile=50.0,
            visual_datedness=None,   # 0.20 missing
            tech_obsolescence=None,  # 0.20 missing
            performance=50.0,
            security=50.0,
            content_freshness=50.0,
            seo_hygiene=50.0,
        )
        _, _, confidence = score_lead(subscores)
        # measured_weight = 0.20+0.15+0.10+0.10+0.05 = 0.60
        assert confidence == pytest.approx(0.60, abs=0.001)

    def test_three_null_subscores_confidence(self):
        """Three nulls → lower measured_weight."""
        subscores = Subscores(
            mobile=50.0,
            visual_datedness=None,   # 0.20 missing
            tech_obsolescence=None,  # 0.20 missing
            performance=None,        # 0.15 missing
            security=50.0,
            content_freshness=50.0,
            seo_hygiene=50.0,
        )
        _, _, confidence = score_lead(subscores)
        # measured_weight = 0.20+0.10+0.10+0.05 = 0.45
        assert confidence == pytest.approx(0.45, abs=0.001)

    def test_one_null_subscore_confidence(self):
        """One null → confidence = sum of remaining weights."""
        subscores = Subscores(
            mobile=None,             # 0.20 missing
            visual_datedness=50.0,
            tech_obsolescence=50.0,
            performance=50.0,
            security=50.0,
            content_freshness=50.0,
            seo_hygiene=50.0,
        )
        _, _, confidence = score_lead(subscores)
        # measured_weight = 0.20+0.20+0.15+0.10+0.10+0.05 = 0.80
        assert confidence == pytest.approx(0.80, abs=0.001)

    def test_normalization_partial_signals(self):
        """Partial signals normalize to same score as if all were measured."""
        # Only security (0.10) and seo_hygiene (0.05) measured, both at 80
        subscores = Subscores(
            mobile=None,
            visual_datedness=None,
            tech_obsolescence=None,
            performance=None,
            security=80.0,
            content_freshness=None,
            seo_hygiene=80.0,
        )
        score, _, _ = score_lead(subscores)
        # measured_weight = 0.10+0.05 = 0.15
        # weighted_sum = 0.10*80 + 0.05*80 = 12
        # normalized = 12 / 0.15 = 80
        assert score == 80.0

    def test_tiering_threshold_70_is_hot(self):
        """Score of exactly 70 → hot."""
        subscores = Subscores(
            mobile=70.0,
            visual_datedness=70.0,
            tech_obsolescence=70.0,
            performance=70.0,
            security=70.0,
            content_freshness=70.0,
            seo_hygiene=70.0,
        )
        score, tier, _ = score_lead(subscores)
        assert score == 70.0
        assert tier == "hot"

    def test_tiering_threshold_69_is_warm(self):
        """Score of 69 → warm."""
        # Construct subscores that yield exactly 69
        # mobile=69.0 → 0.20*69=13.8, etc. Let's use a direct calc approach
        # We need weighted sum = 69
        # Use all subscores = 69 → 69*(0.20+0.20+0.20+0.15+0.10+0.10+0.05) = 69*1.0 = 69
        subscores = Subscores(
            mobile=69.0,
            visual_datedness=69.0,
            tech_obsolescence=69.0,
            performance=69.0,
            security=69.0,
            content_freshness=69.0,
            seo_hygiene=69.0,
        )
        score, tier, _ = score_lead(subscores)
        assert score == 69.0
        assert tier == "warm"

    def test_tiering_threshold_50_is_warm(self):
        """Score of exactly 50 → warm."""
        subscores = Subscores(
            mobile=50.0,
            visual_datedness=50.0,
            tech_obsolescence=50.0,
            performance=50.0,
            security=50.0,
            content_freshness=50.0,
            seo_hygiene=50.0,
        )
        score, tier, _ = score_lead(subscores)
        assert score == 50.0
        assert tier == "warm"

    def test_tiering_threshold_49_is_cold(self):
        """Score of 49 → cold."""
        subscores = Subscores(
            mobile=49.0,
            visual_datedness=49.0,
            tech_obsolescence=49.0,
            performance=49.0,
            security=49.0,
            content_freshness=49.0,
            seo_hygiene=49.0,
        )
        score, tier, _ = score_lead(subscores)
        assert score == 49.0
        assert tier == "cold"


# ---------------------------------------------------------------------------
# compute_content_freshness
# ---------------------------------------------------------------------------


class TestComputeContentFreshness:
    def _years_ago(self, years: float) -> datetime:
        """Return a UTC datetime *years* years in the past."""
        from datetime import timedelta
        return datetime.now(tz=timezone.utc) - timedelta(days=years * 365.25)

    def test_less_than_one_year_returns_zero(self):
        dt = self._years_ago(0.5)
        assert compute_content_freshness(dt, None) == 0.0

    def test_exactly_one_year_returns_zero(self):
        dt = self._years_ago(1.0)
        result = compute_content_freshness(dt, None)
        assert result == pytest.approx(0.0, abs=1.0)  # allow rounding at boundary

    def test_two_years_returns_40(self):
        dt = self._years_ago(2.0)
        result = compute_content_freshness(dt, None)
        assert result == pytest.approx(40.0, abs=2.0)

    def test_three_years_returns_70(self):
        dt = self._years_ago(3.0)
        result = compute_content_freshness(dt, None)
        assert result == pytest.approx(70.0, abs=2.0)

    def test_four_years_returns_100(self):
        dt = self._years_ago(4.0)
        result = compute_content_freshness(dt, None)
        assert result == pytest.approx(100.0, abs=2.0)

    def test_more_than_four_years_capped_at_100(self):
        dt = self._years_ago(10.0)
        result = compute_content_freshness(dt, None)
        assert result == 100.0

    def test_footer_year_fallback(self):
        """When last_change is None, footer_year drives the score."""
        from datetime import datetime
        current_year = datetime.now().year
        four_years_ago = current_year - 4
        result = compute_content_freshness(None, four_years_ago)
        assert result == pytest.approx(100.0, abs=2.0)

    def test_footer_year_current_returns_zero(self):
        from datetime import datetime
        current_year = datetime.now().year
        result = compute_content_freshness(None, current_year)
        assert result == 0.0

    def test_no_data_returns_zero(self):
        assert compute_content_freshness(None, None) == 0.0

    def test_interpolation_between_2_and_3_years(self):
        """2.5 years old → between 40 and 70, ~55."""
        dt = self._years_ago(2.5)
        result = compute_content_freshness(dt, None)
        assert 40.0 < result < 70.0

    def test_naive_datetime_handled(self):
        """Naive datetimes (no tzinfo) should be treated as UTC without errors."""
        naive_dt = datetime(2020, 1, 1)  # naive
        result = compute_content_freshness(naive_dt, None)
        assert result >= 0.0  # no exception

    def test_last_change_preferred_over_footer_year(self):
        """If both last_change and footer_year are provided, last_change wins."""
        from datetime import datetime
        recent_dt = self._years_ago(0.5)  # very recent → score ~0
        old_year = datetime.now().year - 10  # very old → score 100
        result = compute_content_freshness(recent_dt, old_year)
        assert result == pytest.approx(0.0, abs=1.0)


# ---------------------------------------------------------------------------
# compute_seo_hygiene
# ---------------------------------------------------------------------------


class TestComputeSeoHygiene:
    def test_all_present_returns_zero(self):
        assert compute_seo_hygiene(True, True, True) == 0.0

    def test_all_missing_returns_100(self):
        # 3 * 34 = 102, capped at 100
        assert compute_seo_hygiene(False, False, False) == 100.0

    def test_all_none_returns_100(self):
        assert compute_seo_hygiene(None, None, None) == 100.0

    def test_one_missing_returns_34(self):
        assert compute_seo_hygiene(False, True, True) == 34.0
        assert compute_seo_hygiene(True, False, True) == 34.0
        assert compute_seo_hygiene(True, True, False) == 34.0

    def test_two_missing_returns_68(self):
        assert compute_seo_hygiene(False, False, True) == 68.0
        assert compute_seo_hygiene(False, True, False) == 68.0
        assert compute_seo_hygiene(True, False, False) == 68.0

    def test_mixed_none_and_false(self):
        """None and False both count as missing."""
        assert compute_seo_hygiene(None, False, True) == 68.0

    def test_cap_at_100(self):
        result = compute_seo_hygiene(False, False, False)
        assert result <= 100.0


# ---------------------------------------------------------------------------
# compute_tech_obsolescence
# ---------------------------------------------------------------------------


class TestComputeTechObsolescence:
    def test_flash_returns_100(self):
        assert compute_tech_obsolescence(["Flash"], None, None) == 100.0

    def test_jquery_1x_returns_100(self):
        assert compute_tech_obsolescence(["jQuery/1.12.4"], None, None) == 100.0

    def test_jquery_2x_returns_80(self):
        assert compute_tech_obsolescence(["jQuery/2.2.4"], None, None) == 80.0

    def test_wordpress_v4_cms_returns_80(self):
        assert compute_tech_obsolescence([], "WordPress", "4.9") == 80.0

    def test_wordpress_v6_cms_returns_0(self):
        assert compute_tech_obsolescence([], "WordPress", "6.4") == 0.0

    def test_php_5_returns_80(self):
        assert compute_tech_obsolescence(["PHP/5.6"], None, None) == 80.0

    def test_modern_react_returns_0(self):
        assert compute_tech_obsolescence(["React", "Next.js"], None, None) == 0.0

    def test_empty_returns_0(self):
        assert compute_tech_obsolescence([], None, None) == 0.0

    def test_silverlight_returns_80(self):
        assert compute_tech_obsolescence(["Silverlight"], None, None) == 80.0

    def test_bootstrap_3_returns_60(self):
        assert compute_tech_obsolescence(["Bootstrap/3.4.1"], None, None) == 60.0

    def test_drupal_7_returns_60(self):
        assert compute_tech_obsolescence(["Drupal/7.91"], None, None) == 60.0
