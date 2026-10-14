# Runbook: disaster recovery

| Objective | Target |
|---|---|
| RPO (registry, reports, approvals) | ≤ 5 minutes (PITR) |
| RTO | ≤ 4 hours |
| Audit chains | Shipped to WORM storage at least every 15 minutes |

## Database loss or corruption

1. Restore to a new instance at the last good point in time:
   - AWS: `aws rds restore-db-instance-to-point-in-time --source-db-instance-identifier cross-lab-ai-validation --target-db-instance-identifier cross-lab-ai-validation-restore --restore-time <UTC>`
   - Azure: `az postgres flexible-server restore --source-server <name> --restore-time <UTC> --name <new>`
   - GCP: `gcloud sql instances clone cross-lab-ai-validation cross-lab-ai-validation-restore --point-in-time <UTC>`
2. Update the database URL secret to the restored endpoint; External
   Secrets propagates it; restart the StatefulSet.
3. Reconcile: compare the last `reapproval.sign`, `approval.restrict` and
   `validation.run` audit events (WORM) with the restored tables. Any
   decision present in audit but missing from the database is re-entered
   from the audit record by a medical director (re-signature), never by
   editing rows.

## Region loss

1. Apply Terraform in the recovery region (same module, new `region`).
2. Restore the database from the cross-region snapshot copy or replica.
3. Install the chart; register the new ingress host with site agents (DNS
   cut-over).
4. Site agents re-submit current aggregates; approvals with expiry in the
   future remain valid.

## Audit volume loss

A replica's unshipped tail is lost. Record the gap (replica, last shipped
sequence, time window) in the incident record. Decisions in that window are
still in the database with their digests; the medical director attests to
them if required by the quality system.

## Restore test

Quarterly: restore the latest snapshot into a staging account, run
`/readyz`, list approvals for two sites and verify one audit chain from WORM
storage.
