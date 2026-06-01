#!/usr/bin/env python
"""CI schema-drift check — fails if generated domain docs differ from disk."""

import sys
from pathlib import Path

# Ensure src is importable
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
from cam.docs.generator import check_drift, write_docs

MODELS = [Contact, Matter, Document, Deadline, Communication, Task, AuditRecord, FeatureFlag, ACL]
DOCS_PATH = Path("docs/domain_model.md")

if check_drift(MODELS, DOCS_PATH):
    print("✓ Schema docs are in sync.")
    sys.exit(0)
else:
    print("✗ Schema docs are out of sync. Run: python scripts/regen_docs.py")
    # Show the diff to help the developer
    if DOCS_PATH.exists():
        import subprocess
        import tempfile
        from cam.docs.generator import generate_module_docs
        generated = generate_module_docs(MODELS)
        with tempfile.NamedTemporaryFile(suffix=".md", mode="w", delete=False) as f:
            f.write(generated)
            tmp = f.name
        subprocess.run(["diff", str(DOCS_PATH), tmp], check=False)
    sys.exit(1)
