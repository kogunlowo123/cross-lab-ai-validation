# Configuration reference

All settings are `CLV_*` environment variables (see
`src/cross_lab_ai_validation/config.py`). The service refuses to start with
incomplete configuration.

| Variable | Required | Default | Description |
|---|---|---|---|
| `CLV_ENVIRONMENT` | no | `production` | `production` forbids an ephemeral audit key |
| `CLV_PUBLIC_URL` | yes | | Public base URL; A2A card `url` and MCP host allow-list |
| `CLV_DATABASE_URL` | yes | | SQLAlchemy async URL (`postgresql+asyncpg://...`) |
| `CLV_AUDIT_KEY_FILE` | yes* | | Ed25519 PEM that signs the audit chain; key ID is the file stem |
| `CLV_DEV_EPHEMERAL_AUDIT_KEY` | no | `false` | In-memory audit key, non-production only (replaces the * setting) |
| `CLV_AUDIT_LOG_PATH` | no | `/var/lib/cross-lab-ai-validation/audit/audit.jsonl` | Audit chain file (per replica) |
| `CLV_OIDC_ISSUER` | yes | | Enterprise IdP issuer for staff |
| `CLV_OIDC_AUDIENCE` | yes | | Audience of staff tokens |
| `CLV_OIDC_JWKS_URL` | yes | | IdP JWKS |
| `CLV_ROLES_CLAIM` | no | `roles` | Claim carrying staff roles |
| `CLV_SITES_CLAIM` | no | `sites` | Claim listing the sites a person covers (`*` for all) |
| `CLV_SIGNATURE_MAX_AUTH_AGE_SECONDS` | no | `300` | Maximum age of `auth_time` when signing (30–900) |
| `CLV_IDENTITY_FABRIC_URL` | no** | | agent-identity-fabric base URL: agent introspection and `/v1/authorize` |
| `CLV_AGENT_ISSUER`, `CLV_AGENT_AUDIENCE`, `CLV_AGENT_JWKS_URL` | no** | | Local verification of agent credentials when no fabric URL is set; all three together |
| `CLV_POLICY_FILE` | no | `policies/assurance-authorization.yaml` | Authorization policy |
| `CLV_MIN_AGGREGATE_CASES` | no | `20` | Smallest population a site may submit (≥ 10) |
| `CLV_MCP_ALLOWED_HOSTS` | no | host of `CLV_PUBLIC_URL` | JSON list of accepted `Host` headers on `/mcp` (`host:*` for any port) |
| `CLV_LOG_LEVEL` | no | `INFO` | |
| `CLV_OTLP_ENDPOINT` | no | | OTLP gRPC endpoint for traces |
| `CLV_HOST` / `CLV_PORT` | no | `127.0.0.1` / `8080` | The container image sets host `0.0.0.0` |
| `CLV_FORWARDED_ALLOW_IPS` | no | `127.0.0.1` | Proxies trusted for `X-Forwarded-*` |

\*\* With neither a fabric URL nor the three agent settings, agent
credentials are refused; staff access still works. The Helm chart requires
the fabric URL.

## Site agent (`clv-site`)

| Option | Description |
|---|---|
| `--central` | Platform base URL |
| `--fabric` | Identity fabric base URL |
| `--agent-uri` | The site agent's SPIFFE ID |
| `--workload-token-file` | Projected service-account token used for the jwt-bearer grant |
| `--site`, `--model-id`, `--version` | What to submit for |
| `--cases` | CSV extract with `score`, `label` (or blank for unlabeled), each feature and subgroup column |
| `--min-cases`, `--min-subgroup` | Local minimums before anything is shared (defaults 20 and 10) |

## Validation protocol fields

Registered with each model version (`ValidationProtocol`): confidence level,
bootstrap iterations, minimum cases and positives, thresholds for
sensitivity, specificity, PPV, NPV, AUROC lower bound, ECE, subgroup gap and
subgroup sensitivity, PSI moderate and major thresholds, prevalence-shift
limit, shift alpha, agreement, precision and reportable-range criteria, and
the revalidation interval. Omitted optional criteria are not evaluated.
