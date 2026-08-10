"""G5 — LLM structuring client, ResidencyGuard, confidence normalisation tests."""

from __future__ import annotations

import pytest

from cam.core.services.extraction.errors import ProviderUnavailableError, ResidencyError
from cam.core.services.extraction.llm import MockLLMClient, ResidencyGuard, normalise_confidence
from cam.core.services.extraction.types import (
    ExtractedField,
    FieldMapping,
    MappingProfile,
)


def _profile() -> MappingProfile:
    return MappingProfile(
        name="test",
        version="1.0",
        fields=[FieldMapping(source_key="email", domain_key="contact.email",
            target_type="Contact")],
    )


def _field(confidence: float) -> ExtractedField:
    return ExtractedField(
        key="email", target_type="Contact", confidence=confidence, sources=[]
    )


# a. Mock adapter returns fields
def test_mock_returns_fields() -> None:
    client = MockLLMClient(fixed_fields=[_field(0.9)])
    fields = client.structure("text", _profile(), "prompt")
    assert len(fields) == 1
    assert fields[0].confidence == 0.9
    assert client.call_count == 1


# b. Denied endpoint raises ResidencyError + no call (spy)
def test_residency_denied_raises_no_call() -> None:
    inner = MockLLMClient(endpoint="https://external.openai.com/v1")
    guard = ResidencyGuard(inner, allowlist=["http://localhost"], allow_external=False)
    with pytest.raises(ResidencyError):
        guard.structure("text", _profile(), "prompt")
    assert inner.call_count == 0, "Inner client must NOT be called on denied endpoint"


# c. Allowed endpoint proceeds
def test_residency_allowed_proceeds() -> None:
    inner = MockLLMClient(
        endpoint="http://localhost:9999/v1", fixed_fields=[_field(0.85)]
    )
    guard = ResidencyGuard(inner, allowlist=["http://localhost"], allow_external=False)
    result = guard.structure("text", _profile(), "prompt")
    assert len(result) == 1
    assert inner.call_count == 1


# d. External disabled blocks external but allows in-boundary
def test_external_disabled_blocks() -> None:
    external = MockLLMClient(endpoint="https://api.anthropic.com/v1")
    guard = ResidencyGuard(external, allowlist=[], allow_external=False)
    with pytest.raises(ResidencyError):
        guard.structure("text", _profile(), "prompt")


# e. allow_external=True allows any endpoint
def test_allow_external_true() -> None:
    inner = MockLLMClient(endpoint="https://anywhere.example.com")
    guard = ResidencyGuard(inner, allowlist=[], allow_external=True)
    guard.structure("text", _profile(), "prompt")
    assert inner.call_count == 1


# f. Provider unavailable → ProviderUnavailableError
def test_provider_unavailable_wrapped() -> None:
    class FailingClient:
        provider_id = "failing"
        endpoint = "http://localhost:9999"

        def structure(self, *a, **kw):
            raise ConnectionError("refused")

    guard = ResidencyGuard(FailingClient(), allowlist=["http://localhost"], allow_external=False)
    with pytest.raises(ProviderUnavailableError):
        guard.structure("text", _profile(), "prompt")


# g. Confidence normalisation: out-of-range clamped
def test_normalise_above_one() -> None:
    warns: list[str] = []
    assert normalise_confidence(1.3, warnings=warns) == 1.0


def test_normalise_below_zero() -> None:
    warns: list[str] = []
    assert normalise_confidence(-0.2, warnings=warns) == 0.0


def test_normalise_nan_flagged() -> None:
    import math
    warns: list[str] = []
    result = normalise_confidence(math.nan, warnings=warns)
    assert result == 0.0
    assert warns


def test_normalise_valid_passthrough() -> None:
    warns: list[str] = []
    assert normalise_confidence(0.75, warnings=warns) == 0.75
    assert not warns
