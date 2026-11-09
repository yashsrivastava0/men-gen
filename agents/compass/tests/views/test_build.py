"""View model: sections, rows, badges, language, review flags, footer, empty Brief."""

from __future__ import annotations

import json

from mde_compass.catalog.loader import load_catalog
from mde_compass.domain.hashing import brief_hash, section_hashes
from mde_compass.store.ports import BriefVersionRecord
from mde_compass.views.build import build_view_model
from mde_compass.views.models import SECTION_IDS, BriefViewModel
from mde_compass.views.testing import empty_brief, make_session, rich_brief

CATALOG = load_catalog()


def _build(brief=None, *, language="en", state="BRIEF_REVIEW", blockers=(), **kw) -> BriefViewModel:
    return build_view_model(
        brief or rich_brief(),
        catalog=CATALOG,
        language=language,
        session=make_session(state),  # type: ignore[arg-type]
        blockers=list(blockers),
        **kw,
    )


def _rows(vm: BriefViewModel):
    return {r.id: r for s in vm.sections for r in s.rows}


def test_fifteen_sections_in_fixed_order_with_localized_titles() -> None:
    vm = _build()
    assert [s.id for s in vm.sections] == list(SECTION_IDS)
    assert len(vm.sections) == 15
    assert [s.title for s in vm.sections] == [
        CATALOG.t(f"section.{sid}", "en") for sid in SECTION_IDS
    ]
    assert all(s.count == len(s.rows) and s.rows for s in vm.sections)


def test_hindi_labels_render() -> None:
    vm = _build(language="hi")
    assert vm.language == "hi"
    assert (
        vm.sections[0].title == CATALOG.t("section.idea", "hi") != CATALOG.t("section.idea", "en")
    )
    assert vm.footer.primary_label == CATALOG.t("ui.looks_right", "hi")
    assert vm.header.status_label == CATALOG.t("status.in_review", "hi")
    rows = _rows(vm)
    assert rows["scope.FTR-001"].status_chip.startswith(CATALOG.t("scope.in", "hi"))
    assert CATALOG.t("badge.you_said", "hi") in [b.text for b in rows["idea.summary"].badges]
    # slot labels and enum option labels come from the catalog in Hindi
    assert rows["experience.tone_words"].label == CATALOG.slots["look.tone_words"].label.hi
    en_tone = _rows(_build())["experience.tone_words"].value.items
    assert rows["experience.tone_words"].value.items != en_tone


def test_unknown_language_falls_back_to_english() -> None:
    vm = _build(language="xx")
    assert vm.language == "en"
    assert vm.sections[0].title == CATALOG.t("section.idea", "en")


def test_badges_have_text_and_icon_never_colour_alone() -> None:
    vm = _build()
    kinds = set()
    for section in vm.sections:
        for row in section.rows:
            for badge in row.badges:
                assert badge.text.strip() and badge.icon.strip()
                assert badge.text == CATALOG.t(f"badge.{badge.kind}", "en")
                assert badge.icon == CATALOG.t(f"badge.icon.{badge.kind}", "en")
                kinds.add(badge.kind)
    assert kinds == {"you_said", "from_document", "assumed", "typical", "suggestion"}


def test_provenance_type_to_badge_mapping() -> None:
    rows = _rows(_build())
    assert [b.kind for b in rows["idea.summary"].badges] == ["you_said"]  # user_text
    assert [b.kind for b in rows["context.geo"].badges] == ["you_said"]  # user_tap
    assert [b.kind for b in rows["data.existing_systems"].badges] == ["from_document"]
    assert [b.kind for b in rows["idea.business_model"].badges] == ["typical"]  # pattern
    assert [b.kind for b in rows["audience.traits"].badges] == ["suggestion"]  # model_knowledge
    assert [b.kind for b in rows["idea.category"].badges] == ["assumed"]


def test_row_ids_use_the_changeset_path_grammar() -> None:
    ids = set(_rows(_build()))
    assert {
        "idea.summary",
        "scope.FTR-004",
        "assumptions.ASM-002",
        "research_requests.RR-001",
    } <= ids
    assert {
        "apps.APP-001",
        "roles.ROL-002",
        "sources.SRC-001",
        "context.geo",
        "experience.brand.name",
    } <= ids


