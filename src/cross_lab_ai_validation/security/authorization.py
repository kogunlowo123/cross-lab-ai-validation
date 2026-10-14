"""Authorization: the local assurance policy, combined with the identity fabric for agents.

Every request is evaluated against the local policy set (agentic-core
PolicyEngine). For agents, when an identity fabric is configured, the fabric's
``/v1/authorize`` decision is obtained as well and the most restrictive of the
two wins (deny > require_approval > allow). Domain rules such as "agents never
sign approvals" therefore hold regardless of the fabric's generic policy.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from agentic_core.domain.classification import DataClassification, Decision, RiskLevel
from agentic_core.policy import PolicyEngine, PolicyRequest, Resource, Subject
from pydantic import BaseModel, ConfigDict

from cross_lab_ai_validation.domain.ports import Clock
from cross_lab_ai_validation.integrations.identity_fabric import IdentityFabricClient
from cross_lab_ai_validation.security.authentication import Principal

_SEVERITY = {Decision.ALLOW: 0, Decision.REQUIRE_APPROVAL: 1, Decision.DENY: 2}

# Risk tier of each action, recorded with every decision.
ACTION_RISK: dict[str, RiskLevel] = {
    "registry:read": RiskLevel.LOW,
    "report:read": RiskLevel.LOW,
    "benchmark:run": RiskLevel.MODERATE,
    "shift:analyze": RiskLevel.MODERATE,
    "validation:run": RiskLevel.MODERATE,
    "reapproval:request": RiskLevel.HIGH,
    "approval:restrict": RiskLevel.HIGH,
    "site:register": RiskLevel.HIGH,
    "model:register": RiskLevel.HIGH,
    "approval:grant": RiskLevel.CRITICAL,
    "reapproval:sign": RiskLevel.CRITICAL,
}


class AuthorizationOutcome(BaseModel):
    model_config = ConfigDict(frozen=True)

    decision: Decision
    matched_rules: tuple[str, ...]
    policy_version: str
    policy_digest: str
    reason: str
    sources: tuple[str, ...]


class Authorizer:
    def __init__(
        self,
        policy: PolicyEngine,
        *,
        clock: Clock,
        fabric: IdentityFabricClient | None = None,
        signature_max_age: timedelta = timedelta(minutes=5),
    ) -> None:
        self._policy = policy
        self._clock = clock
        self._fabric = fabric
        self._max_age = signature_max_age

    @property
    def policy(self) -> PolicyEngine:
        return self._policy

    def _fresh(self, authenticated_at: datetime | None) -> bool:
        if authenticated_at is None:
            return False
        age = self._clock.now() - authenticated_at
        return timedelta(seconds=-30) <= age <= self._max_age

    async def authorize(
        self,
        principal: Principal,
        action: str,
        *,
        site_id: str | None = None,
        model_key: str | None = None,
        environment: dict[str, Any] | None = None,
    ) -> AuthorizationOutcome:
        environment = dict(environment or {})
        resource_type = "site-model" if site_id else "registry"
        resource_id = f"{site_id or '*'}/{model_key or '*'}"
        attributes = {"site_id": site_id or "", "model_key": model_key or ""}
        subject = Subject(
            id=principal.subject,
            kind=principal.kind,
            roles=principal.roles,
            attributes={
                "scopes": sorted(principal.scopes),
                "action_in_scope": principal.has_scope(action),
                "site_in_scope": site_id is None or principal.covers_site(site_id),
                "fresh_authentication": self._fresh(principal.authenticated_at),
                "delegated": bool(principal.delegation_chain),
            },
        )
        local = self._policy.evaluate(
            PolicyRequest(
                subject=subject,
                action=action,
                resource=Resource(
                    type=resource_type,
                    id=resource_id,
                    classification=DataClassification.CONFIDENTIAL,
                    attributes=attributes,
                ),
                risk=ACTION_RISK.get(action, RiskLevel.HIGH),
                environment=environment,
            )
        )
        outcome = AuthorizationOutcome(
            decision=local.decision,
            matched_rules=local.matched_rules,
            policy_version=local.policy_version,
            policy_digest=local.policy_digest,
            reason=local.reason,
            sources=("local",),
        )
        if principal.kind != "agent" or self._fabric is None or principal.credential is None:
            return outcome
        remote = await self._fabric.authorize(
            principal.credential.get_secret_value(),
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            classification=DataClassification.CONFIDENTIAL,
            attributes=attributes,
            environment=environment,
        )
        if _SEVERITY[remote.decision] > _SEVERITY[outcome.decision]:
            return AuthorizationOutcome(
                decision=remote.decision,
                matched_rules=tuple(f"fabric:{r}" for r in remote.matched_rules),
                policy_version=remote.policy_version,
                policy_digest=remote.policy_digest,
                reason=f"identity fabric: {remote.reason}",
                sources=("local", "identity-fabric"),
            )
        return outcome.model_copy(update={"sources": ("local", "identity-fabric")})
