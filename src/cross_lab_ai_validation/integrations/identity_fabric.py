"""Typed client for agent-identity-fabric.

Wire formats follow the fabric's published contract:

* ``POST /oauth2/token``: RFC 7523 jwt-bearer grant (form encoded) returning a
  short-lived agent credential.
* ``POST /oauth2/introspect``: RFC 7662 token introspection (form encoded).
* ``POST /v1/authorize``: policy decision for an action on a resource, called
  with the agent's own credential as the bearer token.

Transport errors and 5xx responses raise ``DependencyUnavailableError`` so
callers fail closed rather than proceeding without a decision.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import httpx
from agentic_core.domain.classification import DataClassification, Decision
from pydantic import BaseModel, ConfigDict, ValidationError

from cross_lab_ai_validation.domain.errors import (
    DependencyUnavailableError,
    ForbiddenError,
    UnauthorizedError,
)

JWT_BEARER = "urn:ietf:params:oauth:grant-type:jwt-bearer"


class FabricToken(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    access_token: str
    token_type: str
    expires_in: int
    scope: str


class FabricIntrospection(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    active: bool
    sub: str | None = None
    scope: str | None = None
    exp: int | None = None
    iat: int | None = None
    jti: str | None = None
    act: dict[str, Any] | None = None
    agent_status: str | None = None

    def delegation_chain(self) -> tuple[str, ...]:
        chain: list[str] = []
        current = self.act
        while isinstance(current, dict) and isinstance(current.get("sub"), str):
            chain.append(current["sub"])
            current = current.get("act")
        return tuple(chain)


class FabricDecision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    decision: Decision
    agent: str
    action: str
    resource: str
    matched_rules: tuple[str, ...]
    policy_version: str
    policy_digest: str
    reason: str


class IdentityFabricClient:
    def __init__(self, base_url: str, *, client: httpx.AsyncClient) -> None:
        self._base = base_url.rstrip("/")
        self._client = client

    async def _post(self, path: str, **kwargs: Any) -> httpx.Response:
        try:
            response = await self._client.post(f"{self._base}{path}", **kwargs)
        except httpx.HTTPError as exc:
            raise DependencyUnavailableError(f"identity fabric unreachable: {exc}") from exc
        if response.status_code >= 500:
            raise DependencyUnavailableError(
                f"identity fabric returned {response.status_code} for {path}"
            )
        return response

    @staticmethod
    def _error(response: httpx.Response) -> str:
        try:
            body = response.json()
        except ValueError:
            return f"HTTP {response.status_code}"
        if isinstance(body, dict):
            return str(body.get("error_description") or body.get("error") or body)
        return f"HTTP {response.status_code}"

    async def issue_token(
        self, agent_uri: str, workload_token: str, *, scopes: Sequence[str] | None = None
    ) -> FabricToken:
        form = {"grant_type": JWT_BEARER, "client_id": agent_uri, "assertion": workload_token}
        if scopes:
            form["scope"] = " ".join(scopes)
        response = await self._post("/oauth2/token", data=form)
        if response.status_code != 200:
            raise UnauthorizedError(f"credential request rejected: {self._error(response)}")
        return self._parse(FabricToken, response)

    async def introspect(self, token: str) -> FabricIntrospection:
        response = await self._post("/oauth2/introspect", data={"token": token})
        if response.status_code != 200:
            raise DependencyUnavailableError(
                f"introspection failed with HTTP {response.status_code}"
            )
        return self._parse(FabricIntrospection, response)

    async def authorize(
        self,
        token: str,
        *,
        action: str,
        resource_type: str,
        resource_id: str,
        classification: DataClassification,
        attributes: dict[str, Any],
        environment: dict[str, Any],
    ) -> FabricDecision:
        response = await self._post(
            "/v1/authorize",
            json={
                "action": action,
                "resource_type": resource_type,
                "resource_id": resource_id,
                "resource_classification": int(classification),
                "resource_attributes": attributes,
                "environment": environment,
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        if response.status_code == 401:
            raise UnauthorizedError(f"agent credential rejected: {self._error(response)}")
        if response.status_code != 200:
            raise ForbiddenError(f"authorization refused: {self._error(response)}")
        return self._parse(FabricDecision, response)

    @staticmethod
    def _parse[M: BaseModel](model: type[M], response: httpx.Response) -> M:
        try:
            return model.model_validate(response.json())
        except (ValueError, ValidationError) as exc:
            raise DependencyUnavailableError("identity fabric returned a malformed body") from exc
