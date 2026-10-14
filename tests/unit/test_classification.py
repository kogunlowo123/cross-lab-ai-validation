"""Diagnostic accuracy, AUROC and calibration against independent computations."""

from __future__ import annotations

import numpy as np
import pytest
from scipy import stats

from cross_lab_ai_validation.domain.aggregates import ScoreHistogram
from cross_lab_ai_validation.domain.errors import InsufficientDataError
from cross_lab_ai_validation.evaluation.classification import (
    ConfusionCounts,
    auroc,
    auroc_bootstrap,
    calibration,
    confusion_at_threshold,
    diagnostic_metrics,
    wilson,
)
from cross_lab_ai_validation.federation.summarize import CaseRecord, histogram
from tests.factories import SCORE_EDGES, synthetic_cases


@pytest.mark.parametrize(("k", "n"), [(8, 10), (81, 263), (0, 25), (25, 25), (412, 500)])
def test_wilson_matches_scipy(k: int, n: int) -> None:
    ci = stats.binomtest(k, n).proportion_ci(confidence_level=0.95, method="wilson")
    result = wilson(k, n)
    assert result.estimate == pytest.approx(k / n)
    assert result.lower == pytest.approx(ci.low, abs=1e-9)
    assert result.upper == pytest.approx(ci.high, abs=1e-9)


def test_wilson_edge_cases() -> None:
    empty = wilson(0, 0)
    assert empty.estimate is None
    assert empty.lower is None
    with pytest.raises(ValueError, match="successes"):
        wilson(5, 3)


def test_diagnostic_metrics_from_known_table() -> None:
    # Classic 2x2: TP 90, FP 20, FN 10, TN 880.
    m = diagnostic_metrics(
        ConfusionCounts(true_positive=90, false_positive=20, false_negative=10, true_negative=880)
    )
    assert m.sensitivity.estimate == pytest.approx(0.90)
    assert m.specificity.estimate == pytest.approx(880 / 900)
    assert m.ppv.estimate == pytest.approx(90 / 110)
    assert m.npv.estimate == pytest.approx(880 / 890)
    assert m.accuracy.estimate == pytest.approx(970 / 1000)
    assert m.prevalence == pytest.approx(0.10)


def unique_value_histogram(scores: np.ndarray, labels: np.ndarray) -> tuple[list[int], list[int]]:
    values = np.unique(scores)
    index = np.searchsorted(values, scores)
    pos = np.bincount(index[labels == 1], minlength=len(values))
    neg = np.bincount(index[labels == 0], minlength=len(values))
    return pos.tolist(), neg.tolist()


def test_auroc_equals_mann_whitney_u_with_ties() -> None:
    rng = np.random.default_rng(5)
    labels = (rng.random(400) < 0.35).astype(int)
    scores = np.round(np.clip(rng.normal(0.4 + 0.25 * labels, 0.2), 0, 1), 2)  # many ties
    pos, neg = unique_value_histogram(scores, labels)
    u = stats.mannwhitneyu(scores[labels == 1], scores[labels == 0]).statistic
    assert auroc(pos, neg) == pytest.approx(u / (labels.sum() * (len(labels) - labels.sum())))


def test_auroc_perfect_and_inverted() -> None:
    assert auroc([0, 0, 5], [5, 0, 0]) == 1.0
    assert auroc([5, 0, 0], [0, 0, 5]) == 0.0
    assert auroc([3, 3], [3, 3]) == 0.5
    with pytest.raises(InsufficientDataError):
        auroc([0, 0], [1, 2])
    with pytest.raises(ValueError, match="same bins"):
        auroc([1, 2], [1, 2, 3])


def test_bootstrap_is_reproducible_and_brackets_estimate() -> None:
    pos, neg = [2, 5, 9, 20, 30], [40, 30, 12, 6, 2]
    a = auroc_bootstrap(pos, neg, iterations=1000, confidence=0.95, seed=42)
    b = auroc_bootstrap(pos, neg, iterations=1000, confidence=0.95, seed=42)
    assert a == b
    assert a.lower < a.estimate < a.upper
    narrow = auroc_bootstrap(pos, neg, iterations=1000, confidence=0.80, seed=42)
    assert narrow.upper - narrow.lower < a.upper - a.lower


