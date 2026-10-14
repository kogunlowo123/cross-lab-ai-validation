"""Identity fabric client wire formats, authentication and authorization combination."""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

import httpx
import pytest
from agentic_core.domain.classification import DataClassification, Decision
from agentic_core.policy import PolicyEngine, PolicySet
from pydantic import SecretStr

from cross_lab_ai_validation.domain.errors import (
    DependencyUnavailableError,
    ForbiddenError,
    UnauthorizedError,
    UnsafeTextError,
)
from cross_lab_ai_validation.integrations.identity_fabric import IdentityFabricClient
from cross_lab_ai_validation.security.authentication import Authenticator, UserRole
from cross_lab_ai_validation.security.authorization import Authorizer
from cross_lab_ai_validation.security.jwks import RemoteJwks, StaticJwks, unverified_issuer
from cross_lab_ai_validation.security.text_guard import TextGuard
from tests.conftest import (
    AGENT_AUDIENCE,
    AGENT_ISSUER,
    ROOT,
    SITE,
    USER_AUDIENCE,
    USER_ISSUER,
    FakeClock,
    FakeIdp,
    agent,
    staff,
    user_headers,
)

FABRIC = "https://identity.agents.test"


def fabric_handler(
    *, active: bool = True, decision: str = "allow", status: int = 200
) -> tuple[list[httpx.Request], httpx.MockTransport]:
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if status != 200:
            return httpx.Response(
                status, json={"error": "e", "error_description": "fabric says no"}
            )
        if request.url.path == "/oauth2/introspect":
            if not active:
                return httpx.Response(200, json={"active": False})
            return httpx.Response(
                200,
                json={
                    "active": True,
                    "sub": "spiffe://agents.test/agent/lab/site-validator",
                    "scope": f"benchmark:run site:access:{SITE}",
                    "act": {"sub": "spiffe://agents.test/agent/lab/orchestrator"},
                    "agent_status": "active",
                },
            )
        if request.url.path == "/v1/authorize":
            return httpx.Response(
                200,
                json={
                    "decision": decision,
                    "agent": "spiffe://agents.test/agent/lab/site-validator",
                    "action": "benchmark:run",
                    "resource": "site-model:x",
                    "matched_rules": ["allow-scoped-actions"],
                    "policy_version": "2026.10.0",
                    "policy_digest": "sha256:" + "c" * 64,
                    "reason": "explicit " + decision,
                    "delegation_chain": [],
                },
            )
        return httpx.Response(
            200,
            json={"access_token": "t", "token_type": "Bearer", "expires_in": 600, "scope": "a:b"},
        )

    return seen, httpx.MockTransport(handle)


async def test_fabric_client_wire_formats() -> None:
    seen, transport = fabric_handler()
    async with httpx.AsyncClient(transport=transport) as http:
        client = IdentityFabricClient(FABRIC + "/", client=http)
        token = await client.issue_token("spiffe://a/agent/b/c", "wl", scopes=["a:b"])
        assert token.access_token == "t"
        result = await client.introspect("agent-token")
        assert result.delegation_chain() == ("spiffe://agents.test/agent/lab/orchestrator",)
        decision = await client.authorize(
            "agent-token",
            action="benchmark:run",
            resource_type="site-model",
            resource_id="north-lab/m@1.0.0",
            classification=DataClassification.CONFIDENTIAL,
            attributes={"site_id": SITE},
            environment={},
        )
        assert decision.decision is Decision.ALLOW
    token_form = dict(httpx.QueryParams(seen[0].content.decode()))
    assert token_form == {
        "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
        "client_id": "spiffe://a/agent/b/c",
        "assertion": "wl",
        "scope": "a:b",
    }
    assert seen[1].url.path == "/oauth2/introspect"
    authorize = json.loads(seen[2].content)
    assert authorize["resource_classification"] == 2
    assert seen[2].headers["authorization"] == "Bearer agent-token"


@pytest.mark.parametrize(
    ("status", "error"),
    [(400, UnauthorizedError), (503, DependencyUnavailableError)],
)
async def test_fabric_client_token_errors(status: int, error: type[Exception]) -> None:
    _, transport = fabric_handler(status=status)
    async with httpx.AsyncClient(transport=transport) as http:
        with pytest.raises(error):
            await IdentityFabricClient(FABRIC, client=http).issue_token("a", "b")


