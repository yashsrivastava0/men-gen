"""The structure page, the structure block of the consumer views and the export block (ADR 0017, S6): pure functions of
a Brief. A Brief without a structure keeps every view exactly as it was; one with a structure is laid out for a person
to review (names in their words, no provenance) and for the next engine (values only, one slice per module)."""

from __future__ import annotations

import json

from mde_compass.catalog.loader import load_catalog
from mde_compass.catalog.size_bands import get_band, load_size_bands
from mde_compass.domain.brief import Brief
from mde_compass.domain.interview import InterviewBlock
from mde_compass.engine import structure_derive as derive
from mde_compass.engine.structure_testing import sample_app
from mde_compass.views.consumers import blueprint_view as _blueprint
from mde_compass.views.consumers import (
    build_handoff,
    design_view,
    research_view,
    verify_view,
)
from mde_compass.views.structure_view import (
    build_structure_view,
    research_guidance_block,
    structure_block,
    structure_export,
)
from mde_compass.views.testing import rich_brief

CATALOG = load_catalog()
SLICE_BUDGET_TOKENS = 1500
QUOTE_MARKERS = ("provenance", "quote", "history", "confidence", "turn_id")


def with_band(brief: Brief, band: str = "medium", source: str = "tier") -> Brief:
    brief.meta.interview = InterviewBlock(size_band=band, size_source=source)  # type: ignore[arg-type]
    return brief


def page(brief: Brief, *, band: str = "medium", language: str = "en"):
    return build_structure_view(
        brief,
        catalog=CATALOG,
        language=language,
        band=band,
        source="tier",
        enabled=True,
        session_id="ses_x",
    )


# --------------------------------------------------------------------------------------------- the page


def test_the_page_lays_out_modules_with_their_screens_in_the_persons_words() -> None:
    brief = sample_app()
    vm = page(brief)
    assert vm.drafted and vm.enabled and vm.session_id == "ses_x"
    assert [m.id for m in vm.modules] == [m.id for m in brief.modules]
    names = {s.id: s.name for s in brief.screens}
    for module in vm.modules:
        assert [s.id for s in module.screens] == [
            s.id for s in brief.screens if s.module == module.id
        ]
        assert module.entry == names.get(
            next(m.entry for m in brief.modules if m.id == module.id), ""
        )
    assert vm.counts.modules == len(brief.modules) and vm.counts.screens == len(brief.screens)
    assert vm.counts.flows == len(brief.flows) and vm.counts.entities == len(brief.entities)
    # role ids are shown as the names the person used
    role_names = {r.name_in_user_words for r in brief.roles}
    assert {r for m in vm.modules for s in m.screens for r in s.roles} <= role_names


def test_the_page_shows_decision_and_tier_only_never_provenance_or_quotes() -> None:
    dumped = json.dumps(page(sample_app()).model_dump(mode="json"))
    for marker in QUOTE_MARKERS:
        assert f'"{marker}"' not in dumped
    assert (
        '"decision": "proposed"' in dumped and '"tier": "Open"' in dumped
    )  # the sample is a plain draft


def test_a_flow_names_the_modules_it_passes_through_in_order_without_repeats() -> None:
    brief = sample_app()
    vm = page(brief)
    screen_module = {s.id: s.module for s in brief.screens}
    module_name = {m.id: m.name for m in brief.modules}
    for flow, view in zip(brief.flows, vm.flows, strict=True):
        walked: list[str] = []
        for st in flow.steps:
            name = module_name[screen_module[st.screen]]
            if not walked or walked[-1] != name:
                walked.append(name)
        assert view.modules == walked
        assert [s.screen_id for s in view.steps] == [s.screen for s in flow.steps]


