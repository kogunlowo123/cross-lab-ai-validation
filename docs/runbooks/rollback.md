# Runbook: rollback

## Application

```bash
helm -n lab-ai-assurance history clv
helm -n lab-ai-assurance rollback clv <revision> --wait
kubectl -n lab-ai-assurance rollout status statefulset/clv-cross-lab-ai-validation
```

Pin images by digest (`image.digest`) in environment values so a rollback is
exact. The release page lists each version's image digest (`IMAGE` asset).

## Schema compatibility

The store records `schema_version`. Version 0.1.0 creates tables idempotently
and never drops or rewrites columns, so rolling back to an earlier 0.1.x
release is safe. Any future release that changes the schema documents its
downgrade path in `CHANGELOG.md`.

## Policy

`policies/assurance-authorization.yaml` is baked into the image. Every
decision records the policy digest, so audit can show which policy was in
force. To roll back a policy change, roll back the release that carried it.

## What a rollback does not undo

Signatures, approvals, restrictions and reports written by the newer version
remain valid records. A rollback never reinstates use that was withdrawn.
