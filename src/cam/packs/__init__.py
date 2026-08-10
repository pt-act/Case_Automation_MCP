"""Domain packs — the domain-agnostic configuration seam.

The engine is fixed; everything practice-specific lives in a `DomainPack`,
selected at startup via `CAM_DOMAIN_PACK`. Immigration ships as the reference
pack; consulting proves the seam generalises. See `base.py` for the contract
and the two engine-owned safety invariants a pack may only tighten.
"""

from __future__ import annotations

from cam.packs.apply import apply_pack, build_rule_store_from_pack
from cam.packs.base import (
    ENGINE_PII_BASELINE,
    DomainPack,
    PackSelectionError,
    PackValidationError,
    RestrictionPolicy,
    Terminology,
    activate_configured_pack,
    active_pii_patterns,
    get_active_pack,
    load_pack_plugins,
    register_builtin_packs,
    register_pack,
    reset_registry,
    run_pack_conformance,
    select_pack,
    validate_pack,
)

__all__ = [
    "DomainPack",
    "Terminology",
    "RestrictionPolicy",
    "PackValidationError",
    "PackSelectionError",
    "ENGINE_PII_BASELINE",
    "active_pii_patterns",
    "register_pack",
    "register_builtin_packs",
    "get_active_pack",
    "select_pack",
    "activate_configured_pack",
    "load_pack_plugins",
    "validate_pack",
    "run_pack_conformance",
    "reset_registry",
    "apply_pack",
    "build_rule_store_from_pack",
]
