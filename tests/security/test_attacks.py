"""Adversarial tests, one per threat in docs/security/threat-model.md."""

from __future__ import annotations

import base64
import json
import time
from typing import Any

import jwt
import pytest
from fastapi.testclient import TestClient

from cross_lab_ai_validation.security.authentication import UserRole
from tests.conftest import (
    SITE,
    USER_AUDIENCE,
    USER_ISSUER,
    FakeIdp,
    agent_headers,
    user_headers,
)
from tests.e2e.test_http_lifecycle import MCP_HEADERS, director, register
from tests.factories import model_version, passing_studies, site_summary

pytestmark = pytest.mark.security


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def validated(client: TestClient, user_idp: FakeIdp) -> dict[str, Any]:
    """Register, benchmark, analyse and validate as a scientist; return the validation run."""
    register(client, user_idp)
    model = model_version()
    summary = site_summary()
    assert summary.performance is not None
    who = user_headers(user_idp)
    ids = {"site_id": SITE, "model_id": model.model_id, "version": model.version}
    bench = client.post(
        "/v1/benchmarks",
        json={**ids, "aggregate": summary.performance.model_dump(mode="json")},
        headers=who,
    ).json()
    shift = client.post(
        "/v1/shift-analyses",
        json={**ids, "population": summary.population.model_dump(mode="json")},
        headers=who,
    ).json()
    run = client.post(
        "/v1/validations",
        json={
            "benchmark_id": bench["report_id"],
            "shift_id": shift["report"]["report_id"],
            "studies": passing_studies().model_dump(mode="json"),
        },
        headers=who,
    ).json()
    assert run["verdict"] == "pass"
    return dict(run)


def reapproval(client: TestClient, headers: dict[str, str], run: dict[str, Any]) -> Any:
    return client.post(
        "/v1/reapprovals",
        json={"validation_id": run["report_id"], "justification": "Go-live verification"},
        headers=headers,
    )


SIGN = {"meaning": "approval", "statement": "Reviewed the evidence and approve clinical use."}


def test_t1_forged_staff_token(client: TestClient, user_idp: FakeIdp) -> None:
    impostor = FakeIdp(USER_ISSUER, kid=user_idp.kid)
    headers = user_headers(impostor, UserRole.MODEL_REGISTRAR)
    response = client.get("/v1/sites", headers=headers)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_t2_alg_none_rejected(client: TestClient) -> None:
    def b64(value: dict[str, Any]) -> str:
        return base64.urlsafe_b64encode(json.dumps(value).encode()).rstrip(b"=").decode()

    now = int(time.time())
    token = (
        b64({"alg": "none", "kid": "staff-1"})
        + "."
        + b64(
            {
                "iss": USER_ISSUER,
                "aud": USER_AUDIENCE,
                "sub": "x",
                "iat": now,
                "exp": now + 60,
                "roles": [UserRole.MODEL_REGISTRAR],
            }
        )
        + "."
    )
    assert client.get("/v1/sites", headers=bearer(token)).status_code == 401


def test_t3_payload_tampering(client: TestClient, user_idp: FakeIdp) -> None:
    token = user_headers(user_idp, UserRole.AUDITOR)["Authorization"][7:]
    header, payload, signature = token.split(".")
    claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    claims["roles"] = [UserRole.MEDICAL_DIRECTOR, UserRole.MODEL_REGISTRAR]
    forged = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()
    assert client.get(
        "/v1/sites", headers=bearer(f"{header}.{forged}.{signature}")
    ).status_code == (401)


def test_t4_wrong_audience_and_expired_tokens(client: TestClient, user_idp: FakeIdp) -> None:
    other_app = user_idp.mint(sub="u", aud="another-application", roles=[UserRole.AUDITOR])
    assert client.get("/v1/sites", headers=bearer(other_app)).status_code == 401
    expired = user_idp.mint(sub="u", aud=USER_AUDIENCE, ttl=-120, roles=[UserRole.AUDITOR])
    assert client.get("/v1/sites", headers=bearer(expired)).status_code == 401
    hs256 = jwt.encode(
        {"iss": USER_ISSUER, "aud": USER_AUDIENCE, "sub": "u"},
        "a-shared-secret-of-at-least-32-bytes",
        algorithm="HS256",
    )
    assert client.get("/v1/sites", headers=bearer(hs256)).status_code == 401


def test_t5_agents_cannot_approve_clinical_use(
    client: TestClient, user_idp: FakeIdp, agent_idp: FakeIdp
) -> None:
    run = validated(client, user_idp)
    request = reapproval(client, agent_headers(agent_idp), run).json()
    widened = agent_headers(agent_idp, ("reapproval:sign", "*", f"site:access:{SITE}"))
    signed = client.post(
        f"/v1/reapprovals/{request['request_id']}/signature", json=SIGN, headers=widened
    )
    assert signed.status_code == 403
    tools = client.post(
        "/mcp/",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
        headers={**MCP_HEADERS, **widened},
    ).json()["result"]["tools"]
    assert not any("sign" in t["name"] or "approve" in t["name"] for t in tools)


