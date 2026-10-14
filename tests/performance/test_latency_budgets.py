"""Latency budgets for the statistical hot paths. Loose enough for shared CI runners."""

from __future__ import annotations

import time
from datetime import UTC, datetime

import pytest

from cross_lab_ai_validation.evaluation.classification import auroc_bootstrap
from cross_lab_ai_validation.evaluation.reports import build_benchmark, build_shift_report
from tests.factories import model_version, site_summary

pytestmark = pytest.mark.performance

NOW = datetime(2026, 10, 14, tzinfo=UTC)


def test_bootstrap_2000_iterations_budget() -> None:
    pos = [5, 9, 14, 20, 26, 31, 40, 52, 61, 70, 66, 58, 44, 30, 22, 15, 9, 6, 3, 1]
    neg = list(reversed(pos))
    started = time.perf_counter()
    for seed in range(20):
        auroc_bootstrap(pos, neg, iterations=2000, confidence=0.95, seed=seed)
    per_call_ms = (time.perf_counter() - started) * 1000 / 20
    assert per_call_ms < 100


def test_benchmark_report_budget() -> None:
    model = model_version(bootstrap_iterations=2000)
    summary = site_summary()
    assert summary.performance is not None
    started = time.perf_counter()
    for i in range(10):
        build_benchmark(
            report_id=f"bench-{i}",
            site_id="north-lab",
            model=model,
            aggregate=summary.performance,
            created_at=NOW,
            created_by="perf",
            min_n=20,
        )
    per_report_ms = (time.perf_counter() - started) * 1000 / 10
    assert per_report_ms < 400


def test_shift_report_budget() -> None:
    model = model_version()
    population = site_summary().population
    started = time.perf_counter()
    for i in range(50):
        build_shift_report(
            report_id=f"shift-{i}",
            site_id="north-lab",
            model=model,
            population=population,
            created_at=NOW,
            created_by="perf",
            min_n=20,
        )
    per_report_ms = (time.perf_counter() - started) * 1000 / 50
    assert per_report_ms < 50
