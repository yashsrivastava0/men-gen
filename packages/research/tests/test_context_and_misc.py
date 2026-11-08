"""Brief context building, progress labels, the deterministic C8 fallback, config and helpers."""

from __future__ import annotations

import pytest
from research_helpers import (
    NOW,
    fake_clock,
    make_brief,
    make_extras_request,
    make_policy,
    make_request,
    make_slots,
    slot,
)

from mde_contracts.brief import AssumedItem, Brief, BriefFeature, BriefRow
from mde_contracts.calls import BriefContext, C8Input, FactForSynthesis
from mde_research.config import DEPTH_CAPS, ResearchConfig
from mde_research.context import (
    brand_name_of,
    brand_url_of,
    build_brief_context,
    build_privacy,
    references_of,
    scrub_context,
    slot_value,
)
from mde_research.progress import label_for, label_keys
from mde_research.synthesis import (
    assemble_note,
    fallback_draft,
    internal_source_ref,
    normalize_applies_to,
    unavailable_note,
)

# --------------------------------------------------------------------------- brief context


def test_context_collects_the_brief_and_the_slots() -> None:
    row = BriefRow(
        key="features",
        title="Features",
        text="x",
        slot_ids=["scope.features"],
        features=[
            BriefFeature(id="catalog_browse", label="Browse products"),
            BriefFeature(id="loyalty", label="Loyalty points", on=False),
            BriefFeature(id="payments", label="Payments"),
        ],
    )
    look = BriefRow(
        key="look", title="Look", text="Clean and bright", slot_ids=["design.direction"]
    )
    brief = Brief(
        version=2,
        app_summary="  A shop for local crafts  ",
        language="hi",
        mode="enterprise",
        rows=[row, look],
        assumptions=[
            AssumedItem(slot_id="locale", label="Language", value_text="English", reason="r"),
            AssumedItem(
                slot_id="a11y",
                label="Accessibility",
                value_text="Standard",
                reason="r",
                included=False,
            ),
        ],
    ).with_hash()
    slots = make_slots(
        "food_delivery",
        compliance__flags=["child_safety"],
        regions=["in", "eu"],
        users__roles=["customer", "courier"],
        design__direction="warm-friendly",
        auth__sso="required",
    )
    ctx = build_brief_context(make_request(brief=brief, slots=slots))
    assert ctx.app_summary == "A shop for local crafts"
    assert ctx.archetype == "food_delivery"
    assert ctx.language == "hi" and ctx.mode == "enterprise"
    assert [f.id for f in ctx.features] == ["catalog_browse", "payments"]  # only what is on
    assert ctx.roles == ["customer", "courier"]
    assert ctx.regions == ["in", "eu"]
    assert ctx.compliance_flags == ["child_safety"]
    assert ctx.look == "warm-friendly"
    assert ctx.login == "sso"
    assert ctx.assumptions == ["Language: English"]  # excluded assumptions are not research input


def test_context_falls_back_to_the_feature_slot_and_the_look_row() -> None:
    brief = Brief(
        version=1,
        app_summary="An app",
        rows=[
            BriefRow(key="look", title="Look", text="Bold and dark", slot_ids=["design.direction"])
        ],
    ).with_hash()
    slots = make_slots("custom", features=["list_detail", "search"])
    del slots["auth.method"]
    ctx = build_brief_context(make_request(brief=brief, slots=slots))
    assert [f.id for f in ctx.features] == ["list_detail", "search"]
    assert ctx.features[0].label == "List detail"
    assert ctx.look == "Bold and dark"
    assert ctx.login == ""


def test_missing_archetype_defaults_to_custom() -> None:
    slots = make_slots("commerce")
    del slots["idea.archetype"]
    assert build_brief_context(make_request(slots=slots)).archetype == "custom"


def test_slot_value_helper() -> None:
    slots = make_slots("commerce")
    assert slot_value(slots, "idea.archetype") == "commerce"
    assert slot_value(slots, "regions") is None


def test_brand_name_ignores_placeholders_links_and_defaults() -> None:
    def name_for(value: str, **kw: str) -> str | None:
        slots = make_slots("commerce")
        slots["brand.name"] = slot("brand.name", value, **kw)
        return brand_name_of(make_request(slots=slots))

    assert name_for("Zed Shop") == "Zed Shop"
    assert name_for("My App") is None
    assert name_for("https://zed.example") is None
    assert name_for("zed.example") is None
    assert name_for("") is None
    assert name_for("Zed Shop", state="assumed", source="default") is None
    assert brand_name_of(make_request()) is None


