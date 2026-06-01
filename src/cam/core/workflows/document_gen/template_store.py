"""TemplateStore + template:// resource + validation — spec G1.2/1.3/1.4.

Templates are data, not code.  Non-deterministic templates (using now()/random)
are rejected at load time (DG-NFR-1).
"""

from __future__ import annotations

import hashlib
import re

from cam.core.workflows.document_gen.types import TemplateSpec, TemplateVariable

GAP_PLACEHOLDER = "[[MISSING: {label}]]"
_NONDETERMINISTIC = re.compile(r"\bnow\s*\(|random\s*\(|uuid\s*\(", re.IGNORECASE)
_JINJA_VAR = re.compile(r"\{\{\s*([\w.]+)\s*\}\}")


class TemplateNotFound(KeyError):
    def __init__(self, name: str) -> None:
        self.name = name
        super().__init__(f"Template {name!r} not found.")


class TemplateValidationError(ValueError):
    pass


class TemplateStore:
    """In-memory template registry.  Templates registered as data at startup."""

    def __init__(self) -> None:
        self._templates: dict[str, dict[int, tuple[TemplateSpec, bytes]]] = {}

    def register(self, spec: TemplateSpec, content: bytes, *, validate: bool = True) -> None:
        """Register a template.  Raises TemplateValidationError on bad content."""
        if validate:
            _validate(spec, content)
        checksum = hashlib.sha256(content).hexdigest()
        spec = spec.model_copy(update={"checksum": checksum})
        self._templates.setdefault(spec.name, {})[spec.version] = (spec, content)

    def get(self, name: str, version: int | None = None) -> tuple[TemplateSpec, bytes]:
        """Return (TemplateSpec, bytes).  Returns latest version if version is None."""
        versions = self._templates.get(name)
        if not versions:
            raise TemplateNotFound(name)
        if version is not None:
            entry = versions.get(version)
            if entry is None:
                raise TemplateNotFound(f"{name}@v{version}")
            return entry
        latest_version = max(versions)
        return versions[latest_version]

    def resource_template(self, name: str) -> dict:
        """Return self-describing resource payload for template://{name}."""
        spec, _ = self.get(name)
        return {
            "name": spec.name,
            "version": spec.version,
            "format": spec.format,
            "target_form_id": spec.target_form_id,
            "privileged_default": spec.privileged_default,
            "checksum": spec.checksum,
            "variables": [v.model_dump() for v in spec.variables],
        }


def _validate(spec: TemplateSpec, content: bytes) -> None:
    """Reject non-deterministic templates; warn on variable mismatches."""
    text = content.decode("utf-8", errors="replace")
    if _NONDETERMINISTIC.search(text):
        raise TemplateValidationError(
            f"Template {spec.name!r} uses non-deterministic functions "
            "(now()/random()/uuid()) — forbidden (DG-NFR-1)."
        )
    # Check declared vars match Jinja2 vars found in the template
    declared = {v.name for v in spec.variables}
    actual = {m.group(1) for m in _JINJA_VAR.finditer(text)}
    undeclared = actual - declared
    if undeclared:
        raise TemplateValidationError(
            f"Template {spec.name!r} uses undeclared variables: {undeclared}. "
            "Add them to TemplateSpec.variables."
        )


def make_simple_template(spec: TemplateSpec) -> bytes:
    """Generate minimal Jinja2 template bytes from a TemplateSpec (for tests)."""
    lines = [f"# {spec.name} v{spec.version}"]
    for var in spec.variables:
        lines.append(f"{{{{ {var.name} }}}}")
    return "\n".join(lines).encode()
