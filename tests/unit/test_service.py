"""Assurance workflow through the service: authorization, evidence binding, sign-off, audit."""

from __future__ import annotations

import pytest
from agentic_core.audit import verify_chain

from cross_lab_ai_validation.application.service import (
    AssuranceService,
    BenchmarkCommand,
    ReapprovalCommand,
    ShiftCommand,
    SignatureCommand,
    ValidationCommand,
)
from cross_lab_ai_validation.domain.aggregates import PerformanceAggregate, ScoreHistogram
from cross_lab_ai_validation.domain.errors import (
    ApprovalRequiredError,
    ConflictError,
    ForbiddenError,
    InvalidAggregateError,
    InvalidStateError,
    NotFoundError,
    UnsafeTextError,
)
from cross_lab_ai_validation.domain.models import (
    ModelVersion,
    ReapprovalStatus,
    SignatureMeaning,
)
from cross_lab_ai_validation.evaluation.reports import BenchmarkReport, ShiftReport, ValidationRun
from cross_lab_ai_validation.federation.summarize import summarize
from cross_lab_ai_validation.runtime.container import Container
from cross_lab_ai_validation.security.authentication import Principal, UserRole
from tests.conftest import SITE, FakeClock, agent, director, registrar, staff
from tests.factories import (
    model_version,
    passing_studies,
    shifted_summary,
    site,
    site_summary,
    synthetic_cases,
)

APPROVE = SignatureCommand(
    meaning=SignatureMeaning.APPROVAL,
    statement="Reviewed the validation evidence; approve clinical use at this laboratory.",
)


async def seed(service: AssuranceService, clock: FakeClock) -> ModelVersion:
    await service.register_site(registrar(clock), site())
    model = model_version()
    await service.register_model(registrar(clock), model)
    return model


def benchmark_cmd(model: ModelVersion, **kw: object) -> BenchmarkCommand:
    summary = site_summary(**kw)  # type: ignore[arg-type]
    assert summary.performance is not None
    return BenchmarkCommand(
        site_id=SITE, model_id=model.model_id, version=model.version, aggregate=summary.performance
    )


def shift_cmd(model: ModelVersion, *, shifted: bool = False) -> ShiftCommand:
    summary = shifted_summary() if shifted else site_summary()
    return ShiftCommand(
        site_id=SITE, model_id=model.model_id, version=model.version, population=summary.population
    )


async def validation(
    service: AssuranceService, who: Principal, model: ModelVersion
) -> ValidationRun:
    bench = await service.benchmark(who, benchmark_cmd(model))
    shift = await service.analyze_shift(who, shift_cmd(model))
    return await service.validate(
        who,
        ValidationCommand(
            benchmark_id=bench.report_id,
            shift_id=shift.report.report_id,
            studies=passing_studies(),
        ),
    )


async def test_full_reapproval_workflow(
    service: AssuranceService, clock: FakeClock, container: Container
) -> None:
    model = await seed(service, clock)
    scientist = staff(clock)
    run = await validation(service, scientist, model)
    assert run.verdict == "pass", [c for c in run.criteria if not c.passed]
    assert run.verify_seal()

    request = await service.request_reapproval(
        agent(), ReapprovalCommand(validation_id=run.report_id, justification="Annual revalidation")
    )
    assert request.status is ReapprovalStatus.PENDING
    assert request.policy_decision == "require_approval"
    assert "approval-grant-needs-medical-director" in request.policy_rules
    assert (await service.approvals(scientist, SITE)) == []  # nothing granted yet

    signed, approval = await service.sign(director(clock), request.request_id, APPROVE)
    assert signed.status is ReapprovalStatus.APPROVED
    assert signed.signature is not None
    assert signed.signature.printed_name == "Dr. Morgan Director"
    assert signed.signature.evidence_digest == run.evidence_digest
    assert approval.use_permitted
    assert approval.status == "approved"
    listed = await service.approvals(scientist, SITE)
    assert [a.version for a in listed] == [model.version]

    # The signed audit chain verifies end to end.
    events = [e async for e in container.audit._sink.read()]
    public = {container.audit._key_id: container.audit._signer.public_key()}
    assert verify_chain(events, public) == len(events)
    actions = [e.action for e in events]
    assert actions.index("reapproval.request") < actions.index("reapproval.sign")

    clock.advance(days=366)
    expired = await service.approvals(scientist, SITE)
    assert expired[0].status == "expired"
    assert not expired[0].use_permitted