def test_brand_url_comes_from_the_request_or_a_typed_link() -> None:
    assert brand_url_of(make_extras_request(brand_url="zed.example")) == "https://zed.example/"
    assert brand_url_of(make_extras_request(brand_url="javascript:alert(1)")) is None
    slots = make_slots("commerce")
    slots["brand.assets"] = slot("brand.assets", "Our site is https://zed.example/about, thanks.")
    assert brand_url_of(make_request(slots=slots)) == "https://zed.example/about"
    slots = make_slots("commerce")
    slots["brand.name"] = slot("brand.name", "zed.example")
    assert brand_url_of(make_request(slots=slots)) == "https://zed.example/"
    slots = make_slots("commerce")
    slots["brand.assets"] = slot("brand.assets", "https://127.0.0.1/admin")
    assert brand_url_of(make_request(slots=slots)) is None
    slots = make_slots("commerce")
    slots["brand.name"] = slot("brand.name", "Just a name")
    assert brand_url_of(make_request(slots=slots)) is None
    assert brand_url_of(make_request()) is None


def test_references_are_deduplicated_trimmed_and_capped() -> None:
    req = make_extras_request(references=[" A ", "A", "B", "", "C", "D", "E"])
    assert references_of(req) == ["A", "B", "C", "D"]
    assert references_of(make_request()) == []


def test_privacy_is_built_from_every_name_in_the_brief() -> None:
    req = make_extras_request(
        references=["Rival"],
        brand_url="https://www.zedshop.example/",
        slots={**make_slots("commerce"), "brand.name": slot("brand.name", "Zed Shop")},
        policy=make_policy(allow_company_name=False, org="acme-corp"),
    )
    ctx = build_brief_context(req)
    privacy = build_privacy(req, ctx)
    assert privacy.restricted
    for term in ("Zed Shop", "Rival", "zedshop.example", "zedshop", "acme-corp"):
        assert term in privacy.terms
    assert not build_privacy(make_request(), build_brief_context(make_request())).restricted


def test_scrub_context_removes_names_and_the_brand_entirely() -> None:
    req = make_extras_request(
        references=["Rival"],
        brand_url="https://zedshop.example/",
        brief=make_brief(summary="Zedshop sells crafts, a bit like Rival"),
        slots={**make_slots("commerce"), "brand.name": slot("brand.name", "Zedshop")},
        policy=make_policy(allow_company_name=False),
    )
    ctx = build_brief_context(req)
    scrubbed = scrub_context(ctx, build_privacy(req, ctx))
    assert scrubbed.app_summary == "the company sells crafts, a bit like the company"
    assert scrubbed.brand_name is None and scrubbed.brand_url is None and scrubbed.references == []
    open_ctx = build_brief_context(make_extras_request(references=["Rival"]))
    assert scrub_context(open_ctx, build_privacy(make_extras_request(), open_ctx)) is open_ctx


# --------------------------------------------------------------------------- progress labels


def test_labels_are_plain_language_with_fallbacks() -> None:
    assert label_for("plan_research") == "Planning the research"
    assert label_for("search:R1") == "Looking at similar apps"
    assert label_for("search:R5") == "Checking integrations"
    assert label_for("extract") == "Reading what we found"
    assert label_for("synthesize", "hi-Latn") == "Notes taiyaar kar rahe hain"
    assert label_for("synthesize", "fr") == "Putting the notes together"  # falls back to English
    assert label_for("synthesize", "hi-IN") == label_for("synthesize", "hi")
    assert label_for("unknown-step") == "Reading what we found"


def test_every_label_has_all_three_languages_and_no_jargon() -> None:
    for key in label_keys():
        for lang in ("en", "hi", "hi-Latn"):
            text = label_for(key, lang)
            assert text and "R1" not in text and "C7" not in text and "query" not in text.lower()


# --------------------------------------------------------------------------- synthesis helpers


def fact(n: int, kind: str, statement: str, *, verified: bool = True) -> FactForSynthesis:
    return FactForSynthesis.model_validate(
        {
            "id": f"F-{n:03d}",
            "job": "R1",
            "kind": kind,
            "statement": statement,
            "source_urls": [f"https://example.com/{n}"],
            "verified": verified,
        }
    )


def c8_input(facts: list[FactForSynthesis], flags: list[str] | None = None) -> C8Input:
    return C8Input(
        brief=BriefContext(app_summary="x", archetype="commerce", compliance_flags=flags or []),
        facts=facts,
    )


