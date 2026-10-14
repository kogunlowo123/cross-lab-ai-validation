"""Full assurance lifecycle over HTTP, MCP and A2A, as staff and agents would use it."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from cross_lab_ai_validation.security.authentication import UserRole
from tests.conftest import SITE, FakeIdp, agent_headers, user_headers
from tests.factories import model_version, passing_studies, shifted_summary, site, site_summary

pytestmark = pytest.mark.e2e

MCP_HEADERS = {
    "Accept": "application/json, text/event-stream",
    "MCP-Protocol-Version": "2025-06-18",
}


def registrar(idp: FakeIdp) -> dict[str, str]:
    return user_headers(idp, UserRole.MODEL_REGISTRAR, sub="registrar@lab.test", sites=())


def director(idp: FakeIdp) -> dict[str, str]:
    return user_headers(
        idp, UserRole.MEDICAL_DIRECTOR, sub="director@lab.test", name="Dr. Morgan Director"
    )


def register(client: TestClient, idp: FakeIdp) -> None:
    created = client.post("/v1/sites", json=site().model_dump(mode="json"), headers=registrar(idp))
    assert created.status_code == 201, created.text
    model = client.post(
        "/v1/models", json=model_version().model_dump(mode="json"), headers=registrar(idp)
    )
    assert model.status_code == 201, model.text
    assert model.json()["protocol"] == "Sepsis lab risk site verification 1.0.0"


def mcp(client: TestClient, headers: dict[str, str], method: str, params: dict[str, Any]) -> Any:
    response = client.post(
        "/mcp/",
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
        headers={**MCP_HEADERS, **headers},
    )
    assert response.status_code == 200, response.text
    return response.json()


def a2a(client: TestClient, headers: dict[str, str], skill: str, data: dict[str, Any]) -> Any:
    response = client.post(
        "/a2a",
        json={
            "jsonrpc": "2.0",
            "id": "req-1",
            "method": "message/send",
            "params": {
                "message": {
                    "kind": "message",
                    "role": "user",
                    "messageId": "m-1",
                    "contextId": "ctx-1",
                    "parts": [
                        {"kind": "text", "text": "please run"},
                        {"kind": "data", "data": {"skill": skill, "input": data}},
                    ],
                }
            },
        },
        headers=headers,
    )
    return response


def test_full_lifecycle(client: TestClient, user_idp: FakeIdp, agent_idp: FakeIdp) -> None:
    register(client, user_idp)
    model = model_version()
    summary = site_summary()
    assert summary.performance is not None
    bot = agent_headers(agent_idp)

    # Site agent submits aggregates over REST.
    bench = client.post(
        "/v1/benchmarks",
        json={
            "site_id": SITE,
            "model_id": model.model_id,
            "version": model.version,
            "aggregate": summary.performance.model_dump(mode="json"),
        },
        headers=bot,
    )
    assert bench.status_code == 201, bench.text
    assert bench.json()["auroc"]["lower"] > 0.75

    # Shift analysis through the MCP tool.
    tools = mcp(client, bot, "tools/list", {})["result"]["tools"]
    names = {t["name"] for t in tools}
    assert names == {
        "list_site_approvals",
        "benchmark_site_performance",
        "detect_population_shift",
        "run_method_verification",
        "request_reapproval",
    }
    assert all("outputSchema" in t for t in tools)
    called = mcp(
        client,
        bot,
        "tools/call",
        {
            "name": "detect_population_shift",
            "arguments": {
                "request": {
                    "site_id": SITE,
                    "model_id": model.model_id,
                    "version": model.version,
                    "population": summary.population.model_dump(mode="json"),
                }
            },
        },
    )["result"]
    assert called["isError"] is False
    shift = called["structuredContent"]
    assert shift["report"]["severity"] == "none"

    # Scientist runs the validation protocol over REST.
    scientist = user_headers(user_idp)
    run = client.post(
        "/v1/validations",
        json={
            "benchmark_id": bench.json()["report_id"],
            "shift_id": shift["report"]["report_id"],
            "studies": passing_studies().model_dump(mode="json"),
        },
        headers=scientist,
    )
    assert run.status_code == 201, run.text
    assert run.json()["verdict"] == "pass"
    fetched = client.get(f"/v1/reports/{run.json()['report_id']}", headers=scientist)
    assert fetched.json()["evidence_digest"] == run.json()["evidence_digest"]

    # The agent asks for reapproval over A2A: the task waits for a human.
    asked = a2a(
        client,
        bot,
        "request_reapproval",
        {"validation_id": run.json()["report_id"], "justification": "Go-live verification done"},
    )
    assert asked.status_code == 200, asked.text
    task = asked.json()["result"]
    assert task["status"]["state"] == "input-required"
    assert task["contextId"] == "ctx-1"
    request = task["artifacts"][0]["parts"][0]["data"]
    assert request["status"] == "pending_signoff"

    pending = client.get("/v1/reapprovals", params={"status": "pending_signoff"}, headers=scientist)
    assert [r["request_id"] for r in pending.json()["items"]] == [request["request_id"]]

    # The medical director signs.
    signed = client.post(
        f"/v1/reapprovals/{request['request_id']}/signature",
        json={
            "meaning": "approval",
            "statement": "Reviewed validation evidence; approve clinical use at North lab.",
        },
        headers=director(user_idp),
    )
    assert signed.status_code == 200, signed.text
    assert signed.json()["approval"]["use_permitted"] is True
    assert signed.json()["request"]["signature"]["printed_name"] == "Dr. Morgan Director"
    one = client.get(f"/v1/reapprovals/{request['request_id']}", headers=scientist)
    assert one.json()["status"] == "approved"

    # Approved versions per site, read through MCP by the agent.
    approvals = mcp(
        client,
        bot,
        "tools/call",
        {"name": "list_site_approvals", "arguments": {"query": {"site_id": SITE}}},
    )["result"]["structuredContent"]
    assert approvals["items"][0]["status"] == "approved"

    # A sicker population arrives: the agent's shift analysis withdraws use.
    shifted = a2a(
        client,
        bot,
        "detect_population_shift",
        {
            "site_id": SITE,
            "model_id": model.model_id,
            "version": model.version,
            "population": shifted_summary().population.model_dump(mode="json"),
        },
    ).json()["result"]
    assert shifted["status"]["state"] == "completed"
    assert shifted["artifacts"][0]["parts"][0]["data"]["approval_restricted"] is True
    after = client.get(f"/v1/sites/{SITE}/approvals", headers=scientist).json()
    assert after["items"][0]["status"] == "revalidation_required"
    assert after["items"][0]["use_permitted"] is False

    metrics = client.get("/metrics").text
    assert 'clv_evaluations_total{kind="validation",outcome="pass"} 1.0' in metrics
    assert 'clv_reapproval_signatures_total{meaning="approval"} 1.0' in metrics
    assert "clv_approvals_restricted_total 1.0" in metrics
    assert (
        "clv_agent_protocol_calls_total"
        '{operation="request_reapproval",outcome="success",protocol="a2a"} 1.0'
    ) in metrics


def test_registry_reads(client: TestClient, user_idp: FakeIdp, agent_idp: FakeIdp) -> None:
    register(client, user_idp)
    auditor = user_headers(user_idp, UserRole.AUDITOR, sub="auditor@lab.test")
    sites = client.get("/v1/sites", headers=auditor)
    assert [s["site_id"] for s in sites.json()["items"]] == [SITE]
    models = client.get("/v1/models", headers=agent_headers(agent_idp))
    assert models.json()["items"][0]["model_id"] == "sepsis-lab-risk"
    detail = client.get("/v1/models/sepsis-lab-risk/1.2.0", headers=auditor)
    assert detail.json()["reference"]["source"] == "synthetic development cohort"
    assert client.get("/v1/models/sepsis-lab-risk/9.0.0", headers=auditor).status_code == 404


def test_agent_card_and_operations(client: TestClient) -> None:
    card = client.get("/.well-known/agent-card.json").json()
    assert card["url"] == "https://assurance.lab.test/a2a"
    assert card["protocolVersion"] == "0.3.0"
    assert {s["id"] for s in card["skills"]} >= {"request_reapproval", "detect_population_shift"}
    assert card["securitySchemes"]["agentIdentity"]["scheme"] == "bearer"
    assert card["securitySchemes"]["staffOidc"]["openIdConnectUrl"].startswith(
        "https://login.lab.test/tenant"
    )
    assert client.get("/healthz").json() == {"status": "ok"}
    assert client.get("/readyz").json() == {"status": "ready"}
    response = client.get("/healthz", headers={"X-Correlation-ID": "trace-123"})
    assert response.headers["x-correlation-id"] == "trace-123"
    assert response.headers["x-content-type-options"] == "nosniff"
    generated = client.get("/healthz", headers={"X-Correlation-ID": "x" * 200})
    assert generated.headers["x-correlation-id"] != "x" * 200


def test_a2a_protocol_errors(client: TestClient, agent_idp: FakeIdp) -> None:
    bot = agent_headers(agent_idp)
    assert client.post("/a2a", content=b"{", headers=bot).json()["error"]["code"] == -32700
    assert client.post("/a2a", json=[1], headers=bot).json()["error"]["code"] == -32600
    assert client.post("/a2a", json={"jsonrpc": "1.0"}, headers=bot).json()["error"]["code"] == (
        -32600
    )
    missing = client.post(
        "/a2a", json={"jsonrpc": "2.0", "id": 7, "method": "tasks/get"}, headers=bot
    ).json()
    assert missing["error"]["code"] == -32601
    assert missing["id"] == 7
    bad_params = client.post(
        "/a2a", json={"jsonrpc": "2.0", "id": 8, "method": "message/send", "params": {}}
    ).json()
    assert bad_params["error"]["code"] == -32602
    text_only = client.post(
        "/a2a",
        json={
            "jsonrpc": "2.0",
            "id": 9,
            "method": "message/send",
            "params": {
                "message": {
                    "role": "user",
                    "messageId": "m",
                    "parts": [{"kind": "text", "text": "hi"}],
                }
            },
        },
    ).json()
    assert "data part" in text_only["error"]["message"]
    unknown = a2a(client, bot, "approve_model", {}).json()
    assert unknown["error"]["code"] == -32602
    assert "request_reapproval" in unknown["error"]["data"]
    invalid = a2a(client, bot, "list_site_approvals", {"site": "x"}).json()
    assert invalid["error"]["message"].startswith("invalid skill input")
    anonymous = a2a(client, {}, "list_site_approvals", {"site_id": SITE})
    assert anonymous.status_code == 401
    assert anonymous.headers["www-authenticate"] == "Bearer"
    not_found = a2a(client, bot, "list_site_approvals", {"site_id": "ghost-lab"}).json()["result"]
    assert not_found["status"]["state"] == "failed"
    denied = a2a(
        client,
        agent_headers(agent_idp, ("benchmark:run",)),
        "list_site_approvals",
        {"site_id": SITE},
    ).json()["result"]
    assert denied["status"]["state"] == "rejected"


def test_mcp_tool_errors(client: TestClient, agent_idp: FakeIdp) -> None:
    anonymous = mcp(
        client,
        {},
        "tools/call",
        {"name": "list_site_approvals", "arguments": {"query": {"site_id": SITE}}},
    )["result"]
    assert anonymous["isError"] is True
    assert "unauthorized" in anonymous["content"][0]["text"]
    invalid = mcp(
        client,
        agent_headers(agent_idp),
        "tools/call",
        {"name": "list_site_approvals", "arguments": {"query": {"site_id": "x"}}},
    )["result"]
    assert invalid["isError"] is True


def test_staff_errors(client: TestClient, user_idp: FakeIdp) -> None:
    register(client, user_idp)
    assert client.post("/v1/sites", json=site().model_dump(mode="json")).status_code == 401
    duplicate = client.post(
        "/v1/sites", json=site().model_dump(mode="json"), headers=registrar(user_idp)
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["error"] == "conflict"
    invalid = client.post("/v1/sites", json={"site_id": "x"}, headers=registrar(user_idp))
    assert invalid.status_code == 422
    assert invalid.json()["error"] == "invalid_request"
    forbidden = client.post(
        "/v1/models", json=model_version().model_dump(mode="json"), headers=user_headers(user_idp)
    )
    assert forbidden.status_code == 403
    assert forbidden.json()["error"] == "forbidden"


def test_every_mcp_tool(client: TestClient, user_idp: FakeIdp, agent_idp: FakeIdp) -> None:
    register(client, user_idp)
    model = model_version()
    summary = site_summary()
    assert summary.performance is not None
    bot = agent_headers(agent_idp)
    ids = {"site_id": SITE, "model_id": model.model_id, "version": model.version}

    def call(name: str, arguments: dict[str, Any]) -> Any:
        result = mcp(client, bot, "tools/call", {"name": name, "arguments": arguments})["result"]
        assert result["isError"] is False, result
        return result["structuredContent"]

    bench = call(
        "benchmark_site_performance",
        {"request": {**ids, "aggregate": summary.performance.model_dump(mode="json")}},
    )
    shift = call(
        "detect_population_shift",
        {"request": {**ids, "population": summary.population.model_dump(mode="json")}},
    )
    run = call(
        "run_method_verification",
        {
            "request": {
                "benchmark_id": bench["report_id"],
                "shift_id": shift["report"]["report_id"],
                "studies": passing_studies().model_dump(mode="json"),
            }
        },
    )
    assert run["verdict"] == "pass"
    request = call(
        "request_reapproval",
        {"request": {"validation_id": run["report_id"], "justification": "MCP-driven review"}},
    )
    assert request["status"] == "pending_signoff"
    assert request["requested_by_kind"] == "agent"
    approvals = call("list_site_approvals", {"query": {"site_id": SITE}})
    assert approvals["items"] == []  # pending sign-off grants nothing
