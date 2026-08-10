"""QC Verification — pluggable check registry, 7 checks, qc.verify tool."""

from cam.core.services.qc.packet import (
    AttachmentRef,
    PacketKind,
    QCExtractedField,
    TemplateBinding,
    VerificationPacket,
)
from cam.core.services.qc.registry import (
    applicable_checks,
    clear_registry,
    register_check,
    run_checks,
)
from cam.core.services.qc.tool import QCVerifyInput, tool_qc_verify
from cam.core.services.qc.types import (
    Aggregate,
    CheckDescriptor,
    CheckResult,
    QCConfig,
    QCReport,
    SkipReason,
    Verdict,
    compute_config_fingerprint,
)

__all__ = [
    "Aggregate",
    "AttachmentRef",
    "CheckDescriptor",
    "CheckResult",
    "PacketKind",
    "QCConfig",
    "QCExtractedField",
    "QCReport",
    "QCVerifyInput",
    "SkipReason",
    "TemplateBinding",
    "Verdict",
    "VerificationPacket",
    "applicable_checks",
    "clear_registry",
    "compute_config_fingerprint",
    "register_check",
    "run_checks",
    "tool_qc_verify",
]
