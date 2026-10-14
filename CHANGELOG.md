# Changelog

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow [SemVer](https://semver.org/).

## [0.1.0] - 2026-10-14

### Added

- Model registry with immutable versions, validation protocols and reference
  aggregates; approved versions per site with expiry.
- Benchmark harness on federated aggregates: Wilson intervals for
  sensitivity, specificity, PPV and NPV; AUROC with an exact stratified
  bootstrap; ECE, MCE and Brier score; subgroup performance.
- Population shift detection: PSI, binned KS, chi-square with Bonferroni
  adjustment, and Black Box Shift Estimation of prevalence.
- CLIA/CAP-style method verification: agreement and kappa, EP15-style
  precision with verification limits, EP6-style reportable range with range
  trimming.
- Reapproval workflow with medical-director electronic signatures bound to
  sealed evidence, two-person rule and fresh authentication; fail-safe
  restriction on major shift.
- `clv-site` site agent: local summarisation and submission with an
  agent-identity-fabric credential.
- MCP server (five tools) and A2A agent card with `message/send`.
- Layered authorization (local policy plus identity fabric), signed audit
  chains, Prometheus metrics, alert rules and dashboard.
- Helm chart, Kubernetes namespace and secret sync, Terraform for AWS, Azure
  and GCP.
- CI: lint, types, SAST, contract drift, tests (unit, security, chaos,
  performance, e2e, PostgreSQL integration), dependency audit, Helm and
  kubeconform, Terraform validate, Trivy, Syft, Grype, Semgrep, container
  smoke test; release workflow with provenance attestation.
