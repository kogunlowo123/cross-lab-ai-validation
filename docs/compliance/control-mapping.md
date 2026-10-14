# Compliance control mapping

The evidence column names the artifact an auditor or inspector can examine.
Mappings show how the platform supports each control; the operating
laboratory remains responsible for its quality system.

## Laboratory regulation (CLIA, CAP)

| Requirement | How the platform supports it | Evidence |
|---|---|---|
| 42 CFR 493.1253(b)(1): verify accuracy, precision and reportable range before reporting patient results | Protocol criteria for agreement (PPA/NPA/kappa), EP15-style precision with verification limits, EP6-style reportable range; validation verdict per site | `ValidationRun` criteria, `docs/architecture/statistical-methods.md` |
| 42 CFR 493.1253(b)(1)(ii): verify reference intervals are appropriate for the population | Population shift analysis against the model's reference population; label-shift estimate | `ShiftReport` |
| 42 CFR 493.1253(c): document all verification activities | Append-only reports with digests; signed audit chain | `reports` table, audit chains |
| 42 CFR 493.1445(e): laboratory director responsibility for test performance | Only a medical director of the site can approve use; signature bound to evidence | `ElectronicSignature`, `reapproval.sign` events |
| CAP COM.40000-series (method performance specifications) | Same as 493.1253 evidence, per site and per model version | Validation runs and approvals per site |
| CAP COM.04250 (comparability of results across instruments/sites) | Cross-site benchmark comparison and subgroup gaps against the reference | Benchmark reports per site |
| CAP GEN.43875-series (information system validation) | CI evidence for each release; contracts checked in CI | Release CI run, `docs/api/openapi.json` |

## Electronic records and signatures (21 CFR Part 11) and FDA CSV/CSA

| Clause | Control | Evidence |
|---|---|---|
| 11.10(a) validation | Automated test suites with acceptance criteria per release (risk-based CSA approach: scripted tests for high-risk functions such as statistics and sign-off) | `docs/architecture/acceptance-criteria.md`, CI run for the tag |
| 11.10(b) accurate and complete copies | Reports retrievable as JSON with digests | `GET /v1/reports/{id}` |
| 11.10(c) record protection | Append-only reports, WORM audit storage, 7-year retention | Terraform audit bucket configuration |
| 11.10(d) limiting system access | OIDC roles, agent credentials, site scoping | `policies/assurance-authorization.yaml` |
| 11.10(e) audit trails | Time-stamped, hash-chained, signed events for every change | Audit chains, `verify_chain` |
| 11.10(f) operational checks | Workflow sequencing enforced (validation must pass and be sealed before request; request before signature) | `AssuranceService` |
| 11.10(g) authority checks | Policy evaluation per action; two-person rule | Policy digest in every decision |
| 11.50 signature manifestation | Printed name, time, meaning in the signed record | `ElectronicSignature` |
| 11.70 signature/record linking | Signature contains the evidence digest; tampering breaks the seal | `evidence_digest`, `verify_seal` |
| 11.200(a) signature components | Fresh authentication (`auth_time` within 5 minutes) at the IdP for each signing session | `deny-stale-signature-authentication` rule |

## Security and privacy frameworks

| Control objective | HIPAA Security Rule | HITRUST CSF | SOC 2 (TSC 2017) | ISO/IEC 27001:2022 | NIST SP 800-53 r5 | NIST AI RMF 1.0 | Evidence |
|---|---|---|---|---|---|---|---|
| Minimum necessary / data minimisation | 164.502(b), 164.514(d) | 13.l | C1.1 | 5.34, 8.11 | PT-2, SA-8(33) | MAP 4.1 | Aggregate-only schemas, site agent column selection |
| Unique identification of users and agents | 164.312(a)(2)(i) | 01.q | CC6.1 | 5.16 | IA-2, IA-9 | GOVERN 1.4 | OIDC subjects, SPIFFE agent IDs in audit |
| Access control and least privilege | 164.308(a)(4) | 01.c | CC6.3 | 5.15, 8.2 | AC-3, AC-6 | MANAGE 2.4 | Policy file, scopes, site coverage |
| Audit controls | 164.312(b) | 09.aa | CC7.2 | 8.15 | AU-2, AU-9, AU-10 | MEASURE 2.8 | Signed audit chains |
| Integrity of evidence | 164.312(c)(1) | 09.ab | PI1.4 | 8.24 | SI-7 | MEASURE 2.6 | Evidence digests, seals |
| Transmission security | 164.312(e)(1) | 09.m | CC6.7 | 8.24 | SC-8 | — | TLS ingress, forced TLS to databases |
| Model performance validation before use | — | — | PI1.2 | 8.29 | SA-11, CA-2 | MEASURE 2.3, 2.5 | Validation runs, acceptance criteria |
| Fairness / subgroup performance | — | — | — | — | — | MEASURE 2.11 | Subgroup AUROC gaps and sensitivity criteria |
| Monitoring for drift | 164.308(a)(8) | 09.ab | CC7.1 | 8.16 | SI-4, CA-7 | MANAGE 4.1, MEASURE 2.4 | Shift reports, alerts, automatic restriction |
| Human oversight of AI decisions | — | — | CC1.3 | 5.2 | — | GOVERN 3.2, MANAGE 2.2 | Medical-director sign-off; agents cannot approve |
| Change management | 164.308(a)(8) | 10.k | CC8.1 | 8.32 | CM-3 | GOVERN 1.4 | Versioned models and protocols, CODEOWNERS, protocol digests |
| Incident response | 164.308(a)(6) | 11.a | CC7.3 | 5.24 | IR-4 | MANAGE 4.3 | Runbooks, alert rules |
| Availability and recovery | 164.308(a)(7) | 12.c | A1.2 | 8.14 | CP-9, CP-10 | — | Multi-AZ database, PITR, DR runbook |
