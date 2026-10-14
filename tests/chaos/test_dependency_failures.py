"""Fault injection: the platform fails closed and reports unreadiness when dependencies break."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from cross_lab_ai_validation.api.app import create_app
from cross_lab_ai_validation.application.service import AssuranceService, BenchmarkCommand
from cross_lab_ai_validation.infrastructure.database import make_engine
from cross_lab_ai_validation.runtime.container import Container, build_container
from cross_lab_ai_validation.security.jwks import RemoteJwks, StaticJwks
from tests.conftest import SITE, FakeClock, FakeIdp, make_settings, staff, user_headers
from tests.factories import model_version, site_summary
from tests.unit.test_service import seed

pytestmark = pytest.mark.chaos


def test_database_outage_flips_readiness(client: TestClient) -> None:
    container = client.app.state.container  # type: ignore[attr-defined]
    assert client.get("/readyz").status_code == 200
    healthy = container.engine
    container.engine = make_engine("sqlite+aiosqlite:////nonexistent/dir/db.sqlite")
    try:
        assert client.get("/readyz").status_code == 503
        assert client.get("/healthz").status_code == 200  # liveness unaffected
    finally:
        container.engine = healthy


@pytest.fixture
def fabric_down_client(tmp_path: Path, user_idp: FakeIdp, clock: FakeClock) -> Iterator[TestClient]:
    settings = make_settings(
        tmp_path,
        identity_fabric_url="https://identity.agents.test",
        agent_issuer=None,
        agent_audience=None,
        agent_jwks_url=None,
    )

    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    async def factory() -> Container:
        return await build_container(
            settings,
            user_resolver=StaticJwks(user_idp.jwks()),
            clock=clock,
            http=httpx.AsyncClient(transport=httpx.MockTransport(refuse)),
        )

    with TestClient(create_app(factory, mcp_allowed_hosts=("testserver",))) as client:
        yield client


def test_identity_fabric_outage_fails_closed(
    fabric_down_client: TestClient, user_idp: FakeIdp
) -> None:
    response = fabric_down_client.get("/v1/models", headers={"Authorization": "Bearer opaque"})
    assert response.status_code == 503
    assert response.json()["error"] == "temporarily_unavailable"
    # Staff authentication does not depend on the fabric.
    assert fabric_down_client.get("/v1/models", headers=user_headers(user_idp)).status_code == 200


async def test_idp_jwks_outage_rejects_staff(tmp_path: Path, user_idp: FakeIdp) -> None:
    def down(_: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    http = httpx.AsyncClient(transport=httpx.MockTransport(down))
    container = await build_container(make_settings(tmp_path), http=http)
    try:
        assert isinstance(container.authenticator._user_resolver, RemoteJwks)
        from cross_lab_ai_validation.domain.errors import UnauthorizedError

        with pytest.raises(UnauthorizedError, match="cannot fetch"):
            await container.authenticator.authenticate(user_headers(user_idp)["Authorization"])
    finally:
        await container.aclose()


async def test_audit_failure_blocks_the_result(service: AssuranceService, clock: FakeClock) -> None:
    """If evidence cannot be written, the evaluation must not be returned as if it were."""
    await seed(service, clock)
    summary = site_summary()
    assert summary.performance is not None
    model = model_version()

    async def broken_append(_: object) -> None:
        raise OSError("disk full")

    service._audit._sink.append = broken_append  # type: ignore[attr-defined,method-assign]
    with pytest.raises(OSError, match="disk full"):
        await service.benchmark(
            staff(clock),
            BenchmarkCommand(
                site_id=SITE,
                model_id=model.model_id,
                version=model.version,
                aggregate=summary.performance,
            ),
        )
