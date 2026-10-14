"""Diagnostic accuracy, discrimination and calibration from score histograms.

Scores are binned on the model's registered edges. Bin ``i`` covers
``[edges[i], edges[i+1])`` and the last bin is closed on the right. A case is
called positive when its score is at or above the decision threshold, which
is always a bin edge, so threshold metrics are exact.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
from numpy.typing import NDArray
from pydantic import BaseModel, ConfigDict

from cross_lab_ai_validation.domain.aggregates import ScoreHistogram
from cross_lab_ai_validation.domain.errors import InsufficientDataError
from cross_lab_ai_validation.evaluation.special import two_sided_z


class Proportion(BaseModel):
    """A binomial proportion with its Wilson score interval."""

    model_config = ConfigDict(frozen=True)

    estimate: float | None
    lower: float | None
    upper: float | None
    numerator: int
    denominator: int


class ConfusionCounts(BaseModel):
    model_config = ConfigDict(frozen=True)

    true_positive: int
    false_positive: int
    false_negative: int
    true_negative: int

    @property
    def n(self) -> int:
        return self.true_positive + self.false_positive + self.false_negative + self.true_negative


class DiagnosticMetrics(BaseModel):
    model_config = ConfigDict(frozen=True)

    counts: ConfusionCounts
    sensitivity: Proportion
    specificity: Proportion
    ppv: Proportion
    npv: Proportion
    accuracy: Proportion
    prevalence: float


class BootstrapInterval(BaseModel):
    model_config = ConfigDict(frozen=True)

    estimate: float
    lower: float
    upper: float
    confidence: float
    iterations: int
    seed: int
    method: str = "stratified percentile bootstrap"


class CalibrationBin(BaseModel):
    model_config = ConfigDict(frozen=True)

    lower: float
    upper: float
    n: int
    mean_score: float | None
    observed_rate: float | None


class Calibration(BaseModel):
    model_config = ConfigDict(frozen=True)

    ece: float
    mce: float
    brier: float
    bins: tuple[CalibrationBin, ...]


def wilson(successes: int, n: int, confidence: float = 0.95) -> Proportion:
    """Wilson score interval; undefined (None) when the denominator is zero."""
    if n < 0 or successes < 0 or successes > n:
        raise ValueError("require 0 <= successes <= n")
    if n == 0:
        return Proportion(estimate=None, lower=None, upper=None, numerator=0, denominator=0)
    z = two_sided_z(confidence)
    p = successes / n
    denom = 1.0 + z * z / n
    centre = (p + z * z / (2.0 * n)) / denom
    half = z * np.sqrt(p * (1.0 - p) / n + z * z / (4.0 * n * n)) / denom
    return Proportion(
        estimate=p,
        lower=max(0.0, float(centre - half)),
        upper=min(1.0, float(centre + half)),
        numerator=successes,
        denominator=n,
    )


def confusion_at_threshold(histogram: ScoreHistogram, threshold_bin: int) -> ConfusionCounts:
    """Counts with ``score >= edges[threshold_bin]`` called positive."""
    if not 0 < threshold_bin < histogram.bins:
        raise ValueError("threshold bin must be an interior edge index")
    tp = sum(histogram.positives[threshold_bin:])
    fp = sum(histogram.negatives[threshold_bin:])
    return ConfusionCounts(
        true_positive=tp,
        false_positive=fp,
        false_negative=histogram.n_positive - tp,
        true_negative=histogram.n_negative - fp,
    )


def diagnostic_metrics(counts: ConfusionCounts, confidence: float = 0.95) -> DiagnosticMetrics:
    tp, fp, fn, tn = (
        counts.true_positive,
        counts.false_positive,
        counts.false_negative,
        counts.true_negative,
    )
    n = counts.n
    return DiagnosticMetrics(
        counts=counts,
        sensitivity=wilson(tp, tp + fn, confidence),
        specificity=wilson(tn, tn + fp, confidence),
        ppv=wilson(tp, tp + fp, confidence),
        npv=wilson(tn, tn + fn, confidence),
        accuracy=wilson(tp + tn, n, confidence),
        prevalence=(tp + fn) / n if n else 0.0,
    )


def _auroc_matrix(pos: NDArray[np.float64], neg: NDArray[np.float64]) -> NDArray[np.float64]:
    """AUROC for each row of binned positive/negative counts (ties count one half)."""
    n_pos = pos.sum(axis=-1)
    n_neg = neg.sum(axis=-1)
    above = n_pos[..., None] - np.cumsum(pos, axis=-1)  # positives in strictly higher bins
    wins = (neg * (above + 0.5 * pos)).sum(axis=-1)
    result: NDArray[np.float64] = wins / (n_pos * n_neg)
    return result


def auroc(positives: Sequence[int], negatives: Sequence[int]) -> float:
    """Area under the ROC curve from binned counts; equals the Mann-Whitney U / (P*N)."""
    pos = np.asarray(positives, dtype=np.float64)
    neg = np.asarray(negatives, dtype=np.float64)
    if pos.shape != neg.shape:
        raise ValueError("positive and negative histograms must have the same bins")
    if pos.sum() == 0 or neg.sum() == 0:
        raise InsufficientDataError("AUROC needs at least one positive and one negative case")
    return float(_auroc_matrix(pos, neg))


def auroc_bootstrap(
    positives: Sequence[int],
    negatives: Sequence[int],
    *,
    iterations: int,
    confidence: float,
    seed: int,
) -> BootstrapInterval:
    """Stratified percentile bootstrap for AUROC.

    Resampling cases with replacement within each class is, for binned
    scores, exactly a multinomial draw of the bin counts with the observed
    bin proportions, so the bootstrap is computed from aggregates without
    any loss relative to case-level resampling.
    """
    estimate = auroc(positives, negatives)
    pos = np.asarray(positives, dtype=np.float64)
    neg = np.asarray(negatives, dtype=np.float64)
    rng = np.random.default_rng(seed)
    n_pos, n_neg = int(pos.sum()), int(neg.sum())
    pos_draws = rng.multinomial(n_pos, pos / n_pos, size=iterations).astype(np.float64)
    neg_draws = rng.multinomial(n_neg, neg / n_neg, size=iterations).astype(np.float64)
    replicates = _auroc_matrix(pos_draws, neg_draws)
    alpha = 1.0 - confidence
    lower, upper = np.quantile(replicates, [alpha / 2.0, 1.0 - alpha / 2.0])
    return BootstrapInterval(
        estimate=estimate,
        lower=float(lower),
        upper=float(upper),
        confidence=confidence,
        iterations=iterations,
        seed=seed,
    )


def calibration(histogram: ScoreHistogram, edges: Sequence[float]) -> Calibration:
    """Expected and maximum calibration error over the score bins, and the Brier score.

    ECE = sum_b (n_b / N) * |observed_rate_b - mean_score_b|. The Brier score
    is exact: (sum s^2 - 2 * sum_{y=1} s + P) / N.
    """
    if len(edges) != histogram.bins + 1:
        raise ValueError("edges must have one more entry than the histogram has bins")
    n = histogram.n
    if n == 0:
        raise InsufficientDataError("calibration needs at least one case")
    bins: list[CalibrationBin] = []
    ece = 0.0
    mce = 0.0
    for i in range(histogram.bins):
        count = histogram.positives[i] + histogram.negatives[i]
        if count == 0:
            bins.append(
                CalibrationBin(
                    lower=edges[i], upper=edges[i + 1], n=0, mean_score=None, observed_rate=None
                )
            )
            continue
        mean_score = (histogram.positive_score_sum[i] + histogram.negative_score_sum[i]) / count
        observed = histogram.positives[i] / count
        gap = abs(observed - mean_score)
        ece += count / n * gap
        mce = max(mce, gap)
        bins.append(
            CalibrationBin(
                lower=edges[i],
                upper=edges[i + 1],
                n=count,
                mean_score=mean_score,
                observed_rate=observed,
            )
        )
    brier = (
        histogram.score_sq_sum - 2.0 * sum(histogram.positive_score_sum) + histogram.n_positive
    ) / n
    return Calibration(ece=ece, mce=mce, brier=max(0.0, brier), bins=tuple(bins))
