# Cloud reference implementation, cost and scalability

AWS is the reference deployment. Terraform for all three clouds lives in
`deployments/terraform/{aws,azure,gcp}` and provisions the same capabilities.

## Reference deployment (AWS)

| Concern | AWS | Azure | GCP |
|---|---|---|---|
| Kubernetes | EKS (existing cluster) | AKS | GKE |
| Database | RDS PostgreSQL 16, Multi-AZ, KMS, forced TLS, IAM auth, PI | PostgreSQL Flexible Server, zone-redundant HA, private access, TLS | Cloud SQL PostgreSQL 16, regional HA, CMEK, private IP, PITR |
| Secrets | Secrets Manager (KMS) | Key Vault (RBAC, purge protection, private) | Secret Manager (user-managed replication) |
| Encryption keys | KMS with rotation | Key Vault premium | Cloud KMS, 90-day rotation |
| Audit storage | S3 Object Lock, compliance mode, 7 years, TLS-only policy | Immutable blob storage, GZRS, no shared keys | Bucket retention policy (lockable), CMEK |
| Workload identity | IRSA role scoped to the service account | User-assigned identity + federated credential | GSA + Workload Identity binding |
| Secret sync | External Secrets Operator (`deployments/kubernetes/external-secrets.yaml`) | same | same |

Terraform outputs the workload-identity binding, the database URL secret
name, the KMS key and the audit bucket. The Ed25519 audit key is generated
outside Terraform (never in state) and stored as
`cross-lab-ai-validation-audit-key-<kid>`.

Network: the Helm NetworkPolicy admits traffic only from namespaces labelled
`agent-platform/member=true`, the ingress controller and monitoring, and
allows egress only to DNS, PostgreSQL, HTTPS (IdP JWKS, identity fabric) and
OTLP. Site agents reach the platform through the ingress over TLS.

## Cost model

Costs are dominated by the database and the cluster footprint; the
statistics are cheap (a benchmark with 2,000 bootstrap replicates is tens of
milliseconds of CPU). Drivers, in order:

1. Managed PostgreSQL (Multi-AZ instance class and storage). Data volume is
   small: reports are kilobytes, so a general-purpose instance class is
   sufficient for hundreds of sites; size for connection count, not storage.
2. Three replicas at 250m CPU / 384Mi requests, scaling to 10 under load.
3. Audit storage: one JSON line per decision; even thousands of evaluations
   per day amount to megabytes per year under Object Lock.
4. Data transfer from sites: one aggregate submission is a few kilobytes.

No model inference or LLM tokens are billed by this platform.

## Scalability

| Dimension | Approach |
|---|---|
| Request volume | Stateless API (MCP stateless HTTP, no in-memory sessions); HPA on CPU; any replica serves any request |
| Sites and model versions | Indexed lookups by site and model key; reports are append-only documents |
| Statistical load | Bootstrap is vectorised over replicates (multinomial draws), O(iterations x bins) |
| Audit throughput | One chain per replica avoids cross-replica locking; chains are verified independently |
| Database | Read-mostly; connection pool with pre-ping; scale vertically, then add a read replica for report reads |

## Lock-in considerations

The service depends only on PostgreSQL, Kubernetes, an OIDC issuer and an
OTLP endpoint. Cloud-specific pieces are confined to Terraform and
service-account annotations, so moving clouds is an infrastructure change,
not a code change.
