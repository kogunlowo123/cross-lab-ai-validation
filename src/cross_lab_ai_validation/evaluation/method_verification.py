"""CLIA/CAP-style method verification: accuracy, precision and reportable range.

* Accuracy (qualitative): positive, negative and overall percent agreement
  with the comparator method, with Wilson intervals, and Cohen's kappa with
  its large-sample standard error.
* Precision: CLSI EP15-style one-way ANOVA (days x replicates) giving
  repeatability and within-laboratory SD and CV. A claim is verified when the
  observed SD is at or below the claimed SD, or below the upper verification
  limit sigma_claim * sqrt(chi2_{1-alpha/L, df} / df) with Satterthwaite
  degrees of freedom for within-laboratory precision.
* Reportable range: CLSI EP6-style polynomial evaluation. Linear, quadratic
  and cubic fits are compared; the fit with the smallest residual standard
  error is the best description of the data, and its deviation from the
  linear fit at each level is the nonlinearity. Levels must also recover the
  expected value within the allowable bias.
"""

from __future__ import annotations

import math

import numpy as np
from numpy.typing import NDArray
from pydantic import BaseModel, ConfigDict

from cross_lab_ai_validation.domain.aggregates import (
    AgreementStudy,
    LinearityLevel,
    LinearityStudy,
    PrecisionLevel,
    PrecisionStudy,
)
from cross_lab_ai_validation.domain.errors import InsufficientDataError
from cross_lab_ai_validation.domain.models import PrecisionCriteria, ReportableRangeCriteria
from cross_lab_ai_validation.evaluation.classification import Proportion, wilson
from cross_lab_ai_validation.evaluation.special import chi2_ppf, two_sided_z


class AgreementResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    n: int
    comparator: str
    positive_percent_agreement: Proportion
    negative_percent_agreement: Proportion
    overall_percent_agreement: Proportion
    kappa: float
    kappa_lower: float
    kappa_upper: float


class PrecisionLevelResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    material: str
    days: int
    replicates_per_day: int
    mean: float
    repeatability_sd: float
    repeatability_cv_pct: float
    between_day_sd: float
    within_lab_sd: float
    within_lab_cv_pct: float
    repeatability_df: int
    within_lab_df: float
    repeatability_uvl_cv_pct: float
    within_lab_uvl_cv_pct: float
    repeatability_verified: bool
    within_lab_verified: bool


class PrecisionResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    levels: tuple[PrecisionLevelResult, ...]
    passed: bool


class LinearityLevelResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    expected: float
    mean: float
    bias_pct: float
    nonlinearity_pct: float
    passed: bool


class LinearityResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    levels: tuple[LinearityLevelResult, ...]
    best_fit_degree: int
    slope: float
    intercept: float
    verified_low: float | None
    verified_high: float | None
    claimed_low: float
    claimed_high: float
    passed: bool


def agreement(study: AgreementStudy, confidence: float = 0.95) -> AgreementResult:
    a = study.both_positive
    b = study.candidate_positive_comparator_negative
    c = study.candidate_negative_comparator_positive
    d = study.both_negative
    n = study.n
    if n == 0:
        raise InsufficientDataError("agreement study has no specimens")
    po = (a + d) / n
    pe = ((a + b) * (a + c) + (c + d) * (b + d)) / (n * n)
    if math.isclose(pe, 1.0):
        raise InsufficientDataError("kappa is undefined when expected agreement is 1")
    kappa = (po - pe) / (1.0 - pe)
    se = math.sqrt(po * (1.0 - po) / (n * (1.0 - pe) ** 2))
    z = two_sided_z(confidence)
    return AgreementResult(
        n=n,
        comparator=study.comparator,
        positive_percent_agreement=wilson(a, a + c, confidence),
        negative_percent_agreement=wilson(d, b + d, confidence),
        overall_percent_agreement=wilson(a + d, n, confidence),
        kappa=kappa,
        kappa_lower=max(-1.0, kappa - z * se),
        kappa_upper=min(1.0, kappa + z * se),
    )


def _uvl_sd(claimed_sd: float, df: float, alpha: float, levels: int) -> float:
    return claimed_sd * math.sqrt(chi2_ppf(1.0 - alpha / levels, df) / df)


def precision_level(
    level: PrecisionLevel, criteria: PrecisionCriteria, levels: int = 1
) -> PrecisionLevelResult:
    data = np.asarray(level.days, dtype=np.float64)
    days, reps = data.shape
    grand = float(data.mean())
    if math.isclose(grand, 0.0, abs_tol=1e-12):
        raise InsufficientDataError(f"material {level.material!r} has zero mean; CV is undefined")
    day_means = data.mean(axis=1)
    ms_within = float(((data - day_means[:, None]) ** 2).sum() / (days * (reps - 1)))
    ms_between = float(reps * ((day_means - grand) ** 2).sum() / (days - 1))
    var_between = max(0.0, (ms_between - ms_within) / reps)
    sr = math.sqrt(ms_within)
    s_wl = math.sqrt(ms_within + var_between)
    df_r = days * (reps - 1)
    if var_between == 0.0:
        df_wl = float(df_r)
    else:
        # Satterthwaite: s_WL^2 = ((n-1)/n) MS_within + (1/n) MS_between.
        a = (reps - 1) / reps * ms_within
        b = ms_between / reps
        df_wl = (a + b) ** 2 / (a * a / df_r + b * b / (days - 1))
    scale = abs(grand) / 100.0
    claimed_r = criteria.repeatability_cv_pct * scale
    claimed_wl = criteria.within_lab_cv_pct * scale
    uvl_r = _uvl_sd(claimed_r, df_r, criteria.alpha, levels)
    uvl_wl = _uvl_sd(claimed_wl, df_wl, criteria.alpha, levels)
    return PrecisionLevelResult(
        material=level.material,
        days=days,
        replicates_per_day=reps,
        mean=grand,
        repeatability_sd=sr,
        repeatability_cv_pct=sr / scale,
        between_day_sd=math.sqrt(var_between),
        within_lab_sd=s_wl,
        within_lab_cv_pct=s_wl / scale,
        repeatability_df=df_r,
        within_lab_df=df_wl,
        repeatability_uvl_cv_pct=uvl_r / scale,
        within_lab_uvl_cv_pct=uvl_wl / scale,
        repeatability_verified=sr <= claimed_r or sr <= uvl_r,
        within_lab_verified=s_wl <= claimed_wl or s_wl <= uvl_wl,
    )


