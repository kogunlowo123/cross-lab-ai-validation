# Statistical methods

Every number in a report is computed by `src/cross_lab_ai_validation/evaluation`
from aggregate statistics. The test suite checks each method against SciPy
or a textbook value (`tests/unit/test_special.py`, `test_classification.py`,
`test_shift.py`, `test_method_verification.py`).

## Inputs: why aggregates are sufficient

A model's output is a probability score. The model registers score bin edges
from 0 to 1 and a decision threshold that is one of those edges. A site
submits, per bin, the number of positive and negative cases and the sum of
their scores, plus the total sum of squared scores. That is a sufficient
statistic for everything below:

| Quantity | From the aggregate |
|---|---|
| Confusion matrix at the threshold | Bins at or above the threshold edge are predicted positive (exact) |
| AUROC | Mann-Whitney U on binned scores: `sum_i neg_i * (pos_above_i + pos_i / 2) / (P * N)` |
| AUROC bootstrap | Stratified case resampling of binned scores is a multinomial draw of bin counts with observed proportions, so it is computed exactly |
| ECE | Per bin, observed rate `pos_b / n_b` versus mean score `(sum_pos_b + sum_neg_b) / n_b` |
| Brier score | `(sum s^2 - 2 * sum_{y=1} s + P) / N` |

The resolution of the AUROC is the bin width; models register enough bins
(20 or more) that discretisation is negligible relative to sampling error.

## Diagnostic accuracy

- Sensitivity, specificity, PPV, NPV, accuracy: point estimates with Wilson
  score intervals (Wilson, 1927), which keep coverage near nominal at the
  extremes where Wald intervals fail.
- AUROC: point estimate plus a percentile bootstrap interval with
  `protocol.bootstrap_iterations` replicates. The seed is derived from the
  aggregate's digest, so the same evidence always gives the same interval.
- Calibration: expected calibration error (Naeini et al., 2015) over the
  registered bins, maximum calibration error, Brier score.
- Subgroups: per `<attribute>=<value>` histogram, sensitivity, specificity
  and AUROC; a subgroup is reportable when it has at least
  `subgroup_min_cases` cases and both classes. The AUROC gap is overall
  minus subgroup.

## Population shift

| Method | Use | Notes |
|---|---|---|
| PSI | Every continuous feature, every categorical feature and the score distribution | `sum (a - e) ln(a / e)` over bin proportions; empty bins floored at 1e-4. Severity: `>= psi_moderate` moderate, `>= psi_major` major (defaults 0.10 and 0.25) |
| Two-sample KS | Continuous features and scores | Statistic over shared bin boundaries (a lower bound on the case-level statistic, so conservative); p-value from the limiting Kolmogorov distribution at `sqrt(nm / (n + m)) * D` |
| Pearson chi-square | Categorical features | Test of homogeneity over the 2 x k table; Cramér's V; minimum expected count reported so sparse tables can be judged |
| Multiple testing | All of the above | Bonferroni: significance at `shift_alpha / (1 + number of features)` |
| Label shift | Prevalence | Black Box Shift Estimation (Lipton, Wang and Smola, 2018): solve `C w = mu`, where `C` is the reference joint distribution of predicted and true class and `mu` the site's predicted-class distribution. Needs no labels from the site. Negative weights are clipped; an ill-conditioned `C` is reported as not estimable |

Severity drives governance: a major shift fails the `population_shift`
criterion of any validation and withdraws an existing site approval. A
prevalence change beyond `prevalence_shift_max` raises severity to at least
moderate because predictive values change even when sensitivity and
specificity do not.

BBSE assumes label shift (P(x|y) unchanged). When covariates shift as well,
its estimate is biased; reports therefore present it alongside the feature
shift results rather than in isolation.

## Method verification (CLIA/CAP style)

### Accuracy

Agreement with the comparator method on verification specimens:
positive, negative and overall percent agreement with Wilson intervals, and
Cohen's kappa with the large-sample standard error
`sqrt(po (1 - po) / (n (1 - pe)^2))`.

### Precision (CLSI EP15-style)

One-way ANOVA over days x replicates per material:

- repeatability `s_r = sqrt(MS_within)`, df `D (n - 1)`
- between-day variance `max(0, (MS_between - MS_within) / n)`
- within-laboratory `s_WL = sqrt(s_r^2 + s_b^2)`, Satterthwaite df
- a claim is verified when the observed SD is at or below the claimed SD, or
  below the upper verification limit `sigma_claim * sqrt(chi2_{1 - alpha/L, df} / df)`
  where `L` is the number of materials

### Reportable range (CLSI EP6-style)

Linear, quadratic and cubic fits of measured on expected values; the best
fit is the one with the smallest residual standard error (ties go to the
lower degree). Nonlinearity at each level is the difference between the best
fit and the linear fit; bias is recovery against the expected value. When a
level fails, the widest window of at least three consecutive levels that
passes on its own refit is the verified range, and the study passes only if
that range covers the claimed range.

## Special functions

Implemented in `evaluation/special.py` without SciPy at runtime:
regularized incomplete gamma (series and Lentz continued fraction) for the
chi-square tail and quantile, the Kolmogorov survival function (Jacobi theta
form below 1.18, alternating series above), and the normal quantile from the
standard library.

## References

- Wilson EB. Probable inference, the law of succession, and statistical inference. JASA 1927.
- Hanley JA, McNeil BJ. The meaning and use of the area under a ROC curve. Radiology 1982.
- Naeini MP, Cooper G, Hauskrecht M. Obtaining well calibrated probabilities using Bayesian binning. AAAI 2015.
- Lipton ZC, Wang YX, Smola AJ. Detecting and correcting for label shift with black box predictors. ICML 2018.
- CLSI EP15-A3, User Verification of Precision and Estimation of Bias.
- CLSI EP06, Evaluation of Linearity of Quantitative Measurement Procedures.
- CLSI EP12, User Protocol for Evaluation of Qualitative Test Performance.
- 42 CFR 493.1253, Establishment and verification of performance specifications.
