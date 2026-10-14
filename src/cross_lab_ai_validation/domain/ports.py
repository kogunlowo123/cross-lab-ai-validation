"""Ports the application layer depends on. Adapters live in infrastructure."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Annotated, Protocol

from pydantic import Field, TypeAdapter

from cross_lab_ai_validation.domain.models import (
    ModelVersion,
    ReapprovalRequest,
    ReapprovalStatus,
    Site,
    SiteApproval,
)
from cross_lab_ai_validation.evaluation.reports import BenchmarkReport, ShiftReport, ValidationRun

Report = Annotated[BenchmarkReport | ShiftReport | ValidationRun, Field(discriminator="kind")]
REPORT_ADAPTER: TypeAdapter[BenchmarkReport | ShiftReport | ValidationRun] = TypeAdapter(Report)


class AssuranceStore(Protocol):
    async def add_site(self, site: Site, at: datetime) -> None: ...

    async def get_site(self, site_id: str) -> Site | None: ...

    async def list_sites(self, *, limit: int, offset: int) -> Sequence[Site]: ...

    async def add_model(self, model: ModelVersion, at: datetime) -> None: ...

    async def get_model(self, model_id: str, version: str) -> ModelVersion | None: ...

    async def list_models(self, *, limit: int, offset: int) -> Sequence[ModelVersion]: ...

    async def add_report(self, report: BenchmarkReport | ShiftReport | ValidationRun) -> None: ...

    async def get_report(
        self, report_id: str
    ) -> BenchmarkReport | ShiftReport | ValidationRun | None: ...

    async def add_reapproval(self, request: ReapprovalRequest) -> None: ...

    async def get_reapproval(self, request_id: str) -> ReapprovalRequest | None: ...

    async def list_reapprovals(
        self, *, status: ReapprovalStatus | None, site_id: str | None, limit: int, offset: int
    ) -> Sequence[ReapprovalRequest]: ...

    async def complete_reapproval(
        self, request: ReapprovalRequest, approval: SiteApproval
    ) -> None: ...

    async def get_approval(self, site_id: str, model_key: str) -> SiteApproval | None: ...

    async def list_approvals(self, site_id: str) -> Sequence[SiteApproval]: ...

    async def update_approval(self, approval: SiteApproval) -> None: ...


class Clock(Protocol):
    def now(self) -> datetime: ...
