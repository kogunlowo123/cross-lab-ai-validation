# ADR-0002: Deterministic statistics; no generative model in the decision path

- Status: Accepted
- Date: 2026-10-14

## Context

The platform decides whether a diagnostic model may be used on patients. The
evidence must be reproducible by an inspector years later, and laboratories
must not release diagnostic outcomes on model confidence. A language model
could draft narrative summaries, but any text it produced would sit next to,
and be read as, regulatory evidence.

## Decision

- All assurance outputs are produced by deterministic statistical code in
  `evaluation/`. Bootstrap seeds derive from the evidence digest, so
  identical evidence yields byte-identical reports.
- The service does not call a foundation model; it therefore does not
  configure the agentic-core `ModelRouter` or `configs/providers.yaml`.
  Agents that orchestrate the workflow may use models on their side, but
  what they submit is validated typed data and what they receive is computed
  evidence.
- The agentic-core security detectors (PII, secrets) are still applied to
  every free-text field the platform accepts.

## Consequences

- Reports are reproducible and testable against reference implementations.
- Narrative interpretation stays with the validation scientist and the
  medical director, who sign their statement.
- If narrative drafting is added later, it must sit outside the evidence
  digest and be labelled as such.
