"""Report builders: benchmark, shift and protocol evaluation on synthetic sites."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from cross_lab_ai_validation.domain.aggregates import (
    PopulationAggregate,
    ScoreHistogram,
    VerificationStudies,
)
from cross_lab_ai_validation.domain.errors import InsufficientDataError
from cross_lab_ai_validation.evaluation.reports import (
    BenchmarkReport,
    ShiftReport,
    bootstrap_seed,
    build_benchmark,
    build_shift_report,
    evaluate_protocol,
)
from tests.factories import model_version, passing_studies, shifted_summary, site_summary

NOW = datetime(2026, 10, 14, tzinfo=UTC)


def benchmark(**protocol: object) -> BenchmarkReport:
    model = model_version(**protocol)  # type: ignore[arg-type]
    summary = site_summary()
    assert summary.performance is not None
    return build_benchmark(
        report_id="bench-" + "0" * 32,
        site_id="north-lab",
        model=model,
        aggregate=summary.performance,
        created_at=NOW,
        created_by="tester",
        min_n=20,
    )


def shift(population: PopulationAggregate | None = None) -> ShiftReport:
    return build_shift_report(
        report_id="shift-" + "0" * 32,
        site_id="north-lab",
        model=model_version(),
        population=population or site_summary().population,
        created_at=NOW,
        created_by="tester",
        min_n=20,
    )


def test_benchmark_on_comparable_site() -> None:
    report = benchmark()
    assert report.n == 1500
    assert report.auroc.lower < report.auroc.estimate < report.auroc.upper
    assert abs(report.auroc.estimate - report.reference_auroc) < 0.03
    assert report.calibration.ece < 0.05
    assert {s.key for s in report.subgroups} == {
        "age_band=65_plus",
        "age_band=under_65",
        "sex=F",
        "sex=M",
    }
    assert all(s.reportable for s in report.subgroups)
    assert report.warnings == ()
    assert report.digest == benchmark().digest  # deterministic, including the bootstrap
    assert report.auroc.seed == bootstrap_seed(report.aggregate_digest)


def test_benchmark_warnings_and_insufficient_data() -> None:
    report = benchmark(min_cases=2000, min_positives=600, subgroup_min_cases=800)
    assert any("below the protocol minimum of 2000" in w for w in report.warnings)
    assert any("positives" in w for w in report.warnings)
    assert any("too few cases" in w for w in report.warnings)
    model = model_version()
    summary = site_summary()
    assert summary.performance is not None
    no_positives = summary.performance.overall.model_copy(
        update={
            "positives": (0,) * 20,
            "positive_score_sum": (0.0,) * 20,
        }
    )
    with pytest.raises(InsufficientDataError):
        build_benchmark(
            report_id="bench-x",
            site_id="north-lab",
            model=model,
            aggregate=summary.performance.model_copy(
                update={"overall": no_positives, "subgroups": {}}
            ),
            created_at=NOW,
            created_by="t",
            min_n=20,
        )


def test_single_class_subgroup_is_not_reportable() -> None:
    model = model_version()
    summary = site_summary()
    assert summary.performance is not None
    only_negatives = ScoreHistogram(
        positives=(0,) * 20,
        negatives=(60,) + (0,) * 19,
        positive_score_sum=(0.0,) * 20,
        negative_score_sum=(1.2,) + (0.0,) * 19,
        score_sq_sum=0.03,
    )
    report = build_benchmark(
        report_id="bench-y",
        site_id="north-lab",
        model=model,
        aggregate=summary.performance.model_copy(update={"subgroups": {"sex=X": only_negatives}}),
        created_at=NOW,
        created_by="t",
        min_n=20,
    )
    (group,) = report.subgroups
    assert not group.reportable
    assert group.auroc is None
    assert group.auroc_gap is None


def test_shift_none_for_comparable_population() -> None:
    report = shift()
    assert report.severity == "none"
    assert report.max_psi < 0.1
    assert report.estimated_prevalence == pytest.approx(report.reference_prevalence, abs=0.05)
    assert report.bonferroni_alpha == pytest.approx(0.05 / 5)
    categorical = next(f for f in report.features if f.kind == "categorical")
    assert categorical.chi_square is not None


def test_shift_major_for_sicker_population() -> None:
    report = shift(shifted_summary().population)
    assert report.severity == "major"
    by_name = {f.feature: f for f in report.features}
    assert by_name["lactate_mmol_l"].severity == "major"
    assert by_name["lactate_mmol_l"].significant
    assert by_name["care_setting"].severity == "major"
    assert by_name["age_years"].severity == "none"
    assert report.estimated_prevalence is not None
    assert report.estimated_prevalence > report.reference_prevalence + 0.1
    assert any("predictive values" in f for f in report.findings)


def test_prevalence_only_shift_is_moderate() -> None:
    # Same features, but scores (and so predicted positives) move up modestly.
    population = site_summary(seed=21, score_mean=-0.55).population
    report = shift(population)
    assert report.severity in {"moderate", "major"}
    assert report.estimated_prevalence is not None


def test_label_shift_not_estimable_is_reported() -> None:
    model = model_version()
    reference = model.reference.performance.overall
    # A reference where the classifier never predicts positive: C is singular.
    flat = reference.model_copy(
        update={
            "positives": (reference.n_positive,) + (0,) * 19,
            "negatives": (reference.n_negative,) + (0,) * 19,
            "positive_score_sum": (0.0,) * 20,
            "negative_score_sum": (0.0,) * 20,
            "score_sq_sum": 0.0,
        }
    )
    broken = model.model_copy(
        update={
            "reference": model.reference.model_copy(
                update={
                    "performance": model.reference.performance.model_copy(update={"overall": flat})
                }
            )
        }
    )
    report = build_shift_report(
        report_id="shift-z",
        site_id="north-lab",
        model=broken,
        population=site_summary().population,
        created_at=NOW,
        created_by="t",
        min_n=20,
    )
    assert report.label_shift is None
    assert any("not estimable" in f for f in report.findings)


def test_categorical_test_skipped_when_single_category() -> None:
    population = site_summary().population
    single = population.model_copy(update={"categorical": {"care_setting": {"icu": population.n}}})
    model = model_version()
    ref = model.reference.population
    model_single = model.model_copy(
        update={
            "reference": model.reference.model_copy(
                update={
                    "population": ref.model_copy(
                        update={"categorical": {"care_setting": {"icu": ref.n}}}
                    )
                }
            )
        }
    )
    report = build_shift_report(
        report_id="shift-c",
        site_id="north-lab",
        model=model_single,
        population=single,
        created_at=NOW,
        created_by="t",
        min_n=20,
    )
    feature = next(f for f in report.features if f.feature == "care_setting")
    assert feature.chi_square is None
    assert not feature.significant


def test_protocol_pass_and_criteria_detail() -> None:
    run = evaluate_protocol(
        report_id="val-" + "0" * 32,
        model=model_version(),
        benchmark=benchmark(),
        shift=shift(),
        studies=passing_studies(),
        created_at=NOW,
        created_by="tester",
    )
    assert run.verdict == "pass"
    assert run.verify_seal()
    names = [c.criterion for c in run.criteria]
    assert names[:2] == ["min_cases", "min_positives"]
    assert "reportable_range" in names
    tampered = run.model_copy(update={"verdict": "fail"})
    assert not tampered.verify_seal()


def test_protocol_optional_criteria_and_failures() -> None:
    model = model_version(
        ppv_min=0.99,
        npv_min=0.5,
        agreement=None,
        precision=None,
        reportable_range=None,
        subgroup_min_cases=5000,
    )
    run = evaluate_protocol(
        report_id="val-1",
        model=model,
        benchmark=benchmark(subgroup_min_cases=5000),
        shift=shift(),
        studies=VerificationStudies(),
        created_at=NOW,
        created_by="tester",
    )
    by_name = {c.criterion: c for c in run.criteria}
    assert not by_name["ppv"].passed
    assert by_name["npv"].passed
    assert not by_name["subgroup_auroc_gap"].passed  # no reportable subgroup
    assert by_name["subgroup_auroc_gap"].observed is None
    assert not by_name["subgroup_sensitivity"].passed
    assert "precision" not in by_name
    assert run.verdict == "fail"
