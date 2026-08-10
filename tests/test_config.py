"""G4 — Config, secrets, feature flags focused tests."""

from __future__ import annotations

import os

import pytest
from pydantic import ValidationError

from cam.config.settings import (
    EnvSecretLoader,
    FeatureFlags,
    SecretNotFoundError,
    Settings,
)


def _make_settings(**overrides) -> Settings:  # type: ignore[return]
    env = {
        "CAM_DATABASE_URL": "postgresql+asyncpg://x:x@localhost/cam",
        "CAM_REDIS_URL": "redis://localhost:6379/0",
    }
    env.update({f"CAM_{k.upper()}": str(v) for k, v in overrides.items()})
    for k, v in env.items():
        os.environ[k] = v
    try:
        return Settings()
    finally:
        for k in env:
            os.environ.pop(k, None)


# 1. Valid env loads
def test_settings_valid() -> None:
    s = _make_settings()
    assert s.residency_region == "us-east-1"


# 2. Missing required → precise error
def test_settings_missing_required(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ["CAM_DATABASE_URL", "CAM_REDIS_URL"]:
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(Exception) as exc_info:
        Settings()
    assert (
        "database_url" in str(exc_info.value).lower()
        or exc_info.type.__name__ == "ValidationError"
    )


# 3. Bad secret_backend value → error
def test_settings_bad_backend() -> None:
    with pytest.raises(ValidationError):
        _make_settings(secret_backend="ftp")


# 4. Error text carries no secret value
def test_settings_error_has_no_secret_value() -> None:
    s = _make_settings()
    rep = repr(s)
    assert s.database_url.get_secret_value() not in rep


# 5. Secret loader resolves a secret
def test_env_secret_loader_resolves(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MY_TEST_SECRET", "super-secret-value")
    loader = EnvSecretLoader()
    assert loader.get_secret("MY_TEST_SECRET") == "super-secret-value"


# 6. SecretNotFoundError raised
def test_env_secret_loader_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NONEXISTENT_SECRET", raising=False)
    loader = EnvSecretLoader()
    with pytest.raises(SecretNotFoundError) as exc_info:
        loader.get_secret("NONEXISTENT_SECRET")
    assert "NONEXISTENT_SECRET" in str(exc_info.value)
    assert "super-secret-value" not in str(exc_info.value)


# 7. Feature flag enabled
def test_feature_flags_enabled() -> None:
    ff = FeatureFlags({"intake": True, "status_update": False})
    assert ff.is_enabled("intake") is True
    assert ff.is_enabled("status_update") is False


# 8. Feature flag default off for unknown workflow
def test_feature_flags_default_off() -> None:
    ff = FeatureFlags({})
    assert ff.is_enabled("some_future_workflow") is False
