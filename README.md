# Cross-Lab AI Validation

Clinical Laboratory AI Assurance Platform. A laboratory AI model that was
validated at one site is not automatically safe at the next: case mix,
instruments, reagent lots and prevalence differ. This platform decides, with
evidence, whether a specific model version may be used at a specific
laboratory, and keeps that decision current as the site's population moves.

```
Site A ─▶ Benchmark ─▶ Population Shift Detection ─▶ Validation ─▶ Reapproval
         (aggregates)   (PSI, KS, chi-square, BBSE)   (CLIA/CAP)    (medical director)
```

Sites never send patient records. A site agent summarises its cases inside
the laboratory and submits aggregate statistics only; every metric the
platform reports is computed exactly from those aggregates.

## Capabilities

| Capability | Implementation |
|---|---|
| Model registry | Immutable model versions (artifact digest, score binning, decision threshold, features, subgroups, validation protocol, reference population); approved versions per site with expiry |
| Benchmark harness | Sensitivity, specificity, PPV, NPV with Wilson intervals; AUROC with a stratified bootstrap interval; ECE, MCE and Brier score; subgroup performance and AUROC gaps |
| Population shift | PSI and binned two-sample KS for continuous features and scores; Pearson chi-square for categorical features; Bonferroni-adjusted significance; label shift by Black Box Shift Estimation |
| Method verification | Accuracy (PPA, NPA, OPA, Cohen's kappa), precision (EP15-style ANOVA with upper verification limits), reportable range (EP6-style polynomial linearity with range trimming) |
| Reapproval | Passing validations are submitted for sign-off; only a medical director of that site can sign, with fresh authentication, a two-person rule and an electronic signature bound to the evidence digest |
| Fail-safe restriction | A major population shift withdraws permission to use the model at that site until it is revalidated |
| Federated mode | `clv-site` summarises case extracts locally (reading only model-relevant columns, suppressing small subgroups) and submits aggregates with an agent credential |
| Agent access | MCP tools (official `mcp` SDK, streamable HTTP at `/mcp`) and an A2A agent card plus JSON-RPC `message/send` at `/a2a` |
| Identity and policy | Staff via enterprise OIDC; agents via [agent-identity-fabric](https://github.com/kogunlowo123/agent-identity-fabric) introspection and authorization, combined with the local policy (most restrictive wins) |
| Audit | Every decision and state change in an Ed25519-signed hash chain ([agentic-core](https://github.com/kogunlowo123/agentic-core)) |
| Observability | Prometheus metrics, OpenTelemetry traces, JSON logs with correlation IDs, alert rules, Grafana dashboard |

No generative model sits in the decision path (see
[ADR-0002](docs/architecture/adr/0002-deterministic-statistics-no-llm.md)).

## Quick start (local)

Requires Docker. No API keys or cloud credentials are needed.

```bash
docker compose up --build
curl -s localhost:8080/.well-known/agent-card.json | jq '.skills[].id'
```

The compose stack runs PostgreSQL, an OIDC provider standing in for the
enterprise IdP, agent-identity-fabric v0.1.0 and an OpenTelemetry collector.

Summarise a synthetic case extract the way a site would:

```bash
uv run clv-site summarize --model model.json --cases cases.csv --out aggregates.json
```

## Development

```bash
uv sync
make check        # ruff, black, mypy --strict, bandit, contracts, tests (coverage >= 90%)
make integration  # PostgreSQL tests (needs CLV_TEST_DATABASE_URL)
make helm terraform audit-deps
```

Test suites: `tests/unit` (statistics cross-checked against SciPy and
textbook values, domain rules, workflow), `tests/security` (one test per
threat-model entry), `tests/chaos` (dependency failures fail closed),
`tests/performance` (latency budgets; `locustfile.py` for load),
`tests/e2e` (REST, MCP and A2A lifecycle plus a property-based OpenAPI
contract test) and `tests/integration` (PostgreSQL). All test data is
synthetic.

## API

| Endpoint | Caller | Purpose |
|---|---|---|
| `POST /v1/sites`, `POST /v1/models` | Model registrar | Register laboratories and model versions |
| `GET /v1/sites`, `GET /v1/models`, `GET /v1/models/{id}/{version}` | Staff, scoped agents | Registry |
| `GET /v1/sites/{site}/approvals` | Staff, scoped agents | Approved versions per site and whether use is permitted now |
| `POST /v1/benchmarks` | Validation scientist, site agent | Benchmark report from a performance aggregate |
| `POST /v1/shift-analyses` | Validation scientist, site agent | Shift report from a population aggregate; restricts approval on major shift |
| `POST /v1/validations` | Validation scientist, agent | Evaluate the protocol against a benchmark, a shift report and verification studies |
| `GET /v1/reports/{id}` | Staff, scoped agents | Any report |
| `POST /v1/reapprovals` | Validation scientist, agent | Submit a passing validation; always `pending_signoff` |
| `GET /v1/reapprovals`, `GET /v1/reapprovals/{id}` | Staff, scoped agents | Sign-off queue |
| `POST /v1/reapprovals/{id}/signature` | Medical director | Approve or reject (21 CFR Part 11 electronic signature) |
| `/mcp` | Agents | MCP tools (no signing tool exists) |
| `GET /.well-known/agent-card.json`, `POST /a2a` | Agents | A2A discovery and `message/send` |

Full contract: [docs/api/openapi.json](docs/api/openapi.json); JSON Schemas in [schemas/](schemas/).

## Deployment

| Layer | Location |
|---|---|
| Container image | `Dockerfile` (non-root, read-only root filesystem compatible) |
| Helm chart | `deployments/helm/cross-lab-ai-validation` (StatefulSet, PDB, HPA, NetworkPolicy, ServiceMonitor) |
| Namespace and secret sync | `deployments/kubernetes` (restricted Pod Security, External Secrets) |
| Cloud infrastructure | `deployments/terraform/{aws,azure,gcp}`: managed PostgreSQL, KMS, secret store, WORM audit storage, workload identity. AWS is the reference deployment |

See the [startup runbook](docs/runbooks/startup.md) and the
[cloud reference](docs/architecture/cloud-reference.md).

## Documentation

- [Architecture](docs/architecture/overview.md), [statistical methods](docs/architecture/statistical-methods.md), [agent specification](docs/architecture/agent-specification.md), [acceptance criteria](docs/architecture/acceptance-criteria.md), [cloud reference, cost and scale](docs/architecture/cloud-reference.md)
- [ADRs](docs/architecture/adr/)
- [Threat model and risk register](docs/security/threat-model.md)
- [Compliance control mapping](docs/compliance/control-mapping.md) (CLIA/CAP, 21 CFR Part 11, FDA CSA, HIPAA, HITRUST, SOC 2, ISO 27001, NIST AI RMF, NIST 800-53)
- Runbooks: [startup](docs/runbooks/startup.md), [monitoring](docs/runbooks/monitoring.md), [shift response](docs/runbooks/shift-response.md), [incident response](docs/runbooks/incident-response.md), [rollback](docs/runbooks/rollback.md), [disaster recovery](docs/runbooks/disaster-recovery.md)
- [Configuration reference](docs/operations/configuration.md)

## License

Apache-2.0
