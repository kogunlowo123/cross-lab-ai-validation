"""Service metrics exposed on /metrics in Prometheus text format."""

from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Histogram


class AssuranceMetrics:
    def __init__(self, registry: CollectorRegistry | None = None) -> None:
        self.registry = registry or CollectorRegistry()
        self.requests = Histogram(
            "clv_http_request_duration_seconds",
            "HTTP request latency",
            ["method", "route", "status"],
            registry=self.registry,
            buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5),
        )
        self.evaluations = Counter(
            "clv_evaluations_total",
            "Assurance evaluations by kind and outcome",
            ["kind", "outcome"],
            registry=self.registry,
        )
        self.shift_severity = Counter(
            "clv_shift_analyses_total",
            "Population shift analyses by severity",
            ["severity"],
            registry=self.registry,
        )
        self.approvals_restricted = Counter(
            "clv_approvals_restricted_total",
            "Site approvals withdrawn pending revalidation after a major shift",
            registry=self.registry,
        )
        self.signatures = Counter(
            "clv_reapproval_signatures_total",
            "Medical-director signatures by meaning",
            ["meaning"],
            registry=self.registry,
        )
        self.authz_denials = Counter(
            "clv_authorization_denials_total",
            "Requests refused by policy (deny or approval required)",
            ["code"],
            registry=self.registry,
        )
        self.protocol_calls = Counter(
            "clv_agent_protocol_calls_total",
            "MCP tool and A2A skill invocations",
            ["protocol", "operation", "outcome"],
            registry=self.registry,
        )
