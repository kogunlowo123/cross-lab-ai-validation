"""Site-side summarisation, CSV extraction, CLI and the submission client."""

from __future__ import annotations

import argparse
import csv
import io
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from cross_lab_ai_validation.domain.errors import (
    DependencyUnavailableError,
    InsufficientDataError,
    InvalidAggregateError,
)
from cross_lab_ai_validation.federation import cli
from cross_lab_ai_validation.federation.client import CentralClient, CentralError
from cross_lab_ai_validation.federation.summarize import (
    CaseRecord,
    feature_bin,
    read_cases,
    score_bin,
    summarize,
)
from tests.factories import SCORE_EDGES, model_version, synthetic_cases

FABRIC = "https://identity.agents.test"
CENTRAL = "https://assurance.lab.test"


def write_csv(path: Path, cases: list[CaseRecord], *, extra: bool = True) -> None:
    fields = ["mrn", "score", "label", "lactate_mmol_l", "wbc_10e9_l", "age_years"]
    fields += ["care_setting", "sex", "age_band"]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields if extra else fields[1:])
        writer.writeheader()
        for i, case in enumerate(cases):
            row: dict[str, Any] = {
                "score": case.score,
                "label": "" if case.label is None else case.label,
                **case.features,
                **case.subgroups,
            }
            if extra:
                row["mrn"] = f"SYN{i:06d}"
            writer.writerow(row)


def test_binning_conventions() -> None:
    assert score_bin(0.0, SCORE_EDGES) == 0
    assert score_bin(0.05, SCORE_EDGES) == 1
    assert score_bin(1.0, SCORE_EDGES) == 19  # last bin is closed
    assert feature_bin(0.5, (1.0, 2.0)) == 0
    assert feature_bin(1.0, (1.0, 2.0)) == 1
    assert feature_bin(9.0, (1.0, 2.0)) == 2


def test_summary_counts_and_suppression() -> None:
    model = model_version()
    cases = synthetic_cases(300, seed=8)
    summary = summarize(model, cases, min_subgroup=10)
    assert summary.performance is not None
    assert summary.performance.overall.n == 300
    assert summary.population.n == 300
    assert sum(summary.population.categorical["care_setting"].values()) == 300
    assert summary.suppressed_subgroups == ()
    strict = summarize(model, cases, min_subgroup=200)
    assert strict.performance is not None
    assert "sex=F" in strict.suppressed_subgroups
    assert all(k not in strict.performance.subgroups for k in strict.suppressed_subgroups)


def test_summary_rules() -> None:
    model = model_version()
    unlabeled = summarize(model, synthetic_cases(50, seed=1, labeled=False))
    assert unlabeled.performance is None
    mixed = synthetic_cases(30, seed=1) + synthetic_cases(30, seed=2, labeled=False)
    with pytest.raises(InvalidAggregateError, match="partial labels"):
        summarize(model, mixed)
    with pytest.raises(InsufficientDataError):
        summarize(model, synthetic_cases(5, seed=1))
    missing = [c.model_copy(update={"features": {}}) for c in synthetic_cases(25, seed=1)]
    with pytest.raises(InvalidAggregateError, match="lacks feature"):
        summarize(model, missing)
    text = [
        c.model_copy(update={"features": {**c.features, "age_years": "old"}})
        for c in synthetic_cases(25, seed=1)
    ]
    with pytest.raises(InvalidAggregateError, match="numeric"):
        summarize(model, text)
    odd = [
        c.model_copy(update={"features": {**c.features, "care_setting": "hospice"}})
        for c in synthetic_cases(25, seed=1)
    ]
    assert summarize(model, odd).population.categorical["care_setting"] == {"__other__": 25}


def test_csv_extract_ignores_identifier_columns(tmp_path: Path) -> None:
    model = model_version()
    path = tmp_path / "cases.csv"
    write_csv(path, synthetic_cases(60, seed=9))
    records = read_cases(path, model)
    assert len(records) == 60
    dumped = json.dumps([r.model_dump() for r in records])
    assert "SYN0000" not in dumped  # the identifier column is never read
    bad = tmp_path / "bad.csv"
    bad.write_text("score,label\n0.5,1\n", encoding="utf-8")
    with pytest.raises(InvalidAggregateError, match="lacks columns"):
        read_cases(bad, model)
    broken = tmp_path / "broken.csv"
    write_csv(broken, synthetic_cases(3, seed=9), extra=False)
    broken.write_text(
        broken.read_text(encoding="utf-8").replace("\n0.", "\nnot-a-number,", 1), encoding="utf-8"
    )
    with pytest.raises(InvalidAggregateError, match="line 2"):
        read_cases(broken, model)


