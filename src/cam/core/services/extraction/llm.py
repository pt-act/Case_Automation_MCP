"""Provider-agnostic LLM structuring client + ResidencyGuard — spec §4.3, §5 step 5.

The `LLMStructuringClient` is the one swappable seam for the LLM provider.
No concrete vendor adapter is shipped here (deferred to when provider is confirmed
— PRD §11 Q4, PTD §18 Q4).

The `ResidencyGuard` wraps any client and fails closed if the endpoint/region
is not in the configured allow-list.  This is the central security control
for the feature (spec §10).
"""

from __future__ import annotations

import math
from typing import Any, Protocol, runtime_checkable

from cam.core.services.extraction.errors import (
    ProviderUnavailableError,
    ResidencyError,
)
from cam.core.services.extraction.types import ExtractedField, MappingProfile


@runtime_checkable
class LLMStructuringClient(Protocol):
    """Provider-agnostic port for LLM-based field structuring."""

    @property
    def provider_id(self) -> str: ...

    @property
    def endpoint(self) -> str: ...

    def structure(
        self,
        text: str,
        profile: MappingProfile,
        prompt: str,
    ) -> list[ExtractedField]: ...


# ---------------------------------------------------------------------------
# Mock adapter (tests + local dev)
# ---------------------------------------------------------------------------


class MockLLMClient:
    """Deterministic mock that returns a configurable set of fields."""

    def __init__(
        self,
        provider_id: str = "mock",
        endpoint: str = "http://localhost:9999/v1",
        fixed_fields: list[ExtractedField] | None = None,
    ) -> None:
        self._provider_id = provider_id
        self._endpoint = endpoint
        self._fields = fixed_fields or []
        self.call_count = 0

    @property
    def provider_id(self) -> str:
        return self._provider_id

    @property
    def endpoint(self) -> str:
        return self._endpoint

    def structure(
        self, text: str, profile: MappingProfile, prompt: str
    ) -> list[ExtractedField]:
        self.call_count += 1
        return list(self._fields)


# ---------------------------------------------------------------------------
# Confidence normalisation
# ---------------------------------------------------------------------------


def normalise_confidence(value: Any, *, warnings: list[str]) -> float:
    """Clamp provider confidence to [0, 1]; flag unrecoverable values."""
    try:
        f = float(value)
        if math.isnan(f) or math.isinf(f):
            warnings.append(f"Provider returned non-finite confidence {value!r}; clamped to 0.")
            return 0.0
        return max(0.0, min(1.0, f))
    except (TypeError, ValueError):
        warnings.append(f"Provider returned non-numeric confidence {value!r}; clamped to 0.")
        return 0.0


def normalise_fields(
    fields: list[ExtractedField], warnings: list[str]
) -> list[ExtractedField]:
    """Ensure all fields have clamped confidences."""
    result = []
    for f in fields:
        clamped = normalise_confidence(f.confidence, warnings=warnings)
        if clamped != f.confidence:
            f = f.model_copy(update={"confidence": clamped, "requires_verification": True})
        result.append(f)
    return result


# ---------------------------------------------------------------------------
# ResidencyGuard
# ---------------------------------------------------------------------------


class ResidencyGuard:
    """Wraps an LLMStructuringClient and enforces endpoint residency policy.

    Fails closed: if the endpoint is not in the allow-list, raises ResidencyError
    and makes NO outbound call.  This invariant is tested by a spy-based test.
    """

    def __init__(
        self,
        client: LLMStructuringClient,
        allowlist: list[str],
        allow_external: bool = False,
    ) -> None:
        self._client = client
        self._allowlist = allowlist
        self._allow_external = allow_external

    @property
    def provider_id(self) -> str:
        return self._client.provider_id

    @property
    def endpoint(self) -> str:
        return self._client.endpoint

    def _is_allowed(self, endpoint: str) -> bool:
        if self._allow_external:
            return True
        return any(endpoint.startswith(allowed) for allowed in self._allowlist)

    def structure(
        self,
        text: str,
        profile: MappingProfile,
        prompt: str,
        *,
        warnings: list[str] | None = None,
    ) -> list[ExtractedField]:
        """Call the client only if the endpoint is allowed.

        Raises ResidencyError (fail-closed) if endpoint not permitted.
        Translates provider exceptions to ProviderUnavailableError.
        """
        endpoint = self._client.endpoint
        if not self._is_allowed(endpoint):
            import structlog
            structlog.get_logger(__name__).warning(
                "extraction.residency_denied",
                endpoint=endpoint,
                provider=self._client.provider_id,
            )
            raise ResidencyError(endpoint)

        try:
            return self._client.structure(text, profile, prompt)
        except ResidencyError:
            raise
        except Exception as exc:
            raise ProviderUnavailableError(self._client.provider_id, cause=exc) from exc
