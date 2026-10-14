"""Registry and governance model: sites, model versions, validation protocols, approvals."""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from enum import StrEnum
from itertools import pairwise
from typing import Annotated, Literal

from agentic_core.domain.provenance import canonical_json, content_digest
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from cross_lab_ai_validation.domain.aggregates import PerformanceAggregate, PopulationAggregate
from cross_lab_ai_validation.domain.errors import InvalidStateError

SLUG = re.compile(r"^[a-z0-9][a-z0-9\-_.]{1,62}[a-z0-9]$")
SEMVER = re.compile(r"^\d+\.\d+\.\d+$")
CLIA = re.compile(r"^\d{2}D\d{7}$")
DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
FEATURE = re.compile(r"^[a-z][a-z0-9_]{0,62}$")

Probability = Annotated[float, Field(ge=0.0, le=1.0)]


def _slug(value: str) -> str:
    if not SLUG.fullmatch(value):
        raise ValueError(f"{value!r} must be 3-64 lowercase letters, digits, '-', '_' or '.'")
    return value


def model_key(model_id: str, version: str) -> str:
    return f"{model_id}@{version}"


class Site(BaseModel):
    """A laboratory where a model is (or will be) used."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    site_id: str
    name: str = Field(min_length=3, max_length=200)
    clia_number: str = Field(description="CLIA certificate number, 10 characters (NNDNNNNNNN)")
    accreditation: Literal["CAP", "COLA", "TJC", "A2LA", "none"] = "CAP"
    medical_director: str = Field(min_length=3, max_length=200)
    region: str = Field(min_length=2, max_length=64)

    @field_validator("site_id")
    @classmethod
    def _site(cls, value: str) -> str:
        return _slug(value)

    @field_validator("clia_number")
    @classmethod
    def _clia(cls, value: str) -> str:
        if not CLIA.fullmatch(value):
            raise ValueError("CLIA number must be two digits, 'D', seven digits")
        return value


class ContinuousFeature(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["continuous"] = "continuous"
    name: str
    unit: str = Field(min_length=1, max_length=32)
    edges: tuple[float, ...] = Field(
        min_length=1,
        max_length=100,
        description="Interior bin edges; bins are (-inf,e0), [e0,e1), ..., [e_last,+inf)",
    )

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        if not FEATURE.fullmatch(value):
            raise ValueError(f"feature name {value!r} must be snake_case")
        return value

    @field_validator("edges")
    @classmethod
    def _increasing(cls, edges: tuple[float, ...]) -> tuple[float, ...]:
        if any(b <= a for a, b in pairwise(edges)):
            raise ValueError("edges must be strictly increasing")
        return edges

    @property
    def bins(self) -> int:
        return len(self.edges) + 1


class CategoricalFeature(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["categorical"] = "categorical"
    name: str
    categories: tuple[str, ...] = Field(min_length=2, max_length=100)

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        if not FEATURE.fullmatch(value):
            raise ValueError(f"feature name {value!r} must be snake_case")
        return value

    @field_validator("categories")
    @classmethod
    def _unique(cls, categories: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(categories)) != len(categories) or any(not c for c in categories):
            raise ValueError("categories must be unique and non-empty")
        return categories


OTHER_CATEGORY = "__other__"

Feature = Annotated[ContinuousFeature | CategoricalFeature, Field(discriminator="kind")]


class AgreementCriteria(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    ppa_min: Probability
    npa_min: Probability
    kappa_min: float = Field(ge=-1.0, le=1.0)


class PrecisionCriteria(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    repeatability_cv_pct: float = Field(gt=0, le=100, description="Claimed repeatability CV")
    within_lab_cv_pct: float = Field(gt=0, le=100, description="Claimed within-laboratory CV")
    alpha: float = Field(default=0.05, gt=0, lt=0.5)


class ReportableRangeCriteria(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    claimed_low: float
    claimed_high: float
    allowable_nonlinearity_pct: float = Field(gt=0, le=50)
    allowable_bias_pct: float = Field(gt=0, le=50)

    @model_validator(mode="after")
    def _range(self) -> ReportableRangeCriteria:
        if self.claimed_high <= self.claimed_low:
            raise ValueError("claimed_high must exceed claimed_low")
        return self


class ValidationProtocol(BaseModel):
    """Acceptance criteria a site must meet before a medical director may approve use."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(min_length=3, max_length=120)
    version: str
    confidence: float = Field(default=0.95, ge=0.8, le=0.999)
    bootstrap_iterations: int = Field(default=2000, ge=200, le=20000)
    min_cases: int = Field(default=200, ge=20)
    min_positives: int = Field(default=40, ge=5)
    sensitivity_min: Probability | None = None
    specificity_min: Probability | None = None
    ppv_min: Probability | None = None
    npv_min: Probability | None = None
    auroc_lower_ci_min: Probability | None = None
    ece_max: Probability | None = None
    subgroup_min_cases: int = Field(default=50, ge=10)
    subgroup_auroc_gap_max: Probability | None = None
    subgroup_sensitivity_min: Probability | None = None
    psi_moderate: float = Field(default=0.10, gt=0)
    psi_major: float = Field(default=0.25, gt=0)
    prevalence_shift_max: Probability = 0.10
    shift_alpha: float = Field(default=0.05, gt=0, lt=0.5)
    agreement: AgreementCriteria | None = None
    precision: PrecisionCriteria | None = None
    reportable_range: ReportableRangeCriteria | None = None
    revalidation_interval_days: int = Field(default=365, ge=30, le=1095)

    @field_validator("version")
    @classmethod
    def _version(cls, value: str) -> str:
        if not SEMVER.fullmatch(value):
            raise ValueError("protocol version must be MAJOR.MINOR.PATCH")
        return value

    @model_validator(mode="after")
    def _thresholds(self) -> ValidationProtocol:
        if self.psi_major <= self.psi_moderate:
            raise ValueError("psi_major must exceed psi_moderate")
        if self.min_positives >= self.min_cases:
            raise ValueError("min_positives must be below min_cases")
        return self

    @property
    def digest(self) -> str:
        return content_digest(canonical_json(self.model_dump(mode="json")))


