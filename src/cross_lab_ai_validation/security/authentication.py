"""Caller authentication for people (enterprise OIDC) and agents (agent identity fabric).

People present an OIDC access token from the enterprise IdP carrying roles,
the sites they are responsible for, their name and the time they last
authenticated. Agents present a credential issued by agent-identity-fabric:
it is introspected at the fabric when one is configured, otherwise verified
locally against the fabric's JWKS. With neither configured, agent
credentials are refused; there is no anonymous or allow-all mode.
"""

from __future__ import annotations

import fnmatch
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from cross_lab_ai_validation.domain.errors import UnauthorizedError
from cross_lab_ai_validation.integrations.identity_fabric import IdentityFabricClient
from cross_lab_ai_validation.security.jwks import KeyResolver, unverified_issuer, verify_jwt

SITE_SCOPE_PREFIX = "site:access:"


class UserRole:
    MODEL_REGISTRAR = "model-registrar"  # registers sites and model versions
    VALIDATION_SCIENTIST = "lab-validation-scientist"  # runs benchmarks and validations
    MEDICAL_DIRECTOR = "medical-director"  # signs reapprovals for their sites
    AUDITOR = "assurance-auditor"  # read-only


class Principal(BaseModel):
    model_config = ConfigDict(frozen=True)

    subject: str
    kind: Literal["user", "agent"]
    roles: frozenset[str] = frozenset()
    scopes: frozenset[str] = frozenset()
    sites: frozenset[str] = frozenset()
    display_name: str | None = None
    authenticated_at: datetime | None = None
    delegation_chain: tuple[str, ...] = ()
    credential: SecretStr | None = Field(default=None, exclude=True, repr=False)

    def covers_site(self, site_id: str) -> bool:
        return "*" in self.sites or site_id in self.sites

    def has_scope(self, action: str) -> bool:
        return any(fnmatch.fnmatchcase(action, scope) for scope in self.scopes)


def _strings(value: Any) -> frozenset[str]:
    if isinstance(value, str):
        return frozenset(value.split())
    if isinstance(value, list):
        return frozenset(v for v in value if isinstance(v, str))
    return frozenset()


def _timestamp(value: Any) -> datetime | None:
    if isinstance(value, int | float) and not isinstance(value, bool):
        return datetime.fromtimestamp(value, UTC)
    return None


def _agent(subject: str, scopes: frozenset[str], token: str, chain: tuple[str, ...]) -> Principal:
    sites = frozenset(
        s.removeprefix(SITE_SCOPE_PREFIX) for s in scopes if s.startswith(SITE_SCOPE_PREFIX)
    )
    return Principal(
        subject=subject,
        kind="agent",
        scopes=scopes,
        sites=sites,
        delegation_chain=chain,
        credential=SecretStr(token),
    )


class Authenticator:
    def __init__(
        self,
        *,
        user_resolver: KeyResolver,
        user_issuer: str,
        user_audience: str,
        roles_claim: str = "roles",
        sites_claim: str = "sites",
        fabric: IdentityFabricClient | None = None,
        agent_resolver: KeyResolver | None = None,
        agent_issuer: str | None = None,
        agent_audience: str | None = None,
    ) -> None:
        self._user_resolver = user_resolver
        self._user_issuer = user_issuer
        self._user_audience = user_audience
        self._roles_claim = roles_claim
        self._sites_claim = sites_claim
        self._fabric = fabric
        self._agent_resolver = agent_resolver
        self._agent_issuer = agent_issuer
        self._agent_audience = agent_audience

    @property
    def accepts_agents(self) -> bool:
        return self._fabric is not None or self._agent_resolver is not None

    async def authenticate(self, authorization: str | None) -> Principal:
        if not authorization or not authorization.lower().startswith("bearer "):
            raise UnauthorizedError("bearer token required")
        token = authorization[7:].strip()
        if not token or len(token) > 8192:
            raise UnauthorizedError("bearer token required")
        if unverified_issuer(token) == self._user_issuer:
            return await self._user(token)
        return await self._agent_principal(token)

    async def _user(self, token: str) -> Principal:
        claims = await verify_jwt(
            token, self._user_resolver, issuer=self._user_issuer, audience=self._user_audience
        )
        return Principal(
            subject=str(claims["sub"]),
            kind="user",
            roles=_strings(claims.get(self._roles_claim)),
            sites=_strings(claims.get(self._sites_claim)),
            display_name=claims["name"] if isinstance(claims.get("name"), str) else None,
            authenticated_at=_timestamp(claims.get("auth_time")) or _timestamp(claims.get("iat")),
        )

    async def _agent_principal(self, token: str) -> Principal:
        if self._fabric is not None:
            result = await self._fabric.introspect(token)
            if not result.active or not result.sub:
                raise UnauthorizedError("agent credential is not active")
            return _agent(result.sub, _strings(result.scope), token, result.delegation_chain())
        if self._agent_resolver is not None and self._agent_issuer and self._agent_audience:
            claims = await verify_jwt(
                token,
                self._agent_resolver,
                issuer=self._agent_issuer,
                audience=self._agent_audience,
                algorithms=("EdDSA", "ES256"),
            )
            return _agent(str(claims["sub"]), _strings(claims.get("scope")), token, ())
        raise UnauthorizedError("agent credentials are not accepted by this deployment")