def test_the_role_by_module_matrix_counts_screens_a_role_may_open() -> None:
    brief = sample_app()
    vm = page(brief)
    assert vm.matrix.roles == [r.name_in_user_words for r in brief.roles]
    assert [r.module_id for r in vm.matrix.rows] == [m.id for m in brief.modules]
    by_role = derive.role_matrix(brief)
    for row in vm.matrix.rows:
        for role, cell in zip(brief.roles, row.cells, strict=True):
            assert cell == len(by_role.get(role.id, {}).get(row.module_id, []))
    assert all(len(row.cells) == len(brief.roles) for row in vm.matrix.rows)


def test_an_entity_is_shown_with_its_fields_states_and_who_may_do_what() -> None:
    brief = sample_app()
    vm = page(brief)
    by_id = {e.id: e for e in vm.entities}
    for e in brief.entities:
        shown = by_id[e.id]
        assert [f.key for f in shown.fields] == [f.key for f in e.fields]
        assert {a.role_id: a.level for a in shown.access} == dict(e.access)
        assert shown.states == list(e.states)


def test_the_size_banner_carries_the_band_numbers_from_data() -> None:
    bands = load_size_bands()
    for band_id in ("small", "medium", "large"):
        view = page(sample_app(), band=band_id).band
        band = get_band(band_id)
        assert view.id == band_id
        assert view.screens == [band.screens[0], band.screens[1]]
        assert view.open_upper is band.open_upper
        assert view.review == band.review and view.research_depth == band.research.depth
        assert view.label == CATALOG.t(f"size.{band_id}", "en") != ""
    assert set(bands.bands) == {"small", "medium", "large"}


def test_the_large_band_blocks_on_unreviewed_modules_and_the_small_one_does_not() -> None:
    large = page(sample_app(), band="large")
    assert large.review_blocks and large.unreviewed == len(large.modules)
    small = page(sample_app(), band="small")
    assert not small.review_blocks


def test_an_accepted_module_is_not_counted_as_unreviewed() -> None:
    brief = sample_app()
    brief.modules[0] = brief.modules[0].model_copy(update={"decision": "accepted"})
    vm = page(brief, band="large")
    assert vm.unreviewed == len(brief.modules) - 1
    assert vm.modules[0].decision == "accepted"


def test_an_empty_brief_is_an_undrafted_page_with_no_issues_and_no_band_text() -> None:
    vm = page(Brief(), band="")
    assert not vm.drafted and vm.modules == [] and vm.issues == []
    assert vm.band.id == "" and vm.counts.screens == 0 and vm.verdict == "fits"


def test_the_page_lists_what_the_checks_found_with_the_ids_they_name() -> None:
    brief = sample_app()
    gone = brief.screens[-1].id
    brief.screens = brief.screens[:-1]
    vm = page(brief)
    assert vm.issues, "a removed screen leaves something the checks name"
    assert all(i.code and i.text and i.severity for i in vm.issues)
    assert any(gone in i.ids or i.ids for i in vm.issues)


def test_hindi_labels_come_from_the_catalog() -> None:
    en, hi = page(sample_app(), language="en"), page(sample_app(), language="hi")
    assert hi.language == "hi" and hi.band.label != en.band.label
    assert hi.band.label == CATALOG.t("size.medium", "hi")


# ------------------------------------------------------------------------------- consumer views and export


def test_a_brief_without_a_structure_keeps_its_views_exactly_as_they_were() -> None:
    brief = rich_brief()
    assert structure_block(brief, "blueprint") == {} and structure_export(brief) == {}
    assert research_guidance_block(brief) == {}  # no band recorded: the research view is unchanged
    views = build_handoff(brief)
    blob = json.dumps(views)
    assert "guidance" not in blob and '"outline"' not in blob
    assert "structure" not in json.dumps(research_view(brief))


