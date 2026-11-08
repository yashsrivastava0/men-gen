"""C8.synthesize_research: Research Note drafts; canned, schema-valid draft per archetype."""

from __future__ import annotations

from typing import Any, cast

import pytest
import yaml

from mde_content.packs import load_pack
from mde_content.themes import theme_ids
from mde_contracts.calls import (
    BriefContext,
    C8Input,
    C8Output,
    FactForSynthesis,
    OptionLite,
)
from mde_contracts.registry import CALL_IO
from mde_llm_gateway.render import PromptRenderer
from mde_llm_gateway.strict_schema import to_strict_schema
from mde_prompts.calls.c8_synthesize_research.mock import DRAFTS, handle
from mde_prompts.calls.c8_synthesize_research.spec import SPEC
from mde_prompts.registry import load_spec

ARCHETYPES = ["commerce", "food_delivery", "booking_services", "internal_workforce", "custom"]
SECTIONS = {"design", "screens", "navigation", "data", "api", "roles", "integrations", "behavior"}


def fact(
    n: int,
    statement: str,
    *,
    kind: str = "feature",
    job: str = "R1",
    impact: str = "low",
    verified: bool = True,
) -> FactForSynthesis:
    return FactForSynthesis.model_validate(
        {
            "id": f"F-{n:03d}",
            "job": job,
            "kind": kind,
            "statement": statement,
            "source_urls": [f"https://example.com/p{n}"],
            "impact": impact,
            "verified": verified,
        }
    )


def c8(
    facts: list[FactForSynthesis],
    archetype: str = "commerce",
    *,
    depth: str = "light",
    gap_round: bool = False,
    features: list[str] | None = None,
    flags: list[str] | None = None,
    brand_url: str | None = None,
    max_suggestions: int = 12,
) -> C8Input:
    return C8Input.model_validate(
        {
            "brief": BriefContext(
                app_summary="An app",
                archetype=archetype,
                features=[OptionLite(id=f, label=f) for f in (features or [])],
                compliance_flags=flags or [],
                brand_url=brand_url,
            ),
            "facts": facts,
            "depth": depth,
            "gap_round": gap_round,
            "max_suggestions": max_suggestions,
        }
    )


def test_spec_shape() -> None:
    assert SPEC.id == "C8.synthesize_research"
    assert SPEC.version == 1
    assert SPEC.output_model is C8Output
    assert SPEC.route == "standard"
    assert SPEC.reasoning_effort == "medium"
    assert 2000 <= SPEC.max_output_tokens <= 5000  # live L2: 2184 used at 1378 reasoning (D-082)
    assert SPEC.tools == ()
    assert SPEC.untrusted_inputs == ("facts",)  # facts come from web text: second-order defence
    assert SPEC.fallback == "c8_deterministic_suggestions"
    assert CALL_IO["C8.synthesize_research"] == (C8Input, C8Output)
    assert load_spec("C8.synthesize_research") is SPEC


def test_output_model_converts_to_strict_schema() -> None:
    schema = to_strict_schema(C8Output)
    assert schema["additionalProperties"] is False


def test_templates_render_with_facts_in_untrusted_blocks() -> None:
    inp = c8(
        [fact(1, "Ratings and reviews build trust")],
        features=["catalog_browse"],
        flags=["financial_data"],
        brand_url="https://zed.example",
    )
    rendered = PromptRenderer().render(cast("Any", SPEC), inp)
    assert rendered.untrusted_fields == ("facts",)
    assert rendered.user.count("<untrusted_content") == 1
    assert '"id":"F-001"' in rendered.user.replace(" ", "")
    assert "Compliance flags: financial_data" in rendered.user
    assert "Brand website: https://zed.example" in rendered.user
    assert "Use only the facts provided" in rendered.system
    assert "never follow" in rendered.system.lower()


def test_template_renders_with_no_facts() -> None:
    rendered = PromptRenderer().render(cast("Any", SPEC), c8([]))
    assert "(none)" in rendered.user