class ReferenceProfile(BaseModel):
    """Aggregates from the development / pivotal validation population."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source: str = Field(min_length=3, max_length=200, description="Where the reference came from")
    performance: PerformanceAggregate
    population: PopulationAggregate


class ModelVersion(BaseModel):
    """An immutable, versioned AI model that sites validate before clinical use."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    model_id: str
    version: str
    display_name: str = Field(min_length=3, max_length=200)
    intended_use: str = Field(min_length=20, max_length=4000)
    manufacturer: str = Field(min_length=2, max_length=200)
    artifact_digest: str = Field(description="sha256 of the deployed model artifact")
    score_edges: tuple[float, ...] = Field(
        min_length=3,
        max_length=1001,
        description="Probability score bin edges from 0.0 to 1.0, strictly increasing",
    )
    decision_threshold: float = Field(gt=0.0, lt=1.0)
    features: tuple[Feature, ...] = Field(default=(), max_length=200)
    subgroup_attributes: tuple[str, ...] = Field(default=(), max_length=20)
    protocol: ValidationProtocol
    reference: ReferenceProfile

    @field_validator("model_id")
    @classmethod
    def _model(cls, value: str) -> str:
        return _slug(value)

    @field_validator("version")
    @classmethod
    def _version(cls, value: str) -> str:
        if not SEMVER.fullmatch(value):
            raise ValueError("model version must be MAJOR.MINOR.PATCH")
        return value

    @field_validator("artifact_digest")
    @classmethod
    def _digest(cls, value: str) -> str:
        if not DIGEST.fullmatch(value):
            raise ValueError("artifact_digest must be sha256:<64 lowercase hex>")
        return value

    @field_validator("subgroup_attributes")
    @classmethod
    def _subgroups(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        bad = [v for v in value if not FEATURE.fullmatch(v)]
        if bad or len(set(value)) != len(value):
            raise ValueError(f"subgroup attributes must be unique snake_case names: {bad}")
        return value

    @model_validator(mode="after")
    def _consistent(self) -> ModelVersion:
        edges = self.score_edges
        if edges[0] != 0.0 or edges[-1] != 1.0:
            raise ValueError("score_edges must start at 0.0 and end at 1.0")
        if any(b <= a for a, b in pairwise(edges)):
            raise ValueError("score_edges must be strictly increasing")
        if self.decision_threshold not in edges[1:-1]:
            raise ValueError("decision_threshold must be one of the interior score_edges")
        names = [f.name for f in self.features]
        if len(set(names)) != len(names):
            raise ValueError("feature names must be unique")
        return self

    @property
    def key(self) -> str:
        return model_key(self.model_id, self.version)

    @property
    def score_bins(self) -> int:
        return len(self.score_edges) - 1

    @property
    def threshold_bin(self) -> int:
        """Index of the first score bin classified positive (score >= threshold)."""
        return self.score_edges.index(self.decision_threshold)


class ApprovalStatus(StrEnum):
    APPROVED = "approved"  # medical director signed; clinical use permitted until expiry
    REVALIDATION_REQUIRED = "revalidation_required"  # major shift or protocol change
    REJECTED = "rejected"  # medical director declined


class SignatureMeaning(StrEnum):
    APPROVAL = "approval"
    REJECTION = "rejection"


class ElectronicSignature(BaseModel):
    """21 CFR 11.50 signature manifestation, linked to the signed evidence by digest."""

    model_config = ConfigDict(frozen=True)

    signer: str
    printed_name: str
    meaning: SignatureMeaning
    signed_at: datetime
    evidence_digest: str
    statement: str
    authenticated_at: datetime


class SiteApproval(BaseModel):
    """Whether a model version may be used clinically at a site."""

    model_config = ConfigDict(frozen=True)

    site_id: str
    model_id: str
    version: str
    status: ApprovalStatus
    validation_id: str
    reapproval_id: str
    signature: ElectronicSignature
    approved_at: datetime | None = None
    expires_at: datetime | None = None
    updated_at: datetime
    status_reason: str

    @property
    def model_key(self) -> str:
        return model_key(self.model_id, self.version)

    def effective_status(self, now: datetime) -> str:
        if (
            self.status is ApprovalStatus.APPROVED
            and self.expires_at is not None
            and now >= self.expires_at
        ):
            return "expired"
        return self.status.value

    def use_permitted(self, now: datetime) -> bool:
        return self.effective_status(now) == ApprovalStatus.APPROVED.value

    def restrict(self, *, at: datetime, reason: str) -> SiteApproval:
        if self.status is not ApprovalStatus.APPROVED:
            raise InvalidStateError(f"approval is {self.status}, not approved")
        return self.model_copy(
            update={
                "status": ApprovalStatus.REVALIDATION_REQUIRED,
                "updated_at": at,
                "status_reason": reason,
            }
        )


class ReapprovalStatus(StrEnum):
    PENDING = "pending_signoff"
    APPROVED = "approved"
    REJECTED = "rejected"


class ReapprovalRequest(BaseModel):
    """A validated model version awaiting the medical director's signature."""

    model_config = ConfigDict(frozen=True)

    request_id: str
    site_id: str
    model_id: str
    version: str
    validation_id: str
    evidence_digest: str
    justification: str
    requested_by: str
    requested_by_kind: Literal["user", "agent"]
    requested_at: datetime
    status: ReapprovalStatus = ReapprovalStatus.PENDING
    policy_decision: str
    policy_rules: tuple[str, ...]
    policy_digest: str
    signature: ElectronicSignature | None = None
    decided_at: datetime | None = None

    @property
    def model_key(self) -> str:
        return model_key(self.model_id, self.version)

    def decide(self, signature: ElectronicSignature) -> ReapprovalRequest:
        if self.status is not ReapprovalStatus.PENDING:
            raise InvalidStateError(f"reapproval {self.request_id} is already {self.status}")
        status = (
            ReapprovalStatus.APPROVED
            if signature.meaning is SignatureMeaning.APPROVAL
            else ReapprovalStatus.REJECTED
        )
        return self.model_copy(
            update={"status": status, "signature": signature, "decided_at": signature.signed_at}
        )

    def approval(self, *, revalidation_interval: timedelta) -> SiteApproval:
        if self.signature is None or self.decided_at is None:
            raise InvalidStateError("reapproval has not been signed")
        approved = self.status is ReapprovalStatus.APPROVED
        return SiteApproval(
            site_id=self.site_id,
            model_id=self.model_id,
            version=self.version,
            status=ApprovalStatus.APPROVED if approved else ApprovalStatus.REJECTED,
            validation_id=self.validation_id,
            reapproval_id=self.request_id,
            signature=self.signature,
            approved_at=self.decided_at if approved else None,
            expires_at=self.decided_at + revalidation_interval if approved else None,
            updated_at=self.decided_at,
            status_reason=self.signature.statement,
        )
