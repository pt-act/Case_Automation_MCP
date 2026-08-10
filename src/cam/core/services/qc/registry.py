"""Check registry — FR-16 extension point, totality, aggregation, timeout — spec §4.3, §5.

A new check is registered by calling @register_check — zero edits to this file.
Duplicate check_id at registration → startup error (no silent shadowing).
Every selected check produces either a CheckResult or a skipped entry (totality).
A check raising an exception → fail-closed result ("check errored").
Severity-policy enforcement: a check cannot emit a verdict outside its policy.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from datetime import UTC
from typing import Any, Protocol, runtime_checkable

import structlog

from cam.core.services.qc.packet import VerificationPacket
from cam.core.services.qc.types import (
    Aggregate,
    CheckDescriptor,
    CheckResult,
    QCConfig,
    QCReport,
    SeverityPolicy,
    SkipReason,
    Verdict,
    compute_config_fingerprint,
)

log = structlog.get_logger(__name__)

_REGISTRY: dict[str, Check] = {}


# ---------------------------------------------------------------------------
# Check protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class Check(Protocol):
    """Every QC check must implement this protocol."""

    @property
    def id(self) -> str: ...

    @property
    def version(self) -> str: ...

    @property
    def applies_to(self) -> frozenset[str] | None: ...  # None = all packet kinds

    @property
    def severity_policy(self) -> SeverityPolicy: ...

    @property
    def required_inputs(self) -> frozenset[str]: ...

    def describe(self) -> CheckDescriptor: ...

    def run(self, packet: VerificationPacket, cfg: QCConfig) -> CheckResult: ...


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def register_check(check: Check) -> None:
    """Register a check instance.  Duplicate check_id → RuntimeError (startup error)."""
    if check.id in _REGISTRY:
        raise RuntimeError(
            f"Check {check.id!r} is already registered. "
            "Duplicate ids are not allowed (spec §6)."
        )
    _REGISTRY[check.id] = check
    log.debug("qc.check_registered", check_id=check.id, version=check.version)


def get_check(check_id: str) -> Check:
    check = _REGISTRY.get(check_id)
    if check is None:
        raise KeyError(f"No check registered for id {check_id!r}.")
    return check


def applicable_checks(packet_kind: str) -> list[Check]:
    """Return checks applicable to `packet_kind`, id-sorted for stable order."""
    result = []
    for check in _REGISTRY.values():
        if check.applies_to is None or packet_kind in check.applies_to:
            result.append(check)
    return sorted(result, key=lambda c: c.id)


def clear_registry() -> None:
    """Test helper — reset registry between tests."""
    _REGISTRY.clear()


# ---------------------------------------------------------------------------
# Timeout-guarded execution
# ---------------------------------------------------------------------------


def _run_with_timeout(
    fn: Callable[..., Any], timeout_ms: float
) -> tuple[CheckResult | None, Exception | None]:
    """Run fn() in a thread; return (result, None) or (None, exc) on timeout/error."""
    result: list[CheckResult | None] = [None]
    error: list[Exception | None] = [None]

    def target() -> None:
        try:
            result[0] = fn()
        except Exception as exc:
            error[0] = exc

    t = threading.Thread(target=target, daemon=True)
    t.start()
    t.join(timeout=timeout_ms / 1000.0)
    if t.is_alive():
        return None, TimeoutError(f"Check timed out after {timeout_ms}ms.")
    return result[0], error[0]


# ---------------------------------------------------------------------------
# Registry runner
# ---------------------------------------------------------------------------


def run_checks(
    packet: VerificationPacket,
    requested_ids: list[str] | None,
    cfg: QCConfig,
) -> QCReport:
    """Select, run, and aggregate checks for the given packet.

    Args:
        packet:        The VerificationPacket to verify.
        requested_ids: Subset of check ids to run (None = all applicable).
        cfg:           Effective QCConfig for this run.

    Returns:
        A QCReport with per-check results and an aggregate verdict.
    """
    from datetime import datetime

    all_applicable = applicable_checks(packet.kind)
    applicable_map = {c.id: c for c in all_applicable}

    # Determine which checks to run
    if requested_ids is not None:
        selected = []
        skipped: dict[str, str] = {}
        for cid in requested_ids:
            if cid in applicable_map:
                selected.append(applicable_map[cid])
            else:
                skipped[cid] = SkipReason.UNSUPPORTED_PACKET_KIND
    else:
        selected = all_applicable
        skipped = {}

    results: list[CheckResult] = []
    checks_selected = [c.id for c in selected]

    for check in selected:
        t0 = time.monotonic()
        try:
            raw_result, exc = _run_with_timeout(
                lambda c=check: c.run(packet, cfg),
                timeout_ms=cfg.check_timeout_ms,
            )
            if exc is not None:
                raise exc
            result = raw_result
            assert result is not None

            # Severity-policy enforcement
            if (
                result.verdict != Verdict.SKIPPED
                and not check.severity_policy.allows(result.verdict)
            ):
                log.warning(
                    "qc.severity_policy_violation",
                    check_id=check.id,
                    emitted=result.verdict,
                    policy=list(check.severity_policy.allowed),
                )
                result = CheckResult(
                    check_id=check.id,
                    check_version=check.version,
                    verdict=Verdict.FAIL,
                    reason=(
                        f"Check emitted {result.verdict!r} outside its "
                        f"declared severity policy."
                    ),
                    duration_ms=(time.monotonic() - t0) * 1000,
                )

        except Exception as exc:
            log.warning("qc.check_errored", check_id=check.id, error=str(exc))
            result = CheckResult(
                check_id=check.id,
                check_version=check.version,
                verdict=Verdict.FAIL,
                reason="check errored",
                evidence={"error_type": type(exc).__name__},
                duration_ms=(time.monotonic() - t0) * 1000,
            )

        if result.skip_reason is not None:
            skipped[check.id] = result.skip_reason
        result = result.model_copy(
            update={"duration_ms": (time.monotonic() - t0) * 1000}
        )
        results.append(result)

    aggregate = _aggregate(results)
    config_fingerprint = compute_config_fingerprint(cfg)

    log.info(
        "qc.report",
        packet_id=packet.packet_id,
        aggregate=aggregate,
        fail_count=sum(1 for r in results if r.verdict == Verdict.FAIL),
        warn_count=sum(1 for r in results if r.verdict == Verdict.WARN),
        skip_count=len(skipped),
    )

    return QCReport(
        packet_id=packet.packet_id,
        run_id=packet.run_id,
        results=results,
        aggregate=aggregate,
        checks_selected=checks_selected,
        checks_skipped=skipped,
        created_at=datetime.now(tz=UTC),
        config_fingerprint=config_fingerprint,
    )


# ---------------------------------------------------------------------------
# Aggregation — order-independent
# ---------------------------------------------------------------------------


def _aggregate(results: list[CheckResult]) -> Aggregate:
    """any fail → block; else any warn → pass_with_warnings; else pass."""
    for r in results:
        if r.verdict == Verdict.FAIL:
            return Aggregate.BLOCK
    for r in results:
        if r.verdict == Verdict.WARN:
            return Aggregate.PASS_WITH_WARNINGS
    return Aggregate.PASS
