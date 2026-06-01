"""Typed error taxonomy for data-extraction — spec §6.

These errors are distinct from ConnectorError (no vendor involved here).
Callers translate to ConnectorError taxonomy if needed.
"""

from __future__ import annotations


class ExtractionError(Exception):
    """Base for all extraction errors."""


class ResidencyError(ExtractionError):
    """Inference endpoint not in the configured allow-list; fail-closed."""

    def __init__(self, endpoint: str, reason: str = "") -> None:
        self.endpoint = endpoint
        super().__init__(
            f"Inference endpoint {endpoint!r} is not permitted by residency policy. {reason}"
        )


class UnsupportedInputError(ExtractionError):
    """Content kind not supported (e.g. a file format we can't process)."""


class ProviderUnavailableError(ExtractionError):
    """LLM provider could not be reached or timed out."""

    def __init__(self, provider: str, cause: Exception | None = None) -> None:
        self.provider = provider
        self.cause = cause
        super().__init__(f"Provider {provider!r} unavailable.")


class InputLimitError(ExtractionError):
    """Input exceeds the configured page or byte limit."""

    def __init__(self, limit_kind: str, limit: int, actual: int) -> None:
        self.limit_kind = limit_kind
        self.limit = limit
        self.actual = actual
        super().__init__(
            f"Input exceeds {limit_kind} limit: max={limit}, got={actual}."
        )
