"""Special functions and distribution tails used by the statistical tests.

Implemented directly (no SciPy at runtime) so the service image stays small
and every number in a validation report is reproducible from this module.
The test suite cross-checks each function against SciPy reference values.
"""

from __future__ import annotations

import math
from statistics import NormalDist

_EPS = 1e-15
_TINY = 1e-300
_MAX_ITER = 10_000


def normal_quantile(p: float) -> float:
    """Inverse standard normal CDF."""
    if not 0.0 < p < 1.0:
        raise ValueError("p must lie strictly between 0 and 1")
    return NormalDist().inv_cdf(p)


def two_sided_z(confidence: float) -> float:
    """Critical value z such that P(|Z| <= z) = confidence."""
    return normal_quantile(0.5 + confidence / 2.0)


def _log_prefactor(a: float, x: float) -> float:
    return -x + a * math.log(x) - math.lgamma(a)


def _gamma_series(a: float, x: float) -> float:
    """Lower regularized gamma P(a, x) by its power series (x < a + 1)."""
    term = total = 1.0 / a
    ap = a
    for _ in range(_MAX_ITER):
        ap += 1.0
        term *= x / ap
        total += term
        if abs(term) < abs(total) * _EPS:
            break
    return total * math.exp(_log_prefactor(a, x))


def _gamma_continued_fraction(a: float, x: float) -> float:
    """Upper regularized gamma Q(a, x) by Lentz's continued fraction (x >= a + 1)."""
    b = x + 1.0 - a
    c = 1.0 / _TINY
    d = 1.0 / b
    h = d
    for i in range(1, _MAX_ITER):
        an = -i * (i - a)
        b += 2.0
        d = an * d + b
        if abs(d) < _TINY:
            d = _TINY
        c = b + an / c
        if abs(c) < _TINY:
            c = _TINY
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < _EPS:
            break
    return math.exp(_log_prefactor(a, x)) * h


def regularized_gamma_q(a: float, x: float) -> float:
    """Upper regularized incomplete gamma function Q(a, x) = 1 - P(a, x)."""
    if a <= 0:
        raise ValueError("a must be positive")
    if x < 0:
        raise ValueError("x must be non-negative")
    if x == 0:
        return 1.0
    if x < a + 1.0:
        return max(0.0, 1.0 - _gamma_series(a, x))
    return min(1.0, _gamma_continued_fraction(a, x))


def chi2_sf(x: float, df: float) -> float:
    """Survival function of the chi-square distribution."""
    if df <= 0:
        raise ValueError("degrees of freedom must be positive")
    if x <= 0:
        return 1.0
    return regularized_gamma_q(df / 2.0, x / 2.0)


def chi2_ppf(p: float, df: float) -> float:
    """Quantile of the chi-square distribution, by bisection on the CDF."""
    if not 0.0 < p < 1.0:
        raise ValueError("p must lie strictly between 0 and 1")
    if df <= 0:
        raise ValueError("degrees of freedom must be positive")
    low, high = 0.0, max(1.0, 2.0 * df)
    while 1.0 - chi2_sf(high, df) < p:
        high *= 2.0
    for _ in range(200):
        mid = (low + high) / 2.0
        if 1.0 - chi2_sf(mid, df) < p:
            low = mid
        else:
            high = mid
        if high - low <= 1e-12 * max(1.0, high):
            break
    return (low + high) / 2.0


def kolmogorov_sf(lam: float) -> float:
    """Survival function of the limiting Kolmogorov distribution, P(K > lam)."""
    if lam <= 0:
        return 1.0
    if lam < 1.18:
        # Jacobi theta form converges quickly for small lambda.
        y = math.exp(-(math.pi**2) / (8.0 * lam * lam))
        total = 0.0
        k = 1
        while True:
            term = y ** ((2 * k - 1) ** 2)
            total += term
            if term < 1e-17 or k > 100:
                break
            k += 1
        return min(1.0, max(0.0, 1.0 - math.sqrt(2.0 * math.pi) / lam * total))
    total = 0.0
    for k in range(1, 101):
        term = math.exp(-2.0 * k * k * lam * lam)
        total += term if k % 2 == 1 else -term
        if term < 1e-17:
            break
    return min(1.0, max(0.0, 2.0 * total))
