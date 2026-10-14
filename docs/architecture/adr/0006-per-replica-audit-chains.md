# ADR-0006: Per-replica signed audit chains (StatefulSet)

- Status: Accepted
- Date: 2026-10-14

## Context

The agentic-core `AuditLog` is a hash chain: each event includes the hash of
its predecessor and is Ed25519-signed. A chain needs a single writer to stay
linear. Several replicas writing one file or table would interleave and
break linkage, and a shared lock across replicas would serialise the API.

## Decision

- Each replica writes its own chain to `/var/lib/cross-lab-ai-validation/audit/<pod>.jsonl`
  on its own persistent volume, so the chart uses a StatefulSet with a
  volume claim template.
- A log shipper copies chains to WORM object storage (S3 Object Lock,
  immutable blob storage or a retention-locked bucket from Terraform).
- Chains are verified independently with `agentic_core.audit.verify_chain`;
  events carry correlation IDs so a request can be followed across replicas.
- A failed audit write fails the request: no decision is returned that is
  not in the chain.

## Consequences

- Scaling adds chains, not contention.
- Losing a volume before shipping loses that replica's unshipped tail; the
  shipping interval bounds the exposure and the disaster-recovery runbook
  covers it.
