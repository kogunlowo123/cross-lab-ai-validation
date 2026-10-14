"""Shared fixtures: in-process identity providers, a container on SQLite, and the app."""

from __future__ import annotations

import os
import time
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec, ed25519
from fastapi.testclient import TestClient

from cross_lab_ai_validation.api.app import create_app
from cross_lab_ai_validation.application.service import AssuranceService
from cross_lab_ai_validation.config import Settings
from cross_lab_ai_validation.infrastructure.database import make_engine, metadata
from cross_lab_ai_validation.runtime.container import Container, build_container
from cross_lab_ai_validation.security.authentication import Principal, UserRole
from cross_lab_ai_validation.security.jwks import StaticJwks

ROOT = Path(__file__).resolve().parents[1]
USER_ISSUER = "https://login.lab.test/tenant"
USER_AUDIENCE = "cross-lab-ai-validation"
AGENT_ISSUER = "https://identity.agents.test"
AGENT_AUDIENCE = "agent-platform"
SITE = "north-lab"
SITE_AGENT = "spiffe://agents.test/agent/lab-assurance/site-validator"
AGENT_SCOPES = (
    "registry:read",
    "report:read",
    "benchmark:run",
    "shift:analyze",
    "validation:run",
    "reapproval:request",
    f"site:access:{SITE}",
)


@dataclass
class FakeIdp:
    """Minimal token issuer: ES256 (staff IdP) or EdDSA (agent identity fabric)."""

    issuer: str
    kid: str = "k1"
    eddsa: bool = False
    key: Any = None

    def __post_init__(self) -> None:
        if self.key is None:
            self.key = (
                ed25519.Ed25519PrivateKey.generate()
                if self.eddsa
                else ec.generate_private_key(ec.SECP256R1())
            )

    @property
    def alg(self) -> str:
        return "EdDSA" if self.eddsa else "ES256"

    def jwks(self) -> dict[str, Any]:
        algorithm = jwt.algorithms.OKPAlgorithm if self.eddsa else jwt.algorithms.ECAlgorithm
        jwk = algorithm.to_jwk(self.key.public_key(), as_dict=True)
        jwk.update({"kid": self.kid, "alg": self.alg, "use": "sig"})
        return {"keys": [jwk]}

    def mint(self, *, sub: str, aud: str, ttl: int = 300, **extra: Any) -> str:
        now = int(time.time())
        claims = {"iss": self.issuer, "sub": sub, "aud": aud, "iat": now, "exp": now + ttl}
        claims.update(extra)
        return jwt.encode(claims, self.key, algorithm=self.alg, headers={"kid": self.kid})


@dataclass
class FakeClock:
    current: datetime = field(default_factory=lambda: datetime.now(UTC).replace(microsecond=0))

    def now(self) -> datetime:
        return self.current

    def advance(self, **delta: float) -> None:
        self.current += timedelta(**delta)


@pytest.fixture
def user_idp() -> FakeIdp:
    return FakeIdp(USER_ISSUER, kid="staff-1")


@pytest.fixture
def agent_idp() -> FakeIdp:
    return FakeIdp(AGENT_ISSUER, kid="2026-10", eddsa=True)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


def make_settings(tmp_path: Path, **overrides: Any) -> Settings:
    db = os.environ.get("CLV_TEST_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'clv.db'}")
    values: dict[str, Any] = {
        "environment": "test",
        "public_url": "https://assurance.lab.test",
        "database_url": db,
        "dev_ephemeral_audit_key": True,
        "audit_log_path": tmp_path / "audit.jsonl",
        "oidc_issuer": USER_ISSUER,
        "oidc_audience": USER_AUDIENCE,
        "oidc_jwks_url": "https://login.lab.test/jwks",
        "agent_issuer": AGENT_ISSUER,
        "agent_audience": AGENT_AUDIENCE,
        "agent_jwks_url": "https://identity.agents.test/.well-known/jwks.json",
        "policy_file": ROOT / "policies" / "assurance-authorization.yaml",
    }
    values.update(overrides)
    return Settings(**values)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return make_settings(tmp_path)