async def test_fabric_client_authorize_and_introspect_errors() -> None:
    for status, error in ((401, UnauthorizedError), (403, ForbiddenError)):
        _, transport = fabric_handler(status=status)
        async with httpx.AsyncClient(transport=transport) as http:
            client = IdentityFabricClient(FABRIC, client=http)
            with pytest.raises(error):
                await client.authorize(
                    "t",
                    action="a",
                    resource_type="r",
                    resource_id="i",
                    classification=DataClassification.INTERNAL,
                    attributes={},
                    environment={},
                )
            with pytest.raises(DependencyUnavailableError):
                await client.introspect("t")

    def garbage(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not json")

    async with httpx.AsyncClient(transport=httpx.MockTransport(garbage)) as http:
        with pytest.raises(DependencyUnavailableError, match="malformed"):
            await IdentityFabricClient(FABRIC, client=http).introspect("t")

    def plain_error(_: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text="bad")

    async with httpx.AsyncClient(transport=httpx.MockTransport(plain_error)) as http:
        with pytest.raises(UnauthorizedError, match="HTTP 400"):
            await IdentityFabricClient(FABRIC, client=http).issue_token("a", "b")

    def list_error(_: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json=["bad"])

    async with httpx.AsyncClient(transport=httpx.MockTransport(list_error)) as http:
        with pytest.raises(UnauthorizedError, match="HTTP 400"):
            await IdentityFabricClient(FABRIC, client=http).issue_token("a", "b")


def authenticator(
    user_idp: FakeIdp, agent_idp: FakeIdp | None = None, fabric: IdentityFabricClient | None = None
) -> Authenticator:
    return Authenticator(
        user_resolver=StaticJwks(user_idp.jwks()),
        user_issuer=USER_ISSUER,
        user_audience=USER_AUDIENCE,
        fabric=fabric,
        agent_resolver=StaticJwks(agent_idp.jwks()) if agent_idp else None,
        agent_issuer=AGENT_ISSUER if agent_idp else None,
        agent_audience=AGENT_AUDIENCE if agent_idp else None,
    )


async def test_user_authentication(user_idp: FakeIdp) -> None:
    auth = authenticator(user_idp)
    who = await auth.authenticate(
        user_headers(user_idp, UserRole.MEDICAL_DIRECTOR, sites=("north-lab", "east-lab"))[
            "Authorization"
        ]
    )
    assert who.kind == "user"
    assert who.roles == {UserRole.MEDICAL_DIRECTOR}
    assert who.covers_site("east-lab")
    assert not who.covers_site("west-lab")
    assert who.authenticated_at is not None
    assert who.display_name == "Avery Scientist"
    for header in (None, "Basic abc", "Bearer ", "Bearer " + "x" * 9000):
        with pytest.raises(UnauthorizedError):
            await auth.authenticate(header)
    assert not auth.accepts_agents
    with pytest.raises(UnauthorizedError, match="not accepted"):
        await auth.authenticate("Bearer opaque-agent-token")
    minimal = user_idp.mint(sub="u", aud=USER_AUDIENCE, roles="a b", sites=7)
    plain = await auth.authenticate(f"Bearer {minimal}")
    assert plain.roles == {"a", "b"}
    assert plain.sites == frozenset()
    assert plain.display_name is None


async def test_local_agent_authentication(user_idp: FakeIdp, agent_idp: FakeIdp) -> None:
    auth = authenticator(user_idp, agent_idp)
    token = agent_idp.mint(
        sub="spiffe://agents.test/agent/lab/v",
        aud=AGENT_AUDIENCE,
        scope="benchmark:run site:access:*",
    )
    who = await auth.authenticate(f"Bearer {token}")
    assert who.kind == "agent"
    assert who.covers_site("any-lab")
    assert who.has_scope("benchmark:run")
    assert who.credential is not None
    assert "credential" not in who.model_dump()
    wrong_aud = agent_idp.mint(sub="s", aud="other", scope="x:y")
    with pytest.raises(UnauthorizedError):
        await auth.authenticate(f"Bearer {wrong_aud}")


async def test_fabric_agent_authentication(user_idp: FakeIdp) -> None:
    _, transport = fabric_handler()
    async with httpx.AsyncClient(transport=transport) as http:
        auth = authenticator(user_idp, fabric=IdentityFabricClient(FABRIC, client=http))
        who = await auth.authenticate("Bearer opaque-agent-token")
        assert who.sites == {SITE}
        assert who.delegation_chain == ("spiffe://agents.test/agent/lab/orchestrator",)
    _, inactive = fabric_handler(active=False)
    async with httpx.AsyncClient(transport=inactive) as http:
        auth = authenticator(user_idp, fabric=IdentityFabricClient(FABRIC, client=http))
        with pytest.raises(UnauthorizedError, match="not active"):
            await auth.authenticate("Bearer revoked-token")


def engine() -> PolicyEngine:
    return PolicyEngine(PolicySet.from_file(ROOT / "policies" / "assurance-authorization.yaml"))


async def test_authorizer_combines_fabric_most_restrictive(clock: FakeClock) -> None:
    for remote, expected in (
        ("allow", Decision.ALLOW),
        ("require_approval", Decision.REQUIRE_APPROVAL),
        ("deny", Decision.DENY),
    ):
        _, transport = fabric_handler(decision=remote)
        async with httpx.AsyncClient(transport=transport) as http:
            authz = Authorizer(
                engine(), clock=clock, fabric=IdentityFabricClient(FABRIC, client=http)
            )
            who = agent().model_copy(update={"credential": SecretStr("t")})
            outcome = await authz.authorize(who, "benchmark:run", site_id=SITE, model_key="m@1.0.0")
            assert outcome.decision is expected
            assert outcome.sources == ("local", "identity-fabric")
    # A local deny is never relaxed by the fabric.
    _, transport = fabric_handler(decision="allow")
    async with httpx.AsyncClient(transport=transport) as http:
        authz = Authorizer(engine(), clock=clock, fabric=IdentityFabricClient(FABRIC, client=http))
        who = agent().model_copy(update={"credential": SecretStr("t")})
        outcome = await authz.authorize(who, "reapproval:sign", site_id=SITE)
        assert outcome.decision is Decision.DENY
        assert "deny-agent-signature" in outcome.matched_rules


async def test_authorizer_signature_freshness(clock: FakeClock) -> None:
    authz = Authorizer(engine(), clock=clock, signature_max_age=timedelta(minutes=5))
    fresh = staff(clock, UserRole.MEDICAL_DIRECTOR)
    stale = staff(clock, UserRole.MEDICAL_DIRECTOR, auth_age_s=600)
    assert (await authz.authorize(fresh, "reapproval:sign", site_id=SITE)).decision is (
        Decision.ALLOW
    )
    denied = await authz.authorize(stale, "reapproval:sign", site_id=SITE)
    assert "deny-stale-signature-authentication" in denied.matched_rules
    no_time = fresh.model_copy(update={"authenticated_at": None})
    assert (await authz.authorize(no_time, "reapproval:sign", site_id=SITE)).decision is (
        Decision.DENY
    )
    assert authz.policy.policy.name == "assurance-authorization"
    grant = await authz.authorize(fresh, "approval:grant", site_id=SITE)
    assert grant.decision is Decision.REQUIRE_APPROVAL
    restrict = await authz.authorize(
        agent(), "approval:restrict", site_id=SITE, environment={"shift_severity": "moderate"}
    )
    assert restrict.decision is Decision.DENY


def test_text_guard() -> None:
    guard = TextGuard()
    assert guard.check("note", "Annual revalidation per protocol 1.0.0").startswith("Annual")
    for unsafe in ("call 555-201-3344", "SSN 123-45-6789", "key AKIAABCDEFGHIJKLMNOP"):
        with pytest.raises(UnsafeTextError):
            guard.check("note", unsafe)


async def test_remote_jwks_caching_and_failure(user_idp: FakeIdp) -> None:
    calls = {"n": 0}
    now = {"t": 0.0}

    def handler(_: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json=user_idp.jwks())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        jwks = RemoteJwks("https://login.lab.test/jwks", client=http, clock=lambda: now["t"])
        await jwks.key_for(user_idp.kid)
        await jwks.key_for(user_idp.kid)
        assert calls["n"] == 1
        with pytest.raises(UnauthorizedError, match="unknown"):
            await jwks.key_for("rotated")  # within min refresh interval -> no refetch
        now["t"] = 31.0
        with pytest.raises(UnauthorizedError, match="unknown"):
            await jwks.key_for("rotated")
        assert calls["n"] == 2

    def down(_: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    async with httpx.AsyncClient(transport=httpx.MockTransport(down)) as http:
        with pytest.raises(UnauthorizedError, match="cannot fetch"):
            await RemoteJwks("https://x/jwks", client=http).key_for("k")


def test_unverified_issuer_routing() -> None:
    assert unverified_issuer("not-a-jwt") is None
    assert unverified_issuer("a.!!!.c") is None
    assert unverified_issuer("eyJhbGciOiJub25lIn0.WzFd.") is None  # payload is a JSON list
    token: Any = FakeIdp(USER_ISSUER).mint(sub="s", aud="a")
    assert unverified_issuer(token) == USER_ISSUER
