"""Routing config shapes + defaults — spec §3, G1.2.

Classes/folders/naming are config, not code (CONVENTIONS §5/§8).
The folder-map must be total: every class, including 'unknown', must have an entry.
Missing entry at load time → immediate error (never fail silently at route time).

ASSUMPTION (confirm): class enumeration, folder taxonomy, naming convention.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Document classes — closed enum; 'unknown' is always present as fallback.
# ASSUMPTION (confirm): exact set from firm.
# ---------------------------------------------------------------------------

DOCUMENT_CLASSES = {
    "engagement_letter",
    "court_filing",
    "id_document",
    "correspondence",
    "internal_memo",
    "form_filing",
    "unknown",
}


@dataclass
class ClassificationRule:
    """A rule that matches a document classification from metadata or extraction."""

    class_name: str
    keyword_hints: list[str] = field(default_factory=list)
    form_id_patterns: list[str] = field(default_factory=list)
    min_confidence: float = 0.80


@dataclass
class FolderMap:
    """Maps class → folder path within a matter. Must be total over DOCUMENT_CLASSES."""

    entries: dict[str, str] = field(default_factory=dict)

    def folder_for(self, class_name: str) -> str | None:
        return self.entries.get(class_name)

    def validate_totality(self) -> None:
        missing = DOCUMENT_CLASSES - set(self.entries.keys())
        if missing:
            raise ValueError(
                f"FolderMap is missing entries for classes: {missing}. "
                "Every class including 'unknown' must have a folder mapping."
            )


@dataclass
class NamingTemplate:
    """Template for deriving canonical document names. ASSUMPTION (confirm)."""

    pattern: str = "{matter_reference}_{doc_class}_{date}_v{version}"

    def render(self, matter_reference: str, class_name: str, date: str, version: int) -> str:
        import re
        name = self.pattern.format(
            matter_reference=matter_reference,
            doc_class=class_name,
            date=date,
            version=version,
        )
        # Sanitise to filesystem-safe chars
        return re.sub(r"[^\w\-._]", "_", name)


@dataclass
class ClassPermissionPolicy:
    """Per-class principal allowlist (least-privilege)."""

    class_policies: dict[str, list[str]] = field(default_factory=dict)

    def allowed_principals_for(self, class_name: str) -> list[str]:
        return self.class_policies.get(class_name, [])


@dataclass
class RoutingConfig:
    """Full routing configuration.  Loaded at startup; validated for totality."""

    classification_rules: list[ClassificationRule] = field(default_factory=list)
    folder_map: FolderMap = field(default_factory=lambda: FolderMap(
        entries={c: f"docs/{c}" for c in DOCUMENT_CLASSES}
    ))
    naming_template: NamingTemplate = field(default_factory=NamingTemplate)
    class_permission_policies: ClassPermissionPolicy = field(
        default_factory=lambda: ClassPermissionPolicy(
            class_policies={c: [] for c in DOCUMENT_CLASSES}
        )
    )
    classification_threshold: float = 0.80  # ASSUMPTION (confirm)
    routing_intent_version: str = "v1"

    def __post_init__(self) -> None:
        self.folder_map.validate_totality()

    def review_queue_folder(self) -> str:
        return self.folder_map.folder_for("unknown") or "review_queue"


# Module-level default config (overridable in tests/at startup)
_config: RoutingConfig = RoutingConfig()


def get_routing_config() -> RoutingConfig:
    return _config


def set_routing_config(cfg: RoutingConfig) -> None:
    global _config
    _config = cfg