async def test_major_shift_withdraws_approval(service: AssuranceService, clock: FakeClock) -> None:
    model = await seed(service, clock)
    scientist = staff(clock)
    run = await validation(service, scientist, model)
    request = await service.request_reapproval(
        scientist, ReapprovalCommand(validation_id=run.report_id, justification="Go-live review")
    )
    await service.sign(director(clock), request.request_id, APPROVE)

    outcome = await service.analyze_shift(agent(), shift_cmd(model, shifted=True))
    assert outcome.report.severity == "major"
    assert outcome.approval_restricted
    approval = (await service.approvals(scientist, SITE))[0]
    assert approval.status == "revalidation_required"
    assert not approval.use_permitted
    # A second major shift does not restrict again: use is already withdrawn.
    again = await service.analyze_shift(agent(), shift_cmd(model, shifted=True))
    assert not again.approval_restricted


async def test_validation_fails_under_major_shift(
    service: AssuranceService, clock: FakeClock
) -> None:
    model = await seed(service, clock)
    who = staff(clock)
    bench = await service.benchmark(who, benchmark_cmd(model))
    shift = await service.analyze_shift(who, shift_cmd(model, shifted=True))
    run = await service.validate(
        who,
        ValidationCommand(benchmark_id=bench.report_id, shift_id=shift.report.report_id),
    )
    assert run.verdict == "fail"
    failed = {c.criterion for c in run.criteria if not c.passed}
    assert {"population_shift", "accuracy_agreement", "precision", "reportable_range"} <= failed
    with pytest.raises(InvalidStateError, match="failed"):
        await service.request_reapproval(
            who, ReapprovalCommand(validation_id=run.report_id, justification="Please approve")
        )


async def test_signature_controls(service: AssuranceService, clock: FakeClock) -> None:
    model = await seed(service, clock)
    md = director(clock)
    run = await validation(service, md, model)
    request = await service.request_reapproval(
        md, ReapprovalCommand(validation_id=run.report_id, justification="Go-live review")
    )
    with pytest.raises(ConflictError, match="already pending"):
        await service.request_reapproval(
            md, ReapprovalCommand(validation_id=run.report_id, justification="Duplicate request")
        )
    # Two-person rule: the requester cannot sign.
    with pytest.raises(ForbiddenError, match="two-person"):
        await service.sign(md, request.request_id, APPROVE)
    # Agents never sign.
    with pytest.raises(ForbiddenError, match="denied"):
        await service.sign(agent(), request.request_id, APPROVE)
    # Validation scientists cannot sign.
    with pytest.raises(ForbiddenError):
        await service.sign(staff(clock, sub="other@lab.test"), request.request_id, APPROVE)
    # Directors of another site cannot sign.
    with pytest.raises(ForbiddenError):
        await service.sign(
            director(clock, sub="west@lab.test", sites=("west-lab",)), request.request_id, APPROVE
        )
    # Stale authentication (21 CFR 11.200) is refused.
    with pytest.raises(ForbiddenError):
        await service.sign(
            director(clock, sub="second@lab.test", auth_age_s=3600), request.request_id, APPROVE
        )
    # PHI in the statement is refused.
    with pytest.raises(UnsafeTextError):
        await service.sign(
            director(clock, sub="second@lab.test"),
            request.request_id,
            SignatureCommand(
                meaning=SignatureMeaning.APPROVAL,
                statement="Approved after reviewing MRN: 00123456 case",
            ),
        )
    rejected, approval = await service.sign(
        director(clock, sub="second@lab.test"),
        request.request_id,
        SignatureCommand(
            meaning=SignatureMeaning.REJECTION,
            statement="Subgroup evidence insufficient for the paediatric annex.",
        ),
    )
    assert rejected.status is ReapprovalStatus.REJECTED
    assert approval.status == "rejected"
    assert not approval.use_permitted
    with pytest.raises(InvalidStateError, match="already"):
        await service.sign(director(clock, sub="third@lab.test"), request.request_id, APPROVE)
    with pytest.raises(NotFoundError):
        await service.sign(director(clock), "reap-" + "0" * 32, APPROVE)


async def test_authorization_boundaries(service: AssuranceService, clock: FakeClock) -> None:
    model = await seed(service, clock)
    with pytest.raises(ForbiddenError):
        await service.register_site(agent(), site("east-lab", clia="99D9900002"))
    with pytest.raises(ForbiddenError):
        await service.register_model(staff(clock), model_version(version="2.0.0"))
    # Agent without the site scope.
    other_site = agent(("benchmark:run", "site:access:west-lab"))
    with pytest.raises(ForbiddenError):
        await service.benchmark(other_site, benchmark_cmd(model))
    # Agent without the action scope.
    with pytest.raises(ForbiddenError):
        await service.benchmark(
            agent(("shift:analyze", f"site:access:{SITE}")), benchmark_cmd(model)
        )
    # Auditor can read but not run.
    auditor = staff(clock, UserRole.AUDITOR, sub="auditor@lab.test")
    with pytest.raises(ForbiddenError):
        await service.benchmark(auditor, benchmark_cmd(model))
    assert await service.approvals(auditor, SITE) == []
    # Delegated agents cannot request reapproval.
    who = staff(clock)
    run = await validation(service, who, model)
    delegated = agent(chain=("spiffe://agents.test/agent/lab/orchestrator",))
    with pytest.raises(ForbiddenError):
        await service.request_reapproval(
            delegated, ReapprovalCommand(validation_id=run.report_id, justification="Delegated ask")
        )
    with pytest.raises(UnsafeTextError):
        await service.request_reapproval(
            who,
            ReapprovalCommand(
                validation_id=run.report_id, justification="Contact jane.doe@lab.test first"
            ),
        )


