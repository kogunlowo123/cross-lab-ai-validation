# Runbook: monitoring

Dashboard: `configs/grafana/cross-lab-ai-validation.json`. Alerts:
`configs/prometheus/alerts.yaml`.

## Signals to watch

| Panel | Healthy | Investigate when |
|---|---|---|
| Request success | ≥ 99.9% | Error-budget burn alert fires |
| Evaluations by kind and outcome | Steady benchmark/shift flow from site agents | A site stops submitting (missing benchmark series) |
| Shift analyses by severity | Mostly `none` | Any `major` (see shift-response runbook) |
| Approvals withdrawn | 0 | Any increase |
| Medical-director signatures | Matches expected go-lives and renewals | Rejections cluster on one model version |
| MCP and A2A invocations | Mostly `success` | `forbidden`, `invalid_params` or `unauthorized` grow |
| Policy denials | Low, steady | Spike (misconfigured agent or probing) |

## Latency

`ClvEvaluationLatencyHigh` (p95 > 1 s on evaluation routes):

1. Check CPU throttling: `kubectl -n lab-ai-assurance top pods`.
2. Check the protocol's `bootstrap_iterations` for the model involved; the
   cost is linear in iterations (the protocol caps it at 20,000).
3. Check database latency (RDS Performance Insights / Query Store / Cloud SQL
   insights).
4. Scale out if CPU-bound: the HPA should already be acting; raise
   `autoscaling.maxReplicas` if it is pinned.

## Validation failures

`ClvValidationFailures`: more than three failed validations in 24 hours.
Open the failed runs (`GET /v1/reports/{id}`) and group by failed criterion:

- `population_shift`: follow the shift-response runbook.
- `min_cases` / `min_positives`: the site needs a larger evaluation window.
- `sensitivity`, `auroc_lower_ci`, `ece`, subgroup criteria: performance
  genuinely differs at the site; escalate to the model owner and the site's
  medical director; do not loosen the protocol without change control.
- Study criteria (`accuracy_*`, `precision`, `reportable_range`): review the
  verification study design with the validation scientist.

## Audit chain verification (weekly)

```bash
uv run python - <<'PY'
import json
from pathlib import Path
from cryptography.hazmat.primitives.serialization import load_pem_public_key
from agentic_core.audit import AuditEvent, verify_chain
key = load_pem_public_key(Path("audit-2026-10.pub").read_bytes())
events = [AuditEvent.model_validate_json(l) for l in Path("clv-0.jsonl").read_text().splitlines()]
print(verify_chain(events, {"audit-2026-10": key}), "events verified")
PY
```

Run against the copies in WORM storage for each replica's chain.
