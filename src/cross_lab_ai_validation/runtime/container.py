"""Dependency container built once per process from Settings."""

from __future__ import annotations

from dataclasses import dataclass

import httpx
from agentic_core.audit import AuditLog, JsonlAuditSink
from agentic_core.policy import PolicyEngine, PolicySet
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from sqlalchemy.ext.asyncio import AsyncEngine

from cross_lab_ai_validation.application.service import AssuranceService
from cross_lab_ai_validation.config import Settings
from cross_lab_ai_validation.domain.ports import Clock
from cross_lab_ai_validation.infrastructure.database import (
    SqlAssuranceStore,
    SystemClock,
    create_schema,
    make_engine,
)
from cross_lab_ai_validation.integrations.identity_fabric import IdentityFabricClient
from cross_lab_ai_validation.observability.metrics import AssuranceMetrics
from cross_lab_ai_validation.security.authentication import Authenticator
from cross_lab_ai_validation.security.authorization import Authorizer
from cross_lab_ai_validation.security.jwks import KeyResolver, RemoteJwks
from cross_lab_ai_validation.security.text_guard import TextGuard


@dataclass
class Container:
    settings: Settings
    service: AssuranceService
    authenticator: Authenticator
    metrics: AssuranceMetrics
    engine: AsyncEngine
    http: httpx.AsyncClient
    audit: AuditLog

    async def aclose(self) -> None:
        await self.http.aclose()
        await self.engine.dispose()


def _audit_signer(settings: Settings) -> tuple[Ed25519PrivateKey, str]:
    if settings.audit_key_file is None:
        return Ed25519PrivateKey.generate(), "audit-ephemeral"
    key = serialization.load_pem_private_key(settings.audit_key_file.read_bytes(), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError("audit key must be Ed25519")
    return key, settings.audit_key_file.stem


async def build_container(
    settings: Settings,
    *,
    user_resolver: KeyResolver | None = None,
    agent_resolver: KeyResolver | None = None,
    clock: Clock | None = None,
    http: httpx.AsyncClient | None = None,
) -> Container:
    http = http or httpx.AsyncClient(timeout=httpx.Timeout(5.0))
    clock = clock or SystemClock()
    fabric = (
        IdentityFabricClient(settings.identity_fabric_url, client=http)
        if settings.identity_fabric_url
        else None
    )
    if agent_resolver is None and settings.agent_jwks_url:
        agent_resolver = RemoteJwks(settings.agent_jwks_url, client=http)
    authenticator = Authenticator(
        user_resolver=user_resolver or RemoteJwks(settings.oidc_jwks_url, client=http),
        user_issuer=settings.oidc_issuer,
        user_audience=settings.oidc_audience,
        roles_claim=settings.roles_claim,
        sites_claim=settings.sites_claim,
        fabric=fabric,
        agent_resolver=agent_resolver if settings.agent_issuer else None,
        agent_issuer=settings.agent_issuer,
        agent_audience=settings.agent_audience,
    )
    engine = make_engine(settings.database_url)
    await create_schema(engine)
    signer, key_id = _audit_signer(settings)
    audit = AuditLog(JsonlAuditSink(settings.audit_log_path), signer, key_id=key_id)
    metrics = AssuranceMetrics()
    service = AssuranceService(
        store=SqlAssuranceStore(engine),
        authorizer=Authorizer(
            PolicyEngine(PolicySet.from_file(settings.policy_file)),
            clock=clock,
            fabric=fabric,
            signature_max_age=settings.signature_max_age,
        ),
        audit=audit,
        clock=clock,
        text_guard=TextGuard(),
        min_aggregate_cases=settings.min_aggregate_cases,
        metrics=metrics,
    )
    return Container(
        settings=settings,
        service=service,
        authenticator=authenticator,
        metrics=metrics,
        engine=engine,
        http=http,
        audit=audit,
    )
