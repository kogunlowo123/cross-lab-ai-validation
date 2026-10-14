"""Synthetic data for tests. No real patients, sites or devices.

Scores are drawn first and labels are Bernoulli(score), so the synthetic
model is calibrated by construction; features depend on the label so shift
in a feature moves the score distribution the way it would in practice.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

import numpy as np

from cross_lab_ai_validation.domain.aggregates import (
    AgreementStudy,
    LinearityLevel,
    LinearityStudy,
    PrecisionLevel,
    PrecisionStudy,
    VerificationStudies,
)
from cross_lab_ai_validation.domain.models import (
    AgreementCriteria,
    CategoricalFeature,
    ContinuousFeature,
    ModelVersion,
    PrecisionCriteria,
    ReferenceProfile,
    ReportableRangeCriteria,
    Site,
    ValidationProtocol,
)
from cross_lab_ai_validation.federation.summarize import CaseRecord, SiteSummary, summarize

SCORE_EDGES = tuple(round(i * 0.05, 2) for i in range(21))
MODEL_ID = "sepsis-lab-risk"
VERSION = "1.2.0"
ARTIFACT = "sha256:" + "4" * 64


def synthetic_cases(
    n: int,
    *,
    seed: int,
    score_mean: float = -1.0,
    lactate_shift: float = 0.0,
    icu_share: float = 0.2,
    labeled: bool = True,
) -> list[CaseRecord]:
    rng = np.random.default_rng(seed)
    scores = 1.0 / (1.0 + np.exp(-rng.normal(score_mean, 1.6, n)))
    labels = rng.random(n) < scores
    lactate = rng.lognormal(0.4 + 0.5 * labels + lactate_shift, 0.4)
    wbc = np.clip(rng.normal(9 + 4 * labels, 3), 0.5, None)
    age = np.clip(rng.normal(58, 17, n), 18, 99)
    care = rng.choice(["emergency", "inpatient", "icu"], n, p=[0.5, 0.5 - icu_share, icu_share])
    sex = rng.choice(["F", "M"], n)
    return [
        CaseRecord(
            score=float(scores[i]),
            label=int(labels[i]) if labeled else None,
            features={
                "lactate_mmol_l": float(lactate[i]),
                "wbc_10e9_l": float(wbc[i]),
                "age_years": float(age[i]),
                "care_setting": str(care[i]),
            },
            subgroups={"sex": str(sex[i]), "age_band": "under_65" if age[i] < 65 else "65_plus"},
        )
        for i in range(n)
    ]


def protocol(**overrides: Any) -> ValidationProtocol:
    values: dict[str, Any] = {
        "name": "Sepsis lab risk site verification",
        "version": "1.0.0",
        "bootstrap_iterations": 500,
        "min_cases": 200,
        "min_positives": 40,
        "sensitivity_min": 0.50,
        "specificity_min": 0.80,
        "auroc_lower_ci_min": 0.75,
        "ece_max": 0.06,
        "subgroup_min_cases": 50,
        "subgroup_auroc_gap_max": 0.06,
        "subgroup_sensitivity_min": 0.45,
        "agreement": AgreementCriteria(ppa_min=0.9, npa_min=0.9, kappa_min=0.8),
        "precision": PrecisionCriteria(repeatability_cv_pct=3.0, within_lab_cv_pct=5.0),
        "reportable_range": ReportableRangeCriteria(
            claimed_low=0.05,
            claimed_high=0.95,
            allowable_nonlinearity_pct=5.0,
            allowable_bias_pct=8.0,
        ),
        "revalidation_interval_days": 365,
    }
    values.update(overrides)
    return ValidationProtocol(**values)


def features() -> tuple[ContinuousFeature | CategoricalFeature, ...]:
    return (
        ContinuousFeature(name="lactate_mmol_l", unit="mmol/L", edges=(1.0, 2.0, 3.0, 4.0)),
        ContinuousFeature(name="wbc_10e9_l", unit="10^9/L", edges=(4.0, 8.0, 11.0, 15.0, 20.0)),
        ContinuousFeature(name="age_years", unit="years", edges=(40.0, 65.0, 80.0)),
        CategoricalFeature(name="care_setting", categories=("emergency", "inpatient", "icu")),
    )


@lru_cache(maxsize=16)
def model_version(
    *, version: str = VERSION, reference_seed: int = 1, **protocol_overrides: Any
) -> ModelVersion:
    # The summarizer needs only the binning and feature specification, so the
    # reference aggregates are computed from an unvalidated partial model first.
    layout = ModelVersion.model_construct(
        score_edges=SCORE_EDGES, features=features(), subgroup_attributes=("sex", "age_band")
    )
    summary = summarize(layout, synthetic_cases(4000, seed=reference_seed))
    assert summary.performance is not None
    return ModelVersion(
        model_id=MODEL_ID,
        version=version,
        display_name="Sepsis laboratory risk score",
        intended_use=(
            "Estimate the probability of sepsis in adult emergency and inpatient encounters from "
            "routine laboratory results, to prioritise clinician review."
        ),
        manufacturer="Synthetic Diagnostics",
        artifact_digest=ARTIFACT,
        score_edges=SCORE_EDGES,
        decision_threshold=0.5,
        features=features(),
        subgroup_attributes=("sex", "age_band"),
        protocol=protocol(**protocol_overrides),
        reference=ReferenceProfile(
            source="synthetic development cohort",
            performance=summary.performance,
            population=summary.population,
        ),
    )


def site(site_id: str = "north-lab", *, clia: str = "99D9900001") -> Site:
    return Site(
        site_id=site_id,
        name=f"{site_id} core laboratory",
        clia_number=clia,
        accreditation="CAP",
        medical_director="Laboratory Medical Director",
        region="region-1",
    )


def passing_studies(seed: int = 3) -> VerificationStudies:
    rng = np.random.default_rng(seed)

    def level(material: str, mean: float) -> PrecisionLevel:
        day_effect = rng.normal(0, mean * 0.012, 5)
        days = tuple(
            tuple(float(mean + d + e) for e in rng.normal(0, mean * 0.015, 5)) for d in day_effect
        )
        return PrecisionLevel(material=material, days=days)

    expected = (0.05, 0.2, 0.4, 0.6, 0.8, 0.95)
    return VerificationStudies(
        agreement=AgreementStudy(
            both_positive=92,
            candidate_positive_comparator_negative=4,
            candidate_negative_comparator_positive=3,
            both_negative=151,
            comparator="Sepsis-3 adjudication by two clinicians",
        ),
        precision=PrecisionStudy(levels=(level("low control", 0.2), level("high control", 0.8))),
        reportable_range=LinearityStudy(
            levels=tuple(
                LinearityLevel(
                    expected=e,
                    measurements=(e * (1 + 0.01 * rng.normal()), e * (1 + 0.01 * rng.normal())),
                )
                for e in expected
            )
        ),
    )


@lru_cache(maxsize=32)
def site_summary(
    seed: int = 11,
    n: int = 1500,
    *,
    score_mean: float = -1.0,
    lactate_shift: float = 0.0,
    icu_share: float = 0.2,
) -> SiteSummary:
    cases = synthetic_cases(
        n, seed=seed, score_mean=score_mean, lactate_shift=lactate_shift, icu_share=icu_share
    )
    return summarize(model_version(), cases)


def shifted_summary() -> SiteSummary:
    """A site whose population is much sicker than the development cohort."""
    return site_summary(12, score_mean=-0.3, lactate_shift=0.6, icu_share=0.5)