def test_bootstrap_matches_case_level_resampling_distribution() -> None:
    """The multinomial bootstrap has the same spread as resampling cases directly."""
    rng = np.random.default_rng(9)
    labels = (rng.random(300) < 0.4).astype(int)
    scores = np.round(np.clip(rng.normal(0.35 + 0.3 * labels, 0.2), 0, 1), 1)
    pos, neg = unique_value_histogram(scores, labels)
    fast = auroc_bootstrap(pos, neg, iterations=4000, confidence=0.95, seed=1)
    p_scores, n_scores = scores[labels == 1], scores[labels == 0]
    reps = []
    for _ in range(1500):
        ps = rng.choice(p_scores, len(p_scores))
        ns = rng.choice(n_scores, len(n_scores))
        reps.append(stats.mannwhitneyu(ps, ns).statistic / (len(ps) * len(ns)))
    lo, hi = np.quantile(reps, [0.025, 0.975])
    assert fast.lower == pytest.approx(lo, abs=0.015)
    assert fast.upper == pytest.approx(hi, abs=0.015)


def test_confusion_at_threshold_and_bounds() -> None:
    h = histogram(synthetic_cases(300, seed=2), SCORE_EDGES)
    counts = confusion_at_threshold(h, 10)
    assert counts.n == 300
    assert counts.true_positive == sum(h.positives[10:])
    with pytest.raises(ValueError, match="interior"):
        confusion_at_threshold(h, 0)


def test_calibration_matches_direct_computation() -> None:
    cases = synthetic_cases(2000, seed=4)
    h = histogram(cases, SCORE_EDGES)
    cal = calibration(h, SCORE_EDGES)
    scores = np.array([c.score for c in cases])
    labels = np.array([c.label for c in cases])
    assert cal.brier == pytest.approx(float(np.mean((scores - labels) ** 2)), rel=1e-9)
    idx = np.minimum(np.searchsorted(SCORE_EDGES, scores, side="right") - 1, 19)
    ece = sum(
        (idx == b).sum() / len(scores) * abs(labels[idx == b].mean() - scores[idx == b].mean())
        for b in range(20)
        if (idx == b).any()
    )
    assert cal.ece == pytest.approx(ece, rel=1e-9)
    assert cal.mce >= cal.ece
    assert cal.ece < 0.05  # synthetic scores are calibrated by construction


def test_calibration_handles_empty_bins_and_rejects_bad_input() -> None:
    h = ScoreHistogram(
        positives=(0, 3),
        negatives=(4, 0),
        positive_score_sum=(0.0, 2.7),
        negative_score_sum=(0.4, 0.0),
        score_sq_sum=0.04 * 4 + 0.81 * 3,
    )
    cal = calibration(h, (0.0, 0.5, 1.0))
    assert cal.bins[0].observed_rate == 0.0
    empty = ScoreHistogram(
        positives=(0, 0),
        negatives=(0, 0),
        positive_score_sum=(0.0, 0.0),
        negative_score_sum=(0.0, 0.0),
        score_sq_sum=0.0,
    )
    with pytest.raises(InsufficientDataError):
        calibration(empty, (0.0, 0.5, 1.0))
    with pytest.raises(ValueError, match="edges"):
        calibration(h, (0.0, 1.0))
    sparse = ScoreHistogram(
        positives=(0, 0, 2),
        negatives=(2, 0, 0),
        positive_score_sum=(0.0, 0.0, 1.8),
        negative_score_sum=(0.2, 0.0, 0.0),
        score_sq_sum=1.64,
    )
    assert calibration(sparse, (0.0, 0.3, 0.6, 1.0)).bins[1].n == 0


def test_histogram_requires_labels() -> None:
    from cross_lab_ai_validation.domain.errors import InvalidAggregateError

    with pytest.raises(InvalidAggregateError):
        histogram([CaseRecord(score=0.2)], SCORE_EDGES)
