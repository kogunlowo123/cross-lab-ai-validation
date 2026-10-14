"""Shift statistics against hand calculations and SciPy."""

from __future__ import annotations

import math

import numpy as np
import pytest
from scipy import stats

from cross_lab_ai_validation.domain.errors import InsufficientDataError
from cross_lab_ai_validation.evaluation.shift import (
    black_box_shift_estimation,
    chi_square_homogeneity,
    ks_binned,
    psi,
)


def test_psi_hand_calculation() -> None:
    # Expected 25/25/50 %, actual 20/30/50 %:
    # (0.20-0.25) ln(0.20/0.25) + (0.30-0.25) ln(0.30/0.25) + 0 = 0.020273...
    result = psi([250, 250, 500], [200, 300, 500])
    expected = (0.20 - 0.25) * math.log(0.20 / 0.25) + (0.30 - 0.25) * math.log(0.30 / 0.25)
    assert result.psi == pytest.approx(expected, rel=1e-12)
    assert result.contributions[2] == pytest.approx(0.0)


def test_psi_identical_is_zero_and_empty_bins_are_floored() -> None:
    assert psi([10, 20, 30], [20, 40, 60]).psi == pytest.approx(0.0, abs=1e-15)
    floored = psi([100, 0], [50, 50])
    assert math.isfinite(floored.psi)
    assert floored.psi > 0.25
    with pytest.raises(ValueError, match="same"):
        psi([1, 2], [1, 2, 3])
    with pytest.raises(InsufficientDataError):
        psi([0, 0], [1, 1])


def test_binned_ks_equals_scipy_on_discrete_data() -> None:
    rng = np.random.default_rng(3)
    a = rng.integers(0, 8, 600)
    b = np.clip(rng.integers(0, 8, 450) + rng.integers(0, 2, 450), 0, 7)
    counts_a = np.bincount(a, minlength=8).tolist()
    counts_b = np.bincount(b, minlength=8).tolist()
    expected = stats.ks_2samp(a, b, method="asymp")
    result = ks_binned(counts_a, counts_b)
    assert result.statistic == pytest.approx(expected.statistic, rel=1e-12)
    # p-value from the limiting Kolmogorov distribution at sqrt(n m / (n + m)) * D.
    limiting = stats.kstwobign.sf(math.sqrt(600 * 450 / 1050) * expected.statistic)
    assert result.p_value == pytest.approx(limiting, rel=1e-9)
    assert result.p_value == pytest.approx(expected.pvalue, abs=0.02)
    assert (result.n_reference, result.n_site) == (600, 450)


def test_ks_rejects_bad_input() -> None:
    with pytest.raises(ValueError, match="same bins"):
        ks_binned([1, 2], [1])
    with pytest.raises(InsufficientDataError):
        ks_binned([0, 0], [1, 1])


def test_chi_square_matches_scipy() -> None:
    reference = {"emergency": 500, "inpatient": 300, "icu": 200}
    site = {"emergency": 380, "inpatient": 260, "icu": 260, "__other__": 0}
    result = chi_square_homogeneity(reference, site)
    table = [[500, 300, 200], [380, 260, 260]]
    stat, p, dof, expected = stats.chi2_contingency(table, correction=False)
    assert sorted(result.categories) == ["emergency", "icu", "inpatient"]
    assert result.statistic == pytest.approx(stat, rel=1e-12)
    assert result.p_value == pytest.approx(p, rel=1e-8)
    assert result.dof == dof == 2
    assert result.min_expected == pytest.approx(float(np.min(expected)))
    assert result.cramers_v == pytest.approx(math.sqrt(stat / 1900))


def test_chi_square_insufficient() -> None:
    with pytest.raises(InsufficientDataError):
        chi_square_homogeneity({"a": 5}, {"a": 3})
    with pytest.raises(InsufficientDataError):
        chi_square_homogeneity({"a": 0, "b": 0}, {"a": 3, "b": 2})


def test_bbse_recovers_true_prior_exactly() -> None:
    # Reference: prevalence 0.2, sensitivity 0.8, specificity 0.9 on 10 000 cases.
    joint = [[7200, 400], [800, 1600]]  # rows: predicted neg/pos; cols: true neg/pos
    # Site prevalence 0.4 with the same class-conditional behaviour:
    # P(pred pos) = 0.8 * 0.4 + 0.1 * 0.6 = 0.38.
    result = black_box_shift_estimation(joint, [620, 380])
    assert result.estimated_site_prior[1] == pytest.approx(0.4, abs=1e-12)
    assert result.reference_prior[1] == pytest.approx(0.2)
    assert result.class_weights[1] == pytest.approx(2.0)
    assert result.class_weights[0] == pytest.approx(0.75)
    assert not result.clipped


def test_bbse_clips_negative_weights() -> None:
    joint = [[7200, 400], [800, 1600]]
    # P(pred pos) below the false-positive rate implies a negative positive prior.
    result = black_box_shift_estimation(joint, [960, 40])
    assert result.clipped
    assert result.estimated_site_prior[1] == pytest.approx(0.0)


def test_bbse_errors() -> None:
    with pytest.raises(ValueError, match="k x k"):
        black_box_shift_estimation([[1, 2, 3], [4, 5, 6]], [1, 2])
    with pytest.raises(InsufficientDataError, match="predictions"):
        black_box_shift_estimation([[1, 1], [1, 1]], [0, 0])
    with pytest.raises(InsufficientDataError, match="every class"):
        black_box_shift_estimation([[5, 0], [5, 0]], [3, 3])
    with pytest.raises(InsufficientDataError, match="singular"):
        black_box_shift_estimation([[5, 5], [5, 5]], [3, 3])