async def reset_database(settings: Settings) -> None:
    """Each test starts from an empty schema when running against a shared PostgreSQL."""
    if not settings.database_url.startswith("postgresql"):
        return
    engine = make_engine(settings.database_url)
    async with engine.begin() as conn:
        await conn.run_sync(metadata.drop_all)
    await engine.dispose()


@pytest.fixture
async def container(
    settings: Settings, user_idp: FakeIdp, agent_idp: FakeIdp, clock: FakeClock
) -> AsyncIterator[Container]:
    await reset_database(settings)
    built = await build_container(
        settings,
        user_resolver=StaticJwks(user_idp.jwks()),
        agent_resolver=StaticJwks(agent_idp.jwks()),
        clock=clock,
    )
    yield built
    await built.aclose()


@pytest.fixture
def service(container: Container) -> AssuranceService:
    return container.service


def staff(
    clock: FakeClock,
    *roles: str,
    sub: str = "scientist@lab.test",
    sites: tuple[str, ...] = (SITE,),
    name: str | None = "Avery Scientist",
    auth_age_s: int = 0,
) -> Principal:
    return Principal(
        subject=sub,
        kind="user",
        roles=frozenset(roles or (UserRole.VALIDATION_SCIENTIST,)),
        sites=frozenset(sites),
        display_name=name,
        authenticated_at=clock.now() - timedelta(seconds=auth_age_s),
    )


def registrar(clock: FakeClock) -> Principal:
    return staff(clock, UserRole.MODEL_REGISTRAR, sub="registrar@lab.test", sites=())


def director(clock: FakeClock, **kw: Any) -> Principal:
    kw.setdefault("sub", "director@lab.test")
    kw.setdefault("name", "Dr. Morgan Director")
    return staff(clock, UserRole.MEDICAL_DIRECTOR, **kw)


def agent(
    scopes: tuple[str, ...] = AGENT_SCOPES, *, sub: str = SITE_AGENT, chain: tuple[str, ...] = ()
) -> Principal:
    return Principal(
        subject=sub,
        kind="agent",
        scopes=frozenset(scopes),
        sites=frozenset(
            s.removeprefix("site:access:") for s in scopes if s.startswith("site:access:")
        ),
        delegation_chain=chain,
    )


def user_headers(
    idp: FakeIdp,
    *roles: str,
    sub: str = "scientist@lab.test",
    sites: tuple[str, ...] = (SITE,),
    name: str = "Avery Scientist",
    auth_age_s: int = 0,
) -> dict[str, str]:
    token = idp.mint(
        sub=sub,
        aud=USER_AUDIENCE,
        roles=list(roles or (UserRole.VALIDATION_SCIENTIST,)),
        sites=list(sites),
        name=name,
        auth_time=int(time.time()) - auth_age_s,
    )
    return {"Authorization": f"Bearer {token}"}


def agent_headers(
    idp: FakeIdp, scopes: tuple[str, ...] = AGENT_SCOPES, *, sub: str = SITE_AGENT
) -> dict[str, str]:
    token = idp.mint(sub=sub, aud=AGENT_AUDIENCE, scope=" ".join(scopes))
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def client(
    settings: Settings, user_idp: FakeIdp, agent_idp: FakeIdp, clock: FakeClock
) -> Iterator[TestClient]:
    """App whose container is built inside the app's own event loop by its lifespan."""

    async def factory() -> Container:
        await reset_database(settings)
        return await build_container(
            settings,
            user_resolver=StaticJwks(user_idp.jwks()),
            agent_resolver=StaticJwks(agent_idp.jwks()),
            clock=clock,
        )

    app = create_app(factory, mcp_allowed_hosts=("testserver",))
    with TestClient(app) as test_client:
        yield test_client
