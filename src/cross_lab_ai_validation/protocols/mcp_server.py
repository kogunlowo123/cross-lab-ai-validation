"""MCP server (official ``mcp`` SDK, streamable HTTP) exposing the assurance skills as tools.

Every tool has a typed input and output schema, authenticates the caller from
the ``Authorization`` header of the MCP HTTP request, is authorized by the
assurance policy (and the identity fabric for agents) inside the service, and
is recorded in the audit chain. There is deliberately no signing tool.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, cast

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import BaseModel, ValidationError

from cross_lab_ai_validation import __version__
from cross_lab_ai_validation.application.service import (
    ApprovalList,
    BenchmarkCommand,
    ReapprovalCommand,
    ShiftCommand,
    ShiftOutcome,
    ValidationCommand,
)
from cross_lab_ai_validation.domain.errors import AssuranceError
from cross_lab_ai_validation.domain.models import ReapprovalRequest
from cross_lab_ai_validation.evaluation.reports import BenchmarkReport, ValidationRun
from cross_lab_ai_validation.protocols.invoke import invoke_skill
from cross_lab_ai_validation.protocols.skills import SKILLS, SiteQuery
from cross_lab_ai_validation.runtime.container import Container

INSTRUCTIONS = (
    "Clinical laboratory AI assurance. Benchmark a model version at a site from aggregate "
    "statistics, detect population shift, run the validation protocol, and submit passing "
    "validations for medical-director sign-off. Tools never approve clinical use."
)


def _annotations(skill_id: str) -> ToolAnnotations:
    skill = SKILLS[skill_id]
    return ToolAnnotations(
        title=skill.name,
        read_only_hint=skill.read_only,
        destructive_hint=False,
        idempotent_hint=skill.read_only,
        open_world_hint=False,
    )


def build_mcp_server(container: Callable[[], Container]) -> MCPServer:
    server: MCPServer = MCPServer(
        "cross-lab-ai-validation",
        title="Clinical Laboratory AI Assurance",
        version=__version__,
        instructions=INSTRUCTIONS,
    )

    async def call(skill_id: str, payload: BaseModel, ctx: Context) -> Any:
        headers = ctx.headers or {}
        try:
            _, result = await invoke_skill(
                container(),
                protocol="mcp",
                skill_id=skill_id,
                authorization=headers.get("authorization"),
                arguments=payload.model_dump(mode="json"),
            )
        except AssuranceError as exc:
            raise ToolError(f"{exc.code}: {exc.message}") from exc
        except ValidationError as exc:
            raise ToolError(f"invalid_params: {exc.error_count()} validation errors") from exc
        return result

    @server.tool(
        name="list_site_approvals",
        description=SKILLS["list_site_approvals"].description,
        annotations=_annotations("list_site_approvals"),
    )
    async def list_site_approvals(query: SiteQuery, ctx: Context) -> ApprovalList:
        return cast(ApprovalList, await call("list_site_approvals", query, ctx))

    @server.tool(
        name="benchmark_site_performance",
        description=SKILLS["benchmark_site_performance"].description,
        annotations=_annotations("benchmark_site_performance"),
    )
    async def benchmark_site_performance(
        request: BenchmarkCommand, ctx: Context
    ) -> BenchmarkReport:
        return cast(BenchmarkReport, await call("benchmark_site_performance", request, ctx))

    @server.tool(
        name="detect_population_shift",
        description=SKILLS["detect_population_shift"].description,
        annotations=_annotations("detect_population_shift"),
    )
    async def detect_population_shift(request: ShiftCommand, ctx: Context) -> ShiftOutcome:
        return cast(ShiftOutcome, await call("detect_population_shift", request, ctx))

    @server.tool(
        name="run_method_verification",
        description=SKILLS["run_method_verification"].description,
        annotations=_annotations("run_method_verification"),
    )
    async def run_method_verification(request: ValidationCommand, ctx: Context) -> ValidationRun:
        return cast(ValidationRun, await call("run_method_verification", request, ctx))

    @server.tool(
        name="request_reapproval",
        description=SKILLS["request_reapproval"].description,
        annotations=_annotations("request_reapproval"),
    )
    async def request_reapproval(request: ReapprovalCommand, ctx: Context) -> ReapprovalRequest:
        return cast(ReapprovalRequest, await call("request_reapproval", request, ctx))

    return server