def test_each_consumer_view_gets_the_part_of_the_structure_it_reads() -> None:
    brief = with_band(sample_app())
    blue, design, verify = _blueprint(brief), design_view(brief), verify_view(brief)
    structure = blue["structure"]
    assert isinstance(structure, dict)
    assert [m["module"]["id"] for m in structure["modules"]] == [m.id for m in brief.modules]  # type: ignore[index]
    assert structure["outline"]["modules"]  # type: ignore[index]
    assert [s["id"] for s in design["screens"]] == [s.id for s in brief.screens]  # type: ignore[index]
    assert all(s["phase"] in ("core", "extended", "later") for s in design["screens"])  # type: ignore[index]
    assert [f["id"] for f in verify["flows"]] == [f.id for f in brief.flows]  # type: ignore[index]
    assert "must_features_without_acceptance" in verify
    research = research_view(brief)
    assert research["modules"] == [m.name for m in brief.modules]
    assert research["entities"] == [e.name for e in brief.entities]


def test_the_consumer_views_with_a_structure_stay_values_only() -> None:
    brief = with_band(sample_app())
    blob = json.dumps(build_handoff(brief), ensure_ascii=False)
    for marker in QUOTE_MARKERS:
        assert f'"{marker}"' not in blob


def test_a_slice_fits_the_budget_a_downstream_call_can_read_and_holds_its_own_records() -> None:
    brief = with_band(sample_app())
    block = structure_block(brief, "blueprint")["structure"]
    assert isinstance(block, dict)
    for module, slice_ in zip(brief.modules, block["modules"], strict=True):  # type: ignore[arg-type]
        assert derive.slice_tokens(slice_) <= SLICE_BUDGET_TOKENS
        assert slice_["module"]["id"] == module.id
        own = {s.id for s in brief.screens if s.module == module.id}
        assert {s["id"] for s in slice_["screens"]} == own


def test_the_research_guidance_follows_the_band_and_the_category() -> None:
    for band_id, depth in (("small", "light"), ("medium", "standard"), ("large", "deep")):
        guidance = research_view(with_band(sample_app(), band_id))["guidance"]
        band = get_band(band_id)
        assert isinstance(guidance, dict)
        assert guidance["size_band"] == band_id and guidance["depth"] == depth
        assert guidance["search_budget"] == band.research.searches
        assert guidance["gap_fill"] is band.research.gap_fill
    assert (
        research_view(with_band(rich_brief(), "small"))["guidance"]["category"] == "food_delivery"
    )  # type: ignore[index]


def test_the_research_focus_lists_what_to_look_up_and_is_empty_safe() -> None:
    focus = research_guidance_block(with_band(sample_app()))["guidance"]["focus"]  # type: ignore[index]
    topics = {f["topic"]: f["about"] for f in focus}  # type: ignore[index]
    assert "landscape" in topics and "domain" in topics and "compliance" in topics
    assert "health" in topics["compliance"] and "hi" in topics["locales"]
    bare = research_guidance_block(with_band(Brief(), "small"))["guidance"]
    assert bare["depth"] == "light" and "focus" not in bare  # type: ignore[operator]


def test_the_export_block_has_the_band_counts_outline_slices_matrix_and_issues() -> None:
    brief = with_band(sample_app(), "medium", "document")
    out = structure_export(brief)
    assert out["band"] == "medium" and out["source"] == "document"
    assert out["counts"] == {
        "modules": len(brief.modules),
        "screens": len(brief.screens),
        "flows": len(brief.flows),
        "entities": len(brief.entities),
    }
    assert [s["module"]["id"] for s in out["slices"]] == [m.id for m in brief.modules]  # type: ignore[index]
    assert set(out["role_matrix"]) <= {r.id for r in brief.roles}  # type: ignore[arg-type]
    assert out["navigation"] and all(len(e) == 2 for e in out["navigation"])  # type: ignore[union-attr]
    assert not [i for i in out["issues"] if i["severity"] == "error"]  # type: ignore[union-attr,index]
    assert out["research"]["depth"] == "standard"  # type: ignore[index]
    json.dumps(out)  # plain JSON all the way down


def test_the_export_block_without_a_recorded_band_has_no_issues_or_research_keys() -> None:
    out = structure_export(sample_app())
    assert out["band"] == "" and "issues" not in out and "research" not in out
    assert out["slices"] and out["outline"]
