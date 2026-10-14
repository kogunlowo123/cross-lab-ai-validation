"""Response bodies for the HTTP API that are not domain documents themselves."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from cross_lab_ai_validation.application.service import ApprovalView
from cross_lab_ai_validation.domain.models import ModelVersion, ReapprovalRequest, Site


class SiteList(BaseModel):
    items: list[Site]
    limit: int
    offset: int


class ModelSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    model_id: str
    version: str
    display_name: str
    manufacturer: str
    artifact_digest: str
    decision_threshold: float
    protocol: str
    protocol_digest: str

    @classmethod
    def of(cls, model: ModelVersion) -> ModelSummary:
        return cls(
            model_id=model.model_id,
            version=model.version,
            display_name=model.display_name,
            manufacturer=model.manufacturer,
            artifact_digest=model.artifact_digest,
            decision_threshold=model.decision_threshold,
            protocol=f"{model.protocol.name} {model.protocol.version}",
            protocol_digest=model.protocol.digest,
        )


class ModelList(BaseModel):
    items: list[ModelSummary]
    limit: int
    offset: int


class ReapprovalList(BaseModel):
    items: list[ReapprovalRequest]
    limit: int
    offset: int


class SignatureResult(BaseModel):
    request: ReapprovalRequest
    approval: ApprovalView


class OAuthError(BaseModel):
    error: str
    error_description: str
