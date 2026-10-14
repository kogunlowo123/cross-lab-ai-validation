# Runbook: incident response

## Elevated 5xx

`ClvErrorBudgetBurnFast`.

1. `kubectl -n lab-ai-assurance get pods`; check restarts and readiness.
2. `/readyz` returns 503 when PostgreSQL is unreachable; check the database
   and the `DATABASE_URL` secret sync.
3. Logs: filter by `correlation_id` from a failing response's
   `X-Correlation-ID` header.
4. 503 with `temporarily_unavailable` on agent calls means the identity
   fabric is unreachable: requests fail closed by design. Restore the fabric;
   staff access continues.
5. 500s with `OSError` in logs and no audit growth: the audit volume is full
   or read-only. Expand the PVC; requests fail until the chain can be
   written (by design).

## Denial spike

`ClvAuthorizationDenialSpike`.

1. Group recent `deny` / `require_approval` audit events by actor and rule.
2. A single agent with `deny-outside-site` or missing scope: fix its
   registration at the identity fabric.
3. Many actors with `deny-stale-signature-authentication`: the IdP stopped
   emitting `auth_time` or the re-authentication prompt; fix the IdP app.
4. Unknown actors probing: suspend the agent at the fabric
   (`POST /v1/suspend`), which invalidates its credentials immediately, and
   open a security incident.

## Agent protocol errors

`ClvAgentProtocolErrors` (more than 25% of MCP/A2A calls fail).

- `invalid_params`: a client is sending the wrong schema; compare with
  `schemas/` and the MCP `tools/list` output.
- `unauthorized`: credentials expired or not issued for this audience.
- `forbidden`: scopes do not cover the action or site.

## Suspected evidence tampering

Signals: `InvalidStateError` responses mentioning the evidence digest, or a
`verify_chain` failure.

1. Freeze: revoke database write credentials other than the service's;
   snapshot the database.
2. Verify every replica's audit chain from WORM storage.
3. Compare report documents with the digests recorded in audit events
   (`validation.run` details carry `evidence_digest`).
4. Approvals signed on affected evidence: notify the medical directors; treat
   as `revalidation_required` until re-signed.

## Replicas down

`ClvNotReady`: fewer than two replicas scraped. Check node capacity, PVC
binding (each replica needs its audit volume) and image pulls.
