# ADR-0001: Sites share aggregate statistics only

- Status: Accepted
- Date: 2026-10-14

## Context

Cross-site validation needs each laboratory's outcomes for the model, but
moving case-level data between institutions requires data-sharing agreements,
de-identification review and creates a central store of PHI. Federated
learning frameworks solve a harder problem (training) than we have
(evaluation).

## Decision

- Sites run `clv-site` inside their boundary. It reads only the columns the
  model specification names and produces score histograms (per bin: positive
  and negative counts and score sums, plus the total sum of squared scores),
  feature histograms on registered edges, categorical counts and the
  unlabeled score distribution.
- These aggregates are sufficient statistics for every metric we report:
  threshold metrics are exact because the threshold is a bin edge; AUROC is
  the Mann-Whitney statistic on binned scores; the stratified bootstrap is an
  exact multinomial resampling of bin counts; ECE and the Brier score are
  exact from the sums.
- Aggregate schemas forbid unknown fields, so identifiers cannot be added to
  a submission; the API enforces a minimum population size and the site
  agent suppresses small subgroups.
- KS on binned data is computed at bin boundaries and is therefore
  conservative; this is documented with the method.

## Consequences

- No case-level data is stored centrally; the platform's data classification
  is CONFIDENTIAL, not RESTRICTED.
- AUROC resolution is limited by the registered bin width; models register at
  least 20 bins.
- Method-verification studies (agreement counts, precision replicates,
  linearity levels) are measured on verification material and submitted as
  study data.
