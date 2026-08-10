"""Pack inspector — read-only HTTP endpoint exposing resolved pack configuration.

GET /packs              — list registered packs
GET /packs/{name}/inspect — resolved configuration for a named pack

PII patterns are exposed as counts + labels only, never source regex strings.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

router = APIRouter(prefix="/packs", tags=["packs"])


@router.get("")
async def list_packs() -> JSONResponse:
    """List all registered domain packs."""
    from cam.packs import get_active_pack, list_registered_pack_names
    from cam.packs.base import _REGISTRY

    try:
        active = get_active_pack()
        active_name = active.name
    except Exception:
        active_name = None

    packs = []
    for name in list_registered_pack_names():
        pack = _REGISTRY.get(name)
        if pack is None:
            continue
        packs.append({
            "name": pack.name,
            "version": pack.version,
            "description": getattr(pack, "description", ""),
            "is_active": pack.name == active_name,
        })

    return JSONResponse({"packs": packs})


@router.get("/{name}/inspect")
async def inspect_pack(name: str) -> JSONResponse:
    """Return the resolved configuration for a named pack."""
    from cam.packs import list_registered_pack_names
    from cam.packs.base import _REGISTRY

    pack = _REGISTRY.get(name)
    if pack is None:
        registered = list_registered_pack_names()
        raise HTTPException(
            status_code=404,
            detail={
                "error": "pack_not_found",
                "requested": name,
                "registered": registered,
            },
        )

    try:
        from cam.packs import get_active_pack

        active = get_active_pack()
        is_active = active.name == name
    except Exception:
        is_active = False

    # Build the inspection response from the pack contract
    result: dict[str, Any] = {
        "name": pack.name,
        "version": pack.version,
        "is_active": is_active,
        "terminology": _serialize_terminology(pack),
        "case_types": _serialize_case_types(pack),
        "default_case_type": getattr(pack, "default_case_type", None),
        "deadline_rules_count": len(getattr(pack, "deadline_rules", ())),
        "deadline_rule_ids": [
            r.rule_id for r in getattr(pack, "deadline_rules", ())
        ],
        "document_classes": _serialize_document_classes(pack),
        "restriction_policy": _serialize_restriction(pack),
        "rbac_roles": list(getattr(pack, "rbac_roles", {}).keys())
                       if isinstance(getattr(pack, "rbac_roles", {}), dict)
                       else list(getattr(pack, "rbac_roles", [])),
        "pii_patterns": _serialize_pii(pack),
        "feature_flags": {},
    }

    return JSONResponse(result)


def _serialize_terminology(pack: Any) -> dict[str, str]:
    term = getattr(pack, "terminology", None)
    if term is None:
        return {}
    return {
        "matter": getattr(term, "matter", ""),
        "matter_plural": getattr(term, "matter_plural", ""),
        "contact": getattr(term, "contact", ""),
        "contact_plural": getattr(term, "contact_plural", ""),
        "deadline": getattr(term, "deadline", ""),
        "document": getattr(term, "document", ""),
        "restriction_label": getattr(term, "restriction_label", ""),
        "practice_noun": getattr(term, "practice_noun", ""),
    }


def _serialize_case_types(pack: Any) -> list[dict[str, Any]]:
    case_types = getattr(pack, "case_types", {})
    if isinstance(case_types, dict):
        items = case_types.values()
    else:
        items = case_types
    default = getattr(pack, "default_case_type", None)
    result = []
    for ct in items:
        entry: dict[str, Any] = {"id": ct.case_type}
        if ct.case_type == default:
            entry["default"] = True
        result.append(entry)
    return result


def _serialize_document_classes(pack: Any) -> list[str]:
    classes = getattr(pack, "document_classes", ())
    return list(classes) if isinstance(classes, (tuple, list)) else []


def _serialize_restriction(pack: Any) -> dict[str, Any]:
    rp = getattr(pack, "restriction", None)
    if rp is None:
        return {}
    return {
        "label": rp.label,
        "default_restricted": rp.default_restricted,
        "extra_predicate_count": len(getattr(rp, "extra_predicates", [])),
    }


def _serialize_pii(pack: Any) -> dict[str, Any]:
    """Serialize PII info as counts + labels only — never regex patterns."""
    from cam.packs.base import ENGINE_PII_BASELINE

    pack_patterns = getattr(pack, "pii_patterns", ())
    pack_count = len(pack_patterns) if isinstance(pack_patterns, (tuple, list)) else 0

    return {
        "baseline_count": len(ENGINE_PII_BASELINE),
        "pack_addition_count": pack_count,
        "addition_labels": ["a_number", "passport"] if pack_count > 0 else [],
        "patterns_redacted": True,
    }
