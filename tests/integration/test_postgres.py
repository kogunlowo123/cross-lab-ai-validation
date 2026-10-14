"""Store behaviour against a real PostgreSQL (CI service container).

Runs when CLV_TEST_DATABASE_URL points at PostgreSQL, e.g.
postgresql+asyncpg://clv:ci-only@localhost:5432/clv
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import pytest
from agentic_core.domain.identifiers import new_id

from cross_lab_ai_validation.domain.errors import ConflictError
from cross_lab_ai_validation.domain.models import ReapprovalStatus
from cross_lab_ai_validation.evaluation.reports import build_benchmark
from cross_lab_ai_validation.infrastructure.database import (
    SqlAssuranceStore,
    create_schema,
    make_engine,
)
from tests.factories import model_version, site, site_summary
from tests.unit.test_models import request, signature

URL = os.environ.get("CLV_TEST_DATABASE_URL", "")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not URL.startswith("postgresql"), reason="PostgreSQL not configured"),
]


async def test_store_round_trip() -> None:
    engine = make_engine(URL)
    await create_schema(engine)
    await create_schema(engine)  # idempotent
    store = SqlAssuranceStore(engine)
    now = datetime.now(UTC)
    suffix = new_id()[:8]
    lab = site(f"pg-{suffix}")
    await store.add_site(lab, now)
    assert await store.get_site(lab.site_id) == lab
    with pytest.raises(ConflictError):
        await store.add_site(lab, now)

    model = model_version(version=f"1.{int(suffix, 16) % 1000}.0")
    await store.add_model(model, now)
    assert (await store.get_model(model.model_id, model.version)) == model

    summary = site_summary()
    assert summary.performance is not None
    report = build_benchmark(
        report_id=f"bench-{new_id()}",
        site_id=lab.site_id,
        model=model,
        aggregate=summary.performance,
        created_at=now,
        created_by="integration",
        min_n=20,
    )
    await store.add_report(report)
    assert await store.get_report(report.report_id) == report

    pending = request().model_copy(
        update={"request_id": f"reap-{new_id()}", "site_id": lab.site_id}
    )
    await store.add_reapproval(pending)
    decided = pending.decide(signature())
    approval = decided.approval(revalidation_interval=timedelta(days=365))
    await store.complete_reapproval(decided, approval)
    stored = await store.get_reapproval(pending.request_id)
    assert stored is not None
    assert stored.status is ReapprovalStatus.APPROVED
    assert [a.status for a in await store.list_approvals(lab.site_id)] == [approval.status]
    restricted = approval.restrict(at=now + timedelta(days=1), reason="shift")
    await store.update_approval(restricted)
    current = await store.get_approval(lab.site_id, approval.model_key)
    assert current is not None
    assert current.status == restricted.status
    await engine.dispose()