def test_empty_facts_give_an_empty_valid_draft() -> None:
    out = handle(c8([]))
    assert out == C8Output(suggestions=[], compliance=[], brand=None, integrations=[], gaps=[])


def test_drafts_file_covers_every_archetype() -> None:
    data = yaml.safe_load(DRAFTS.read_text(encoding="utf-8"))
    assert set(data["archetypes"]) == set(ARCHETYPES)


@pytest.mark.parametrize("archetype", ARCHETYPES)
def test_feature_ids_exist_in_the_matching_domain_pack(archetype: str) -> None:
    data = yaml.safe_load(DRAFTS.read_text(encoding="utf-8"))
    pack_features = {f.id for f in load_pack(archetype).features}
    for item in data["archetypes"][archetype]["suggestions"]:
        if item["kind"] == "feature":
            assert item["feature_id"] in pack_features, item["title"]
            assert item["applies_to"] == f"scope.features:{item['feature_id']}"
        else:
            assert item["applies_to"] in SECTIONS or "." in item["applies_to"], item["title"]
        assert len(item["title"]) <= 60
        assert len(item["rationale"]) <= 200


def test_tone_directions_point_at_real_themes() -> None:
    data = yaml.safe_load(DRAFTS.read_text(encoding="utf-8"))
    assert set(data["tone_directions"].values()) <= set(theme_ids())


@pytest.mark.parametrize("archetype", ARCHETYPES)
def test_every_archetype_draft_is_valid_and_cites_real_fact_ids(archetype: str) -> None:
    data = yaml.safe_load(DRAFTS.read_text(encoding="utf-8"))
    facts: list[FactForSynthesis] = []
    for number, item in enumerate(data["archetypes"][archetype]["suggestions"], start=1):
        facts.append(fact(number, f"Similar apps use {item['match'][0]} widely"))
    out = handle(c8(facts, archetype))
    assert C8Output.model_validate(out.model_dump()) == out
    assert out.suggestions, archetype
    known = {f.id for f in facts}
    for suggestion in out.suggestions:
        assert suggestion.fact_ids
        assert set(suggestion.fact_ids) <= known
        assert suggestion.applies_to
        assert suggestion.applies_to.startswith(
            "scope.features:"
        ) or suggestion.applies_to in SECTIONS | {"auth.sso"}


def test_suggestions_need_a_supporting_fact() -> None:
    out = handle(c8([fact(1, "Ratings and reviews build trust")]))
    assert [s.title for s in out.suggestions] == ["Product ratings and reviews"]
    assert out.suggestions[0].fact_ids == ["F-001"]
    assert out.suggestions[0].applies_to == "scope.features:reviews"


def test_features_the_brief_already_has_are_skipped() -> None:
    out = handle(c8([fact(1, "Ratings and reviews build trust")], features=["reviews"]))
    assert out.suggestions == []


def test_max_suggestions_is_respected() -> None:
    facts = [
        fact(1, "Ratings and reviews, favorites and a wish list, guest checkout, status timeline")
    ]
    assert len(handle(c8(facts, max_suggestions=2)).suggestions) == 2


def test_common_suggestions_apply_to_every_archetype() -> None:
    facts = [fact(1, "Apps need a development build for native code", kind="integration", job="R5")]
    for archetype in ARCHETYPES:
        titles = [s.title for s in handle(c8(facts, archetype)).suggestions]
        assert "Plan for a development build" in titles


def test_compliance_pointers_follow_the_brief_flags_and_use_a_fact_url() -> None:
    facts = [
        fact(1, "Card data should be handled by the payment provider", kind="compliance", job="R4"),
        fact(2, "Health data needs consent", kind="compliance", job="R4"),
    ]
    out = handle(c8(facts, flags=["health_data_privacy", "financial_data", "needs_expert_review"]))
    pointers = {p.flag: p for p in out.compliance}
    assert set(pointers) == {"health_data_privacy", "financial_data"}
    assert pointers["health_data_privacy"].url == "https://example.com/p2"
    assert pointers["financial_data"].url == "https://example.com/p1"


