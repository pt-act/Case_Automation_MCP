#!/usr/bin/env python
"""CI PACK.md gate (domain-packs task 7.5).

Mirrors the connector-framework `CONNECTOR.md` gate: every domain pack directory
(a `src/cam/packs/<name>/` that ships a `pack.py`) must carry a `PACK.md` with the
required sections, so a new pack cannot merge without its operator-facing doc.
"""

import sys
from pathlib import Path

PACKS_DIR = Path(__file__).resolve().parents[1] / "src" / "cam" / "packs"

# Required section headers (prefix match — the immigration PACK.md appends a
# parenthetical to "## PII additions", so we match on the heading stem).
REQUIRED_SECTIONS = (
    "## Terminology",
    "## Confidentiality (restriction) semantics",
    "## PII additions",
    "## Case types",
    "## Deadline rules",
    "## RBAC roles",
    "## Provenance / assumptions",
)
REQUIRED_HEADER_TOKENS = ("**Pack id:**", "**Version:**")


def _pack_dirs() -> list[Path]:
    return sorted(p.parent for p in PACKS_DIR.glob("*/pack.py"))


def check() -> list[str]:
    errors: list[str] = []
    pack_dirs = _pack_dirs()
    if not pack_dirs:
        return [f"no pack directories found under {PACKS_DIR}"]

    for pack_dir in pack_dirs:
        doc = pack_dir / "PACK.md"
        if not doc.exists():
            errors.append(f"{pack_dir.name}: missing PACK.md")
            continue
        text = doc.read_text()
        for token in REQUIRED_HEADER_TOKENS:
            if token not in text:
                errors.append(f"{pack_dir.name}/PACK.md: missing header field {token}")
        for section in REQUIRED_SECTIONS:
            if not any(line.startswith(section) for line in text.splitlines()):
                errors.append(f"{pack_dir.name}/PACK.md: missing section '{section}'")
    return errors


if __name__ == "__main__":
    problems = check()
    if problems:
        print("✗ PACK.md gate failed:")
        for p in problems:
            print(f"  - {p}")
        sys.exit(1)
    print(f"✓ PACK.md present and complete for all {len(_pack_dirs())} packs.")
    sys.exit(0)
