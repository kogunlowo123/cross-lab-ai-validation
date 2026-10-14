# Contributing

## Setup

```bash
uv sync
make check
```

## Requirements for every change

- `make check` passes: ruff, black, mypy `--strict`, bandit, contract drift,
  tests with at least 90% branch coverage.
- New behaviour has tests. Statistical methods are checked against a
  reference implementation or a published worked example. Security controls
  need adversarial cases in `tests/security` mapped to the threat model.
- Public API or schema changes regenerate contracts
  (`uv run python -m cross_lab_ai_validation.contracts`) and are recorded in
  `CHANGELOG.md`.
- Changes to a statistical method, the approval workflow or the policy need
  an ADR in `docs/architecture/adr/` and an update to
  `docs/architecture/statistical-methods.md` or the control mapping.
- Synthetic data only. No patient data, real site identifiers or
  credentials in code, tests or fixtures.

## Commit messages

Conventional Commits (`feat:`, `fix:`, `docs:`, `refactor:`, `test:`,
`chore:`). Breaking changes use `!` and a `BREAKING CHANGE:` footer.

## Review

Changes to `src/cross_lab_ai_validation/security`,
`src/cross_lab_ai_validation/evaluation`, `policies/` or `deployments/` need
approval from a code owner (see `CODEOWNERS`).
