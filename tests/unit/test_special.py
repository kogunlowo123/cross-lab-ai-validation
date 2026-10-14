"""Special functions against SciPy reference values."""

from __future__ import annotations

import math

import pytest
from scipy import stats

from cross_lab_ai_validation.evaluation.special import (
    chi2_ppf,
    chi2_sf,
    kolmogorov_sf,
    normal_quantile,
    regularized_gamma_q,
    two_sided_z,
)


@pytest.mark.parametrize("df", [1, 2, 3, 5, 10, 24, 99])
@pytest.mark.parametrize("x", [0.01, 0.5, 1.0, 3.84, 7.5, 20.0, 60.0, 150.0])
def test_chi2_sf_matches_scipy(x: float, df: int) -> None:
    assert chi2_sf(x, df) == pytest.approx(stats.chi2.sf(x, df), rel=1e-9, abs=1e-14)


@pytest.mark.parametrize("df", [1, 4, 19.7, 24, 120])
@pytest.mark.parametrize("p", [0.01, 0.5, 0.95, 0.975, 0.999])
def test_chi2_ppf_matches_scipy(p: float, df: float) -> None:
    assert chi2_ppf(p, df) == pytest.approx(stats.chi2.ppf(p, df), rel=1e-8)


def test_chi2_known_critical_value() -> None:
    # Textbook: chi-square 0.95 quantile with 1 df is 3.8415.
    assert chi2_ppf(0.95, 1) == pytest.approx(3.841459, abs=1e-6)
    assert chi2_sf(0.0, 3) == 1.0


@pytest.mark.parametrize("lam", [0.05, 0.3, 0.6, 0.9, 1.0, 1.17, 1.19, 1.358, 2.0, 3.5])
def test_kolmogorov_sf_matches_scipy(lam: float) -> None:
    assert kolmogorov_sf(lam) == pytest.approx(stats.kstwobign.sf(lam), rel=1e-9, abs=1e-15)


def test_kolmogorov_known_value() -> None:
    # 1.358 is the classic 5% critical value of the Kolmogorov distribution.
    assert kolmogorov_sf(1.358) == pytest.approx(0.05, abs=5e-4)
    assert kolmogorov_sf(0.0) == 1.0
    assert kolmogorov_sf(-1.0) == 1.0


def test_normal_quantiles() -> None:
    assert two_sided_z(0.95) == pytest.approx(1.959964, abs=1e-6)
    assert normal_quantile(0.5) == pytest.approx(0.0)
    with pytest.raises(ValueError, match="strictly"):
        normal_quantile(1.0)


def test_domain_errors() -> None:
    with pytest.raises(ValueError, match="positive"):
        regularized_gamma_q(0, 1)
    with pytest.raises(ValueError, match="non-negative"):
        regularized_gamma_q(1, -1)
    with pytest.raises(ValueError, match="degrees"):
        chi2_sf(1.0, 0)
    with pytest.raises(ValueError, match="strictly"):
        chi2_ppf(0.0, 3)
    with pytest.raises(ValueError, match="degrees"):
        chi2_ppf(0.5, -2)
    assert regularized_gamma_q(2.5, 0.0) == 1.0
    assert math.isclose(regularized_gamma_q(1.0, 2.0), math.exp(-2.0), rel_tol=1e-12)
