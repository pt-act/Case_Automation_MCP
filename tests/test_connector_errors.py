"""G2 — ConnectorError taxonomy + classify() focused tests."""

from __future__ import annotations

import pytest

from cam.connectors.errors import (
    AuthError,
    ConnectorError,
    FatalError,
    NotFoundError,
    RateLimitError,
    TransientError,
    classify,
)

CONNECTOR = "test_vendor"


def _response(status: int, headers: dict | None = None):  # type: ignore[return]
    import httpx

    return httpx.Response(status, headers=headers or {})


# a. Each baseline HTTP status → expected class
@pytest.mark.parametrize(
    "status,expected",
    [
        (401, AuthError),
        (403, AuthError),
        (429, RateLimitError),
        (404, NotFoundError),
        (500, TransientError),
        (502, TransientError),
        (503, TransientError),
        (504, TransientError),
        (408, TransientError),
        (400, FatalError),
        (409, FatalError),
        (422, FatalError),
    ],
)
def test_classify_http_status(status: int, expected: type) -> None:
    result = classify(_response(status), CONNECTOR)
    assert isinstance(result, expected)
    assert result.connector == CONNECTOR


# b. Timeout/conn-reset → transient
def test_classify_timeout() -> None:
    import httpx

    exc = httpx.ReadTimeout("timed out", request=None)
    result = classify(exc, CONNECTOR)
    assert isinstance(result, TransientError)


def test_classify_connect_error() -> None:
    import httpx

    exc = httpx.ConnectError("connection refused")
    result = classify(exc, CONNECTOR)
    assert isinstance(result, TransientError)


# c. Unmapped/None → fatal
def test_classify_none_gives_fatal() -> None:
    result = classify(None, CONNECTOR)
    assert isinstance(result, FatalError)


def test_classify_random_exception_gives_fatal() -> None:
    result = classify(ValueError("something weird"), CONNECTOR)
    assert isinstance(result, FatalError)


# d. retryable flags correct per class
def test_auth_not_retryable() -> None:
    assert AuthError.retryable is False


def test_rate_limit_retryable() -> None:
    assert RateLimitError.retryable is True


def test_not_found_not_retryable() -> None:
    assert NotFoundError.retryable is False


def test_transient_retryable() -> None:
    assert TransientError.retryable is True


def test_fatal_not_retryable() -> None:
    assert FatalError.retryable is False


# e. retry_after parsed from Retry-After header
def test_rate_limit_retry_after_parsed() -> None:
    result = classify(_response(429, {"Retry-After": "30"}), CONNECTOR)
    assert isinstance(result, RateLimitError)
    assert result.retry_after == 30.0


def test_rate_limit_no_retry_after_is_none() -> None:
    result = classify(_response(429), CONNECTOR)
    assert isinstance(result, RateLimitError)
    assert result.retry_after is None


# f. detail does not contain body / PII
def test_detail_no_body() -> None:
    result = classify(_response(500), CONNECTOR)
    assert "body" not in result.detail.lower()


# g. Already-classified ConnectorError passes through
def test_passthrough_connector_error() -> None:
    orig = TransientError(CONNECTOR, "already classified")
    result = classify(orig, CONNECTOR)
    assert result is orig
