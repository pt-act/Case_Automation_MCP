"""SopsSecretLoader tests — mocks the sops CLI, no real encryption needed."""

from __future__ import annotations

import json
import os
import subprocess
from unittest import mock

import pytest

from cam.config.settings import (
    EnvSecretLoader,
    SecretNotFoundError,
    Settings,
    SopsSecretLoader,
    build_secret_loader,
)

# -- SopsSecretLoader with mocked subprocess --------------------------------

def _mock_sops_result(stdout: str, returncode: int = 0, stderr: str = "") -> mock.MagicMock:
    """Create a mock subprocess.CompletedProcess."""
    m = mock.MagicMock(spec=subprocess.CompletedProcess)
    m.stdout = stdout
    m.returncode = returncode
    m.stderr = stderr
    return m


def test_sops_loader_resolves_yaml_secret() -> None:
    """SopsSecretLoader decrypts a YAML file and serves secrets by key."""
    decrypted_yaml = "database_url: postgresql://localhost/cam\napi_key: secret123\n"
    loader = SopsSecretLoader(file_path="/tmp/secrets.enc.yaml", age_key_file="/tmp/age.key")

    with mock.patch("subprocess.run", return_value=_mock_sops_result(decrypted_yaml)):
        assert loader.get_secret("database_url") == "postgresql://localhost/cam"
        assert loader.get_secret("api_key") == "secret123"


def test_sops_loader_resolves_json_secret() -> None:
    """SopsSecretLoader decrypts a JSON file."""
    decrypted_json = json.dumps({"database_url": "postgresql://localhost/cam", "api_key": "k"})
    loader = SopsSecretLoader(file_path="/tmp/secrets.enc.json")

    with mock.patch("subprocess.run", return_value=_mock_sops_result(decrypted_json)):
        assert loader.get_secret("database_url") == "postgresql://localhost/cam"


def test_sops_loader_caches_decrypted_values() -> None:
    """Second get_secret call does not invoke sops again."""
    decrypted_yaml = "key1: val1\n"
    loader = SopsSecretLoader(file_path="/tmp/secrets.enc.yaml")

    with mock.patch("subprocess.run", return_value=_mock_sops_result(decrypted_yaml)) as run_mock:
        loader.get_secret("key1")
        loader.get_secret("key1")
        assert run_mock.call_count == 1  # cached


def test_sops_loader_not_found() -> None:
    """Missing key raises SecretNotFoundError."""
    decrypted_yaml = "key1: val1\n"
    loader = SopsSecretLoader(file_path="/tmp/secrets.enc.yaml")

    with mock.patch("subprocess.run", return_value=_mock_sops_result(decrypted_yaml)):
        with pytest.raises(SecretNotFoundError) as exc_info:
            loader.get_secret("nonexistent")
        assert "nonexistent" in str(exc_info.value)


def test_sops_loader_flattens_nested_keys() -> None:
    """Nested YAML keys are flattened with dot notation."""
    decrypted_yaml = "db:\n  host: localhost\n  port: 5432\n"
    loader = SopsSecretLoader(file_path="/tmp/secrets.enc.yaml")

    with mock.patch("subprocess.run", return_value=_mock_sops_result(decrypted_yaml)):
        assert loader.get_secret("db.host") == "localhost"
        assert loader.get_secret("db.port") == "5432"


def test_sops_loader_decrypt_failure_raises() -> None:
    """sops CLI failure raises RuntimeError with stderr."""
    loader = SopsSecretLoader(file_path="/tmp/secrets.enc.yaml")

    with mock.patch(
        "subprocess.run",
        return_value=_mock_sops_result("", returncode=1, stderr="decrypt error"),
    ):
        with pytest.raises(RuntimeError, match="decrypt error"):
            loader.get_secret("key1")


def test_sops_loader_passes_age_key_env() -> None:
    """SOPS_AGE_KEY_FILE is passed to the subprocess environment."""
    decrypted_yaml = "key1: val1\n"
    loader = SopsSecretLoader(file_path="/tmp/secrets.enc.yaml", age_key_file="/tmp/age.key")

    with mock.patch("subprocess.run", return_value=_mock_sops_result(decrypted_yaml)) as run_mock:
        loader.get_secret("key1")
        call_args = run_mock.call_args
        env = call_args.kwargs.get("env", {})
        assert env.get("SOPS_AGE_KEY_FILE") == "/tmp/age.key"


# -- Settings validation for SOPS backend ----------------------------------

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


def test_settings_sops_backend_requires_file_path() -> None:
    """sops backend without sops_file_path → validation error."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        _make_settings(secret_backend="sops")


def test_settings_sops_backend_valid() -> None:
    """sops backend with sops_file_path → valid."""
    s = _make_settings(secret_backend="sops", sops_file_path="/tmp/secrets.enc.yaml")
    assert s.secret_backend == "sops"
    assert s.sops_file_path == "/tmp/secrets.enc.yaml"


def test_build_secret_loader_sops() -> None:
    """build_secret_loader returns SopsSecretLoader for sops backend."""
    s = _make_settings(secret_backend="sops", sops_file_path="/tmp/secrets.enc.yaml")
    loader = build_secret_loader(s)
    assert isinstance(loader, SopsSecretLoader)


def test_build_secret_loader_env() -> None:
    """build_secret_loader returns EnvSecretLoader for env backend."""
    s = _make_settings(secret_backend="env")
    loader = build_secret_loader(s)
    assert isinstance(loader, EnvSecretLoader)
