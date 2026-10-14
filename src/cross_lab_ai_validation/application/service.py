"""Assurance use cases.

Flow: Site -> Benchmark -> Population Shift Detection -> Validation -> Reapproval.

Every call is authorized (local policy, plus the identity fabric for agents),
every decision and state change is written to the signed audit chain before
the result is returned, and a failed audit write fails the request. Clinical
use of a model version at a site changes only through a medical director's
electronic signature; no code path lets an agent or the platform grant it.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import timedelta
from typing import Any

from agentic_core.audit import AuditLog
from agentic_core.domain.classification import Decision
from agentic_core.domain.identifiers import new_id
from agentic_core.observability import correlation_id
from pydantic import BaseModel, ConfigDict, Field

from cross_lab_ai_validation.domain.aggregates import (
    PerformanceAggregate,
    PopulationAggregate,
    VerificationStudies,
)
from cross_lab_ai_validation.domain.errors import (
    ApprovalRequiredError,
    ForbiddenError,
    InvalidStateError,
    NotFoundError,
)
from cross_lab_ai_validation.domain.models import (
    ElectronicSignature,
    ModelVersion,
    ReapprovalRequest,
    ReapprovalStatus,
    SignatureMeaning,
    Site,
    SiteApproval,
    model_key,
)
from cross_lab_ai_validation.domain.ports import AssuranceStore, Clock
from cross_lab_ai_validation.evaluation.reports import (
    BenchmarkReport,
    ShiftReport,
    ValidationRun,
    build_benchmark,
    build_shift_report,
    check_performance,
    check_population,
    evaluate_protocol,
)
from cross_lab_ai_validation.observability.metrics import AssuranceMetrics
from cross_lab_ai_validation.security.authentication import Principal
from cross_lab_ai_validation.security.authorization import AuthorizationOutcome, Authorizer
from cross_lab_ai_validation.security.text_guard import TextGuard


class BenchmarkCommand(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    site_id: str = Field(min_length=3, max_length=64)
    model_id: str = Field(min_length=3, max_length=64)
    version: str = Field(min_length=5, max_length=32)
    aggregate: PerformanceAggregate


class ShiftCommand(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    site_id: str = Field(min_length=3, max_length=64)
    model_id: str = Field(min_length=3, max_length=64)
    version: str = Field(min_length=5, max_length=32)
    population: PopulationAggregate


class ValidationCommand(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    benchmark_id: str = Field(min_length=8, max_length=64)
    shift_id: str = Field(min_length=8, max_length=64)
    studies: VerificationStudies = Field(default_factory=VerificationStudies)


class ReapprovalCommand(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    validation_id: str = Field(min_length=8, max_length=64)
    justification: str = Field(min_length=10, max_length=2000)


class SignatureCommand(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    meaning: SignatureMeaning
    statement: str = Field(
        min_length=10,
        max_length=2000,
        description="What the signer attests, e.g. reviewed evidence and approve clinical use",
    )


class ApprovalView(BaseModel):
    model_config = ConfigDict(frozen=True)

    site_id: str
    model_id: str
    version: str
    status: str
    use_permitted: bool
    approved_at: str | None
    expires_at: str | None
    validation_id: str
    reapproval_id: str
    signed_by: str
    status_reason: str


class ApprovalList(BaseModel):
    model_config = ConfigDict(frozen=True)

    site_id: str
    items: list[ApprovalView]


class ShiftOutcome(BaseModel):
    model_config = ConfigDict(frozen=True)

    report: ShiftReport
    approval_restricted: bool


class AssuranceService:
    def __init__(
        self,
        *,
        store: AssuranceStore,
        authorizer: Authorizer,
        audit: AuditLog,
        clock: Clock,
        text_guard: TextGuard,
        min_aggregate_cases: int,
        metrics: AssuranceMetrics,
    ) -> None:
        self._metrics = metrics
        self._store = store
        self._authorizer = authorizer
        self._audit = audit
        self._clock = clock
        self._text = text_guard
        self._min_n = min_aggregate_cases

    # ------------------------------------------------------------- authorization

    async def _authorize(
        self,
        who: Principal,
        action: str,
        *,
        site_id: str | None = None,
        key: str | None = None,
        environment: dict[str, Any] | None = None,
    ) -> AuthorizationOutcome:
        outcome = await self._authorizer.authorize(
            who, action, site_id=site_id, model_key=key, environment=environment
        )
        resource = f"{site_id or '*'}/{key or '*'}"
        if outcome.decision is not Decision.ALLOW:
            self._metrics.authz_denials.labels(outcome.decision.value).inc()
            await self._record(
                who,
                action,
                resource,
                outcome.decision.value,
                {
                    "rules": list(outcome.matched_rules),
                    "policy_digest": outcome.policy_digest,
                    "reason": outcome.reason,
                },
            )
        if outcome.decision is Decision.DENY:
            raise ForbiddenError(f"{action} denied: {outcome.reason}")
        if outcome.decision is Decision.REQUIRE_APPROVAL:
            raise ApprovalRequiredError(f"{action} requires human approval: {outcome.reason}")
        return outcome

    async def authorize(
        self, who: Principal, action: str, *, site_id: str | None = None
    ) -> AuthorizationOutcome:
        """Authorization check for read paths served directly by the API layer."""
        return await self._authorize(who, action, site_id=site_id)

    # ------------------------------------------------------------------ registry

    async def register_site(self, who: Principal, site: Site) -> Site:
        await self._authorize(who, "site:register")
        await self._store.add_site(site, self._clock.now())
        await self._record(
            who, "site.register", site.site_id, "success", {"clia": site.clia_number}
        )
        return site

    async def register_model(self, who: Principal, model: ModelVersion) -> ModelVersion:
        await self._authorize(who, "model:register")
        check_performance(model, model.reference.performance, 1)
        check_population(model, model.reference.population, 1)
        await self._store.add_model(model, self._clock.now())
        await self._record(
            who,
            "model.register",
            model.key,
            "success",
            {"artifact_digest": model.artifact_digest, "protocol_digest": model.protocol.digest},
        )
        return model

    async def site(self, site_id: str) -> Site:
        site = await self._store.get_site(site_id)
        if site is None:
            raise NotFoundError(f"site {site_id} not found")
        return site

    async def model(self, model_id: str, version: str) -> ModelVersion:
        model = await self._store.get_model(model_id, version)
        if model is None:
            raise NotFoundError(f"model version {model_key(model_id, version)} not found")
        return model

    async def list_sites(self, *, limit: int, offset: int) -> Sequence[Site]:
        return await self._store.list_sites(limit=limit, offset=offset)

    async def list_models(self, *, limit: int, offset: int) -> Sequence[ModelVersion]:
        return await self._store.list_models(limit=limit, offset=offset)

    def view(self, approval: SiteApproval) -> ApprovalView:
        now = self._clock.now()
        return ApprovalView(
            site_id=approval.site_id,
            model_id=approval.model_id,
            version=approval.version,
            status=approval.effective_status(now),
            use_permitted=approval.use_permitted(now),
            approved_at=approval.approved_at.isoformat() if approval.approved_at else None,
            expires_at=approval.expires_at.isoformat() if approval.expires_at else None,
            validation_id=approval.validation_id,
            reapproval_id=approval.reapproval_id,
            signed_by=approval.signature.signer,
            status_reason=approval.status_reason,
        )

    async def approvals(self, who: Principal, site_id: str) -> list[ApprovalView]:
        await self._authorize(who, "registry:read", site_id=site_id)
        await self.site(site_id)
        return [self.view(a) for a in await self._store.list_approvals(site_id)]

    # ------------------------------------------------------------------- reports

    async def report(
        self, who: Principal, report_id: str
    ) -> BenchmarkReport | ShiftReport | ValidationRun:
        report = await self._store.get_report(report_id)
        if report is None:
            raise NotFoundError(f"report {report_id} not found")
        await self._authorize(
            who,
            "report:read",
            site_id=report.site_id,
            key=model_key(report.model_id, report.version),
        )
        return report

    async def _subject(self, site_id: str, model_id: str, version: str) -> ModelVersion:
        await self.site(site_id)
        return await self.model(model_id, version)

    async def benchmark(self, who: Principal, command: BenchmarkCommand) -> BenchmarkReport:
        key = model_key(command.model_id, command.version)
        await self._authorize(who, "benchmark:run", site_id=command.site_id, key=key)
        model = await self._subject(command.site_id, command.model_id, command.version)
        report = build_benchmark(
            report_id=f"bench-{new_id()}",
            site_id=command.site_id,
            model=model,
            aggregate=command.aggregate,
            created_at=self._clock.now(),
            created_by=who.subject,
            min_n=self._min_n,
        )
        await self._store.add_report(report)
        self._metrics.evaluations.labels("benchmark", "completed").inc()
        await self._record(
            who,
            "benchmark.run",
            f"{command.site_id}/{key}",
            "success",
            {
                "report_id": report.report_id,
                "report_digest": report.digest,
                "aggregate_digest": report.aggregate_digest,
                "auroc": report.auroc.estimate,
                "n": report.n,
            },
        )
        return report

    async def analyze_shift(self, who: Principal, command: ShiftCommand) -> ShiftOutcome:
        key = model_key(command.model_id, command.version)
        await self._authorize(who, "shift:analyze", site_id=command.site_id, key=key)
        model = await self._subject(command.site_id, command.model_id, command.version)
        report = build_shift_report(
            report_id=f"shift-{new_id()}",
            site_id=command.site_id,
            model=model,
            population=command.population,
            created_at=self._clock.now(),
            created_by=who.subject,
            min_n=self._min_n,
        )
        await self._store.add_report(report)
        self._metrics.evaluations.labels("shift", "completed").inc()
        self._metrics.shift_severity.labels(report.severity).inc()
        await self._record(
            who,
            "shift.analyze",
            f"{command.site_id}/{key}",
            "success",
            {
                "report_id": report.report_id,
                "report_digest": report.digest,
                "severity": report.severity,
                "max_psi": report.max_psi,
            },
        )
        restricted = False
        approval = await self._store.get_approval(command.site_id, key)
        if (
            report.severity == "major"
            and approval is not None
            and approval.use_permitted(self._clock.now())
        ):
            await self._authorize(
                who,
                "approval:restrict",
                site_id=command.site_id,
                key=key,
                environment={"shift_severity": report.severity},
            )
            reason = f"major population shift in {report.report_id}; revalidation required"
            await self._store.update_approval(
                approval.restrict(at=self._clock.now(), reason=reason)
            )
            await self._record(
                who,
                "approval.restrict",
                f"{command.site_id}/{key}",
                "success",
                {"shift_report": report.report_id, "previous_validation": approval.validation_id},
            )
            self._metrics.approvals_restricted.inc()
            restricted = True
        return ShiftOutcome(report=report, approval_restricted=restricted)

    async def validate(self, who: Principal, command: ValidationCommand) -> ValidationRun:
        benchmark = await self._store.get_report(command.benchmark_id)
        shift = await self._store.get_report(command.shift_id)
        if not isinstance(benchmark, BenchmarkReport):
            raise NotFoundError(f"benchmark report {command.benchmark_id} not found")
        if not isinstance(shift, ShiftReport):
            raise NotFoundError(f"shift report {command.shift_id} not found")
        key = model_key(benchmark.model_id, benchmark.version)
        await self._authorize(who, "validation:run", site_id=benchmark.site_id, key=key)
        if (shift.site_id, shift.model_id, shift.version) != (
            benchmark.site_id,
            benchmark.model_id,
            benchmark.version,
        ):
            raise InvalidStateError("benchmark and shift reports concern different sites or models")
        model = await self.model(benchmark.model_id, benchmark.version)
        run = evaluate_protocol(
            report_id=f"val-{new_id()}",
            model=model,
            benchmark=benchmark,
            shift=shift,
            studies=command.studies,
            created_at=self._clock.now(),
            created_by=who.subject,
        )
        await self._store.add_report(run)
        self._metrics.evaluations.labels("validation", run.verdict).inc()
        await self._record(
            who,
            "validation.run",
            f"{benchmark.site_id}/{key}",
            run.verdict,
            {
                "report_id": run.report_id,
                "evidence_digest": run.evidence_digest,
                "failed": [c.criterion for c in run.criteria if not c.passed],
            },
        )
        return run

    # ---------------------------------------------------------------- reapproval

    async def request_reapproval(
        self, who: Principal, command: ReapprovalCommand
    ) -> ReapprovalRequest:
        run = await self._store.get_report(command.validation_id)
        if not isinstance(run, ValidationRun):
            raise NotFoundError(f"validation run {command.validation_id} not found")
        key = model_key(run.model_id, run.version)
        await self._authorize(who, "reapproval:request", site_id=run.site_id, key=key)
        justification = self._text.check("justification", command.justification)
        if run.verdict != "pass":
            raise InvalidStateError(
                f"validation {run.report_id} failed; only a passing validation can be submitted"
            )
        if not run.verify_seal():
            raise InvalidStateError(f"validation {run.report_id} evidence digest does not match")
        # Granting use is always a human decision. The policy is consulted so the
        # decision and its digest are recorded, but a policy that returned "allow"
        # here would still not grant anything: approval only follows a signature.
        grant = await self._authorizer.authorize(
            who, "approval:grant", site_id=run.site_id, model_key=key
        )
        if grant.decision is Decision.DENY:
            await self._record(
                who, "reapproval.request", f"{run.site_id}/{key}", "deny", {"reason": grant.reason}
            )
            raise ForbiddenError(f"approval:grant denied: {grant.reason}")
        decision = Decision.REQUIRE_APPROVAL
        request = ReapprovalRequest(
            request_id=f"reap-{new_id()}",
            site_id=run.site_id,
            model_id=run.model_id,
            version=run.version,
            validation_id=run.report_id,
            evidence_digest=run.evidence_digest,
            justification=justification,
            requested_by=who.subject,
            requested_by_kind=who.kind,
            requested_at=self._clock.now(),
            policy_decision=decision.value,
            policy_rules=grant.matched_rules,
            policy_digest=grant.policy_digest,
        )
        await self._store.add_reapproval(request)
        await self._record(
            who,
            "reapproval.request",
            f"{run.site_id}/{key}",
            decision.value,
            {
                "request_id": request.request_id,
                "validation_id": run.report_id,
                "evidence_digest": run.evidence_digest,
                "rules": list(grant.matched_rules),
                "policy_digest": grant.policy_digest,
            },
        )
        return request

    async def reapproval(self, who: Principal, request_id: str) -> ReapprovalRequest:
        request = await self._store.get_reapproval(request_id)
        if request is None:
            raise NotFoundError(f"reapproval {request_id} not found")
        await self._authorize(who, "report:read", site_id=request.site_id, key=request.model_key)
        return request

    async def list_reapprovals(
        self,
        who: Principal,
        *,
        status: ReapprovalStatus | None,
        site_id: str | None,
        limit: int,
        offset: int,
    ) -> Sequence[ReapprovalRequest]:
        await self._authorize(who, "registry:read")
        return await self._store.list_reapprovals(
            status=status, site_id=site_id, limit=limit, offset=offset
        )

    async def sign(
        self, who: Principal, request_id: str, command: SignatureCommand
    ) -> tuple[ReapprovalRequest, ApprovalView]:
        request = await self._store.get_reapproval(request_id)
        if request is None:
            raise NotFoundError(f"reapproval {request_id} not found")
        resource = f"{request.site_id}/{request.model_key}"
        await self._authorize(
            who, "reapproval:sign", site_id=request.site_id, key=request.model_key
        )
        statement = self._text.check("statement", command.statement)
        if request.status is not ReapprovalStatus.PENDING:
            raise InvalidStateError(f"reapproval {request_id} is already {request.status}")
        if who.subject == request.requested_by:
            await self._record(who, "reapproval.sign", resource, "deny", {"reason": "same person"})
            raise ForbiddenError("the requester cannot sign their own reapproval (two-person rule)")
        run = await self._store.get_report(request.validation_id)
        if not isinstance(run, ValidationRun) or run.evidence_digest != request.evidence_digest:
            raise InvalidStateError("validation evidence changed since the request was made")
        if not run.verify_seal():
            raise InvalidStateError("validation evidence digest does not match its content")
        if who.authenticated_at is None:
            raise ForbiddenError("signature requires an authentication time")
        model = await self.model(request.model_id, request.version)
        now = self._clock.now()
        signature = ElectronicSignature(
            signer=who.subject,
            printed_name=who.display_name or who.subject,
            meaning=command.meaning,
            signed_at=now,
            evidence_digest=request.evidence_digest,
            statement=statement,
            authenticated_at=who.authenticated_at,
        )
        decided = request.decide(signature)
        approval = decided.approval(
            revalidation_interval=timedelta(days=model.protocol.revalidation_interval_days)
        )
        await self._store.complete_reapproval(decided, approval)
        self._metrics.signatures.labels(command.meaning.value).inc()
        await self._record(
            who,
            "reapproval.sign",
            resource,
            decided.status.value,
            {
                "request_id": request_id,
                "meaning": command.meaning.value,
                "evidence_digest": request.evidence_digest,
                "expires_at": approval.expires_at.isoformat() if approval.expires_at else None,
            },
        )
        return decided, self.view(approval)

    # --------------------------------------------------------------------- audit

    async def record_call(
        self,
        who: Principal | None,
        *,
        protocol: str,
        operation: str,
        outcome: str,
        detail: str | None = None,
    ) -> None:
        """Audit an agent-protocol invocation (MCP tool call or A2A message)."""
        details: dict[str, Any] = {"actor_kind": who.kind if who else "unauthenticated"}
        if detail:
            details["detail"] = detail[:500]
        await self._audit.record(
            actor=who.subject if who else "anonymous",
            action=f"{protocol}.{operation}",
            resource=operation,
            outcome=outcome,
            correlation_id=correlation_id(),
            details=details,
        )

    async def _record(
        self,
        who: Principal,
        action: str,
        resource: str,
        outcome: str,
        details: dict[str, Any],
    ) -> None:
        details = {"actor_kind": who.kind, **details}
        if who.delegation_chain:
            details["delegation_chain"] = list(who.delegation_chain)
        await self._audit.record(
            actor=who.subject,
            action=action,
            resource=resource,
            outcome=outcome,
            correlation_id=correlation_id(),
            details=details,
        )
