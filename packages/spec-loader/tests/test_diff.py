from __future__ import annotations

from mde_contracts.spec import AppSpec, Entity, FieldDef, RevisionBlock, ScreenBlock
from mde_spec_builder.render import render_spec
from mde_spec_builder.testing import make_sample_spec
from mde_spec_loader import SpecIndex, diff_specs, diff_texts


def _screen(spec: AppSpec, screen_id: str) -> ScreenBlock:
    return next(s for s in spec.screens if s.id == screen_id)


def _bump(spec: AppSpec) -> AppSpec:
    """A new version number changes the header and revision, which are meta and never diffed."""
    header = spec.header.model_copy(update={"spec_version": spec.header.spec_version + 1})
    return spec.model_copy(
        update={"header": header, "revision": RevisionBlock(version=header.spec_version)}
    )


def test_identical_specs_have_no_diff(spec12: AppSpec) -> None:
    diff = diff_specs(spec12, spec12)
    assert diff.empty and diff.all_ids == [] and diff.stale_screens == []


def test_meta_blocks_are_never_listed(spec12: AppSpec) -> None:
    assert diff_specs(spec12, _bump(spec12)).empty


def test_a_changed_screen_is_listed_and_stale(spec12: AppSpec) -> None:
    changed = spec12.model_copy(
        update={
            "screens": [
                s.model_copy(update={"purpose": "A new purpose"}) if s.id == "SCR-003" else s
                for s in spec12.screens
            ]
        }
    )
    diff = diff_specs(spec12, _bump(changed))
    assert diff.changed == ["SCR-003"] and diff.added == [] and diff.removed == []
    assert diff.stale_screens == ["SCR-003"]


def test_added_and_removed_screens(spec12: AppSpec) -> None:
    fewer = make_sample_spec(11)
    diff = diff_specs(spec12, fewer)
    assert "SCR-012" in diff.removed
    reverse = diff_specs(fewer, spec12)
    assert "SCR-012" in reverse.added and "SCR-012" in reverse.stale_screens


def test_a_changed_entity_makes_its_users_stale(spec12: AppSpec) -> None:
    product = next(e for e in spec12.data.entities if e.id == "ENT-Product")
    renamed = Entity.model_validate(
        {**product.model_dump(), "fields": [*product.fields, FieldDef(name="sku")]}
    )
    data = spec12.data.model_copy(
        update={"entities": [renamed if e.id == "ENT-Product" else e for e in spec12.data.entities]}
    )
    newer = spec12.model_copy(update={"data": data})
    diff = diff_specs(spec12, newer)
    assert "ENT-Product" in diff.changed and "DATA" in diff.changed
    users = set(diff.stale_screens)
    assert {"SCR-003", "SCR-005", "SCR-006"} <= users  # screens that bind Product operations
    assert "SCR-001" not in users and "SCR-002" not in users  # the sign-in screens do not


def test_a_changed_operation_makes_its_screens_stale(spec12: AppSpec) -> None:
    ops = [
        o.model_copy(update={"path": "/v2/products/{id}"}) if o.id == "API-getProduct" else o
        for o in spec12.api.operations
    ]
    newer = spec12.model_copy(update={"api": spec12.api.model_copy(update={"operations": ops})})
    diff = diff_specs(spec12, newer)
    assert "API-getProduct" in diff.changed
    assert "SCR-006" in diff.stale_screens and "SCR-001" not in diff.stale_screens


def test_diff_on_text_and_through_the_index(spec12: AppSpec, index12: SpecIndex) -> None:
    changed = spec12.model_copy(
        update={
            "screens": [
                s.model_copy(update={"checks": [*s.checks, "A new check"]})
                if s.id == "SCR-009"
                else s
                for s in spec12.screens
            ]
        }
    )
    text_b = render_spec(_bump(changed))
    assert diff_texts(render_spec(spec12), text_b).changed == ["SCR-009"]
    assert index12.diff(text_b).changed == ["SCR-009"]
    assert index12.diff(SpecIndex.from_text(text_b)).stale_screens == ["SCR-009"]


def test_summary_and_design_changes_are_reported_as_blocks(spec12: AppSpec) -> None:
    newer = spec12.model_copy(update={"summary": "A different summary"})
    assert diff_specs(spec12, newer).changed == ["SUMMARY"]
    tokens = spec12.design.tokens.model_copy(update={"spacing": [2, 4, 8]})
    design = spec12.design.model_copy(update={"tokens": tokens})
    assert diff_specs(spec12, spec12.model_copy(update={"design": design})).changed == ["DESIGN"]


def test_diff_ids_are_sorted_and_stable(spec12: AppSpec) -> None:
    fewer = make_sample_spec(8)
    first = diff_specs(spec12, fewer)
    assert first == diff_specs(spec12, fewer)
    assert first.removed == sorted(first.removed)
