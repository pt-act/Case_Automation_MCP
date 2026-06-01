"""Schema-derived documentation generator — spec §4.7, PTD §16.

Renders Pydantic model schemas + docstrings into Markdown.
A CI check re-runs this and fails on drift or missing descriptions.
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel


def generate_model_doc(model: type[BaseModel]) -> str:
    """Render a Markdown section for a single Pydantic model."""
    lines: list[str] = []
    class_doc = inspect.getdoc(model) or ""
    lines.append(f"## `{model.__name__}`\n")
    if class_doc:
        lines.append(class_doc + "\n")

    schema = model.model_json_schema()
    properties = schema.get("properties", {})

    if properties:
        lines.append("| Field | Type | Required | Description |")
        lines.append("|-------|------|----------|-------------|")
        required_fields = set(schema.get("required", []))
        for field_name, field_info in properties.items():
            field_type = _type_label(field_info)
            description = field_info.get("description", "")
            required = "✓" if field_name in required_fields else ""
            lines.append(f"| `{field_name}` | `{field_type}` | {required} | {description} |")

    return "\n".join(lines) + "\n"


def _type_label(field_info: dict[str, Any]) -> str:
    if "anyOf" in field_info:
        types = [_type_label(t) for t in field_info["anyOf"]]
        return " | ".join(types)
    if "type" in field_info:
        return field_info["type"]
    if "$ref" in field_info:
        return field_info["$ref"].split("/")[-1]
    return "any"


def generate_module_docs(
    models: list[type[BaseModel]],
    module_title: str = "Domain Model",
    preamble: str = "",
) -> str:
    """Generate a full Markdown document for a list of models."""
    lines = [f"# {module_title}\n"]
    if preamble:
        lines.append(preamble + "\n")

    missing: list[str] = []
    for model in models:
        # Use __doc__ directly — inspect.getdoc() inherits from parent classes
        # which would mask a truly undocumented subclass.
        if not model.__doc__ or not model.__doc__.strip():
            missing.append(model.__name__)
        lines.append(generate_model_doc(model))

    if missing:
        raise ValueError(
            f"The following models are missing docstrings: {missing}. "
            "Add a class-level docstring before generating docs."
        )

    return "\n".join(lines)


def check_drift(models: list[type[BaseModel]], docs_path: Path) -> bool:
    """Return True if the generated docs match the file on disk.

    Called by the CI drift checker:  regenerate → compare → fail if different.
    """
    generated = generate_module_docs(models)
    if not docs_path.exists():
        return False
    return docs_path.read_text() == generated


def write_docs(models: list[type[BaseModel]], docs_path: Path) -> None:
    """Write regenerated docs to disk."""
    content = generate_module_docs(models)
    docs_path.parent.mkdir(parents=True, exist_ok=True)
    docs_path.write_text(content)
