"""Apply the active domain pack to the engine's runtime configuration (G6).

`apply_pack()` is the dependency inversion: instead of each consumer holding its
own immigration defaults, the consumers' runtime config is *populated from the
active pack*. Call it once at startup after the pack is selected (the sidecar
lifespan does this), and in tests after selecting a pack (the conftest parity
net does this with the immigration pack).

Covered consumers:
  * intake   — case types, dedupe, default case type      (settable singleton)
  * routing  — document-class enumeration + folder/ACL map (settable singleton)
  * deadline — `build_rule_store_from_pack()` seeds a RuleStore from the pack's
               `DeadlineRule` objects (the store is injected, not a singleton)

Observability PII composition is applied separately by
`cam.obs.observability.configure_pii_patterns()` (G6.1). RBAC role-set and QC
packet-kind enums remain engine-structural; the pack carries that data for a
later dedicated refactor.
"""

from __future__ import annotations

from cam.packs.base import DomainPack, get_active_pack


def apply_pack(pack: DomainPack | None = None) -> DomainPack:
    """Populate the engine's runtime config singletons from the active pack."""
    pack = pack or get_active_pack()
    _apply_intake(pack)
    _apply_routing(pack)
    return pack


def _apply_intake(pack: DomainPack) -> None:
    from cam.core.workflows.intake.config import IntakeConfig, set_intake_config

    set_intake_config(
        IntakeConfig(
            case_type_configs=dict(pack.case_types),
            dedupe=pack.dedupe,
            default_case_type=pack.default_case_type,
        )
    )


def _apply_routing(pack: DomainPack) -> None:
    from cam.core.workflows.document_routing.config import (
        ClassPermissionPolicy,
        FolderMap,
        RoutingConfig,
        set_routing_config,
    )

    classes = frozenset(pack.document_classes)
    set_routing_config(
        RoutingConfig(
            classes=classes,
            folder_map=FolderMap(entries={c: f"docs/{c}" for c in classes}),
            class_permission_policies=ClassPermissionPolicy(
                class_policies={c: [] for c in classes}
            ),
        )
    )


def build_rule_store_from_pack(pack: DomainPack | None = None):  # type: ignore[no-untyped-def]
    """Return a `RuleStore` seeded with the active pack's deadline rules.

    The deadline engine takes an injected `RuleStore`; the application builds it
    from the active pack rather than loading immigration YAML by default."""
    from cam.core.services.deadline.rules import RuleStore

    pack = pack or get_active_pack()
    store = RuleStore()
    for rule in pack.deadline_rules:
        store.register_rule(rule)
    return store
