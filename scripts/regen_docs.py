#!/usr/bin/env python
"""Regenerate domain model docs from Pydantic schemas."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cam.core.domain.models import (
    ACL,
    AuditRecord,
    Communication,
    Contact,
    Deadline,
    Document,
    FeatureFlag,
    Matter,
    Task,
)
from cam.docs.generator import write_docs

MODELS = [Contact, Matter, Document, Deadline, Communication, Task, AuditRecord, FeatureFlag, ACL]
DOCS_PATH = Path("docs/domain_model.md")

write_docs(MODELS, DOCS_PATH)
print(f"✓ Wrote {DOCS_PATH}")