def test_fallback_draft_uses_verified_facts_only_and_never_includes_them_by_default() -> None:
    inp = c8_input(
        [
            fact(1, "feature", "Shops offer a wish list for saved products"),
            fact(2, "pattern", "Guest checkout reduces drop-off for new shoppers", verified=False),
            fact(3, "brand", "The site uses a blue color for buttons and links"),
            fact(4, "integration", "Payment providers publish a mobile SDK for apps " * 3),
        ]
    )
    out = fallback_draft(inp)
    assert [s.fact_ids for s in out.suggestions] == [["F-001"], ["F-004"]]
    assert all(not s.default_included and s.applies_to is None for s in out.suggestions)
    assert out.suggestions[1].title.endswith("…") and len(out.suggestions[1].title) <= 60
    assert out.brand is None and out.integrations == [] and out.gaps == []


def test_fallback_draft_is_empty_without_verified_facts() -> None:
    out = fallback_draft(
        c8_input([fact(1, "feature", "Shops offer a wish list for saved products", verified=False)])
    )
    assert out.suggestions == [] and out.compliance == []


def test_fallback_draft_points_to_a_verified_compliance_fact() -> None:
    inp = c8_input(
        [fact(1, "compliance", "Card data stays with the payment provider")],
        flags=["financial_data", "needs_expert_review"],
    )
    out = fallback_draft(inp)
    assert [(p.flag, p.url) for p in out.compliance] == [
        ("financial_data", "https://example.com/1")
    ]


def test_fallback_draft_is_capped() -> None:
    facts = [fact(i, "feature", f"Shops offer feature number {i} to people") for i in range(1, 15)]
    assert len(fallback_draft(c8_input(facts)).suggestions) == 6


def test_normalize_applies_to() -> None:
    features = {"favorites", "reviews"}
    assert normalize_applies_to(None, features) is None
    assert normalize_applies_to("", features) is None
    assert normalize_applies_to(" screens ", features) == "screens"
    assert normalize_applies_to("scope.features:reviews", features) == "scope.features:reviews"
    assert normalize_applies_to("reviews", features) == "scope.features:reviews"
    assert normalize_applies_to("idea.archetype", features) == "idea.archetype"
    assert normalize_applies_to("bogus", features) is None


def test_unavailable_note_has_the_contract_shape() -> None:
    note = unavailable_note(
        "a" * 64, "policy", "deep", created_at=NOW, queries_run=2, cost_micros=5
    )
    assert (note.status, note.unavailable_reason, note.depth) == ("unavailable", "policy", "deep")
    assert note.facts == [] and note.suggestions == []
    assert (note.queries_run, note.cost_micros, note.created_at) == (2, 5, NOW)
    assert note.hash == note.compute_hash()


def test_internal_source_refs_are_never_web_urls() -> None:
    ref = internal_source_ref("docs/PRD v2 (final).pdf", NOW.date())
    assert ref.url == "internal://docs/PRD-v2-final-.pdf" or ref.url.startswith(
        "internal://docs/PRD"
    )
    assert ref.domain == "internal"
    assert internal_source_ref("", NOW.date()).url == "internal://document"


def test_assemble_note_increments_the_version_of_a_previous_note() -> None:
    from mde_research.synthesis import NoteParts

    parts = NoteParts(suggestions=[], compliance=[], brand=None, integrations=[], gaps=[])
    first = assemble_note(
        brief_hash="b" * 64, depth="light", facts=[], parts=parts, status="ok", reason=None,
        queries_run=1, cost_micros=0, created_at=NOW, previous=None,
    )  # fmt: skip
    second = assemble_note(
        brief_hash="b" * 64, depth="light", facts=[], parts=parts, status="ok", reason=None,
        queries_run=1, cost_micros=0, created_at=NOW, previous=first,
    )  # fmt: skip
    assert (first.version, second.version) == (1, 2)


# --------------------------------------------------------------------------- config


def test_default_depth_caps_follow_the_spec() -> None:
    assert dict(DEPTH_CAPS) == {"light": 6, "standard": 12, "deep": 20}
    cfg = ResearchConfig()
    assert cfg.concurrency == 1
    assert cfg.max_suggestions == 12 and cfg.statement_max_chars == 240
    assert cfg.overhead_cost_micros == 5_500 and cfg.per_query_cost_micros == 13_000
    assert cfg.gap_fill_max_queries == 4 and cfg.stale_after_days == 14


def test_the_fake_clock_helper_is_utc() -> None:
    assert fake_clock().now().tzinfo is not None


@pytest.mark.parametrize("depth", ["light", "standard", "deep"])
def test_every_depth_has_a_cap(depth: str) -> None:
    assert ResearchConfig().depth_cap(depth) > 0  # type: ignore[arg-type]