def test_cli_summarize(tmp_path: Path) -> None:
    model_file = tmp_path / "model.json"
    model_file.write_text(model_version().model_dump_json(), encoding="utf-8")
    cases = tmp_path / "cases.csv"
    write_csv(cases, synthetic_cases(80, seed=10))
    out = tmp_path / "aggregates.json"
    stream = io.StringIO()
    code = cli.main(
        ["summarize", "--model", str(model_file), "--cases", str(cases), "--out", str(out)],
        out=stream,
    )
    assert code == 0
    document = json.loads(out.read_text(encoding="utf-8"))
    assert document["population"]["n"] == 80
    assert "80 cases" in stream.getvalue()
    assert (
        cli.main(
            [
                "summarize",
                "--model",
                str(model_file),
                "--cases",
                str(tmp_path / "missing.csv"),
                "--out",
                str(out),
            ],
            out=stream,
        )
        == 1
    )


def central_transport(seen: list[httpx.Request], *, fail: int | None = None) -> httpx.MockTransport:
    model = model_version()

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/oauth2/token":
            form = dict(httpx.QueryParams(request.content.decode()))
            assert form["grant_type"] == "urn:ietf:params:oauth:grant-type:jwt-bearer"
            assert "site:access:north-lab" in form["scope"].split()
            return httpx.Response(
                200,
                json={
                    "access_token": "agent-credential",
                    "token_type": "Bearer",
                    "expires_in": 600,
                    "scope": form["scope"],
                },
            )
        assert request.headers["authorization"] == "Bearer agent-credential"
        if fail is not None:
            return httpx.Response(fail, json={"error": "x", "error_description": "refused"})
        if request.url.path.startswith("/v1/models/"):
            return httpx.Response(200, json=model.model_dump(mode="json"))
        body = json.loads(request.content)
        assert "aggregate" in body or "population" in body
        assert "mrn" not in request.content.decode()
        return httpx.Response(201, json={"report_id": request.url.path})

    return httpx.MockTransport(handler)


def submit_args(tmp_path: Path) -> argparse.Namespace:
    token = tmp_path / "token"
    token.write_text("workload.jwt.value\n", encoding="utf-8")
    cases = tmp_path / "cases.csv"
    write_csv(cases, synthetic_cases(120, seed=13))
    return argparse.Namespace(
        central=CENTRAL,
        fabric=FABRIC,
        agent_uri="spiffe://agents.test/agent/lab-assurance/site-validator",
        workload_token_file=token,
        site="north-lab",
        model_id="sepsis-lab-risk",
        version="1.2.0",
        cases=cases,
        min_cases=20,
        min_subgroup=10,
    )


async def test_submit_flow_sends_only_aggregates(tmp_path: Path) -> None:
    seen: list[httpx.Request] = []
    result = await cli.submit(submit_args(tmp_path), transport=central_transport(seen))
    assert result["benchmark"] == {"report_id": "/v1/benchmarks"}
    assert result["shift"] == {"report_id": "/v1/shift-analyses"}
    assert [r.url.path for r in seen] == [
        "/oauth2/token",
        "/v1/models/sepsis-lab-risk/1.2.0",
        "/v1/benchmarks",
        "/v1/shift-analyses",
    ]


async def test_submit_surfaces_central_errors(tmp_path: Path) -> None:
    with pytest.raises(CentralError, match="refused"):
        await cli.submit(submit_args(tmp_path), transport=central_transport([], fail=403))

    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("unreachable", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(down)) as http:
        with pytest.raises(DependencyUnavailableError):
            await CentralClient(CENTRAL, client=http, token="t").model("m", "1.0.0")

    def text_error(_: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="<html>gateway</html>")

    async with httpx.AsyncClient(transport=httpx.MockTransport(text_error)) as http:
        with pytest.raises(CentralError, match="gateway"):
            await CentralClient(CENTRAL, client=http, token="t").model("m", "1.0.0")


def test_cli_submit_reports_failures(tmp_path: Path) -> None:
    args = submit_args(tmp_path)
    args.workload_token_file.unlink()
    argv = [
        "submit",
        "--central",
        CENTRAL,
        "--fabric",
        FABRIC,
        "--agent-uri",
        args.agent_uri,
        "--workload-token-file",
        str(args.workload_token_file),
        "--site",
        "north-lab",
        "--model-id",
        "sepsis-lab-risk",
        "--version",
        "1.2.0",
        "--cases",
        str(args.cases),
    ]
    assert cli.main(argv, out=io.StringIO()) == 1