async def test_policy_approval_outcome_is_surfaced(
    service: AssuranceService, clock: FakeClock
) -> None:
    await seed(service, clock)
    with pytest.raises(ApprovalRequiredError):
        await service._authorize(staff(clock), "approval:grant", site_id=SITE)


async def test_registry_and_report_reads(service: AssuranceService, clock: FakeClock) -> None:
    model = await seed(service, clock)
    with pytest.raises(ConflictError):
        await service.register_site(registrar(clock), site())
    with pytest.raises(ConflictError):
        await service.register_model(registrar(clock), model)
    assert [s.site_id for s in await service.list_sites(limit=10, offset=0)] == [SITE]
    assert [m.key for m in await service.list_models(limit=10, offset=0)] == [model.key]
    who = staff(clock)
    bench = await service.benchmark(who, benchmark_cmd(model))
    assert isinstance(await service.report(who, bench.report_id), BenchmarkReport)
    with pytest.raises(NotFoundError):
        await service.report(who, "bench-" + "f" * 32)
    with pytest.raises(ForbiddenError):
        await service.report(staff(clock, sub="w@lab.test", sites=("west-lab",)), bench.report_id)
    with pytest.raises(NotFoundError):
        await service.model(model.model_id, "9.9.9")
    with pytest.raises(NotFoundError):
        await service.approvals(who, "unknown-lab")
    with pytest.raises(NotFoundError):
        await service.benchmark(
            staff(clock, sites=("*",)),
            benchmark_cmd(model).model_copy(update={"site_id": "unknown-lab"}),
        )


async def test_validation_requires_matching_reports(
    service: AssuranceService, clock: FakeClock
) -> None:
    model = await seed(service, clock)
    await service.register_site(registrar(clock), site("east-lab", clia="99D9900002"))
    who = staff(clock, sites=(SITE, "east-lab"))
    bench = await service.benchmark(who, benchmark_cmd(model))
    shift = await service.analyze_shift(
        who, shift_cmd(model).model_copy(update={"site_id": "east-lab"})
    )
    with pytest.raises(InvalidStateError, match="different"):
        await service.validate(
            who, ValidationCommand(benchmark_id=bench.report_id, shift_id=shift.report.report_id)
        )
    with pytest.raises(NotFoundError, match="benchmark"):
        await service.validate(
            who,
            ValidationCommand(benchmark_id=shift.report.report_id, shift_id=shift.report.report_id),
        )
    with pytest.raises(NotFoundError, match="shift"):
        await service.validate(
            who, ValidationCommand(benchmark_id=bench.report_id, shift_id=bench.report_id)
        )
    with pytest.raises(NotFoundError, match="validation"):
        await service.request_reapproval(
            who, ReapprovalCommand(validation_id=bench.report_id, justification="Not a validation")
        )
    assert isinstance(await service.report(who, shift.report.report_id), ShiftReport)


