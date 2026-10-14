# Runbook: population shift response

Triggered by `ClvMajorPopulationShift` or `ClvApprovalWithdrawn`.

## What already happened automatically

A shift analysis classified `major` (PSI at or above the protocol's
`psi_major` on the score distribution or any feature). If the site had a
permitted approval for that model version, the platform moved it to
`revalidation_required` in the same request and recorded
`approval.restrict` in the audit chain. Clinical use at that site is no
longer permitted by the platform.

## Steps

1. **Notify.** Page the site's laboratory medical director and the model
   owner. Laboratory operations must stop relying on the model's output at
   that site (LIS rule or middleware flag) until a new approval exists.
2. **Read the report.** `GET /v1/reports/<shift report id>`:
   - `features[]`: which features moved (PSI, KS or chi-square, significance
     after Bonferroni).
   - `score`: whether the model's output distribution moved.
   - `estimated_prevalence` vs `reference_prevalence`: label shift.
3. **Classify the cause.**
   - Real population change (new service line, seasonal surge, referral
     pattern): revalidate on current data.
   - Pre-analytical or analytical change (new analyser, reagent lot,
     unit or calibration change): involve QC; fix the measurement issue
     first, then re-submit.
   - Extraction error at the site (wrong unit, column mapping): correct
     `clv-site` input and re-submit; document the error.
4. **Revalidate.** Submit a fresh benchmark and shift analysis, run the
   validation protocol, and request reapproval. Use is restored only when a
   medical director signs.
5. **Close.** Record the cause and the new approval in the incident ticket;
   link the shift report, validation run and signature IDs.

## Approval withdrawn

If `ClvApprovalWithdrawn` fires without a known cause, confirm the
`approval.restrict` audit event (actor, shift report, previous validation)
before taking further action. A restriction cannot be undone by an
operator; it is resolved only through revalidation and signature.