def test_t6_agent_outside_site_scope(
    client: TestClient, user_idp: FakeIdp, agent_idp: FakeIdp
) -> None:
    register(client, user_idp)
    model = model_version()
    summary = site_summary()
    assert summary.performance is not None
    west = agent_headers(agent_idp, ("benchmark:run", "site:access:west-lab"))
    response = client.post(
        "/v1/benchmarks",
        json={
            "site_id": SITE,
            "model_id": model.model_id,
            "version": model.version,
            "aggregate": summary.performance.model_dump(mode="json"),
        },
        headers=west,
    )
    assert response.status_code == 403


def test_t7_record_level_data_cannot_be_smuggled(client: TestClient, user_idp: FakeIdp) -> None:
    register(client, user_idp)
    summary = site_summary()
    assert summary.performance is not None
    aggregate = summary.performance.model_dump(mode="json")
    aggregate["overall"]["patient_ids"] = ["SYN000001", "SYN000002"]
    response = client.post(
        "/v1/benchmarks",
        json={
            "site_id": SITE,
            "model_id": "sepsis-lab-risk",
            "version": "1.2.0",
            "aggregate": aggregate,
        },
        headers=user_headers(user_idp),
    )
    assert response.status_code == 422


def test_t8_phi_in_free_text_is_refused(client: TestClient, user_idp: FakeIdp) -> None:
    run = validated(client, user_idp)
    response = client.post(
        "/v1/reapprovals",
        json={
            "validation_id": run["report_id"],
            "justification": "Discussed case MRN: 7781234 DOB: 01/02/1960 with team",
        },
        headers=user_headers(user_idp),
    )
    assert response.status_code == 422
    assert response.json()["error"] == "unsafe_text"


def test_t9_two_person_rule_and_fresh_authentication(client: TestClient, user_idp: FakeIdp) -> None:
    run = validated(client, user_idp)
    md = director(user_idp)
    request = reapproval(client, md, run).json()
    self_signed = client.post(
        f"/v1/reapprovals/{request['request_id']}/signature", json=SIGN, headers=md
    )
    assert self_signed.status_code == 403
    stale = user_headers(
        user_idp, UserRole.MEDICAL_DIRECTOR, sub="second@lab.test", auth_age_s=3600
    )
    assert (
        client.post(
            f"/v1/reapprovals/{request['request_id']}/signature", json=SIGN, headers=stale
        ).status_code
        == 403
    )
    other_site = user_headers(
        user_idp, UserRole.MEDICAL_DIRECTOR, sub="west@lab.test", sites=("west-lab",)
    )
    assert (
        client.post(
            f"/v1/reapprovals/{request['request_id']}/signature", json=SIGN, headers=other_site
        ).status_code
        == 403
    )


def test_t10_audit_chain_has_no_credentials(
    client: TestClient, user_idp: FakeIdp, agent_idp: FakeIdp, settings: Any
) -> None:
    run = validated(client, user_idp)
    request = reapproval(client, agent_headers(agent_idp), run).json()
    client.post(
        f"/v1/reapprovals/{request['request_id']}/signature",
        json=SIGN,
        headers=director(user_idp),
    )
    audit = settings.audit_log_path.read_text(encoding="utf-8")
    assert "eyJ" not in audit  # no bearer tokens
    events = [json.loads(line) for line in audit.splitlines()]
    assert [e["sequence"] for e in events] == list(range(len(events)))
    assert any(e["action"] == "reapproval.sign" for e in events)
    assert all(e["signature"] for e in events)


def test_t11_mcp_dns_rebinding_protection(client: TestClient, agent_idp: FakeIdp) -> None:
    response = client.post(
        "/mcp/",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
        headers={**MCP_HEADERS, **agent_headers(agent_idp), "Host": "attacker.test"},
    )
    assert response.status_code == 421
    origin = client.post(
        "/mcp/",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
        headers={**MCP_HEADERS, **agent_headers(agent_idp), "Origin": "https://attacker.test"},
    )
    assert origin.status_code == 403


def test_t12_staff_role_boundaries(client: TestClient, user_idp: FakeIdp) -> None:
    run = validated(client, user_idp)
    auditor = user_headers(user_idp, UserRole.AUDITOR, sub="auditor@lab.test")
    assert reapproval(client, auditor, run).status_code == 403
    assert client.get(f"/v1/reports/{run['report_id']}", headers=auditor).status_code == 200
    outsider = user_headers(user_idp, sub="west@lab.test", sites=("west-lab",))
    assert client.get(f"/v1/reports/{run['report_id']}", headers=outsider).status_code == 403
