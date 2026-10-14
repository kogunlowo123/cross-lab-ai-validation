# Runbook: first deployment and startup

## Prerequisites

- Kubernetes ≥ 1.27 with External Secrets Operator and a
  `ClusterSecretStore` named `platform-secrets` bound to the cloud secret
  manager.
- agent-identity-fabric deployed and reachable from the namespace.
- Enterprise IdP application exposing roles `model-registrar`,
  `lab-validation-scientist`, `medical-director`, `assurance-auditor`, a
  `sites` claim (list of site IDs) and `auth_time`, with a re-authentication
  prompt for the signing flow (`max_age=300`).

## 1. Provision cloud resources

```bash
cd deployments/terraform/aws        # or azure / gcp
terraform init                      # configure an encrypted remote backend first
terraform apply -var-file=<environment>.tfvars
```

Record the outputs: service-account role (or identity), database URL secret
name, KMS key, audit bucket.

## 2. Create the audit signing key (outside Terraform)

```bash
openssl genpkey -algorithm ed25519 -out audit-2026-10.pem
aws secretsmanager create-secret --name cross-lab-ai-validation-audit-key-2026-10 \
  --kms-key-id <kms_key_arn> --secret-string file://audit-2026-10.pem
openssl pkey -in audit-2026-10.pem -pubout -out audit-2026-10.pub   # keep for chain verification
shred -u audit-2026-10.pem
```

Azure: `az keyvault secret set --vault-name <vault> --name ... --file audit-2026-10.pem`.
GCP: `gcloud secrets create ... --data-file=audit-2026-10.pem`.

## 3. Namespace and secret sync

```bash
kubectl apply -k deployments/kubernetes
kubectl -n lab-ai-assurance get externalsecret   # READY=True for both
```

## 4. Register the site agents at the identity fabric

For each laboratory, register an agent (e.g. `lab-assurance/<site>-site-agent`)
with scopes `registry:read benchmark:run shift:analyze site:access:<site>`.
Assurance orchestration agents additionally get `report:read validation:run
reapproval:request` and the sites they serve.

## 5. Install the chart

Create `<environment>.yaml` with every value marked REQUIRED in `values.yaml`
plus the workload-identity annotation from Terraform:

```bash
helm upgrade --install clv oci://ghcr.io/kogunlowo123/charts/cross-lab-ai-validation \
  --version 0.1.0 -n lab-ai-assurance -f <environment>.yaml --wait
```

## 6. Verify

```bash
kubectl -n lab-ai-assurance rollout status statefulset/clv-cross-lab-ai-validation
kubectl -n lab-ai-assurance port-forward svc/clv-cross-lab-ai-validation 8080:80
curl -sf localhost:8080/readyz
curl -s localhost:8080/.well-known/agent-card.json | jq '.url, [.skills[].id]'
```

Expected: `{"status":"ready"}`, the public A2A URL and five skills. The
Prometheus target `cross-lab-ai-validation` is up and the Grafana dashboard
`configs/grafana/cross-lab-ai-validation.json` shows traffic.

## 7. Register the first site and model

A model registrar posts the site (`POST /v1/sites`) and the model version
with its protocol and reference aggregates (`POST /v1/models`). The site agent
then runs `clv-site submit`.
