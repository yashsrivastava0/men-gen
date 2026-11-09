"""The Needs Registry (build prompt 02): every need says what a sufficient answer contains; frames are
style guides for the planner, not scripts."""

from __future__ import annotations

import re

import pytest

from mde_compass.catalog import loader
from mde_compass.catalog.loader import CatalogError, load_catalog
from mde_compass.domain.interview import FACT_TYPES


def test_every_need_has_a_usable_answer_spec() -> None:
    catalog = load_catalog()
    assert len(catalog.slots) == 44
    for slot in catalog.slots.values():
        spec = slot.answer_spec
        assert spec is not None, slot.id
        assert len(spec.sufficient.split()) >= 5, slot.id
        assert spec.aspects and len(set(spec.aspects)) == len(spec.aspects), slot.id
        assert all(re.fullmatch(r"[a-z][a-z0-9_]*", a) for a in spec.aspects), slot.id
        assert spec.fact_types and set(spec.fact_types) <= set(FACT_TYPES), slot.id
        assert spec.min_facts >= 1, slot.id


def test_the_examples_from_the_prompt_are_covered() -> None:
    slots = load_catalog().slots
    geo = slots["context.geo"].answer_spec
    assert geo is not None and {"country", "city_or_region", "languages"} <= set(geo.aspects)
    seg = slots["audience.segments"].answer_spec
    assert seg is not None and {"who", "goal", "payer_vs_user"} <= set(seg.aspects)
    biz = slots["idea.business_model"].answer_spec
    assert biz is not None and {"payer", "revenue_source", "free_vs_paid"} <= set(biz.aspects)
    roles = slots["roles.list"].answer_spec
    assert roles is not None and {"role_name", "top_goal", "permissions"} <= set(roles.aspects)
    feat = slots["scope.features"].answer_spec
    assert feat is not None and {"purpose", "first_release"} <= set(feat.aspects)


def test_universal_needs_are_the_few_that_need_no_project_term() -> None:
    universal = {s.id for s in load_catalog().slots.values() if s.universal}
    assert universal == {"idea.summary", "context.geo", "brand.name", "brand.assets", "locales"}


def test_frames_are_style_guides_with_examples() -> None:
    frames = load_catalog().frames
    assert len(frames) == 17
    for frame in frames.values():
        assert frame.style_guide and len(frame.style_guide.split()) >= 8, frame.id
        assert len(frame.examples) >= 2, frame.id
        assert all(e.strip() and "TODO" not in e for e in frame.examples), frame.id


def test_the_loader_rejects_a_need_without_an_answer_spec() -> None:
    slot = load_catalog().slots["idea.summary"]
    broken = slot.model_copy(update={"answer_spec": None})
    with pytest.raises(CatalogError, match="answer_spec is missing"):
        loader._validate_slots({"idea.summary": broken})


def test_the_loader_rejects_duplicate_or_non_snake_case_aspects() -> None:
    slot = load_catalog().slots["idea.summary"]
    assert slot.answer_spec is not None
    twice = slot.answer_spec.model_copy(update={"aspects": ["who", "who"]})
    with pytest.raises(CatalogError, match="distinct"):
        loader._validate_slots({"idea.summary": slot.model_copy(update={"answer_spec": twice})})
    shouty = slot.answer_spec.model_copy(update={"aspects": ["Who Is It For"]})
    with pytest.raises(CatalogError, match="snake_case"):
        loader._validate_slots({"idea.summary": slot.model_copy(update={"answer_spec": shouty})})