def precision(study: PrecisionStudy, criteria: PrecisionCriteria) -> PrecisionResult:
    results = tuple(precision_level(level, criteria, len(study.levels)) for level in study.levels)
    return PrecisionResult(
        levels=results,
        passed=all(r.repeatability_verified and r.within_lab_verified for r in results),
    )


def _percent(delta: float, reference: float, span: float) -> float:
    base = abs(reference) if not math.isclose(reference, 0.0, abs_tol=1e-12) else span
    return 100.0 * delta / base


class _Fit(BaseModel):
    model_config = ConfigDict(frozen=True)

    levels: tuple[LinearityLevelResult, ...]
    best_fit_degree: int
    slope: float
    intercept: float

    @property
    def all_pass(self) -> bool:
        return all(level.passed for level in self.levels)


def _evaluate(levels: tuple[LinearityLevel, ...], criteria: ReportableRangeCriteria) -> _Fit:
    """Polynomial evaluation of one set of levels (EP6 polynomial method)."""
    x = np.asarray([lvl.expected for lvl in levels for _ in lvl.measurements], dtype=np.float64)
    y = np.asarray([m for lvl in levels for m in lvl.measurements], dtype=np.float64)
    expected = np.asarray([lvl.expected for lvl in levels], dtype=np.float64)
    span = criteria.claimed_high - criteria.claimed_low

    fits: dict[int, NDArray[np.float64]] = {}
    errors: dict[int, float] = {}
    for degree in (1, 2, 3):
        dof = len(y) - (degree + 1)
        if degree >= len(levels) or dof <= 0:
            continue
        coefficients = np.polyfit(x, y, degree)
        residual = y - np.polyval(coefficients, x)
        fits[degree] = coefficients
        errors[degree] = math.sqrt(float((residual**2).sum()) / dof)
    # Prefer the lowest degree unless a higher one fits clearly better (ties -> linear).
    best = min(errors, key=lambda d: (round(errors[d], 9), d))
    linear_at = np.polyval(fits[1], expected)
    best_at = np.polyval(fits[best], expected)

    results: list[LinearityLevelResult] = []
    for i, level in enumerate(levels):
        mean = float(np.mean(level.measurements))
        nonlinearity = _percent(float(best_at[i] - linear_at[i]), float(linear_at[i]), span)
        bias = _percent(mean - level.expected, level.expected, span)
        results.append(
            LinearityLevelResult(
                expected=level.expected,
                mean=mean,
                bias_pct=bias,
                nonlinearity_pct=nonlinearity,
                passed=abs(nonlinearity) <= criteria.allowable_nonlinearity_pct
                and abs(bias) <= criteria.allowable_bias_pct,
            )
        )
    return _Fit(
        levels=tuple(results),
        best_fit_degree=best,
        slope=float(fits[1][0]),
        intercept=float(fits[1][1]),
    )


def linearity(study: LinearityStudy, criteria: ReportableRangeCriteria) -> LinearityResult:
    """Evaluate all levels, then find the widest contiguous range that is linear on its own.

    When the response saturates at one end, a fit through every level is
    distorted and can flag the whole range. EP6 practice is to remove the
    nonlinear end levels and re-evaluate; the verified reportable range is
    the widest window of at least three consecutive levels whose own fit
    passes the nonlinearity and bias criteria at every level.
    """
    levels = study.levels
    full = _evaluate(levels, criteria)
    window: tuple[int, int] | None = None
    if full.all_pass:
        window = (0, len(levels) - 1)
    else:
        candidates = sorted(
            (
                (i, j)
                for i in range(len(levels))
                for j in range(i + 2, len(levels))
                if (i, j) != (0, len(levels) - 1)
            ),
            key=lambda w: (-(levels[w[1]].expected - levels[w[0]].expected), w[0]),
        )
        window = next(
            (w for w in candidates if _evaluate(levels[w[0] : w[1] + 1], criteria).all_pass),
            None,
        )
    low = levels[window[0]].expected if window else None
    high = levels[window[1]].expected if window else None
    covers = (
        low is not None
        and high is not None
        and low <= criteria.claimed_low
        and high >= criteria.claimed_high
    )
    return LinearityResult(
        levels=full.levels,
        best_fit_degree=full.best_fit_degree,
        slope=full.slope,
        intercept=full.intercept,
        verified_low=low,
        verified_high=high,
        claimed_low=criteria.claimed_low,
        claimed_high=criteria.claimed_high,
        passed=covers,
    )
