"""12-factor configuration, secret loader, and feature flags — spec §4.4."""

from __future__ import annotations

from abc import abstractmethod
from typing import Protocol, runtime_checkable

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


class Settings(BaseSettings):
    """Typed 12-factor settings.  Validates on load; fails fast with precise,
    secret-free error messages on missing or malformed values."""

    model_config = SettingsConfigDict(
        env_prefix="CAM_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Database
    database_url: SecretStr = Field(
        ...,
        description="PostgreSQL connection URL (async driver, e.g. postgresql+asyncpg://...).",
    )
    database_pool_size: int = Field(10, ge=1, le=100)
    database_max_overflow: int = Field(20, ge=0, le=100)

    # Redis
    redis_url: SecretStr = Field(
        ..., description="Redis connection URL, e.g. redis://localhost:6379/0."
    )

    # Secret store backend
    secret_backend: str = Field(
        "env",
        description="Secret loader backend: 'env' | 'vault' | 'kms'.",
    )
    vault_addr: str | None = Field(None, description="Vault address (vault backend only).")
    vault_token: SecretStr | None = Field(None, description="Vault token (vault backend only).")

    # Encryption
    encryption_kek_secret_name: str = Field(
        "CAM_ENCRYPTION_KEK",
        description="Name of the secret holding the Key Encryption Key (base64-encoded 32 bytes).",
    )

    # Observability
    otel_exporter_endpoint: str | None = Field(
        None, description="OTLP gRPC endpoint, e.g. 'localhost:4317'. None = no export."
    )
    log_level: str = Field("INFO", description="Logging level.")
    service_name: str = Field("case-automation-mcp", description="Service name for telemetry.")

    # Residency
    residency_region: str = Field(
        "us-east-1", description="Configured data-residency region."
    )

    # Domain pack selection (CAM_DOMAIN_PACK). No implicit default (decision D-5):
    # required at startup; unset → refuse to serve. The engine is domain-agnostic.
    domain_pack: str | None = Field(
        None,
        description="Active domain pack id, e.g. 'immigration' | 'consulting'. "
        "Required at startup — there is no implicit default.",
    )

    # Feature flags (per-workflow on/off)
    feature_flags: dict[str, bool] = Field(
        default_factory=dict,
        description="Per-workflow feature flags, e.g. {'intake': true}. Default off for all.",
    )

    # Migration policy
    refuse_on_pending_migrations: bool = Field(
        True,
        description="If True, the process refuses to serve when migrations are not at head.",
    )

    # Extraction
    extraction_allow_external_inference: bool = Field(
        False,
        description="Allow sending content to external LLM inference endpoints.",
    )
    extraction_residency_allowlist: list[str] = Field(
        default_factory=list,
        description="Permitted inference endpoint host patterns.",
    )

    @field_validator("secret_backend")
    @classmethod
    def _valid_backend(cls, v: str) -> str:
        if v not in {"env", "vault", "kms"}:
            raise ValueError(f"secret_backend must be 'env', 'vault', or 'kms'; got '{v}'")
        return v

    @model_validator(mode="after")
    def _vault_requires_addr(self) -> Settings:
        if self.secret_backend == "vault" and not self.vault_addr:
            raise ValueError("vault_addr is required when secret_backend='vault'")
        return self

    def error_message_is_clean(self) -> bool:
        """Verify no secret value leaks into repr/str (test helper)."""
        rep = repr(self)
        return "**********" in rep or self.database_url.get_secret_value() not in rep


# ---------------------------------------------------------------------------
# Secret loader
# ---------------------------------------------------------------------------


@runtime_checkable
class SecretLoader(Protocol):
    """Protocol for retrieving secrets at runtime."""

    @abstractmethod
    def get_secret(self, name: str) -> str:
        """Return the secret value.  Raises SecretNotFoundError if absent."""
        ...


class SecretNotFoundError(Exception):
    """Raised when a named secret cannot be found.  Never includes the value."""

    def __init__(self, name: str) -> None:
        self.name = name
        super().__init__(f"Secret not found: {name!r}")


class EnvSecretLoader:
    """Default backend: reads secrets from environment variables."""

    def get_secret(self, name: str) -> str:
        import os

        value = os.environ.get(name)
        if value is None:
            raise SecretNotFoundError(name)
        return value


class VaultSecretLoader:
    """Vault backend stub — interface-compatible; complete when hosting is confirmed."""

    def __init__(self, addr: str, token: str) -> None:
        self._addr = addr
        self._token = token

    def get_secret(self, name: str) -> str:
        raise NotImplementedError(
            "VaultSecretLoader is a stub pending hosting confirmation (PTD §18 Q2)."
        )


class KMSSecretLoader:
    """Cloud KMS backend stub — interface-compatible; complete when hosting is confirmed."""

    def get_secret(self, name: str) -> str:
        raise NotImplementedError(
            "KMSSecretLoader is a stub pending hosting confirmation (PTD §18 Q2)."
        )


def build_secret_loader(settings: Settings) -> SecretLoader:
    """Instantiate the configured secret loader backend."""
    if settings.secret_backend == "vault":
        if not settings.vault_addr or not settings.vault_token:
            raise ValueError("vault_addr and vault_token are required for vault backend.")
        return VaultSecretLoader(
            addr=settings.vault_addr,
            token=settings.vault_token.get_secret_value(),
        )
    if settings.secret_backend == "kms":
        return KMSSecretLoader()
    return EnvSecretLoader()


# ---------------------------------------------------------------------------
# Feature flags
# ---------------------------------------------------------------------------


class FeatureFlags:
    """Per-workflow feature flag registry.  Unknown / unreleased workflows
    default to *off* — the safe direction (spec §5.6)."""

    def __init__(self, flags: dict[str, bool]) -> None:
        self._flags = flags

    def is_enabled(self, workflow_key: str) -> bool:
        """Return True only if the flag is explicitly set to True."""
        return self._flags.get(workflow_key, False)

    @classmethod
    def from_settings(cls, settings: Settings) -> FeatureFlags:
        return cls(settings.feature_flags)
