"""Assurance reports: site benchmark, population shift analysis and protocol validation.

Each builder validates the submitted aggregates against the registered model
specification, computes the statistics, and returns an immutable report.
Report digests (sha256 over canonical JSON) bind later steps to the exact
evidence they used: a validation run names the digests of the benchmark and
shift reports, and the medical director signs the validation's evidence
digest.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from agentic_core.domain.provenance import canonical_json, content_digest
from pydantic import BaseModel, ConfigDict

from cross_lab_ai_validation.domain.aggregates import (
    PerformanceAggregate,
    PopulationAggregate,
    ScoreHistogram,
    VerificationStudies,
)
from cross_lab_ai_validation.domain.errors import InsufficientDataError, InvalidAggregateError
from cross_lab_ai_validation.domain.models import (
    OTHER_CATEGORY,
    CategoricalFeature,
    ContinuousFeature,
    ModelVersion,
    ValidationProtocol,
)
from cross_lab_ai_validation.evaluation.classification import (
    BootstrapInterval,
    Calibration,
    DiagnosticMetrics,
    Proportion,
    auroc,
    auroc_bootstrap,
    calibration,
    confusion_at_threshold,
    diagnostic_metrics,
)
from cross_lab_ai_validation.evaluation.method_verification import (
    AgreementResult,
    LinearityResult,
    PrecisionResult,
    agreement,
    linearity,
    precision,
)
from cross_lab_ai_validation.evaluation.shift import (
    ChiSquareResult,
    KsResult,
    LabelShiftResult,
    black_box_shift_estimation,
    chi_square_homogeneity,
    ks_binned,
    psi,
)

Severity = Literal["none", "moderate", "major"]
_RANK: dict[str, int] = {"none": 0, "moderate": 1, "major": 2}


def digest_of(document: BaseModel, *, exclude: set[str] | None = None) -> str:
    return content_digest(canonical_json(document.model_dump(mode="json", exclude=exclude)))


# --------------------------------------------------------------------- inputs


def check_histogram(model: ModelVersion, histogram: ScoreHistogram, label: str) -> None:
    if histogram.bins != model.score_bins:
        raise InvalidAggregateError(
            f"{label}: {histogram.bins} score bins, model {model.key} defines {model.score_bins}"
        )


def check_performance(model: ModelVersion, aggregate: PerformanceAggregate, min_n: int) -> None:
    check_histogram(model, aggregate.overall, "overall")
    if aggregate.overall.n < min_n:
        raise InvalidAggregateError(
            f"aggregate covers {aggregate.overall.n} cases; at least {min_n} are required"
        )
    for key, histogram in aggregate.subgroups.items():
        attribute = key.split("=", 1)[0]
        if attribute not in model.subgroup_attributes:
            raise InvalidAggregateError(f"subgroup attribute {attribute!r} is not registered")
        check_histogram(model, histogram, f"subgroup {key}")
        if histogram.n > aggregate.overall.n:
            raise InvalidAggregateError(f"subgroup {key} is larger than the overall population")


def check_population(model: ModelVersion, aggregate: PopulationAggregate, min_n: int) -> None:
    if aggregate.n < min_n:
        raise InvalidAggregateError(
            f"aggregate covers {aggregate.n} cases; at least {min_n} are required"
        )
    if len(aggregate.score_counts) != model.score_bins:
        raise InvalidAggregateError("score_counts must have one entry per model score bin")
    for feature in model.features:
        if isinstance(feature, ContinuousFeature):
            counts = aggregate.continuous.get(feature.name)
            if counts is None or len(counts) != feature.bins:
                raise InvalidAggregateError(
                    f"continuous feature {feature.name!r} needs {feature.bins} bin counts"
                )
        else:
            levels = aggregate.categorical.get(feature.name)
            if levels is None:
                raise InvalidAggregateError(f"categorical feature {feature.name!r} is missing")
            unknown = set(levels) - set(feature.categories) - {OTHER_CATEGORY}
            if unknown:
                raise InvalidAggregateError(
                    f"feature {feature.name!r} has unregistered categories {sorted(unknown)}"
                )
    expected = {f.name for f in model.features}
    extra = (set(aggregate.continuous) | set(aggregate.categorical)) - expected
    if extra:
        raise InvalidAggregateError(f"features not registered for the model: {sorted(extra)}")


def bootstrap_seed(aggregate_digest: str) -> int:
    """Deterministic seed: identical evidence always yields identical intervals."""
    return int(aggregate_digest.removeprefix("sha256:")[:15], 16)


# ------------------------------------------------------------------ benchmark


class SubgroupPerformance(BaseModel):
    model_config = ConfigDict(frozen=True)

    key: str
    n: int
    positives: int
    reportable: bool
    sensitivity: Proportion
    specificity: Proportion
    auroc: float | None
    auroc_gap: float | None


class BenchmarkReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    report_id: str
    kind: Literal["benchmark"] = "benchmark"
    site_id: str
    model_id: str
    version: str
    created_at: datetime
    created_by: str
    aggregate_digest: str
    protocol_digest: str
    threshold: float
    n: int
    positives: int
    metrics: DiagnosticMetrics
    auroc: BootstrapInterval
    reference_auroc: float
    calibration: Calibration
    subgroups: tuple[SubgroupPerformance, ...]
    warnings: tuple[str, ...]

    @property
    def digest(self) -> str:
        return digest_of(self)


def build_benchmark(
    *,
    report_id: str,
    site_id: str,
    model: ModelVersion,
    aggregate: PerformanceAggregate,
    created_at: datetime,
    created_by: str,
    min_n: int,
) -> BenchmarkReport:
    check_performance(model, aggregate, min_n)
    protocol = model.protocol
    overall = aggregate.overall
    if overall.n_positive == 0 or overall.n_negative == 0:
        raise InsufficientDataError("benchmark needs positive and negative cases")
    aggregate_digest = digest_of(aggregate)
    metrics = diagnostic_metrics(
        confusion_at_threshold(overall, model.threshold_bin), protocol.confidence
    )
    interval = auroc_bootstrap(
        overall.positives,
        overall.negatives,
        iterations=protocol.bootstrap_iterations,
        confidence=protocol.confidence,
        seed=bootstrap_seed(aggregate_digest),
    )
    reference = model.reference.performance.overall
    reference_auroc = auroc(reference.positives, reference.negatives)

    warnings: list[str] = []
    if overall.n < protocol.min_cases:
        warnings.append(f"{overall.n} cases is below the protocol minimum of {protocol.min_cases}")
    if overall.n_positive < protocol.min_positives:
        warnings.append(
            f"{overall.n_positive} positives is below the protocol minimum of "
            f"{protocol.min_positives}"
        )
    subgroups: list[SubgroupPerformance] = []
    for key in sorted(aggregate.subgroups):
        histogram = aggregate.subgroups[key]
        sub_metrics = diagnostic_metrics(
            confusion_at_threshold(histogram, model.threshold_bin), protocol.confidence
        )
        has_both = histogram.n_positive > 0 and histogram.n_negative > 0
        sub_auroc = auroc(histogram.positives, histogram.negatives) if has_both else None
        reportable = histogram.n >= protocol.subgroup_min_cases and has_both
        if not reportable:
            warnings.append(f"subgroup {key} has too few cases for comparison ({histogram.n})")
        subgroups.append(
            SubgroupPerformance(
                key=key,
                n=histogram.n,
                positives=histogram.n_positive,
                reportable=reportable,
                sensitivity=sub_metrics.sensitivity,
                specificity=sub_metrics.specificity,
                auroc=sub_auroc,
                auroc_gap=interval.estimate - sub_auroc if sub_auroc is not None else None,
            )
        )
    return BenchmarkReport(
        report_id=report_id,
        site_id=site_id,
        model_id=model.model_id,
        version=model.version,
        created_at=created_at,
        created_by=created_by,
        aggregate_digest=aggregate_digest,
        protocol_digest=protocol.digest,
        threshold=model.decision_threshold,
        n=overall.n,
        positives=overall.n_positive,
        metrics=metrics,
        auroc=interval,
        reference_auroc=reference_auroc,
        calibration=calibration(overall, model.score_edges),
        subgroups=tuple(subgroups),
        warnings=tuple(warnings),
    )


# ---------------------------------------------------------------------- shift


class FeatureShift(BaseModel):
    model_config = ConfigDict(frozen=True)

    feature: str
    kind: Literal["continuous", "categorical", "score"]
    psi: float
    severity: Severity
    ks: KsResult | None = None
    chi_square: ChiSquareResult | None = None
    significant: bool


class ShiftReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    report_id: str
    kind: Literal["shift"] = "shift"
    site_id: str
    model_id: str
    version: str
    created_at: datetime
    created_by: str
    aggregate_digest: str
    n: int
    features: tuple[FeatureShift, ...]
    score: FeatureShift
    label_shift: LabelShiftResult | None
    reference_prevalence: float
    estimated_prevalence: float | None
    alpha: float
    bonferroni_alpha: float
    severity: Severity
    findings: tuple[str, ...]

    @property
    def digest(self) -> str:
        return digest_of(self)

    @property
    def max_psi(self) -> float:
        return max([self.score.psi, *(f.psi for f in self.features)])


def _severity(value: float, protocol: ValidationProtocol) -> Severity:
    if value >= protocol.psi_major:
        return "major"
    if value >= protocol.psi_moderate:
        return "moderate"
    return "none"


def build_shift_report(
    *,
    report_id: str,
    site_id: str,
    model: ModelVersion,
    population: PopulationAggregate,
    created_at: datetime,
    created_by: str,
    min_n: int,
) -> ShiftReport:
    check_population(model, population, min_n)
    protocol = model.protocol
    reference = model.reference.population
    tests = 1 + len(model.features)  # score distribution + one test per feature
    bonferroni = protocol.shift_alpha / tests

    features: list[FeatureShift] = []
    for feature in model.features:
        if isinstance(feature, ContinuousFeature):
            features.append(
                _binned(
                    name=feature.name,
                    kind="continuous",
                    reference=reference.continuous[feature.name],
                    site=population.continuous[feature.name],
                    protocol=protocol,
                    bonferroni=bonferroni,
                )
            )
        else:
            features.append(_categorical(feature, reference, population, protocol, bonferroni))
    score = _binned(
        name="score",
        kind="score",
        reference=reference.score_counts,
        site=population.score_counts,
        protocol=protocol,
        bonferroni=bonferroni,
    )

    findings: list[str] = []
    for item in [score, *features]:
        if item.severity != "none":
            findings.append(f"{item.feature}: PSI {item.psi:.3f} ({item.severity} shift)")
        elif item.significant:
            findings.append(f"{item.feature}: statistically significant but PSI below threshold")
    severity: Severity = max(
        [score.severity, *(f.severity for f in features)], key=lambda s: _RANK[s]
    )

    ref_perf = model.reference.performance.overall
    reference_prevalence = ref_perf.n_positive / ref_perf.n
    counts = confusion_at_threshold(ref_perf, model.threshold_bin)
    joint = [
        [counts.true_negative, counts.false_negative],
        [counts.false_positive, counts.true_positive],
    ]
    t = model.threshold_bin
    site_predicted = [sum(population.score_counts[:t]), sum(population.score_counts[t:])]
    label_shift: LabelShiftResult | None
    estimated: float | None
    try:
        label_shift = black_box_shift_estimation(joint, site_predicted)
        estimated = label_shift.estimated_site_prior[1]
    except InsufficientDataError as exc:
        label_shift, estimated = None, None
        findings.append(f"label shift not estimable: {exc.message}")
    if estimated is not None and abs(estimated - reference_prevalence) > (
        protocol.prevalence_shift_max
    ):
        findings.append(
            f"estimated prevalence {estimated:.3f} differs from reference "
            f"{reference_prevalence:.3f}; predictive values will differ"
        )
        if severity == "none":
            severity = "moderate"
    return ShiftReport(
        report_id=report_id,
        site_id=site_id,
        model_id=model.model_id,
        version=model.version,
        created_at=created_at,
        created_by=created_by,
        aggregate_digest=digest_of(population),
        n=population.n,
        features=tuple(features),
        score=score,
        label_shift=label_shift,
        reference_prevalence=reference_prevalence,
        estimated_prevalence=estimated,
        alpha=protocol.shift_alpha,
        bonferroni_alpha=bonferroni,
        severity=severity,
        findings=tuple(findings),
    )


def _binned(
    *,
    name: str,
    kind: Literal["continuous", "score"],
    reference: tuple[int, ...],
    site: tuple[int, ...],
    protocol: ValidationProtocol,
    bonferroni: float,
) -> FeatureShift:
    index = psi(reference, site).psi
    ks = ks_binned(reference, site)
    return FeatureShift(
        feature=name,
        kind=kind,
        psi=index,
        severity=_severity(index, protocol),
        ks=ks,
        significant=ks.p_value < bonferroni,
    )


def _categorical(
    feature: CategoricalFeature,
    reference: PopulationAggregate,
    population: PopulationAggregate,
    protocol: ValidationProtocol,
    bonferroni: float,
) -> FeatureShift:
    levels = [*feature.categories, OTHER_CATEGORY]
    ref_counts = reference.categorical[feature.name]
    site_counts = population.categorical[feature.name]
    index = psi([ref_counts.get(c, 0) for c in levels], [site_counts.get(c, 0) for c in levels]).psi
    try:
        test: ChiSquareResult | None = chi_square_homogeneity(ref_counts, site_counts)
    except InsufficientDataError:
        test = None
    return FeatureShift(
        feature=feature.name,
        kind="categorical",
        psi=index,
        severity=_severity(index, protocol),
        chi_square=test,
        significant=test is not None and test.p_value < bonferroni,
    )


# ----------------------------------------------------------------- validation


class CriterionResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    criterion: str
    observed: float | None
    threshold: float | None
    comparator: Literal[">=", "<=", "<", "is"]
    passed: bool
    detail: str


class ValidationRun(BaseModel):
    model_config = ConfigDict(frozen=True)

    report_id: str
    kind: Literal["validation"] = "validation"
    site_id: str
    model_id: str
    version: str
    created_at: datetime
    created_by: str
    protocol_name: str
    protocol_version: str
    protocol_digest: str
    benchmark_id: str
    benchmark_digest: str
    shift_id: str
    shift_digest: str
    agreement: AgreementResult | None
    precision: PrecisionResult | None
    reportable_range: LinearityResult | None
    criteria: tuple[CriterionResult, ...]
    verdict: Literal["pass", "fail"]
    evidence_digest: str = ""

    def sealed(self) -> ValidationRun:
        return self.model_copy(
            update={"evidence_digest": digest_of(self, exclude={"evidence_digest"})}
        )

    def verify_seal(self) -> bool:
        return self.evidence_digest == digest_of(self, exclude={"evidence_digest"})


def _at_least(name: str, observed: float | None, threshold: float, detail: str) -> CriterionResult:
    passed = observed is not None and observed >= threshold
    return CriterionResult(
        criterion=name,
        observed=observed,
        threshold=threshold,
        comparator=">=",
        passed=passed,
        detail=detail,
    )


def _at_most(name: str, observed: float | None, threshold: float, detail: str) -> CriterionResult:
    passed = observed is not None and observed <= threshold
    return CriterionResult(
        criterion=name,
        observed=observed,
        threshold=threshold,
        comparator="<=",
        passed=passed,
        detail=detail,
    )


def _missing(name: str) -> CriterionResult:
    return CriterionResult(
        criterion=name,
        observed=None,
        threshold=None,
        comparator="is",
        passed=False,
        detail="required by the protocol but no study was submitted",
    )


def _benchmark_criteria(protocol: ValidationProtocol, b: BenchmarkReport) -> list[CriterionResult]:
    m = b.metrics
    results = [
        _at_least("min_cases", b.n, protocol.min_cases, "cases in the site benchmark"),
        _at_least("min_positives", b.positives, protocol.min_positives, "positive cases"),
    ]
    for name, proportion, threshold in (
        ("sensitivity", m.sensitivity, protocol.sensitivity_min),
        ("specificity", m.specificity, protocol.specificity_min),
        ("ppv", m.ppv, protocol.ppv_min),
        ("npv", m.npv, protocol.npv_min),
    ):
        if threshold is not None:
            results.append(
                _at_least(
                    name,
                    proportion.estimate,
                    threshold,
                    f"point estimate at threshold {b.threshold}; 95% Wilson "
                    f"[{proportion.lower}, {proportion.upper}]",
                )
            )
    if protocol.auroc_lower_ci_min is not None:
        results.append(
            _at_least(
                "auroc_lower_ci",
                b.auroc.lower,
                protocol.auroc_lower_ci_min,
                f"AUROC {b.auroc.estimate:.4f}, {b.auroc.iterations} bootstrap replicates",
            )
        )
    if protocol.ece_max is not None:
        results.append(
            _at_most("ece", b.calibration.ece, protocol.ece_max, "expected calibration error")
        )
    reportable = [s for s in b.subgroups if s.reportable]
    if protocol.subgroup_auroc_gap_max is not None:
        gaps = [s.auroc_gap for s in reportable if s.auroc_gap is not None]
        results.append(
            _at_most(
                "subgroup_auroc_gap",
                max(gaps) if gaps else None,
                protocol.subgroup_auroc_gap_max,
                f"largest AUROC shortfall across {len(gaps)} reportable subgroups",
            )
        )
    if protocol.subgroup_sensitivity_min is not None:
        values = [s.sensitivity.estimate for s in reportable if s.sensitivity.estimate is not None]
        results.append(
            _at_least(
                "subgroup_sensitivity",
                min(values) if values else None,
                protocol.subgroup_sensitivity_min,
                f"lowest sensitivity across {len(values)} reportable subgroups",
            )
        )
    return results


def evaluate_protocol(
    *,
    report_id: str,
    model: ModelVersion,
    benchmark: BenchmarkReport,
    shift: ShiftReport,
    studies: VerificationStudies,
    created_at: datetime,
    created_by: str,
) -> ValidationRun:
    protocol = model.protocol
    criteria = _benchmark_criteria(protocol, benchmark)
    criteria.append(
        CriterionResult(
            criterion="population_shift",
            observed=shift.max_psi,
            threshold=protocol.psi_major,
            comparator="<",
            passed=shift.severity != "major",
            detail=f"largest PSI across score and features; severity {shift.severity}",
        )
    )

    agreement_result: AgreementResult | None = None
    if protocol.agreement is not None:
        if studies.agreement is None:
            criteria.append(_missing("accuracy_agreement"))
        else:
            agreement_result = agreement(studies.agreement, protocol.confidence)
            a = agreement_result
            criteria += [
                _at_least(
                    "accuracy_ppa",
                    a.positive_percent_agreement.estimate,
                    protocol.agreement.ppa_min,
                    f"positive percent agreement vs {a.comparator}",
                ),
                _at_least(
                    "accuracy_npa",
                    a.negative_percent_agreement.estimate,
                    protocol.agreement.npa_min,
                    f"negative percent agreement vs {a.comparator}",
                ),
                _at_least("accuracy_kappa", a.kappa, protocol.agreement.kappa_min, "Cohen kappa"),
            ]

    precision_result: PrecisionResult | None = None
    if protocol.precision is not None:
        if studies.precision is None:
            criteria.append(_missing("precision"))
        else:
            precision_result = precision(studies.precision, protocol.precision)
            worst = max(lvl.within_lab_cv_pct for lvl in precision_result.levels)
            criteria.append(
                CriterionResult(
                    criterion="precision",
                    observed=worst,
                    threshold=protocol.precision.within_lab_cv_pct,
                    comparator="<=",
                    passed=precision_result.passed,
                    detail="repeatability and within-lab SD at or below claim or its UVL",
                )
            )

    range_result: LinearityResult | None = None
    if protocol.reportable_range is not None:
        if studies.reportable_range is None:
            criteria.append(_missing("reportable_range"))
        else:
            range_result = linearity(studies.reportable_range, protocol.reportable_range)
            criteria.append(
                CriterionResult(
                    criterion="reportable_range",
                    observed=None,
                    threshold=None,
                    comparator="is",
                    passed=range_result.passed,
                    detail=(
                        f"verified [{range_result.verified_low}, {range_result.verified_high}] "
                        f"vs claimed [{range_result.claimed_low}, {range_result.claimed_high}]"
                    ),
                )
            )

    run = ValidationRun(
        report_id=report_id,
        site_id=benchmark.site_id,
        model_id=model.model_id,
        version=model.version,
        created_at=created_at,
        created_by=created_by,
        protocol_name=protocol.name,
        protocol_version=protocol.version,
        protocol_digest=protocol.digest,
        benchmark_id=benchmark.report_id,
        benchmark_digest=benchmark.digest,
        shift_id=shift.report_id,
        shift_digest=shift.digest,
        agreement=agreement_result,
        precision=precision_result,
        reportable_range=range_result,
        criteria=tuple(criteria),
        verdict="pass" if all(c.passed for c in criteria) else "fail",
    )
    return run.sealed()
