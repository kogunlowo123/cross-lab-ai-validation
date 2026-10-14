"""Agent-facing skills shared by the MCP server and the A2A endpoint.

A skill validates its arguments into a typed command, runs the use case with
the caller's identity (so authorization and audit happen in the service),
and returns a typed result. Approval itself is not a skill: signing is a
human act performed over the HTTP API with fresh OIDC authentication.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from cross_lab_ai_validation.application.service import (
    ApprovalList,
    AssuranceService,
    BenchmarkCommand,
    ReapprovalCommand,
    ShiftCommand,
    ValidationCommand,
)
from cross_lab_ai_validation.security.authentication import Principal


class SiteQuery(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    site_id: str = Field(min_length=3, max_length=64)


Handler = Callable[[AssuranceService, Principal, Any], Awaitable[BaseModel]]


@dataclass(frozen=True)
class Skill:
    id: str
    name: str
    description: str
    tags: tuple[str, ...]
    input_model: type[BaseModel]
    handler: Handler
    read_only: bool = False
    human_gate: bool = False

    async def run(
        self, service: AssuranceService, who: Principal, arguments: Mapping[str, Any]
    ) -> BaseModel:
        payload = self.input_model.model_validate(dict(arguments))
        return await self.handler(service, who, payload)


async def _approvals(service: AssuranceService, who: Principal, query: SiteQuery) -> ApprovalList:
    return ApprovalList(site_id=query.site_id, items=await service.approvals(who, query.site_id))


async def _benchmark(
    service: AssuranceService, who: Principal, command: BenchmarkCommand
) -> BaseModel:
    return await service.benchmark(who, command)


async def _shift(service: AssuranceService, who: Principal, command: ShiftCommand) -> BaseModel:
    return await service.analyze_shift(who, command)


async def _validate(
    service: AssuranceService, who: Principal, command: ValidationCommand
) -> BaseModel:
    return await service.validate(who, command)


async def _reapproval(
    service: AssuranceService, who: Principal, command: ReapprovalCommand
) -> BaseModel:
    return await service.request_reapproval(who, command)


SKILLS: dict[str, Skill] = {
    skill.id: skill
    for skill in (
        Skill(
            id="list_site_approvals",
            name="List site approvals",
            description=(
                "Model versions approved, rejected or awaiting revalidation at a laboratory site, "
                "with whether clinical use is currently permitted."
            ),
            tags=("registry", "approval"),
            input_model=SiteQuery,
            handler=_approvals,
            read_only=True,
        ),
        Skill(
            id="benchmark_site_performance",
            name="Benchmark site performance",
            description=(
                "Sensitivity, specificity, PPV, NPV with Wilson intervals, AUROC with a stratified "
                "bootstrap interval, calibration (ECE, Brier) and subgroup performance, computed "
                "from a site's aggregate score histograms."
            ),
            tags=("benchmark", "performance", "federated"),
            input_model=BenchmarkCommand,
            handler=_benchmark,
        ),
        Skill(
            id="detect_population_shift",
            name="Detect population shift",
            description=(
                "PSI and KS on continuous features and model scores, chi-square on categorical "
                "features, and BBSE label-shift estimation against the model's reference "
                "population. A major shift withdraws an existing site approval."
            ),
            tags=("shift", "drift", "federated"),
            input_model=ShiftCommand,
            handler=_shift,
        ),
        Skill(
            id="run_method_verification",
            name="Run method verification",
            description=(
                "Evaluate the model's validation protocol: benchmark criteria, population shift, "
                "and CLIA/CAP-style accuracy, precision and reportable-range studies."
            ),
            tags=("validation", "clia", "cap"),
            input_model=ValidationCommand,
            handler=_validate,
        ),
        Skill(
            id="request_reapproval",
            name="Request reapproval",
            description=(
                "Submit a passing validation for medical-director sign-off. The result is always "
                "pending human approval; no agent can approve clinical use."
            ),
            tags=("approval", "human-in-the-loop"),
            input_model=ReapprovalCommand,
            handler=_reapproval,
            human_gate=True,
        ),
    )
}
