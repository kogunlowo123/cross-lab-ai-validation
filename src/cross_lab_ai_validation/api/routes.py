"""HTTP routes.

All ``/v1`` routes require a bearer token: an enterprise OIDC token for
laboratory staff, or an agent credential from agent-identity-fabric.
Authorization is decided per action by the assurance policy in the service.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Path, Query, Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import Field
from sqlalchemy import text

from cross_lab_ai_validation.api.schemas import (
    ModelList,
    ModelSummary,
    ReapprovalList,
    SignatureResult,
    SiteList,
)
from cross_lab_ai_validation.application.service import (
    ApprovalList,
    BenchmarkCommand,
    ReapprovalCommand,
    ShiftCommand,
    ShiftOutcome,
    SignatureCommand,
    ValidationCommand,
)
from cross_lab_ai_validation.domain.models import (
    ModelVersion,
    ReapprovalRequest,
    ReapprovalStatus,
    Site,
)
from cross_lab_ai_validation.evaluation.reports import BenchmarkReport, ShiftReport, ValidationRun
from cross_lab_ai_validation.runtime.container import Container
from cross_lab_ai_validation.security.authentication import Principal

router = APIRouter()

SLUG = r"^[a-z0-9][a-z0-9\-_.]{1,62}[a-z0-9]$"
SEMVER = r"^\d+\.\d+\.\d+$"
IDENT = r"^[a-z]{3,6}-[0-9a-f]{32}$"


def container(request: Request) -> Container:
    value: Container = request.app.state.container
    return value


async def caller(
    request: Request, authorization: Annotated[str | None, Header()] = None
) -> Principal:
    return await container(request).authenticator.authenticate(authorization)


Deps = Annotated[Container, Depends(container)]
Caller = Annotated[Principal, Depends(caller)]
SiteId = Annotated[str, Path(pattern=SLUG)]
ReportId = Annotated[str, Path(pattern=IDENT)]
Limit = Annotated[int, Query(ge=1, le=500)]
Offset = Annotated[int, Query(ge=0)]
AnyReport = Annotated[BenchmarkReport | ShiftReport | ValidationRun, Field(discriminator="kind")]


# ------------------------------------------------------------------- registry


@router.post("/v1/sites", status_code=201, tags=["registry"])
async def register_site(body: Site, c: Deps, who: Caller) -> Site:
    return await c.service.register_site(who, body)


@router.get("/v1/sites", tags=["registry"])
async def list_sites(c: Deps, who: Caller, limit: Limit = 100, offset: Offset = 0) -> SiteList:
    await c.service.authorize(who, "registry:read")
    items = await c.service.list_sites(limit=limit, offset=offset)
    return SiteList(items=list(items), limit=limit, offset=offset)


@router.post("/v1/models", status_code=201, tags=["registry"])
async def register_model(body: ModelVersion, c: Deps, who: Caller) -> ModelSummary:
    return ModelSummary.of(await c.service.register_model(who, body))


@router.get("/v1/models", tags=["registry"])
async def list_models(c: Deps, who: Caller, limit: Limit = 100, offset: Offset = 0) -> ModelList:
    await c.service.authorize(who, "registry:read")
    items = await c.service.list_models(limit=limit, offset=offset)
    return ModelList(items=[ModelSummary.of(m) for m in items], limit=limit, offset=offset)


@router.get("/v1/models/{model_id}/{version}", tags=["registry"])
async def get_model(
    model_id: Annotated[str, Path(pattern=SLUG)],
    version: Annotated[str, Path(pattern=SEMVER)],
    c: Deps,
    who: Caller,
) -> ModelVersion:
    await c.service.authorize(who, "registry:read")
    return await c.service.model(model_id, version)


@router.get("/v1/sites/{site_id}/approvals", tags=["registry"])
async def site_approvals(site_id: SiteId, c: Deps, who: Caller) -> ApprovalList:
    return ApprovalList(site_id=site_id, items=await c.service.approvals(who, site_id))


# ------------------------------------------------------------------ assurance


@router.post("/v1/benchmarks", status_code=201, tags=["assurance"])
async def run_benchmark(body: BenchmarkCommand, c: Deps, who: Caller) -> BenchmarkReport:
    return await c.service.benchmark(who, body)


@router.post("/v1/shift-analyses", status_code=201, tags=["assurance"])
async def run_shift_analysis(body: ShiftCommand, c: Deps, who: Caller) -> ShiftOutcome:
    return await c.service.analyze_shift(who, body)


@router.post("/v1/validations", status_code=201, tags=["assurance"])
async def run_validation(body: ValidationCommand, c: Deps, who: Caller) -> ValidationRun:
    return await c.service.validate(who, body)


@router.get("/v1/reports/{report_id}", tags=["assurance"])
async def get_report(report_id: ReportId, c: Deps, who: Caller) -> AnyReport:
    return await c.service.report(who, report_id)


# ----------------------------------------------------------------- reapproval


@router.post("/v1/reapprovals", status_code=202, tags=["reapproval"])
async def request_reapproval(body: ReapprovalCommand, c: Deps, who: Caller) -> ReapprovalRequest:
    """Submit a passing validation for sign-off. Always pending until a medical director signs."""
    return await c.service.request_reapproval(who, body)


@router.get("/v1/reapprovals", tags=["reapproval"])
async def list_reapprovals(
    *,
    c: Deps,
    who: Caller,
    status: ReapprovalStatus | None = None,
    site_id: Annotated[str | None, Query(pattern=SLUG)] = None,
    limit: Limit = 100,
    offset: Offset = 0,
) -> ReapprovalList:
    items = await c.service.list_reapprovals(
        who, status=status, site_id=site_id, limit=limit, offset=offset
    )
    return ReapprovalList(items=list(items), limit=limit, offset=offset)


@router.get("/v1/reapprovals/{request_id}", tags=["reapproval"])
async def get_reapproval(request_id: ReportId, c: Deps, who: Caller) -> ReapprovalRequest:
    return await c.service.reapproval(who, request_id)


@router.post("/v1/reapprovals/{request_id}/signature", tags=["reapproval"])
async def sign_reapproval(
    request_id: ReportId, body: SignatureCommand, c: Deps, who: Caller
) -> SignatureResult:
    """Medical-director electronic signature (21 CFR Part 11): approve or reject clinical use."""
    request, approval = await c.service.sign(who, request_id, body)
    return SignatureResult(request=request, approval=approval)


# ----------------------------------------------------------------- operations


@router.get("/healthz", tags=["operations"], include_in_schema=False)
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/readyz", tags=["operations"], include_in_schema=False)
async def readyz(c: Deps, response: Response) -> dict[str, str]:
    try:
        async with c.engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception:
        response.status_code = 503
        return {"status": "database unavailable"}
    return {"status": "ready"}


@router.get("/metrics", tags=["operations"], include_in_schema=False)
async def metrics(c: Deps) -> Response:
    return Response(generate_latest(c.metrics.registry), media_type=CONTENT_TYPE_LATEST)
