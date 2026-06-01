"""ConnectorError taxonomy and HTTP-status classifier — spec §4.2.

Every adapter MUST map every vendor failure into exactly one class (totality).
The orchestrator decision contract:

  Class          retryable  Orchestrator action
  AuthError      No         Gate / park for human (re-auth)
  RateLimitError Yes        Retry after retry_after / backoff, bounded
  NotFoundError  No         Surface as domain "missing"; do not retry
  TransientError Yes        Retry with backoff+jitter, bounded
  FatalError     No         Park run for human; never silent-fail
"""

from __future__ import annotations

from typing import Any


# ---------------------------------------------------------------------------
# Base class
# ---------------------------------------------------------------------------


class ConnectorError(Exception):
    """Base for all connector failures.  Never instantiated directly."""

    retryable: bool = False

    def __init__(
        self,
        connector: str,
        detail: str,
        cause: Exception | None = None,
    ) -> None:
        self.connector = connector
        self.detail = detail
        self.cause = cause
        super().__init__(f"[{connector}] {detail}")


# ---------------------------------------------------------------------------
# Terminal classes (exactly five)
# ---------------------------------------------------------------------------


class AuthError(ConnectorError):
    """401 / 403 / expired or invalid token / insufficient scope."""

    retryable = False


class RateLimitError(ConnectorError):
    """429 / quota exceeded.  retry_after in seconds if provided by vendor."""

    retryable = True

    def __init__(
        self,
        connector: str,
        detail: str,
        cause: Exception | None = None,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(connector, detail, cause)
        self.retry_after = retry_after


class NotFoundError(ConnectorError):
    """404 / unknown resource id."""

    retryable = False


class TransientError(ConnectorError):
    """5xx / timeout / connection reset / DNS failure."""

    retryable = True


class FatalError(ConnectorError):
    """4xx (non-auth/404) / schema mismatch / exhausted retries / unmapped error."""

    retryable = False


# ---------------------------------------------------------------------------
# Baseline HTTP-status classifier — overridable per adapter
# ---------------------------------------------------------------------------


def classify(
    response_or_exc: Any,
    connector: str = "unknown",
) -> ConnectorError:
    """Map an httpx Response or exception to exactly one ConnectorError subclass.

    This baseline mapping implements spec §4.2 Table.  Adapters may call
    this and re-raise, or override it entirely for vendor-specific quirks.

    A 409 that represents an idempotent replay should be handled by the
    adapter *before* calling classify (ASSUMPTION: confirm per-vendor).
    """
    # Avoid a hard import of httpx at module level so the file is importable
    # even when httpx is not installed (e.g. during docs generation).
    _httpx: Any = None
    try:
        import httpx as _httpx
    except ImportError:
        pass

    detail_prefix = "[redacted — no body/PII in error detail]"

    # --- httpx Response ---
    if _httpx and isinstance(response_or_exc, _httpx.Response):
        status = response_or_exc.status_code
        detail = f"HTTP {status}"

        if status in (401, 403):
            return AuthError(connector, detail)
        if status == 429:
            retry_after_raw = response_or_exc.headers.get("Retry-After")
            retry_after: float | None = None
            if retry_after_raw is not None:
                try:
                    retry_after = float(retry_after_raw)
                except ValueError:
                    pass
            return RateLimitError(connector, detail, retry_after=retry_after)
        if status == 404:
            return NotFoundError(connector, detail)
        if status in (408, 425, 500, 502, 503, 504):
            return TransientError(connector, detail)
        # 400/409/422 and anything else → fatal
        return FatalError(connector, f"HTTP {status} (non-retryable)")

    # --- httpx network exceptions ---
    if _httpx:
        if isinstance(response_or_exc, _httpx.TimeoutException):
            return TransientError(connector, "Request timed out.", cause=response_or_exc)
        if isinstance(response_or_exc, (_httpx.ConnectError, _httpx.NetworkError)):
            return TransientError(connector, "Network error.", cause=response_or_exc)

    # --- Already-classified ConnectorError (pass through) ---
    if isinstance(response_or_exc, ConnectorError):
        return response_or_exc

    # --- Anything else → fatal ---
    return FatalError(
        connector,
        f"Unclassified error: {type(response_or_exc).__name__}",
        cause=response_or_exc if isinstance(response_or_exc, Exception) else None,
    )
