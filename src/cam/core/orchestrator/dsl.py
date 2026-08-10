"""Workflow definition DSL — @workflow decorator, registry, StepContext — spec §4.2.

Usage::

    @workflow("intake", version=1)
    class IntakeWorkflow:
        steps = [
            "parse_lead", "dedupe_contact", "create_contact",
            "GATE:human_review",
            "send_welcome",
        ]
        gates = {
            "human_review": GateConfig(required_role="attorney", channels=["mcp","web","email"])
        }

        async def parse_lead(self, ctx: StepContext) -> dict: ...
        async def send_welcome(self, ctx: StepContext) -> dict: ...
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

GATE_PREFIX = "GATE:"

_REGISTRY: dict[str, WorkflowDef] = {}  # key: "name:version"


# ---------------------------------------------------------------------------
# GateConfig
# ---------------------------------------------------------------------------


@dataclass
class GateConfig:
    """Per-gate configuration embedded in a workflow class."""

    required_role: str = "attorney"
    quorum: int = 1
    channels: list[str] = field(default_factory=lambda: ["mcp", "web", "email"])
    ttl_seconds: int = 86400  # 24 h


_DEFAULT_GATE_CONFIG = GateConfig()


# ---------------------------------------------------------------------------
# StepContext — passed to every step handler
# ---------------------------------------------------------------------------


class StepContext:
    """Immutable run context + prior outputs accessor + compensation hook."""

    def __init__(
        self,
        run_id: str,
        context: dict[str, Any],
        step_outputs: dict[str, Any],
        idem_key: str,
    ) -> None:
        self.run_id = run_id
        self.context = dict(context)          # immutable copy
        self._outputs = dict(step_outputs)
        self.idem_key = idem_key              # forwarded to connector calls
        self._compensations: list[Callable] = []

    def output(self, step_name: str) -> Any:
        """Return a prior step's output dict, or None if not yet run."""
        return self._outputs.get(step_name)

    def compensate(self, fn: Callable) -> None:
        """Register a compensation hook to be called if the run parks."""
        self._compensations.append(fn)

    @property
    def compensations(self) -> list[Callable]:
        return list(self._compensations)


# ---------------------------------------------------------------------------
# WorkflowDef — the parsed, validated workflow definition
# ---------------------------------------------------------------------------


@dataclass
class WorkflowDef:
    name: str
    version: int
    steps: list[str]
    handlers: dict[str, Callable]   # step_name → bound method (non-gate)
    gate_configs: dict[str, GateConfig]  # gate_name → config
    input_schema: type[BaseModel] | None
    cls: type

    def is_gate(self, step: str) -> bool:
        return step.startswith(GATE_PREFIX)

    def gate_name(self, step: str) -> str:
        """Strip the 'GATE:' prefix."""
        return step[len(GATE_PREFIX):]

    def gate_config(self, step: str) -> GateConfig:
        name = self.gate_name(step)
        return self.gate_configs.get(name, _DEFAULT_GATE_CONFIG)

    def validate_context(self, context: dict[str, Any]) -> None:
        """Validate context against input_schema if defined."""
        if self.input_schema is not None:
            self.input_schema.model_validate(context)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def _registry_key(name: str, version: int) -> str:
    return f"{name}:{version}"


def get_workflow(name: str, version: int) -> WorkflowDef:
    key = _registry_key(name, version)
    defn = _REGISTRY.get(key)
    if defn is None:
        raise KeyError(f"No workflow registered for {name!r} version {version}.")
    return defn


def get_workflow_latest(name: str) -> WorkflowDef:
    """Return the highest-version registration for a workflow name."""
    matches = [d for k, d in _REGISTRY.items() if d.name == name]
    if not matches:
        raise KeyError(f"No workflow registered for {name!r}.")
    return max(matches, key=lambda d: d.version)


def registered_workflows() -> list[tuple[str, int]]:
    return [(d.name, d.version) for d in _REGISTRY.values()]


def clear_registry() -> None:
    """Test helper — reset registry between tests."""
    _REGISTRY.clear()


# ---------------------------------------------------------------------------
# @workflow decorator
# ---------------------------------------------------------------------------


def workflow(
    name: str,
    version: int = 1,
    input_schema: type[BaseModel] | None = None,
) -> Callable[[type], type]:
    """Class decorator that registers a workflow definition.

    The decorated class must declare:
        steps: list[str]               — ordered step names; GATE:* prefix for gates
        gates: dict[str, GateConfig]   — optional gate-level config overrides
    and must implement an `async def <step_name>(self, ctx)` method for each
    non-gate step.
    """

    def decorator(cls: type) -> type:
        steps: list[str] = getattr(cls, "steps", [])
        if not steps:
            raise ValueError(f"Workflow {name!r}: `steps` list is empty.")

        gate_configs: dict[str, GateConfig] = dict(getattr(cls, "gates", {}))
        handlers: dict[str, Callable] = {}

        instance = cls()  # instantiate to access bound methods for validation

        for step in steps:
            if step.startswith(GATE_PREFIX):
                gate_name = step[len(GATE_PREFIX):]
                if gate_name not in gate_configs:
                    gate_configs[gate_name] = _DEFAULT_GATE_CONFIG
            else:
                method = getattr(instance, step, None)
                if method is None or not callable(method):
                    raise ValueError(
                        f"Workflow {name!r}: step {step!r} has no handler method on {cls.__name__}."
                    )
                if not inspect.iscoroutinefunction(method):
                    raise ValueError(
                        f"Workflow {name!r}: handler {step!r} must be an async def."
                    )
                handlers[step] = method

        key = _registry_key(name, version)
        if key in _REGISTRY:
            raise ValueError(
                f"Workflow {name!r} version {version} is already registered. "
                "Use a different version number."
            )

        _REGISTRY[key] = WorkflowDef(
            name=name,
            version=version,
            steps=steps,
            handlers=handlers,
            gate_configs=gate_configs,
            input_schema=input_schema,
            cls=cls,
        )
        return cls

    return decorator
