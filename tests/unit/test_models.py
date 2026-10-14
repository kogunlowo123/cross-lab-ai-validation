"""Domain model validation and state transitions."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from cross_lab_ai_validation.domain.aggregates import (
    LinearityLevel,
    LinearityStudy,
    PerformanceAggregate,
    PopulationAggregate,
    PrecisionLevel,
    ScoreHistogram,
)
from cross_lab_ai_validation.domain.errors import InvalidStateError
from cross_lab_ai_validation.domain.models import (
    ApprovalStatus,
    CategoricalFeature,
    ContinuousFeature,
    ElectronicSignature,
    ModelVersion,
    ReapprovalRequest,
    ReportableRangeCriteria,
    SignatureMeaning,
    Site,
)
from tests.factories import model_version, protocol, site

NOW = datetime(2026, 10, 14, 9, 0, tzinfo=UTC)


def signature(meaning: SignatureMeaning = SignatureMeaning.APPROVAL) -> ElectronicSignature:
    return ElectronicSignature(
        signer="director@lab.test",
        printed_name="Dr. Morgan Director",
        meaning=meaning,
        signed_at=NOW,
        evidence_digest="sha256:" + "a" * 64,
        statement="Reviewed and approved for clinical use.",
        authenticated_at=NOW - timedelta(minutes=1),
    )


def request() -> ReapprovalRequest:
    return ReapprovalRequest(
        request_id="reap-" + "1" * 32,
        site_id="north-lab",
        model_id="sepsis-lab-risk",
        version="1.2.0",
        validation_id="val-" + "2" * 32,
        evidence_digest="sha256:" + "a" * 64,
        justification="Annual revalidation",
        requested_by="scientist@lab.test",
        requested_by_kind="user",
        requested_at=NOW - timedelta(hours=1),
        policy_decision="require_approval",
        policy_rules=("approval-grant-needs-medical-director",),
        policy_digest="sha256:" + "b" * 64,
    )


def test_site_validation() -> None:
    assert site().clia_number == "99D9900001"
    with pytest.raises(ValidationError, match="CLIA"):
        Site(**{**site().model_dump(), "clia_number": "12345"})
    with pytest.raises(ValidationError, match="lowercase"):
        Site(**{**site().model_dump(), "site_id": "North Lab"})


def test_model_version_rules() -> None:
    model = model_version()
    assert model.key == "sepsis-lab-risk@1.2.0"
    assert model.score_bins == 20
    assert model.threshold_bin == 10
    base = model.model_dump()
    with pytest.raises(ValidationError, match="interior"):
        ModelVersion.model_validate({**base, "decision_threshold": 0.52})
    with pytest.raises(ValidationError, match=r"0\.0"):
        ModelVersion.model_validate({**base, "score_edges": (0.1, 0.5, 1.0)})
    with pytest.raises(ValidationError, match="increasing"):
        ModelVersion.model_validate({**base, "score_edges": (0.0, 0.6, 0.5, 1.0)})
    with pytest.raises(ValidationError, match="sha256"):
        ModelVersion.model_validate({**base, "artifact_digest": "md5:abc"})
    with pytest.raises(ValidationError, match="MAJOR"):
        ModelVersion.model_validate({**base, "version": "v1"})
    with pytest.raises(ValidationError, match="unique"):
        ModelVersion.model_validate({**base, "subgroup_attributes": ("sex", "sex")})
    with pytest.raises(ValidationError, match="unique"):
        ModelVersion.model_validate({**base, "features": (base["features"][0],) * 2})


def test_feature_and_protocol_validation() -> None:
    with pytest.raises(ValidationError, match="increasing"):
        ContinuousFeature(name="lactate", unit="mmol/L", edges=(2.0, 1.0))
    with pytest.raises(ValidationError, match="snake_case"):
        ContinuousFeature(name="Lactate", unit="mmol/L", edges=(1.0,))
    with pytest.raises(ValidationError, match="snake_case"):
        CategoricalFeature(name="Care Setting", categories=("a", "b"))
    with pytest.raises(ValidationError, match="unique"):
        CategoricalFeature(name="setting", categories=("a", "a"))
    with pytest.raises(ValidationError, match="psi_major"):
        protocol(psi_moderate=0.3, psi_major=0.2)
    with pytest.raises(ValidationError, match="min_positives"):
        protocol(min_cases=50, min_positives=50)
    with pytest.raises(ValidationError, match="MAJOR"):
        protocol(version="draft")
    with pytest.raises(ValidationError, match="claimed_high"):
        ReportableRangeCriteria(
            claimed_low=5, claimed_high=1, allowable_nonlinearity_pct=5, allowable_bias_pct=5
        )
    assert protocol().digest == protocol().digest
    assert protocol().digest != protocol(min_cases=300).digest


def test_aggregate_schema_rejects_inconsistent_or_extra_fields() -> None:
    good = {
        "positives": (1, 2),
        "negatives": (3, 4),
        "positive_score_sum": (0.2, 1.8),
        "negative_score_sum": (0.3, 2.5),
        "score_sq_sum": 3.0,
    }
    assert ScoreHistogram(**good).n == 10
    with pytest.raises(ValidationError, match="one entry per score bin"):
        ScoreHistogram(**{**good, "negatives": (1, 2, 3)})
    with pytest.raises(ValidationError, match="between 0 and the bin count"):
        ScoreHistogram(**{**good, "positive_score_sum": (5.0, 1.0)})
    with pytest.raises(ValidationError, match="Extra inputs"):
        ScoreHistogram(**good, patient_ids=["A1"])
    with pytest.raises(ValidationError, match="attribute"):
        PerformanceAggregate(
            overall=ScoreHistogram(**good), subgroups={"sex": ScoreHistogram(**good)}
        )
    with pytest.raises(ValidationError, match="sum to n"):
        PopulationAggregate(n=5, score_counts=(1, 1))
    with pytest.raises(ValidationError, match="continuous"):
        PopulationAggregate(n=2, score_counts=(1, 1), continuous={"age": (1, 0)})
    with pytest.raises(ValidationError, match="categorical"):
        PopulationAggregate(n=2, score_counts=(1, 1), categorical={"sex": {"F": 1}})
    with pytest.raises(ValidationError, match="balanced"):
        PrecisionLevel(material="qc", days=((1.0, 2.0), (1.0,)))
    with pytest.raises(ValidationError, match="increasing"):
        LinearityStudy(
            levels=tuple(LinearityLevel(expected=e, measurements=(e,)) for e in (1.0, 3.0, 2.0))
        )


def test_reapproval_decision_and_resulting_approval() -> None:
    pending = request()
    with pytest.raises(InvalidStateError, match="not been signed"):
        pending.approval(revalidation_interval=timedelta(days=365))
    approved = pending.decide(signature())
    with pytest.raises(InvalidStateError, match="already"):
        approved.decide(signature())
    approval = approved.approval(revalidation_interval=timedelta(days=365))
    assert approval.status is ApprovalStatus.APPROVED
    assert approval.expires_at == NOW + timedelta(days=365)
    assert approval.use_permitted(NOW + timedelta(days=10))
    assert approval.effective_status(NOW + timedelta(days=365)) == "expired"
    restricted = approval.restrict(at=NOW + timedelta(days=30), reason="major shift")
    assert restricted.status is ApprovalStatus.REVALIDATION_REQUIRED
    assert not restricted.use_permitted(NOW + timedelta(days=31))
    with pytest.raises(InvalidStateError):
        restricted.restrict(at=NOW, reason="again")

    rejected = request().decide(signature(SignatureMeaning.REJECTION))
    rejection = rejected.approval(revalidation_interval=timedelta(days=365))
    assert rejection.status is ApprovalStatus.REJECTED
    assert rejection.expires_at is None
    assert rejection.model_key == "sepsis-lab-risk@1.2.0"
