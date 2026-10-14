"""Method verification statistics against textbook values and independent computations."""

from __future__ import annotations

import math

import numpy as np
import pytest
from scipy import stats

from cross_lab_ai_validation.domain.aggregates import (
    AgreementStudy,
    LinearityLevel,
    LinearityStudy,
    PrecisionLevel,
    PrecisionStudy,
)
from cross_lab_ai_validation.domain.errors import InsufficientDataError
from cross_lab_ai_validation.domain.models import PrecisionCriteria, ReportableRangeCriteria
from cross_lab_ai_validation.evaluation.method_verification import (
    agreement,
    linearity,
    precision,
    precision_level,
)

RANGE = ReportableRangeCriteria(
    claimed_low=10, claimed_high=500, allowable_nonlinearity_pct=5, allowable_bias_pct=10
)


def study(a: int, b: int, c: int, d: int) -> AgreementStudy:
    return AgreementStudy(
        both_positive=a,
        candidate_positive_comparator_negative=b,
        candidate_negative_comparator_positive=c,
        both_negative=d,
        comparator="reference culture",
    )


def test_kappa_textbook_example() -> None:
    # po = 0.70, pe = 0.50 -> kappa = 0.40.
    result = agreement(study(20, 5, 10, 15))
    assert result.kappa == pytest.approx(0.40)
    se = math.sqrt(0.7 * 0.3 / (50 * 0.5**2))
    assert result.kappa_lower == pytest.approx(0.40 - 1.959964 * se, abs=1e-6)
    assert result.positive_percent_agreement.estimate == pytest.approx(20 / 30)
    assert result.negative_percent_agreement.estimate == pytest.approx(15 / 20)
    assert result.overall_percent_agreement.estimate == pytest.approx(0.70)


def test_agreement_undefined_cases() -> None:
    with pytest.raises(InsufficientDataError):
        agreement(study(0, 0, 0, 0))
    with pytest.raises(InsufficientDataError, match="kappa"):
        agreement(study(10, 0, 0, 0))


def test_precision_anova_matches_independent_computation() -> None:
    days = [
        [101.0, 99.5, 100.2, 100.8, 99.9],
        [102.1, 101.5, 102.8, 101.9, 102.3],
        [98.9, 99.6, 99.1, 98.7, 99.4],
        [100.5, 100.1, 101.2, 100.7, 100.0],
        [101.7, 102.4, 101.1, 102.0, 101.6],
    ]
    data = np.array(days)
    criteria = PrecisionCriteria(repeatability_cv_pct=0.5, within_lab_cv_pct=0.8)
    result = precision_level(PrecisionLevel(material="QC1", days=tuple(map(tuple, days))), criteria)

    ms_within = float(np.mean(data.var(axis=1, ddof=1)))
    f = stats.f_oneway(*data)
    ms_between = f.statistic * ms_within
    var_between = (ms_between - ms_within) / 5
    assert result.repeatability_sd == pytest.approx(math.sqrt(ms_within))
    assert result.within_lab_sd == pytest.approx(math.sqrt(ms_within + var_between))
    assert result.repeatability_df == 20
    a, b = 4 / 5 * ms_within, ms_between / 5
    df_wl = (a + b) ** 2 / (a * a / 20 + b * b / 4)
    assert result.within_lab_df == pytest.approx(df_wl)
    claimed = 0.5 * data.mean() / 100
    uvl = claimed * math.sqrt(stats.chi2.ppf(0.95, 20) / 20)
    assert result.repeatability_uvl_cv_pct == pytest.approx(uvl / (data.mean() / 100), rel=1e-7)
    assert result.repeatability_cv_pct == pytest.approx(100 * math.sqrt(ms_within) / data.mean())


def test_precision_verification_decisions() -> None:
    rng = np.random.default_rng(1)
    tight = tuple(tuple(100 + rng.normal(0, 0.5, 4)) for _ in range(5))
    loose = tuple(tuple(100 + rng.normal(0, 6.0, 4)) for _ in range(5))
    criteria = PrecisionCriteria(repeatability_cv_pct=1.0, within_lab_cv_pct=1.5)
    ok = precision(PrecisionStudy(levels=(PrecisionLevel(material="tight", days=tight),)), criteria)
    assert ok.passed
    bad = precision(
        PrecisionStudy(levels=(PrecisionLevel(material="loose", days=loose),)), criteria
    )
    assert not bad.passed
    # No between-day component: within-lab equals repeatability.
    flat = ((1.0, 2.0), (1.0, 2.0), (1.0, 2.0))
    flat_result = precision_level(PrecisionLevel(material="flat", days=flat), criteria)
    assert flat_result.between_day_sd == 0.0
    assert flat_result.within_lab_df == flat_result.repeatability_df


def test_precision_zero_mean_is_rejected() -> None:
    with pytest.raises(InsufficientDataError, match="zero mean"):
        precision_level(
            PrecisionLevel(material="blank", days=((0.0, 0.0), (0.0, 0.0))),
            PrecisionCriteria(repeatability_cv_pct=1, within_lab_cv_pct=1),
        )


def test_linearity_linear_data_verifies_full_range() -> None:
    levels = tuple(
        LinearityLevel(expected=x, measurements=(x * 1.01, x * 0.99))
        for x in (10, 50, 100, 200, 350, 500)
    )
    result = linearity(LinearityStudy(levels=levels), RANGE)
    assert result.passed
    assert result.slope == pytest.approx(1.0, abs=1e-9)
    assert (result.verified_low, result.verified_high) == (10, 500)
    assert all(abs(level.nonlinearity_pct) <= 5 for level in result.levels)


def test_linearity_detects_saturation_at_top_of_range() -> None:
    expected = (10, 50, 100, 200, 350, 500)
    # Response flattens above ~300 (detector saturation).
    measured = [x if x <= 200 else 200 + 0.45 * (x - 200) for x in expected]
    levels = tuple(
        LinearityLevel(expected=x, measurements=(m, m))
        for x, m in zip(expected, measured, strict=True)
    )
    result = linearity(LinearityStudy(levels=levels), RANGE)
    assert result.best_fit_degree > 1
    assert not result.passed
    assert result.verified_high is not None
    assert result.verified_high < 500
    assert not result.levels[-1].passed


def test_linearity_zero_level_uses_range_span() -> None:
    levels = (
        LinearityLevel(expected=0.0, measurements=(0.5,)),
        LinearityLevel(expected=250.0, measurements=(250.0,)),
        LinearityLevel(expected=500.0, measurements=(500.0,)),
        LinearityLevel(expected=750.0, measurements=(749.0,)),
    )
    result = linearity(LinearityStudy(levels=levels), RANGE)
    assert result.levels[0].bias_pct == pytest.approx(100 * 0.5 / 490)
    assert result.passed


def test_linearity_with_no_passing_level() -> None:
    levels = tuple(LinearityLevel(expected=x, measurements=(2 * x,)) for x in (10.0, 100.0, 500.0))
    result = linearity(LinearityStudy(levels=levels), RANGE)
    assert result.verified_low is None
    assert not result.passed
