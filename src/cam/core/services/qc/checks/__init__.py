"""QC check implementations — seven checks registered at import time."""

from cam.core.services.qc.checks.attachment import AttachmentIntegrityCheck
from cam.core.services.qc.checks.completeness import CompletenessCheck
from cam.core.services.qc.checks.confidence import ExtractionConfidenceCheck
from cam.core.services.qc.checks.consistency import ConsistencyCheck
from cam.core.services.qc.checks.deadline import DeadlineSanityCheck
from cam.core.services.qc.checks.privilege import PrivilegeCheck
from cam.core.services.qc.checks.recipient import RecipientIntegrityCheck

ALL_CHECKS = [
    AttachmentIntegrityCheck(),
    CompletenessCheck(),
    ExtractionConfidenceCheck(),
    ConsistencyCheck(),
    DeadlineSanityCheck(),
    PrivilegeCheck(),
    RecipientIntegrityCheck(),
]


def register_all(clear: bool = False) -> None:
    """Register all seven checks into the registry."""
    from cam.core.services.qc.registry import clear_registry, register_check

    if clear:
        clear_registry()
    for check in ALL_CHECKS:
        try:
            register_check(check)
        except RuntimeError:
            pass  # already registered


__all__ = [
    "ALL_CHECKS",
    "AttachmentIntegrityCheck",
    "CompletenessCheck",
    "ExtractionConfidenceCheck",
    "ConsistencyCheck",
    "DeadlineSanityCheck",
    "PrivilegeCheck",
    "RecipientIntegrityCheck",
    "register_all",
]
