# Threat model

## Assets

| Asset | Impact if compromised |
|---|---|
| Site approvals | A model used where it was never verified, or withdrawn without cause |
| Validation evidence (reports, digests) | Approval granted on altered evidence |
| Signatures | Repudiation of a clinical-use decision; forged approvals |
| Audit chains | Loss of accountability; regulatory breach (CLIA, Part 11) |
| Site case extracts (at the site) | PHI disclosure |
| Audit signing key | Forged audit history |

## Trust boundaries

1. Laboratory network → platform (site agent submissions over TLS).
2. Agents and staff → platform API, `/mcp`, `/a2a`.
3. Platform → identity fabric and IdP JWKS.
4. Platform → PostgreSQL, secret store, audit storage.

## STRIDE

| ID | Threat | Category | Control | Test |
|---|---|---|---|---|
| T1 | Forged staff token with a guessed key ID | Spoofing | Signature verification against the IdP JWKS | `tests/security::test_t1_forged_staff_token` |
| T2 | `alg: none` or algorithm confusion | Spoofing | Accepted algorithms allow-listed per verifier | `test_t2_alg_none_rejected`, `test_t4_wrong_audience_and_expired_tokens` (HS256) |
| T3 | Role escalation by editing token claims | Elevation | Signature covers claims | `test_t3_payload_tampering` |
| T4 | Token for another application, or expired | Spoofing | Issuer, audience, `exp` checks | `test_t4_wrong_audience_and_expired_tokens` |
| T5 | Agent approves clinical use | Elevation | Policy deny for agents, no signing tool, approval only through a signature | `test_t5_agents_cannot_approve_clinical_use` |
| T6 | Agent acts on another laboratory | Elevation | `site:access:<site>` scopes and `deny-outside-site` | `test_t6_agent_outside_site_scope` |
| T7 | Case-level data smuggled in an aggregate | Information disclosure | `extra="forbid"` on every aggregate model | `test_t7_record_level_data_cannot_be_smuggled` |
| T8 | PHI or secrets pasted in free text | Information disclosure | agentic-core PII and secret detectors; reject, never store | `test_t8_phi_in_free_text_is_refused` |
| T9 | Self-approval, stale session, director of another site | Elevation / repudiation | Two-person rule, `auth_time` window, site coverage | `test_t9_two_person_rule_and_fresh_authentication`, `tests/unit/test_service.py::test_signature_controls` |
| T10 | Credentials leak into audit; audit tampering | Disclosure / repudiation | No tokens in audit details; signed hash chain | `test_t10_audit_chain_has_no_credentials`, `test_full_reapproval_workflow` (verify_chain) |
| T11 | DNS rebinding against `/mcp` from a browser | Spoofing | Host and Origin allow-lists | `test_t11_mcp_dns_rebinding_protection` |
| T12 | Auditor or outsider reads or changes another site's evidence | Elevation | Role and site checks on every read and write | `test_t12_staff_role_boundaries` |
| T13 | Evidence altered in the database after validation | Tampering | Evidence digest sealed at validation, checked at request and signature | `test_tampered_evidence_cannot_be_signed`, `test_request_time_integrity_and_policy_denial` |
| T14 | Identity fabric or IdP outage used to bypass checks | DoS / elevation | Fail closed (503 for agents, 401 for staff) | `tests/chaos/` |
| T15 | Audit write fails silently | Repudiation | Audit failure fails the request | `test_audit_failure_blocks_the_result` |
| T16 | Fabricated aggregates to pass validation | Tampering | Submissions are attributed to the site agent's attested identity; evidence and digests audited; medical director reviews | Organisational control + audit |

## MITRE ATT&CK (Enterprise)

| Technique | Mitigation |
|---|---|
| T1078 Valid Accounts | OIDC with roles and sites; short-lived agent credentials; fresh authentication for signatures |
| T1528 Steal Application Access Token | Fabric introspection on every agent call; audience binding |
| T1565.001 Stored Data Manipulation | Evidence digests, append-only reports, signed audit chains |
| T1530 Data from Cloud Storage | Private, KMS-encrypted stores; no case-level data centrally |
| T1562.008 Disable Cloud Logs | WORM audit storage; audit failure fails closed |
| T1190 Exploit Public-Facing Application | Strict input schemas, property-based contract test, SAST, image scanning |

## MITRE ATLAS (AI-specific)

| Technique | Relevance | Control |
|---|---|---|
| AML.T0015 Evade ML Model / AML.T0043 Craft Adversarial Data | Inputs crafted to make a model look better at one site | Site-level benchmark on real outcomes; subgroup gaps; shift detection against reference |
| AML.T0020 Poison Training Data | Submitting manipulated aggregates to pass validation | Attributed submissions, audit, human sign-off; aggregates must be consistent (bin sums within counts, totals) |
| AML.T0031 Erode ML Model Integrity | Gradual drift degrades performance | Periodic shift analysis; major shift withdraws use; approvals expire |
| AML.T0048 External Harms | Model used outside its intended population | Population-shift criterion in every validation |

## OWASP Top 10 for LLM Applications (2025)

The platform does not call an LLM (ADR-0002); the relevant risks concern the
agents that call it.

| Risk | Control |
|---|---|
| LLM06 Excessive Agency | Agents cannot sign, approve, or register; reapproval always waits for a human |
| LLM01 Prompt Injection | A manipulated agent can only submit typed data within its scopes; outcomes still require human sign-off |
| LLM02 Sensitive Information Disclosure | No case-level data accepted; free text scanned for identifiers |
| LLM09 Misinformation | Evidence is computed, reproducible and digest-sealed, not generated |

## OWASP API Security Top 10 (2023)

| Risk | Control |
|---|---|
| API1 Broken Object Level Authorization | Site coverage checked on every report, request and signature |
| API2 Broken Authentication | Signed tokens only, allow-listed algorithms, issuer/audience checks, fabric introspection |
| API3 Broken Object Property Level Authorization | `extra="forbid"` on inputs; credentials excluded from serialisation |
| API4 Unrestricted Resource Consumption | Bounded array sizes, paging limits, bootstrap iterations capped at 20,000, HPA |
| API5 Broken Function Level Authorization | Distinct roles for registration, assurance, signature, audit |
| API6 Unrestricted Access to Sensitive Business Flows | Reapproval and signature are human-gated with a two-person rule |
| API8 Security Misconfiguration | Production refuses ephemeral audit keys; Helm `required`; restricted Pod Security; MCP host checks |
| API10 Unsafe Consumption of APIs | Fabric and JWKS responses validated; malformed or unavailable responses fail closed |

## Risk register

| ID | Risk | Likelihood | Impact | Owner | Treatment | Residual |
|---|---|---|---|---|---|---|
| R1 | Site submits aggregates that do not reflect its cases | Low | High | Laboratory director | Attested site agent, audit, scientist review, periodic re-benchmark | Medium |
| R2 | BBSE misestimates prevalence under concurrent covariate shift | Medium | Medium | AI governance | Reported alongside feature shift; not a pass/fail criterion on its own | Low |
| R3 | Policy file edited to loosen controls | Low | High | AI governance | CODEOWNERS, policy digest in every decision; signature path not policy-controlled | Low |
| R4 | Medical director signs without reviewing evidence | Medium | High | Laboratory director | Statement required, evidence digest shown, audit, periodic QA review | Medium |
| R5 | PostgreSQL outage blocks validations | Medium | Medium | Platform | Multi-AZ, readiness probe, PITR | Low |
| R6 | Audit volume lost before shipping | Low | Medium | Platform | Short shipping interval, persistent volumes, DR runbook | Low |