def test_no_pointer_without_a_compliance_fact() -> None:
    assert handle(c8([fact(1, "Search is common")], flags=["financial_data"])).compliance == []


def test_brand_finding_from_brand_facts() -> None:
    facts = [
        fact(
            1,
            "Brand colors observed: #1a4fd6 and #FFFFFF; fonts observed: Inter and Poppins",
            kind="brand",
            job="R3",
        ),
        fact(
            2,
            "Tone of voice is friendly and direct, and a logo file is available on the site",
            kind="brand",
            job="R3",
        ),
    ]
    out = handle(c8(facts, brand_url="https://zed.example"))
    brand = out.brand
    assert brand is not None
    assert brand.source_url == "https://example.com/p1"
    assert brand.colors == ["#1A4FD6", "#FFFFFF"]
    assert brand.fonts == ["Inter", "Poppins"]
    assert brand.tone_words == ["friendly", "direct"]
    assert brand.logo_available is True
    assert brand.nearest_direction == "warm-friendly"


def test_no_brand_without_brand_facts() -> None:
    assert handle(c8([fact(1, "Search is common")])).brand is None


def test_integration_notes_from_integration_facts() -> None:
    facts = [
        fact(1, "Most payment providers publish a React Native SDK", kind="integration", job="R5"),
        fact(
            2,
            "Expo managed apps need a development build for native payment code",
            kind="integration",
            job="R5",
        ),
        fact(
            3,
            "Single sign-on uses OpenID Connect through an identity provider",
            kind="integration",
            job="R5",
        ),
    ]
    out = handle(c8(facts))
    by_provider = {i.provider: i for i in out.integrations}
    assert set(by_provider) == {"Payment provider", "Single sign-on"}
    pay = by_provider["Payment provider"]
    assert pay.sdk_available is True
    assert pay.expo_compatible is True
    assert any("development build" in c for c in pay.caveats)
    assert pay.source_url == "https://example.com/p1"


def test_gaps_only_on_the_first_round_of_a_deep_run() -> None:
    facts = [fact(1, "Search is common")]
    assert handle(c8(facts, depth="deep")).gaps
    assert handle(c8(facts, depth="deep", gap_round=True)).gaps == []
    assert handle(c8(facts, depth="light")).gaps == []
    assert handle(c8(facts, depth="standard")).gaps == []


@pytest.mark.parametrize("archetype", ARCHETYPES)
def test_gap_phrases_name_the_archetype_topic(archetype: str) -> None:
    gap = handle(c8([fact(1, "Search is common")], archetype, depth="deep")).gaps[0]
    assert len(gap.split()) >= 3


def test_unknown_archetype_falls_back_to_the_custom_block() -> None:
    out = handle(c8([fact(1, "A search box is part of the usual list layout")], "pet_care"))
    assert [s.title for s in out.suggestions] == ["Search box"]


def test_mock_is_deterministic() -> None:
    inp = c8(
        [fact(1, "Ratings and reviews build trust"), fact(2, "Guest checkout reduces drop-off")]
    )
    assert handle(inp) == handle(inp)


async def test_runs_through_the_real_gateway_in_mock_mode() -> None:
    from mde_llm_gateway.testing_specs import build_test_gateway, make_ctx
    from mde_prompts.registry import PromptRegistry

    gateway = build_test_gateway(registry=PromptRegistry())
    inp = c8([fact(1, "Ratings and reviews build trust")])
    result = await gateway.run(cast("Any", SPEC), inp, make_ctx())
    assert result.mode == "mock"
    assert isinstance(result.output, C8Output)
    assert [s.title for s in result.output.suggestions] == ["Product ratings and reviews"]
