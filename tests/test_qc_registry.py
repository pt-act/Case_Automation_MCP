"""G2 — Check registry, aggregation, totality, severity policy, timeout tests."""

from __future__ import annotations

import time
from datetime import datetime, timezone

import pytest

from cam.core.services.qc.registry import (
    Check,
    CheckResult,
    QCConfig,
    Verdict,
    _aggregate,
    applicable_checks,
    clear_registry,
    register_check,
    run_checks,
)
from cam.core.services.qc.types import (
    Aggregate,
    CheckDescriptor,
    FAIL_OR_PASS,
    SkipReason,
    WARN_OR_FAIL_OR_PASS,
    SeverityPolicy,
)


@pytest.fixture(autouse=True)
def _clear():
    clear_registry()


def _packet(kind="generic"):
    from cam.core.services.qc.packet import PacketKind, VerificationPacket
    from cam.core.domain.models import Contact, Matter

    contact = Contact(id="c1", source="crm", name="Ana Garcia", external_ids={})
    matter = Matter(
        id="m1", source="case", reference="REF-001", title="T",
        status="open", client=contact, opened_at=datetime.now(tz=timezone.utc),
        external_ids={},
    )
    return VerificationPacket(
        packet_id="p1",
        kind=PacketKind(kind),
        matter=matter,
        now=datetime.now(tz=timezone.utc),
    )


class _PassCheck:
    id = "always_pass"
    version = "1.0"
    applies_to = None
    severity_policy = WARN_OR_FAIL_OR_PASS
    required_inputs = frozenset()
    def describe(self): return CheckDescriptor(check_id=self.id, version=self.version, description="Always passes.")
    def run(self, packet, cfg): return CheckResult(check_id=self.id, check_version=self.version, verdict=Verdict.PASS)


class _FailCheck:
    id = "always_fail"
    version = "1.0"
    applies_to = None
    severity_policy = WARN_OR_FAIL_OR_PASS
    required_inputs = frozenset()
    def describe(self): return CheckDescriptor(check_id=self.id, version=self.version, description="Always fails.")
    def run(self, packet, cfg): return CheckResult(check_id=self.id, check_version=self.version, verdict=Verdict.FAIL, reason="forced fail")


class _WarnCheck:
    id = "always_warn"
    version = "1.0"
    applies_to = None
    severity_policy = WARN_OR_FAIL_OR_PASS
    required_inputs = frozenset()
    def describe(self): return CheckDescriptor(check_id=self.id, version=self.version, description="Always warns.")
    def run(self, packet, cfg): return CheckResult(check_id=self.id, check_version=self.version, verdict=Verdict.WARN, reason="forced warn")


class _ErrorCheck:
    id = "always_error"
    version = "1.0"
    applies_to = None
    severity_policy = WARN_OR_FAIL_OR_PASS
    required_inputs = frozenset()
    def describe(self): return CheckDescriptor(check_id=self.id, version=self.version, description="Always errors.")
    def run(self, packet, cfg): raise RuntimeError("check exploded")


class _SlowCheck:
    id = "always_slow"
    version = "1.0"
    applies_to = None
    severity_policy = WARN_OR_FAIL_OR_PASS
    required_inputs = frozenset()
    def describe(self): return CheckDescriptor(check_id=self.id, version=self.version, description="Slow check.")
    def run(self, packet, cfg):
        time.sleep(2)  # exceeds 500ms timeout
        return CheckResult(check_id=self.id, check_version=self.version, verdict=Verdict.PASS)


class _PolicyViolatorCheck:
    """Claims FAIL_OR_PASS but emits WARN."""
    id = "policy_violator"
    version = "1.0"
    applies_to = None
    severity_policy = FAIL_OR_PASS  # warns not allowed
    required_inputs = frozenset()
    def describe(self): return CheckDescriptor(check_id=self.id, version=self.version, description="Violates policy.")
    def run(self, packet, cfg): return CheckResult(check_id=self.id, check_version=self.version, verdict=Verdict.WARN)


# a. Discovery finds a registered check
def test_discovery_finds_registered() -> None:
    register_check(_PassCheck())
    checks = applicable_checks("generic")
    assert any(c.id == "always_pass" for c in checks)


# b. Duplicate id errors at startup
def test_duplicate_id_raises() -> None:
    register_check(_PassCheck())
    with pytest.raises(RuntimeError, match="already registered"):
        register_check(_PassCheck())


# c. Aggregation truth table
def test_aggregation_any_fail_is_block() -> None:
    results = [
        CheckResult(check_id="a", check_version="1.0", verdict=Verdict.PASS),
        CheckResult(check_id="b", check_version="1.0", verdict=Verdict.FAIL),
        CheckResult(check_id="c", check_version="1.0", verdict=Verdict.WARN),
    ]
    assert _aggregate(results) == Aggregate.BLOCK


def test_aggregation_warn_only_is_pass_with_warnings() -> None:
    results = [
        CheckResult(check_id="a", check_version="1.0", verdict=Verdict.PASS),
        CheckResult(check_id="b", check_version="1.0", verdict=Verdict.WARN),
    ]
    assert _aggregate(results) == Aggregate.PASS_WITH_WARNINGS


def test_aggregation_all_pass_is_pass() -> None:
    results = [CheckResult(check_id="a", check_version="1.0", verdict=Verdict.PASS)]
    assert _aggregate(results) == Aggregate.PASS


def test_aggregation_all_skipped_is_pass() -> None:
    results = [CheckResult(check_id="a", check_version="1.0", verdict=Verdict.SKIPPED)]
    assert _aggregate(results) == Aggregate.PASS


# d. Order-independence
def test_aggregation_order_independent() -> None:
    r1 = CheckResult(check_id="a", check_version="1.0", verdict=Verdict.FAIL)
    r2 = CheckResult(check_id="b", check_version="1.0", verdict=Verdict.PASS)
    assert _aggregate([r1, r2]) == _aggregate([r2, r1])


# e. Errored check → fail-closed
def test_errored_check_produces_fail() -> None:
    register_check(_ErrorCheck())
    report = run_checks(_packet(), None, QCConfig())
    error_result = next(r for r in report.results if r.check_id == "always_error")
    assert error_result.verdict == Verdict.FAIL
    assert report.aggregate == Aggregate.BLOCK


# f. Out-of-policy verdict rejected → fail
def test_policy_violation_produces_fail() -> None:
    register_check(_PolicyViolatorCheck())
    report = run_checks(_packet(), None, QCConfig())
    result = next(r for r in report.results if r.check_id == "policy_violator")
    assert result.verdict == Verdict.FAIL
    assert report.aggregate == Aggregate.BLOCK


# g. Totality: selected-but-not-applicable recorded as skipped
def test_requested_non_applicable_skipped() -> None:
    register_check(_PassCheck())
    report = run_checks(_packet(), ["always_pass", "nonexistent_check"], QCConfig())
    assert "nonexistent_check" in report.checks_skipped


# h. Timeout → fail
def test_slow_check_timeout_produces_fail() -> None:
    register_check(_SlowCheck())
    cfg = QCConfig(check_timeout_ms=100)  # 100ms timeout, check sleeps 2s
    report = run_checks(_packet(), None, cfg)
    result = next(r for r in report.results if r.check_id == "always_slow")
    assert result.verdict == Verdict.FAIL
