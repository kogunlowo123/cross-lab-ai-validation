# ADR-0003: Clinical use changes only through a medical director's signature

- Status: Accepted
- Date: 2026-10-14

## Context

CLIA places responsibility for test performance on the laboratory director.
An agent that could approve a model, or a policy edit that let one, would
move accountability to software. 21 CFR Part 11 requires electronic
signatures to show the signer, time and meaning and to be linked to the
record they sign.

## Decision

- A validation run is sealed by `evidence_digest`, a sha256 over its full
  content, including the digests of the benchmark and shift reports and the
  protocol.
- `request_reapproval` accepts only a passing, intact validation and always
  returns `pending_signoff`. The policy is consulted for `approval:grant`
  so its decision and digest are recorded, but the code never grants use on
  a policy result: a signature is the only path.
- Signing requires the `medical-director` role for that site, an
  authentication time within five minutes (Part 11 §11.200), a signer
  different from the requester, and an unchanged evidence digest. The
  signature records signer, printed name, meaning, time, authentication time,
  evidence digest and statement (§11.50, §11.70), and the decision and the
  resulting approval are written in one transaction.
- Approvals expire after the protocol's revalidation interval.
- Restriction is asymmetric: a major population shift withdraws use
  automatically (fail-safe); restoring use always needs a new signature.

## Consequences

- Agents can do all the work up to the decision and none of the decision.
- Altering stored evidence after a request breaks the seal and blocks the
  signature.
- Directors must re-authenticate shortly before signing; the IdP must emit
  `auth_time`.
