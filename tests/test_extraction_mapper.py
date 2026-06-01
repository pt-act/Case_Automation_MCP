"""G6 — Field mapper + confidence gating focused tests."""

from __future__ import annotations

from cam.core.services.extraction.mapper import FieldMapper, apply_threshold
from cam.core.services.extraction.types import (
    ExtractedField,
    FieldMapping,
    MappingProfile,
)


def _profile(*mappings: tuple[str, str]) -> MappingProfile:
    fields = [
        FieldMapping(source_key=src, domain_key=dst, target_type="Contact")
        for src, dst in mappings
    ]
    return MappingProfile(name="test", version="1.0", fields=fields)


def _field(key: str, conf: float = 0.9) -> ExtractedField:
    return ExtractedField(key=key, target_type="Contact", confidence=conf, sources=[])


mapper = FieldMapper()


# a. Known profile maps fields
def test_known_field_mapped() -> None:
    profile = _profile(("email", "contact.email"))
    fields = [_field("email")]
    result = mapper.to_domain(fields, profile)
    assert result[0].key == "contact.email"


# b. Unmapped key dropped + warned
def test_unmapped_key_dropped() -> None:
    profile = _profile(("email", "contact.email"))
    fields = [_field("unknown_field")]
    warns: list[str] = []
    result = mapper.to_domain(fields, profile, warnings=warns)
    assert result == []
    assert any("unknown_field" in w for w in warns)


# c. Absent field not fabricated
def test_absent_field_not_fabricated() -> None:
    profile = _profile(("email", "contact.email"), ("phone", "contact.phone"))
    fields = [_field("email")]  # phone not provided by LLM
    result = mapper.to_domain(fields, profile)
    keys = [f.key for f in result]
    assert "contact.phone" not in keys


# d. Two profiles yield different maps from same input
def test_two_profiles_different_output() -> None:
    p1 = _profile(("email", "contact.email"))
    p2 = _profile(("email", "matter.title"))
    fields = [_field("email")]
    r1 = mapper.to_domain(fields, p1)
    r2 = mapper.to_domain(fields, p2)
    assert r1[0].key != r2[0].key


# e. Below-threshold field flagged
def test_below_threshold_flagged() -> None:
    fields = [_field("email", conf=0.5)]
    result = apply_threshold(fields, threshold=0.8)
    assert result[0].requires_verification is True


# f. At-threshold not flagged
def test_at_threshold_not_flagged() -> None:
    fields = [_field("email", conf=0.8)]
    result = apply_threshold(fields, threshold=0.8)
    assert result[0].requires_verification is False


# g. Above-threshold not flagged
def test_above_threshold_not_flagged() -> None:
    fields = [_field("email", conf=0.95)]
    result = apply_threshold(fields, threshold=0.8)
    assert result[0].requires_verification is False


# h. Already-flagged field stays flagged even above threshold
def test_already_flagged_stays_flagged() -> None:
    field = ExtractedField(
        key="email", target_type="Contact",
        confidence=0.99, requires_verification=True, sources=[]
    )
    result = apply_threshold([field], threshold=0.5)
    assert result[0].requires_verification is True
