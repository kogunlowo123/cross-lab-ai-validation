"""Site agent: obtain an agent credential and submit aggregates to the central platform."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import httpx

from cross_lab_ai_validation.domain.aggregates import PerformanceAggregate, PopulationAggregate
from cross_lab_ai_validation.domain.errors import AssuranceError, DependencyUnavailableError
from cross_lab_ai_validation.domain.models import ModelVersion
from cross_lab_ai_validation.integrations.identity_fabric import IdentityFabricClient

SITE_AGENT_SCOPES = ("registry:read", "benchmark:run", "shift:analyze")


class CentralError(AssuranceError):
    """The central platform refused a submission."""

    code = "central_error"
    status = 502


async def obtain_credential(
    fabric: IdentityFabricClient, *, agent_uri: str, workload_token_file: Path, site_id: str
) -> str:
    """Exchange the workload's projected token for a short-lived agent credential."""
    raw = await asyncio.to_thread(workload_token_file.read_text, encoding="utf-8")
    workload_token = raw.strip()
    issued = await fabric.issue_token(
        agent_uri, workload_token, scopes=[*SITE_AGENT_SCOPES, f"site:access:{site_id}"]
    )
    return issued.access_token


class CentralClient:
    def __init__(self, base_url: str, *, client: httpx.AsyncClient, token: str) -> None:
        self._base = base_url.rstrip("/")
        self._client = client
        self._headers = {"Authorization": f"Bearer {token}"}

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            response = await self._client.request(
                method, f"{self._base}{path}", headers=self._headers, **kwargs
            )
        except httpx.HTTPError as exc:
            raise DependencyUnavailableError(f"central platform unreachable: {exc}") from exc
        if response.status_code >= 400:
            try:
                body = response.json()
                detail = body.get("error_description", body) if isinstance(body, dict) else body
            except ValueError:
                detail = response.text[:200]
            raise CentralError(f"{method} {path} returned {response.status_code}: {detail}")
        return response.json()

    async def model(self, model_id: str, version: str) -> ModelVersion:
        return ModelVersion.model_validate(
            await self._request("GET", f"/v1/models/{model_id}/{version}")
        )

    async def submit_benchmark(
        self, site_id: str, model: ModelVersion, aggregate: PerformanceAggregate
    ) -> dict[str, Any]:
        body = {
            "site_id": site_id,
            "model_id": model.model_id,
            "version": model.version,
            "aggregate": aggregate.model_dump(mode="json"),
        }
        result: dict[str, Any] = await self._request("POST", "/v1/benchmarks", json=body)
        return result

    async def submit_population(
        self, site_id: str, model: ModelVersion, population: PopulationAggregate
    ) -> dict[str, Any]:
        body = {
            "site_id": site_id,
            "model_id": model.model_id,
            "version": model.version,
            "population": population.model_dump(mode="json"),
        }
        result: dict[str, Any] = await self._request("POST", "/v1/shift-analyses", json=body)
        return result
