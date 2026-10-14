# ADR-0004: Local assurance policy combined with the identity fabric, most restrictive wins

- Status: Accepted
- Date: 2026-10-14

## Context

agent-identity-fabric authenticates agents and makes generic, scope-based
authorization decisions for the whole agent estate. This platform has
domain rules the fabric cannot know: agents never sign, callers act only on
their own sites, signatures need fresh authentication, restriction is
allowed only on a major shift.

## Decision

- Every request is evaluated against `policies/assurance-authorization.yaml`
  with the agentic-core `PolicyEngine` (deny overrides, then
  require_approval, then allow; default deny).
- For agents, when `CLV_IDENTITY_FABRIC_URL` is set, the credential is
  introspected at the fabric and the fabric's `/v1/authorize` decision is
  also obtained. The final decision is the more restrictive of the two.
- Without a fabric, agent credentials are verified locally against the
  fabric's JWKS (`CLV_AGENT_*`); with neither configured, agent credentials
  are refused. There is no allow-all mode.
- A fabric outage fails closed with HTTP 503; staff authentication does not
  depend on the fabric.

## Consequences

- Domain safety rules hold even if the fabric's policy is permissive.
- Each decision records which sources contributed and the policy digest.
- Agent requests incur two calls to the fabric; both are low-latency and the
  fabric is deployed in-cluster.
