"""Settings, production wiring, persistence edge cases and published contracts."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ec import SECP256R1, generate_private_key
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pydantic import ValidationError

from cross_lab_ai_validation.contracts import main
from cross_lab_ai_validation.domain.errors import ConflictError, InvalidStateError, NotFoundError
from cross_lab_ai_validation.infrastructure.database import (
    SqlAssuranceStore,
    create_schema,
    make_engine,
)
from cross_lab_ai_validation.runtime.container import build_container
from cross_lab_ai_validation.security.jwks import RemoteJwks
from tests.conftest import ROOT, make_settings
from tests.unit.test_models import request, signature


def write_pem(path: Path, key: object) -> None:
    path.write_bytes(
        key.private_bytes(  # type: ignore[attr-defined]
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )


def test_settings_validation(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="production"):
        make_settings(tmp_path, environment="production")
    with pytest.raises(ValidationError, match="AUDIT_KEY_FILE"):
        make_settings(tmp_path, dev_ephemeral_audit_key=False)
    with pytest.raises(ValidationError, match="together"):
        make_settings(tmp_path, agent_jwks_url=None)
    settings = make_settings(tmp_path)
    assert settings.public_base == "https://assurance.lab.test"
    assert settings.allowed_hosts == ["assurance.lab.test", "assurance.lab.test:*"]
    assert make_settings(tmp_path, public_url="http://127.0.0.1:8080").allowed_hosts == [
        "127.0.0.1:8080"
    ]
    assert make_settings(tmp_path, mcp_allowed_hosts=["a.test"]).allowed_hosts == ["a.test"]
    assert settings.signature_max_age.total_seconds() == 300


async def test_production_wiring(tmp_path: Path) -> None:
    audit_key = tmp_path / "audit-2026-10.pem"
    write_pem(audit_key, Ed25519PrivateKey.generate())
    settings = make_settings(
        tmp_path,
        environment="production",
        dev_ephemeral_audit_key=False,
        audit_key_file=audit_key,
        identity_fabric_url="https://identity.agents.test",
    )
    container = await build_container(settings)
    try:
        assert container.audit._key_id == "audit-2026-10"
        assert isinstance(container.authenticator._user_resolver, RemoteJwks)
        assert container.authenticator.accepts_agents
        assert container.authenticator._fabric is not None
    finally:
        await container.aclose()


async def test_rejects_non_ed25519_audit_key(tmp_path: Path) -> None:
    audit_key = tmp_path / "audit.pem"
    write_pem(audit_key, generate_private_key(SECP256R1()))
    settings = make_settings(tmp_path, dev_ephemeral_audit_key=False, audit_key_file=audit_key)
    with pytest.raises(ValueError, match="audit key must be Ed25519"):
        await build_container(settings)


async def test_store_edge_cases(tmp_path: Path) -> None:
    engine = make_engine(f"sqlite+aiosqlite:///{tmp_path / 's.db'}")
    await create_schema(engine)
    await create_schema(engine)  # idempotent
    store = SqlAssuranceStore(engine)
    pending = request()
    with pytest.raises(InvalidStateError, match="no decision"):
        await store.complete_reapproval(
            pending, pending.decide(signature()).approval(revalidation_interval=timedelta(days=1))
        )
    approval = pending.decide(signature()).approval(revalidation_interval=timedelta(days=30))
    with pytest.raises(NotFoundError):
        await store.update_approval(approval)
    await store.add_reapproval(pending)
    decided = pending.decide(signature())
    await store.complete_reapproval(decided, approval)
    with pytest.raises(ConflictError, match="no longer pending"):
        await store.complete_reapproval(decided, approval)
    # Upsert path: a second decision for the same site/model replaces the approval row.
    second = request().model_copy(update={"request_id": "reap-" + "9" * 32})
    await store.add_reapproval(second)
    await store.complete_reapproval(second.decide(signature()), approval)
    assert len(await store.list_approvals("north-lab")) == 1
    assert len(await store.list_reapprovals(status=None, site_id=None, limit=10, offset=0)) == 2
    await engine.dispose()


def test_committed_contracts_are_current() -> None:
    assert main(["--check"], root=ROOT) == 0


def test_export_and_check_round_trip(tmp_path: Path) -> None:
    assert main(["--check"], root=tmp_path) == 1
    assert main([], root=tmp_path) == 0
    assert main(["--check"], root=tmp_path) == 0
    assert (tmp_path / "schemas" / "performance-aggregate.schema.json").is_file()
    assert (tmp_path / "docs" / "api" / "openapi.json").is_file()
