"""Population shift statistics on aggregate counts.

* Population Stability Index (PSI) between a reference and a site histogram.
* Two-sample Kolmogorov-Smirnov statistic on binned data with the asymptotic
  Kolmogorov p-value. Evaluated only at bin boundaries, so on binned data it
  is a lower bound on the case-level statistic (conservative).
* Pearson chi-square test of homogeneity for categorical features.
* Label shift estimation by Black Box Shift Estimation (Lipton, Wang and
  Smola, 2018): solve C w = mu, where C is the reference joint distribution
  of (predicted class, true class) and mu the site's predicted-class
  distribution, which needs no labels from the site.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence

import numpy as np
from pydantic import BaseModel, ConfigDict

from cross_lab_ai_validation.domain.errors import InsufficientDataError
from cross_lab_ai_validation.evaluation.special import chi2_sf, kolmogorov_sf

PSI_EPSILON = 1e-4


class PsiResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    psi: float
    contributions: tuple[float, ...]


class KsResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    statistic: float
    p_value: float
    n_reference: int
    n_site: int


class ChiSquareResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    statistic: float
    dof: int
    p_value: float
    cramers_v: float
    min_expected: float
    categories: tuple[str, ...]


class LabelShiftResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    class_weights: tuple[float, ...]
    reference_prior: tuple[float, ...]
    estimated_site_prior: tuple[float, ...]
    condition_number: float
    clipped: bool


def psi(reference: Sequence[int], site: Sequence[int], epsilon: float = PSI_EPSILON) -> PsiResult:
    """PSI = sum (a_i - e_i) * ln(a_i / e_i) over bin proportions.

    Empty bins are floored at ``epsilon`` so the index stays finite; the
    floor is part of the published method so values are reproducible.
    """
    if len(reference) != len(site) or len(reference) < 2:
        raise ValueError("PSI needs two histograms over the same (>= 2) bins")
    ref_total, site_total = sum(reference), sum(site)
    if ref_total == 0 or site_total == 0:
        raise InsufficientDataError("PSI needs non-empty reference and site histograms")
    contributions: list[float] = []
    for ref_count, site_count in zip(reference, site, strict=True):
        expected = max(ref_count / ref_total, epsilon)
        actual = max(site_count / site_total, epsilon)
        contributions.append((actual - expected) * math.log(actual / expected))
    return PsiResult(psi=sum(contributions), contributions=tuple(contributions))


def ks_binned(reference: Sequence[int], site: Sequence[int]) -> KsResult:
    """Two-sample KS statistic over shared bins with the asymptotic p-value."""
    if len(reference) != len(site):
        raise ValueError("KS needs two histograms over the same bins")
    n_ref, n_site = sum(reference), sum(site)
    if n_ref == 0 or n_site == 0:
        raise InsufficientDataError("KS needs non-empty reference and site histograms")
    cdf_ref = np.cumsum(np.asarray(reference, dtype=np.float64)) / n_ref
    cdf_site = np.cumsum(np.asarray(site, dtype=np.float64)) / n_site
    statistic = float(np.max(np.abs(cdf_ref - cdf_site)))
    effective_n = n_ref * n_site / (n_ref + n_site)
    p_value = kolmogorov_sf(math.sqrt(effective_n) * statistic)
    return KsResult(statistic=statistic, p_value=p_value, n_reference=n_ref, n_site=n_site)


def chi_square_homogeneity(
    reference: Mapping[str, int], site: Mapping[str, int]
) -> ChiSquareResult:
    """Pearson chi-square test that two samples share one categorical distribution."""
    categories = sorted(
        c for c in set(reference) | set(site) if reference.get(c, 0) + site.get(c, 0) > 0
    )
    if len(categories) < 2:
        raise InsufficientDataError("chi-square needs at least two observed categories")
    table = np.array(
        [[reference.get(c, 0) for c in categories], [site.get(c, 0) for c in categories]],
        dtype=np.float64,
    )
    row = table.sum(axis=1)
    if (row == 0).any():
        raise InsufficientDataError("chi-square needs non-empty reference and site counts")
    total = table.sum()
    expected = np.outer(row, table.sum(axis=0)) / total
    statistic = float(((table - expected) ** 2 / expected).sum())
    dof = len(categories) - 1
    return ChiSquareResult(
        statistic=statistic,
        dof=dof,
        p_value=chi2_sf(statistic, dof),
        cramers_v=math.sqrt(statistic / total),  # min(rows - 1, cols - 1) = 1
        min_expected=float(expected.min()),
        categories=tuple(categories),
    )


def black_box_shift_estimation(
    joint_counts: Sequence[Sequence[int]], site_predicted: Sequence[int]
) -> LabelShiftResult:
    """Estimate the site's class prior from its predicted-class counts.

    ``joint_counts[i][j]`` counts reference cases predicted ``i`` with true
    class ``j``. Negative importance weights (possible under sampling noise)
    are clipped to zero before the prior is renormalised.
    """
    joint = np.asarray(joint_counts, dtype=np.float64)
    predicted = np.asarray(site_predicted, dtype=np.float64)
    k = joint.shape[0]
    if joint.shape != (k, k) or predicted.shape != (k,) or k < 2:
        raise ValueError("BBSE needs a k x k joint table and k site predicted counts")
    if joint.sum() == 0 or predicted.sum() == 0:
        raise InsufficientDataError("BBSE needs reference and site predictions")
    c = joint / joint.sum()
    mu = predicted / predicted.sum()
    reference_prior = c.sum(axis=0)
    if (reference_prior == 0).any():
        raise InsufficientDataError("BBSE needs every class present in the reference data")
    condition = float(np.linalg.cond(c))
    if not math.isfinite(condition) or condition > 1e12:
        raise InsufficientDataError("reference confusion matrix is singular; BBSE is undefined")
    weights = np.linalg.solve(c, mu)
    clipped = bool((weights < 0).any())
    weights = np.clip(weights, 0.0, None)
    site_prior = weights * reference_prior
    # C w = mu with C, mu >= 0 and sum(mu) = 1 forces at least one positive weight,
    # so the clipped prior always has positive mass.
    site_prior = site_prior / site_prior.sum()
    return LabelShiftResult(
        class_weights=tuple(float(w) for w in weights),
        reference_prior=tuple(float(p) for p in reference_prior),
        estimated_site_prior=tuple(float(q) for q in site_prior),
        condition_number=condition,
        clipped=clipped,
    )