async def test_aggregate_validation(service: AssuranceService, clock: FakeClock) -> None:
    model = await seed(service, clock)
    who = staff(clock)
    small = summarize(model, synthetic_cases(15, seed=5), min_cases=10)
    assert small.performance is not None
    cmd = BenchmarkCommand(
        site_id=SITE, model_id=model.model_id, version=model.version, aggregate=small.performance
    )
    with pytest.raises(InvalidAggregateError, match="at least 20"):
        await service.benchmark(who, cmd)
    full = site_summary()
    assert full.performance is not None
    overall = full.performance.overall
    wrong_attribute = full.performance.model_copy(update={"subgroups": {"ward=7": overall}})
    with pytest.raises(InvalidAggregateError, match="not registered"):
        await service.benchmark(who, cmd.model_copy(update={"aggregate": wrong_attribute}))
    coarse = ScoreHistogram(
        positives=(10, 20),
        negatives=(30, 40),
        positive_score_sum=(2.0, 15.0),
        negative_score_sum=(5.0, 25.0),
        score_sq_sum=30.0,
    )
    with pytest.raises(InvalidAggregateError, match="score bins"):
        await service.benchmark(
            who, cmd.model_copy(update={"aggregate": PerformanceAggregate(overall=coarse)})
        )
    medium = site_summary(seed=6, n=200).performance
    assert medium is not None
    too_big = PerformanceAggregate(overall=medium.overall, subgroups={"sex=F": overall})
    with pytest.raises(InvalidAggregateError, match="larger"):
        await service.benchmark(who, cmd.model_copy(update={"aggregate": too_big}))
    population = full.population
    missing = population.model_copy(update={"categorical": {}})
    shift = ShiftCommand(
        site_id=SITE, model_id=model.model_id, version=model.version, population=missing
    )
    with pytest.raises(InvalidAggregateError, match="care_setting"):
        await service.analyze_shift(who, shift)
    unknown = population.model_copy(
        update={"categorical": {"care_setting": {"hospice": population.n}}}
    )
    with pytest.raises(InvalidAggregateError, match="unregistered"):
        await service.analyze_shift(who, shift.model_copy(update={"population": unknown}))
    extra = population.model_copy(
        update={"continuous": {**population.continuous, "crp_mg_l": (population.n, 0)}}
    )
    with pytest.raises(InvalidAggregateError, match="not registered"):
        await service.analyze_shift(who, shift.model_copy(update={"population": extra}))
    short = population.model_copy(
        update={"continuous": {**population.continuous, "age_years": (population.n, 0)}}
    )
    with pytest.raises(InvalidAggregateError, match="age_years"):
        await service.analyze_shift(who, shift.model_copy(update={"population": short}))
    bins = population.model_copy(update={"score_counts": (population.n, 0)})
    with pytest.raises(InvalidAggregateError, match="score_counts"):
        await service.analyze_shift(who, shift.model_copy(update={"population": bins}))
    tiny = population.model_copy(update={"n": 5})
    with pytest.raises(InvalidAggregateError, match="at least"):
        await service.analyze_shift(who, shift.model_copy(update={"population": tiny}))


async def test_tampered_evidence_cannot_be_signed(
    service: AssuranceService, clock: FakeClock, container: Container
) -> None:
    model = await seed(service, clock)
    who = staff(clock)
    run = await validation(service, who, model)
    request = await service.request_reapproval(
        who, ReapprovalCommand(validation_id=run.report_id, justification="Go-live review")
    )
    # Someone with database access flips a failed criterion to passed.
    from sqlalchemy import update

    from cross_lab_ai_validation.infrastructure.database import reports

    forged = run.model_copy(update={"verdict": "pass", "protocol_version": "9.9.9"})
    async with container.engine.begin() as conn:
        await conn.execute(
            update(reports)
            .where(reports.c.report_id == run.report_id)
            .values(document=forged.model_dump_json())
        )
    with pytest.raises(InvalidStateError, match="digest"):
        await service.sign(director(clock), request.request_id, APPROVE)
    listed = await service.list_reapprovals(
        who, status=ReapprovalStatus.PENDING, site_id=SITE, limit=10, offset=0
    )
    assert [r.request_id for r in listed] == [request.request_id]
    assert (await service.reapproval(who, request.request_id)).request_id == request.request_id
    with pytest.raises(NotFoundError):
        await service.reapproval(who, "reap-" + "1" * 32)


async def test_request_time_integrity_and_policy_denial(
    service: AssuranceService, clock: FakeClock, container: Container
) -> None:
    from agentic_core.policy import PolicyEngine, PolicySet
    from sqlalchemy import update

    from cross_lab_ai_validation.infrastructure.database import reports
    from cross_lab_ai_validation.security.authorization import Authorizer

    model = await seed(service, clock)
    who = staff(clock)
    run = await validation(service, who, model)
    forged = run.model_copy(update={"protocol_version": "9.9.9"})
    async with container.engine.begin() as conn:
        await conn.execute(
            update(reports)
            .where(reports.c.report_id == run.report_id)
            .values(document=forged.model_dump_json())
        )
    with pytest.raises(InvalidStateError, match="evidence digest"):
        await service.request_reapproval(
            who, ReapprovalCommand(validation_id=run.report_id, justification="Go-live review")
        )
    second = await validation(service, who, model)
    # A stricter policy that forbids granting use outright is honoured.
    policy = PolicySet.from_yaml("""
name: strict
version: "1"
rules:
  - id: allow-request
    effect: allow
    actions: ["reapproval:request"]
  - id: freeze-approvals
    effect: deny
    actions: ["approval:grant"]
""")
    service._authorizer = Authorizer(PolicyEngine(policy), clock=clock)
    with pytest.raises(ForbiddenError, match="approval:grant denied"):
        await service.request_reapproval(
            who, ReapprovalCommand(validation_id=second.report_id, justification="Go-live review")
        )
