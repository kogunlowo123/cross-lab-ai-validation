"""SQLAlchemy Core persistence. PostgreSQL in production; SQLite in tests.

Documents are stored as validated JSON alongside the indexed columns used for
lookups. Reports are append-only: there is no update path for benchmark,
shift or validation documents, so signed evidence cannot be altered in place.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import (
    Column,
    DateTime,
    Index,
    Integer,
    MetaData,
    PrimaryKeyConstraint,
    String,
    Table,
    Text,
    insert,
    select,
    update,
)
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

from cross_lab_ai_validation.domain.errors import ConflictError, InvalidStateError, NotFoundError
from cross_lab_ai_validation.domain.models import (
    ModelVersion,
    ReapprovalRequest,
    ReapprovalStatus,
    Site,
    SiteApproval,
)
from cross_lab_ai_validation.domain.ports import REPORT_ADAPTER
from cross_lab_ai_validation.evaluation.reports import BenchmarkReport, ShiftReport, ValidationRun

metadata = MetaData()

sites = Table(
    "sites",
    metadata,
    Column("site_id", String(64), primary_key=True),
    Column("document", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

model_versions = Table(
    "model_versions",
    metadata,
    Column("model_key", String(160), primary_key=True),
    Column("model_id", String(64), nullable=False),
    Column("version", String(32), nullable=False),
    Column("document", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Index("ix_model_versions_model_id", "model_id"),
)

reports = Table(
    "reports",
    metadata,
    Column("report_id", String(64), primary_key=True),
    Column("kind", String(16), nullable=False),
    Column("site_id", String(64), nullable=False),
    Column("model_key", String(160), nullable=False),
    Column("document", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Index("ix_reports_site_model", "site_id", "model_key", "kind"),
)

reapprovals = Table(
    "reapprovals",
    metadata,
    Column("request_id", String(64), primary_key=True),
    Column("site_id", String(64), nullable=False),
    Column("model_key", String(160), nullable=False),
    Column("status", String(24), nullable=False),
    Column("document", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Index("ix_reapprovals_status", "status"),
    Index("ix_reapprovals_site_model", "site_id", "model_key"),
)

approvals = Table(
    "approvals",
    metadata,
    Column("site_id", String(64), nullable=False),
    Column("model_key", String(160), nullable=False),
    Column("status", String(32), nullable=False),
    Column("document", Text, nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    PrimaryKeyConstraint("site_id", "model_key", name="pk_approvals"),
)

schema_version = Table(
    "schema_version",
    metadata,
    Column("version", Integer, primary_key=True),
)

SCHEMA_VERSION = 1


def make_engine(url: str) -> AsyncEngine:
    return create_async_engine(url, pool_pre_ping=True)


async def create_schema(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(metadata.create_all)
        current = (await conn.execute(select(schema_version.c.version))).scalar()
        if current is None:
            await conn.execute(insert(schema_version).values(version=SCHEMA_VERSION))


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


class SqlAssuranceStore:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    # ---------------------------------------------------------------- registry

    async def add_site(self, site: Site, at: datetime) -> None:
        async with self._engine.begin() as conn:
            exists = (
                await conn.execute(select(sites.c.site_id).where(sites.c.site_id == site.site_id))
            ).first()
            if exists:
                raise ConflictError(f"site {site.site_id} already registered")
            await conn.execute(
                insert(sites).values(
                    site_id=site.site_id, document=site.model_dump_json(), created_at=at
                )
            )

    async def get_site(self, site_id: str) -> Site | None:
        async with self._engine.connect() as conn:
            row = (
                await conn.execute(select(sites.c.document).where(sites.c.site_id == site_id))
            ).first()
        return Site.model_validate_json(row[0]) if row else None

    async def list_sites(self, *, limit: int, offset: int) -> Sequence[Site]:
        query = select(sites.c.document).order_by(sites.c.site_id).limit(limit).offset(offset)
        async with self._engine.connect() as conn:
            rows = (await conn.execute(query)).all()
        return [Site.model_validate_json(r[0]) for r in rows]

    async def add_model(self, model: ModelVersion, at: datetime) -> None:
        async with self._engine.begin() as conn:
            exists = (
                await conn.execute(
                    select(model_versions.c.model_key).where(
                        model_versions.c.model_key == model.key
                    )
                )
            ).first()
            if exists:
                raise ConflictError(f"model version {model.key} already registered")
            await conn.execute(
                insert(model_versions).values(
                    model_key=model.key,
                    model_id=model.model_id,
                    version=model.version,
                    document=model.model_dump_json(),
                    created_at=at,
                )
            )

    async def get_model(self, model_id: str, version: str) -> ModelVersion | None:
        key = f"{model_id}@{version}"
        async with self._engine.connect() as conn:
            row = (
                await conn.execute(
                    select(model_versions.c.document).where(model_versions.c.model_key == key)
                )
            ).first()
        return ModelVersion.model_validate_json(row[0]) if row else None

    async def list_models(self, *, limit: int, offset: int) -> Sequence[ModelVersion]:
        query = (
            select(model_versions.c.document)
            .order_by(model_versions.c.model_key)
            .limit(limit)
            .offset(offset)
        )
        async with self._engine.connect() as conn:
            rows = (await conn.execute(query)).all()
        return [ModelVersion.model_validate_json(r[0]) for r in rows]

    # ----------------------------------------------------------------- reports

    async def add_report(self, report: BenchmarkReport | ShiftReport | ValidationRun) -> None:
        async with self._engine.begin() as conn:
            await conn.execute(
                insert(reports).values(
                    report_id=report.report_id,
                    kind=report.kind,
                    site_id=report.site_id,
                    model_key=f"{report.model_id}@{report.version}",
                    document=report.model_dump_json(),
                    created_at=report.created_at,
                )
            )

    async def get_report(
        self, report_id: str
    ) -> BenchmarkReport | ShiftReport | ValidationRun | None:
        async with self._engine.connect() as conn:
            row = (
                await conn.execute(
                    select(reports.c.document).where(reports.c.report_id == report_id)
                )
            ).first()
        return REPORT_ADAPTER.validate_json(row[0]) if row else None

    # -------------------------------------------------------------- reapproval

    async def add_reapproval(self, request: ReapprovalRequest) -> None:
        async with self._engine.begin() as conn:
            pending = (
                await conn.execute(
                    select(reapprovals.c.request_id).where(
                        reapprovals.c.site_id == request.site_id,
                        reapprovals.c.model_key == request.model_key,
                        reapprovals.c.status == ReapprovalStatus.PENDING.value,
                    )
                )
            ).first()
            if pending:
                raise ConflictError(
                    f"reapproval {pending[0]} is already pending for "
                    f"{request.model_key} at {request.site_id}"
                )
            await conn.execute(
                insert(reapprovals).values(
                    request_id=request.request_id,
                    site_id=request.site_id,
                    model_key=request.model_key,
                    status=request.status.value,
                    document=request.model_dump_json(),
                    created_at=request.requested_at,
                    updated_at=request.requested_at,
                )
            )

    async def get_reapproval(self, request_id: str) -> ReapprovalRequest | None:
        async with self._engine.connect() as conn:
            row = (
                await conn.execute(
                    select(reapprovals.c.document).where(reapprovals.c.request_id == request_id)
                )
            ).first()
        return ReapprovalRequest.model_validate_json(row[0]) if row else None

    async def list_reapprovals(
        self, *, status: ReapprovalStatus | None, site_id: str | None, limit: int, offset: int
    ) -> Sequence[ReapprovalRequest]:
        query = (
            select(reapprovals.c.document)
            .order_by(reapprovals.c.created_at, reapprovals.c.request_id)
            .limit(limit)
            .offset(offset)
        )
        if status is not None:
            query = query.where(reapprovals.c.status == status.value)
        if site_id is not None:
            query = query.where(reapprovals.c.site_id == site_id)
        async with self._engine.connect() as conn:
            rows = (await conn.execute(query)).all()
        return [ReapprovalRequest.model_validate_json(r[0]) for r in rows]

    async def complete_reapproval(self, request: ReapprovalRequest, approval: SiteApproval) -> None:
        """Record the signed decision and the resulting site approval in one transaction."""
        if request.decided_at is None:
            raise InvalidStateError("reapproval has no decision")
        async with self._engine.begin() as conn:
            result = await conn.execute(
                update(reapprovals)
                .where(
                    reapprovals.c.request_id == request.request_id,
                    reapprovals.c.status == ReapprovalStatus.PENDING.value,
                )
                .values(
                    status=request.status.value,
                    document=request.model_dump_json(),
                    updated_at=request.decided_at,
                )
            )
            if result.rowcount == 0:
                raise ConflictError(f"reapproval {request.request_id} is no longer pending")
            await self._upsert_approval(conn, approval)

    # --------------------------------------------------------------- approvals

    async def _upsert_approval(self, conn: AsyncConnection, approval: SiteApproval) -> None:
        values = {
            "status": approval.status.value,
            "document": approval.model_dump_json(),
            "updated_at": approval.updated_at,
        }
        result = await conn.execute(
            update(approvals)
            .where(
                approvals.c.site_id == approval.site_id,
                approvals.c.model_key == approval.model_key,
            )
            .values(**values)
        )
        if result.rowcount == 0:
            await conn.execute(
                insert(approvals).values(
                    site_id=approval.site_id, model_key=approval.model_key, **values
                )
            )

    async def get_approval(self, site_id: str, model_key: str) -> SiteApproval | None:
        async with self._engine.connect() as conn:
            row = (
                await conn.execute(
                    select(approvals.c.document).where(
                        approvals.c.site_id == site_id, approvals.c.model_key == model_key
                    )
                )
            ).first()
        return SiteApproval.model_validate_json(row[0]) if row else None

    async def list_approvals(self, site_id: str) -> Sequence[SiteApproval]:
        query = (
            select(approvals.c.document)
            .where(approvals.c.site_id == site_id)
            .order_by(approvals.c.model_key)
        )
        async with self._engine.connect() as conn:
            rows = (await conn.execute(query)).all()
        return [SiteApproval.model_validate_json(r[0]) for r in rows]

    async def update_approval(self, approval: SiteApproval) -> None:
        async with self._engine.begin() as conn:
            result = await conn.execute(
                update(approvals)
                .where(
                    approvals.c.site_id == approval.site_id,
                    approvals.c.model_key == approval.model_key,
                )
                .values(
                    status=approval.status.value,
                    document=approval.model_dump_json(),
                    updated_at=_aware(approval.updated_at),
                )
            )
            if result.rowcount == 0:
                raise NotFoundError(f"no approval for {approval.model_key} at {approval.site_id}")


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)
