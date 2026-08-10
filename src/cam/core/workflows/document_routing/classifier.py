"""Classifier — extraction fields + rules → (class, confidence, source) — spec G2."""

from __future__ import annotations

from typing import Any

from cam.core.domain.models import Document
from cam.core.workflows.document_routing.config import (
    DOCUMENT_CLASSES,
    RoutingConfig,
    get_routing_config,
)


class Classifier:
    """Classifies a document from extraction fields and classification rules."""

    def classify(
        self,
        document: Document,
        extraction_fields: list[dict[str, Any]] | None = None,
        config: RoutingConfig | None = None,
    ) -> tuple[str, float, str]:
        """Return (class_name, confidence, source).

        Source ∈ {rule, extraction, fallback}.
        Below-threshold or conflicting → 'unknown' → review_queue.
        Deterministic for identical inputs.
        """
        cfg = config or get_routing_config()

        # 1. Rule-based: check document classification field or name hints
        if document.classification and document.classification in DOCUMENT_CLASSES:
            return document.classification, 1.0, "rule"

        # 2. Check classification rules against document name
        doc_name_lower = document.name.lower()
        for rule in cfg.classification_rules:
            if rule.class_name not in DOCUMENT_CLASSES:
                continue
            for hint in rule.keyword_hints:
                if hint.lower() in doc_name_lower:
                    return rule.class_name, 0.90, "rule"

        # 3. Extraction-driven
        if extraction_fields:
            best_class: str | None = None
            best_confidence: float = 0.0
            for field in extraction_fields:
                field_class = field.get("value", "")
                conf = float(field.get("confidence", 0.0))
                if field_class in DOCUMENT_CLASSES and conf > best_confidence:
                    best_class = field_class
                    best_confidence = conf

            if best_class and best_confidence >= cfg.classification_threshold:
                return best_class, best_confidence, "extraction"

        # 4. Fallback → unknown → review_queue
        return "unknown", 0.0, "fallback"
