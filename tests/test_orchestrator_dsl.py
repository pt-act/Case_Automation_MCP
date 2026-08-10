"""G1 — Workflow DSL & registry focused tests."""

from __future__ import annotations

import pytest

from cam.core.orchestrator.dsl import (
    GateConfig,
    StepContext,
    clear_registry,
    get_workflow_latest,
    workflow,
)


@pytest.fixture(autouse=True)
def _clear() -> None:
    clear_registry()


# a. Decorated workflow registers & resolves by name/version
def test_workflow_registers() -> None:
    @workflow("intake", version=1)
    class IntakeWF:
        steps = ["parse_lead", "GATE:human_review", "send_welcome"]
        gates = {"human_review": GateConfig(required_role="attorney")}

        async def parse_lead(self, ctx: StepContext) -> dict:
            return {}

        async def send_welcome(self, ctx: StepContext) -> dict:
            return {}

    defn = get_workflow_latest("intake")
    assert defn.name == "intake"
    assert defn.version == 1
    assert len(defn.steps) == 3


# b. Duplicate registration errors
def test_duplicate_registration_errors() -> None:
    @workflow("dup", version=1)
    class DupWF:
        steps = ["step_a"]
        async def step_a(self, ctx: StepContext) -> dict: return {}

    with pytest.raises(ValueError, match="already registered"):
        @workflow("dup", version=1)
        class DupWF2:
            steps = ["step_a"]
            async def step_a(self, ctx: StepContext) -> dict: return {}


# c. GATE:* step recognised vs normal step
def test_gate_step_recognised() -> None:
    @workflow("gtest", version=1)
    class GWF:
        steps = ["step_a", "GATE:review", "step_b"]
        gates = {"review": GateConfig()}

        async def step_a(self, ctx: StepContext) -> dict: return {}
        async def step_b(self, ctx: StepContext) -> dict: return {}

    defn = get_workflow_latest("gtest")
    assert defn.is_gate("GATE:review") is True
    assert defn.is_gate("step_a") is False
    assert defn.gate_name("GATE:review") == "review"


# d. Missing handler fails registration
def test_missing_handler_fails() -> None:
    with pytest.raises((ValueError, AttributeError)):
        @workflow("broken", version=1)
        class BrokenWF:
            steps = ["no_such_method"]


# e. Context schema mismatch rejected
def test_context_schema_mismatch_rejected() -> None:
    from pydantic import BaseModel

    class IntakeSchema(BaseModel):
        lead_email: str

    @workflow("schema_wf", version=1, input_schema=IntakeSchema)
    class SchemaWF:
        steps = ["step_a"]
        async def step_a(self, ctx: StepContext) -> dict: return {}

    defn = get_workflow_latest("schema_wf")
    with pytest.raises((ValueError, KeyError)):
        defn.validate_context({"wrong_field": "value"})

    # Valid context passes
    defn.validate_context({"lead_email": "client@example.com"})


# f. Default gate config applied when not specified
def test_default_gate_config() -> None:
    @workflow("defaultgate", version=1)
    class DGF:
        steps = ["step_a", "GATE:approve", "step_b"]

        async def step_a(self, ctx: StepContext) -> dict: return {}
        async def step_b(self, ctx: StepContext) -> dict: return {}

    defn = get_workflow_latest("defaultgate")
    cfg = defn.gate_config("GATE:approve")
    assert cfg.required_role == "attorney"
    assert "mcp" in cfg.channels