def test_features_grouped_by_app_and_family_with_status_chip() -> None:
    features = next(s for s in _build().sections if s.id == "features")
    rows = {r.id: r for r in features.rows}
    assert rows["scope.FTR-001"].group == "Customer app › Catalog and commerce"
    assert rows["scope.FTR-006"].group == "Cook app › Payments and wallet"
    assert rows["scope.FTR-004"].group == "Customer app, Cook app › Other"  # custom, no family
    assert rows["scope.FTR-001"].status_chip == "In the first version · Must have"
    assert rows["scope.FTR-003"].status_chip == "Later"  # priority label equals status label
    assert rows["scope.FTR-005"].status_chip == "Cannot be built here"
    assert rows["scope.FTR-006"].status_chip == "Left out"
    # groups are contiguous: once a group ends it never comes back
    seen: list[str] = []
    for r in features.rows:
        if not seen or seen[-1] != r.group:
            assert r.group not in seen
            seen.append(r.group)


def test_needs_review_flags() -> None:
    rows = _rows(_build())
    assert rows["assumptions.ASM-002"].needs_review  # high impact, still proposed
    assert not rows["assumptions.ASM-001"].needs_review  # low impact
    assert not rows["assumptions.ASM-003"].needs_review  # high impact but accepted
    assert rows["scope.FTR-004"].needs_review  # custom feature awaiting confirmation
    assert not rows["scope.FTR-001"].needs_review
    assert rows["open_questions.Q-001"].needs_review  # blocking question


def test_assumption_row_shows_claim_reason_and_what_changes() -> None:
    row = _rows(_build())["assumptions.ASM-002"]
    assert row.label == "Only Pune at launch"
    assert row.value.items == [
        "Why: You mentioned one city",
        "If you change this: Other cities need delivery zones",
    ]
    assert row.group == "Users"


def test_lookup_and_cannot_build_sections() -> None:
    vm = _build()
    lookup = next(s for s in vm.sections if s.id == "lookup")
    assert [r.label for r in lookup.rows] == [
        "Tiffin services in Pune",
        "UPI payments for small sellers",
    ]
    declined = next(s for s in vm.sections if s.id == "cannot_build")
    assert "Instead: Riders on bikes" in declined.rows[0].value.items


def test_history_available_and_tier() -> None:
    rows = _rows(_build())
    assert rows["idea.summary"].history_available and rows["idea.summary"].tier == "Locked"
    assert not rows["roles.ROL-003"].history_available and rows["roles.ROL-003"].tier == "Open"


def test_blockers_listed_and_approval_blocked() -> None:
    vm = _build(blockers=["blocker.hash_mismatch", "Something custom"])
    assert not vm.footer.approvable
    assert vm.footer.blockers == [CATALOG.t("blocker.hash_mismatch", "en"), "Something custom"]
    assert (
        vm.footer.primary_label == "Looks right" and vm.footer.secondary_label == "Change something"
    )
    assert _build().footer.approvable and _build().footer.blockers == []
    hi = _build(language="hi", blockers=["hash_mismatch"])
    assert hi.footer.blockers == [CATALOG.t("blocker.hash_mismatch", "hi")]


def test_status_follows_session_state() -> None:
    assert _build(state="APPROVED").header.status == "approved"
    assert _build(state="BRIEF_REVIEW").header.status == "in_review"
    assert _build(state="INTERVIEWING").header.status == "draft"


def test_header_title_idea_line_readiness_and_progress() -> None:
    vm = _build(reviewed_sections=["idea", "who"])
    h = vm.header
    assert h.title == "Tiffin Express" and h.version == 3
    assert h.idea_line == "Order lunch boxes from home cooks in Pune"
    assert [c.consumer for c in h.readiness] == ["research", "blueprint", "design"]
    assert all(
        c.ready and c.label == f"Ready for {CATALOG.t('readiness.' + c.consumer, 'en')}"
        for c in h.readiness
    )
    assert (h.reviewed, h.of_total) == (2, 15)
    assert [s.reviewed for s in vm.sections[:3]] == [True, True, False]


def test_readiness_chips_show_gaps() -> None:
    brief = rich_brief()
    del brief.context["geo"]
    chips = {c.consumer: c for c in _build(brief).header.readiness}
    assert not chips["research"].ready and chips["research"].gaps == [
        CATALOG.slots["context.geo"].label.en
    ]
    assert "1" in chips["research"].label
    assert not chips["design"].ready


def test_degraded_flag_and_title_fallback() -> None:
    brief = rich_brief()
    brief.meta.degraded = True
    del brief.experience["brand.name"]
    vm = _build(brief)
    assert vm.header.degraded
    assert vm.header.title == "Order lunch boxes from home cooks in Pune"


