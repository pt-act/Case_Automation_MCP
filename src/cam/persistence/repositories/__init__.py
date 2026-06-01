"""Persistence repositories package.

Re-exports all repository classes from the implementation module.
The flat module is being migrated to per-entity submodules; see base.py.
"""
from cam.persistence._repositories_legacy import (
    AuditRepository,
    CommunicationRepository,
    ContactRepository,
    DeadlineRepository,
    DocumentRepository,
    MatterRepository,
    Repository,
    TaskRepository,
)

__all__ = [
    "AuditRepository",
    "CommunicationRepository",
    "ContactRepository",
    "DeadlineRepository",
    "DocumentRepository",
    "MatterRepository",
    "Repository",
    "TaskRepository",
]
