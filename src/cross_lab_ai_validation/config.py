"""Runtime settings, read from CLV_* environment variables."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import Field, HttpUrl, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CLV_", env_nested_delimiter="__")

    environment: str = "production"
    public_url: HttpUrl = Field(description="Public base URL; used in the A2A agent card")
    database_url: str = Field(description="SQLAlchemy async URL, e.g. postgresql+asyncpg://...")

    audit_log_path: Path = Path("/var/lib/cross-lab-ai-validation/audit/audit.jsonl")
    audit_key_file: Path | None = Field(
        default=None, description="Ed25519 PEM private key that signs the audit chain"
    )
    dev_ephemeral_audit_key: bool = Field(
        default=False,
        description="Generate an in-memory audit key. Refused when environment=production.",
    )

    oidc_issuer: str = Field(description="Enterprise OIDC issuer for laboratory staff")
    oidc_audience: str = Field(description="Audience staff access tokens must carry")
    oidc_jwks_url: str
    roles_claim: str = "roles"
    sites_claim: str = "sites"
    signature_max_auth_age_seconds: int = Field(default=300, ge=30, le=900)

    identity_fabric_url: str | None = Field(
        default=None, description="agent-identity-fabric base URL (introspection and authorize)"
    )
    agent_issuer: str | None = Field(
        default=None, description="Agent credential issuer for local verification"
    )
    agent_audience: str | None = None
    agent_jwks_url: str | None = None

    policy_file: Path = Path("policies/assurance-authorization.yaml")
    min_aggregate_cases: int = Field(
        default=20, ge=10, description="Smallest population a site may submit aggregates for"
    )
    mcp_allowed_hosts: list[str] = Field(
        default_factory=list,
        description="Host headers accepted on /mcp (DNS-rebinding protection); default: public URL",
    )

    log_level: str = "INFO"
    otlp_endpoint: str | None = None

    @model_validator(mode="after")
    def _consistent(self) -> Settings:
        if self.dev_ephemeral_audit_key:
            if self.environment == "production":
                raise ValueError("dev_ephemeral_audit_key cannot be used in production")
        elif self.audit_key_file is None:
            raise ValueError("CLV_AUDIT_KEY_FILE is required unless an ephemeral key is allowed")
        local = (self.agent_issuer, self.agent_audience, self.agent_jwks_url)
        if any(local) and not all(local):
            raise ValueError(
                "CLV_AGENT_ISSUER, CLV_AGENT_AUDIENCE and CLV_AGENT_JWKS_URL are set together"
            )
        return self

    @property
    def public_base(self) -> str:
        return str(self.public_url).rstrip("/")

    @property
    def signature_max_age(self) -> timedelta:
        return timedelta(seconds=self.signature_max_auth_age_seconds)

    @property
    def allowed_hosts(self) -> list[str]:
        if self.mcp_allowed_hosts:
            return list(self.mcp_allowed_hosts)
        netloc = urlsplit(self.public_base).netloc
        return [netloc, f"{netloc}:*"] if ":" not in netloc else [netloc]
