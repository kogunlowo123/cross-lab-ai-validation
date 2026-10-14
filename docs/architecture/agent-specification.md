# Agent specification

Two kinds of agents interact with the platform: the **site agent**
(`clv-site`, one per laboratory) and **assurance orchestration agents** that
use the MCP tools or A2A skills. The platform itself acts as an agent towards
A2A callers. This document states their mandate, scope, objectives,
permissions and contracts.

## Role and mission

| | Site agent | Assurance agent | Platform (A2A agent) |
|---|---|---|---|
| Purpose | Produce aggregate evidence from the laboratory's own cases | Drive benchmark, shift, validation and reapproval requests across sites | Compute assurance reports and enforce governance |
| Business objective | Keep each site's evidence current without moving patient data | Reduce time from new model version to a signed decision | Every clinical-use decision is evidence-based, attributable and current |
| Protected resources | Case extract (never leaves the site) | Reports and requests it creates | Registry, reports, approvals, audit chain |
| Operating boundary | Reads only model-relevant columns; suppresses subgroups below the minimum size; submits aggregates only | May request reapproval; may never approve | Never approves; restricts use only on a major shift |
| Delegated responsibility | Submission accuracy | Workflow progress | Statistics and policy enforcement |

## Delegated scope

| Action | Site agent | Assurance agent | Staff | Risk | Escalation |
|---|---|---|---|---|---|
| `registry:read`, `report:read` | allowed (own site) | allowed (scoped sites) | all staff roles | LOW | — |
| `benchmark:run`, `shift:analyze` | allowed (own site) | allowed (scoped sites) | scientist, director | MODERATE | Major shift pages the site's on-call and medical director |
| `validation:run` | not granted | allowed | scientist, director | MODERATE | Repeated failures open a ticket |
| `reapproval:request` | not granted | allowed, not via delegation | scientist, director | HIGH | Always `require_approval` |
| `approval:grant` | never | never | never directly; only through a signature | CRITICAL | Medical director sign-off |
| `reapproval:sign` | denied | denied | medical director of that site, fresh authentication, not the requester | CRITICAL | — |
| `approval:restrict` | on major shift | on major shift | on major shift | HIGH | Alert `ClvApprovalWithdrawn` |
| `site:register`, `model:register` | denied | denied | model registrar | HIGH | Change-managed |

Disallowed for all agents: signing, approving, registering models or sites,
submitting case-level data, acting on sites outside their credential scopes.

## Objectives

| Type | Definition | Target |
|---|---|---|
| SLO availability | Non-5xx share of `/v1/*`, `/mcp`, `/a2a` | 99.9% over 30 days |
| SLO latency | p95 `/v1/benchmarks`, `/v1/shift-analyses`, `/v1/validations` | < 1 s (budget tests: report build < 400 ms with 2000 bootstrap replicates) |
| SLI | `clv:request_errors:ratio_5m`, latency histograms | Prometheus |
| KPI | Sites with a current (non-expired) approval for each deployed version | 100% |
| KPI | Time from major shift to withdrawn use | Same request (no delay) |
| KPI | Approvals without an audit-chain signature event | 0 |
| Completion | A use case is complete when its outcome is in the signed audit chain | Enforced: an audit write failure fails the request |

## Permissions

| Mechanism | Use |
|---|---|
| RBAC | Staff roles `model-registrar`, `lab-validation-scientist`, `medical-director`, `assurance-auditor` from the enterprise IdP |
| ABAC | Policy `policies/assurance-authorization.yaml`: subject kind, roles, scopes, site coverage, delegation, authentication freshness; resource site and model; environment shift severity |
| Agent identity | SPIFFE IDs issued by agent-identity-fabric |
| OAuth 2.0 / OIDC | Site agent uses RFC 7523 jwt-bearer at the fabric; the platform introspects (RFC 7662) and calls `/v1/authorize`; staff use OIDC access tokens with `roles`, `sites`, `name`, `auth_time` |
| Workload identity | Site agent: projected service-account token. Platform: IRSA / Azure Workload Identity / GKE Workload Identity for its own cloud access |
| Site scoping | Staff `sites` claim; agent scopes `site:access:<site>` (or `site:access:*`) |

## Inputs and outputs

All bodies are Pydantic models with JSON Schemas in `schemas/` and the
OpenAPI document in `docs/api/openapi.json`.

| Input | Schema | Validation |
|---|---|---|
| Model version | `model-version.request` | Semver, sha256 artifact digest, score edges 0..1 strictly increasing, threshold on an interior edge, unique snake_case features, protocol thresholds consistent |
| Performance aggregate | `performance-aggregate` | One entry per score bin; bin score sums within [0, count]; subgroup keys `<attribute>=<value>` registered for the model; unknown fields rejected; minimum population size |
| Population aggregate | `population-aggregate` | Every registered feature present with the registered bins or categories; totals equal `n` |
| Verification studies | `validation.request` | Balanced precision design; strictly increasing linearity levels |
| Reapproval / signature | `reapproval.request`, `signature.request` | Free text scanned for identifiers and credentials |

| Output | Provenance and audit metadata |
|---|---|
| Benchmark report | `aggregate_digest`, `protocol_digest`, bootstrap seed and iterations, report digest |
| Shift report | `aggregate_digest`, Bonferroni alpha, findings |
| Validation run | Benchmark and shift report IDs and digests, protocol digest, per-criterion observed/threshold/result, `evidence_digest` sealing the whole run |
| Reapproval request | Evidence digest, requester and kind, policy decision, matched rules and policy digest |
| Signature | Signer, printed name, meaning, time, authentication time, evidence digest, statement |
| Audit event | Actor, actor kind, delegation chain, action, resource, outcome, correlation ID, hash link, Ed25519 signature |
