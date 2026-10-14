# Acceptance criteria

Each criterion names the automated test that demonstrates it. The CI run for
a release tag is the operational-qualification evidence for that version.

| # | Criterion | Evidence |
|---|---|---|
| AC1 | Sensitivity, specificity, PPV, NPV and Wilson intervals match SciPy for known tables | `tests/unit/test_classification.py::test_wilson_matches_scipy`, `test_diagnostic_metrics_from_known_table` |
| AC2 | AUROC from aggregates equals the Mann-Whitney U statistic, including ties | `test_auroc_equals_mann_whitney_u_with_ties` |
| AC3 | The aggregate bootstrap reproduces case-level bootstrap intervals and is deterministic for identical evidence | `test_bootstrap_matches_case_level_resampling_distribution`, `test_bootstrap_is_reproducible_and_brackets_estimate` |
| AC4 | ECE and Brier score from aggregates equal direct case-level computation | `test_calibration_matches_direct_computation` |
| AC5 | PSI matches a hand calculation; KS and chi-square match SciPy | `tests/unit/test_shift.py` |
| AC6 | BBSE recovers a known prevalence exactly under pure label shift | `test_bbse_recovers_true_prior_exactly` |
| AC7 | Kappa, EP15 ANOVA components, Satterthwaite df and verification limits match independent computation | `tests/unit/test_method_verification.py` |
| AC8 | Linearity verifies a linear range and detects saturation, reporting the verified sub-range | `test_linearity_linear_data_verifies_full_range`, `test_linearity_detects_saturation_at_top_of_range` |
| AC9 | A comparable site passes the protocol; a shifted site fails it | `tests/unit/test_service.py::test_full_reapproval_workflow`, `test_validation_fails_under_major_shift` |
| AC10 | Clinical use is granted only by a medical director of that site, with fresh authentication, who did not make the request | `test_signature_controls`, `tests/security/test_attacks.py::test_t9_two_person_rule_and_fresh_authentication` |
| AC11 | Agents cannot sign through REST, MCP or A2A, and no signing tool exists | `test_t5_agents_cannot_approve_clinical_use`, `tests/e2e/test_http_lifecycle.py::test_full_lifecycle` |
| AC12 | A major shift withdraws an existing approval in the same request | `test_major_shift_withdraws_approval`, e2e lifecycle |
| AC13 | Altered evidence cannot be submitted or signed | `test_tampered_evidence_cannot_be_signed`, `test_request_time_integrity_and_policy_denial` |
| AC14 | Case-level fields and PHI in free text are rejected | `test_t7_record_level_data_cannot_be_smuggled`, `test_t8_phi_in_free_text_is_refused` |
| AC15 | The audit chain verifies end to end and contains no credentials | `test_full_reapproval_workflow`, `test_t10_audit_chain_has_no_credentials` |
| AC16 | Dependency failures fail closed | `tests/chaos/` |
| AC17 | No generated API input causes a server error | `tests/e2e/test_api_contract.py` |
| AC18 | Report builds stay within latency budgets | `tests/performance/test_latency_budgets.py` |
| AC19 | Store behaviour on PostgreSQL matches SQLite | `tests/integration/test_postgres.py`, CI `integration-postgres` |
| AC20 | Branch coverage at least 90% | CI `quality` job |