def test_changed_rows_from_changed_paths_and_baseline() -> None:
    old = rich_brief(version=2)
    new = rich_brief(version=3)
    new.scope[0].priority = "nice"
    new.scope.append(new.scope[0].model_copy(update={"id": "FTR-007", "name": "Reviews"}))
    baseline = BriefVersionRecord(
        session_id="ses_TEST",
        version=2,
        brief=old,
        hash=brief_hash(old),
        section_hashes=section_hashes(old),
        baseline_label="B-G1",
    )
    vm = _build(new, baseline=baseline, changed_paths=["assumptions.ASM-001"])
    rows = _rows(vm)
    assert rows["scope.FTR-001"].changed and rows["scope.FTR-007"].changed
    assert rows["assumptions.ASM-001"].changed
    assert not rows["scope.FTR-002"].changed
    assert vm.changed_since_approval == ["features"]
    assert not any(r.changed for r in _rows(_build()).values())


def test_hashes_pass_through_and_model_serializes() -> None:
    brief = rich_brief()
    vm = _build(brief, brief_hash=brief_hash(brief), section_hashes=section_hashes(brief))
    assert vm.brief_hash == brief_hash(brief) and "idea" in vm.section_hashes
    assert BriefViewModel.model_validate(json.loads(vm.model_dump_json())) == vm


def test_empty_brief_renders_empty_text_not_a_crash() -> None:
    for lang in ("en", "hi"):
        vm = _build(empty_brief(), language=lang)
        assert len(vm.sections) == 15
        assert all(not s.rows and s.empty_text and s.count == 0 for s in vm.sections)
        assert vm.header.title and vm.header.of_total == 0 and not vm.header.readiness[0].ready
        assert vm.footer.approvable  # no blockers were passed in


def test_view_model_is_deterministic() -> None:
    assert _build().model_dump() == _build().model_dump()


def test_no_raw_string_keys_or_placeholders_leak_into_the_output() -> None:
    for brief in (rich_brief(), empty_brief()):
        for lang in ("en", "hi"):
            vm = _build(brief, language=lang, blockers=["hash_mismatch"])
            text = vm.model_dump_json()
            for leak in (
                "view.",
                "badge.",
                "section.",
                "readiness.",
                "scope.in",
                "priority.",
                "{value}",
                "{count}",
                "{consumer}",
            ):
                assert leak not in text.replace("scope.FTR", "").replace("scope.", "scope_"), (
                    lang,
                    leak,
                )


def test_hindi_output_has_no_english_labels_for_catalog_driven_text() -> None:
    vm = _build(language="hi")
    english = {CATALOG.t(f"section.{s}", "en") for s in SECTION_IDS}
    assert not english & {s.title for s in vm.sections}
    chips = [c.label for c in vm.header.readiness]
    assert all("Ready" not in c and "gaps" not in c for c in chips)
    rows = _rows(vm)
    assert rows["assumptions.ASM-002"].status_chip == "अहम"
    assert rows["assumptions.ASM-002"].group == "उपयोगकर्ता"


def test_details_we_captured_is_a_read_only_section_grouped_by_topic() -> None:
    vm = _build()
    section = next(s for s in vm.sections if s.id == "details")
    assert section.title == CATALOG.t("section.details", "en") == "Details we captured"
    # sorted by topic, then id: "look and feel" < "orders" < "riders"
    assert [r.id for r in section.rows] == [
        "domain_facts.FCT-003",
        "domain_facts.FCT-002",
        "domain_facts.FCT-001",
    ]
    assert all(not r.editable and r.group for r in section.rows)
    assert {r.group for r in section.rows} == {"orders", "look and feel", "riders"}
    hi = _build(language="hi")
    assert next(s for s in hi.sections if s.id == "details").title == CATALOG.t(
        "section.details", "hi"
    )


def test_slot_less_assumptions_render_with_a_typical_badge_and_a_review_flag_when_high() -> None:
    """ADR 0016: typical details, corrections, review findings, document conflicts and probe gaps have no slot; the
    page still shows them like any assumption, and a high-impact one needs the person's decision."""
    from mde_compass.domain.brief import AssumptionRecord

    brief = rich_brief()
    brief.assumptions += [
        AssumptionRecord(
            id="ASM-090",
            claim="Customers get a receipt",
            basis="typical",
            reason="r",
            what_changes="w",
            theme="Features",
        ),
        AssumptionRecord(
            id="ASM-091",
            claim="We used 30; you first said 20",
            basis="correction",
            impact="high",
            theme="Business",
        ),
    ]
    rows = _rows(_build(brief))
    typical, correction = rows["assumptions.ASM-090"], rows["assumptions.ASM-091"]
    assert "typical" in {b.kind for b in typical.badges} and "assumed" in {
        b.kind for b in typical.badges
    }
    assert not typical.needs_review and correction.needs_review
    assert len(typical.value.items) == 2  # the reason and what changes
