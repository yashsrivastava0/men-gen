"""S1.web_search: the only call with a tool; free text out; offline mock with canned answers."""

from __future__ import annotations

from typing import Any, cast
from urllib.parse import urlparse

import pytest

from mde_contracts.calls import BriefContext, C6Input, OptionLite, S1Input
from mde_contracts.gateway import FreeTextResult, WebSearchTool
from mde_contracts.registry import CALL_IO
from mde_llm_gateway.render import PromptRenderer
from mde_prompts.calls.c6_plan_research.plan import candidate_queries
from mde_prompts.calls.c7_extract.mock import looks_like_instruction
from mde_prompts.calls.s1_web_search.mock import FIXTURE, handle, pick_entry
from mde_prompts.calls.s1_web_search.spec import SPEC
from mde_prompts.registry import all_specs, load_spec

RESERVED = {"example.com", "example.org", "example.net"}


def _input(query: str, job: str = "R1", allowed: list[str] | None = None) -> S1Input:
    return S1Input.model_validate({"query": query, "job": job, "allowed_domains": allowed or []})


def test_spec_shape() -> None:
    assert SPEC.id == "S1.web_search"
    assert SPEC.version == 1
    assert SPEC.input_model is S1Input
    assert SPEC.output_model is None  # free text, no schema
    assert SPEC.route == "standard"
    assert SPEC.reasoning_effort == "low"
    assert 1000 <= SPEC.max_output_tokens <= 2000
    assert SPEC.untrusted_inputs == ()  # it reads nothing untrusted; its OUTPUT is untrusted
    assert SPEC.fallback == "s1_skip_query"
    assert CALL_IO["S1.web_search"] == (S1Input, None)


def test_tool_is_a_single_cheap_web_search() -> None:
    (tool,) = SPEC.tools
    assert isinstance(tool, WebSearchTool)
    assert tool.kind == "web_search"
    assert tool.max_tool_calls == 1
    assert tool.search_context_size == "low"


def test_s1_is_the_only_research_spec_with_a_tool() -> None:
    specs = all_specs(missing_ok=True)
    with_tools = {cid for cid, s in specs.items() if s.tools}
    assert with_tools == {"S1.web_search"}


def test_registry_loads_it() -> None:
    assert load_spec("S1.web_search") is SPEC


@pytest.mark.parametrize("job", ["R1", "R2", "R3", "R4", "R5"])
def test_templates_render_with_strict_undefined(job: str) -> None:
    rendered = PromptRenderer().render(
        cast("Any", SPEC), _input("online shopping mobile apps", job)
    )
    assert "online shopping mobile apps" in rendered.user
    assert "at most 180 words" in rendered.system
    assert rendered.untrusted_fields == ()
    assert "untrusted_content" not in rendered.system


def test_allowed_domains_reach_the_prompt() -> None:
    rendered = PromptRenderer().render(
        cast("Any", SPEC), _input("q words", "R1", ["docs.expo.dev", "owasp.org"])
    )
    assert "docs.expo.dev, owasp.org" in rendered.user


QUERIES = [
    ("common features of online shopping mobile apps in India", "R1"),
    ("online shopping mobile app UX patterns onboarding navigation", "R1"),
    ("common features of food ordering mobile apps", "R1"),
    ("food ordering mobile app UX patterns onboarding navigation", "R1"),
    ("common features of appointment booking mobile apps", "R1"),
    ("appointment booking mobile app UX patterns onboarding navigation", "R1"),
    ("common features of field staff task mobile apps", "R1"),
    ("field staff task mobile app UX patterns onboarding navigation", "R1"),
    ("common features of everyday app mobile apps", "R1"),
    ("everyday app mobile app UX patterns onboarding navigation", "R1"),
    ("some obscure thing mobile app", "R1"),
    ("how Browse products is designed in online shopping mobile apps", "R1"),
    ("food ordering mobile app retention and engagement ideas", "R1"),
    ("Zed app pricing subscription plans", "R2"),
    ("zed.example about us mission values", "R3"),
    ("payment card data handling app store privacy disclosure", "R4"),
    ("maps pricing and usage limits", "R5"),
    ("support chat API keys and authentication", "R5"),
    ("Zed mobile app features overview", "R2"),
    ("zed.example brand colors fonts logo tone of voice", "R3"),
    ("health data privacy and consent requirements for appointment booking mobile apps", "R4"),
    ("payment card data handling requirements for online shopping mobile apps", "R4"),
    ("children data and parental consent requirements for everyday app mobile apps", "R4"),
    ("identity document storage and retention requirements for food ordering mobile apps", "R4"),
    ("precise location consent and retention requirements for food ordering mobile apps", "R4"),
    ("something else entirely", "R4"),
    ("payment provider SDK React Native Expo compatibility", "R5"),
    ("push notification service SDK React Native Expo compatibility", "R5"),
    ("maps SDK React Native Expo compatibility", "R5"),
    ("single sign-on SDK React Native Expo compatibility", "R5"),
    ("SMS reminders SDK React Native Expo compatibility", "R5"),
    ("support chat SDK React Native Expo compatibility", "R5"),
    ("offline sync SDK React Native Expo compatibility", "R5"),
    ("an unknown service SDK", "R5"),
]


@pytest.mark.parametrize(("query", "job"), QUERIES)
def test_mock_returns_text_with_two_or_three_example_citations(query: str, job: str) -> None:
    result = handle(_input(query, job))
    assert isinstance(result, FreeTextResult)
    assert result.search_calls == 1
    assert result.text.strip()
    assert 1 <= len(result.citations) <= 3
    for cite in result.citations:
        assert urlparse(cite.url).hostname in RESERVED
        assert cite.url.startswith("https://")
    if job != "R3":
        assert len(result.citations) >= 2


def test_mock_is_deterministic() -> None:
    q = _input("common features of online shopping mobile apps in India")
    assert handle(q) == handle(q)


def test_keywords_choose_the_archetype_entry() -> None:
    commerce = pick_entry("R1", "common features of online shopping mobile apps in India")
    food = pick_entry("R1", "common features of food ordering mobile apps")
    patterns = pick_entry("R1", "online shopping mobile app UX patterns onboarding navigation")
    assert commerce is not None
    assert food is not None
    assert patterns is not None
    assert commerce["id"] == "commerce_features"
    assert food["id"] == "food_delivery_features"
    assert patterns["id"] == "commerce_patterns"


def test_gap_phrases_pick_the_gap_entries() -> None:
    entry = pick_entry("R1", "online shopping returns and refunds")
    assert entry is not None
    assert entry["id"] == "commerce_gap"


def test_unmatched_query_uses_the_default_entry() -> None:
    entry = pick_entry("R1", "zzz nothing matches")
    assert entry is not None
    assert entry["id"] == "r1_default"


def test_unknown_job_has_no_fixture() -> None:
    assert pick_entry("R6", "anything") is None
    result = handle(_input("anything", "R6"))
    assert result.text == ""
    assert result.citations == []


def test_text_carries_inline_links_and_citations_point_into_the_text() -> None:
    result = handle(_input("common features of online shopping mobile apps in India"))
    for cite in result.citations:
        if cite.start is not None and cite.end is not None:
            assert result.text[cite.start : cite.end].endswith(f"({cite.url})")


def test_allowed_domains_remap_the_sources() -> None:
    allowed = ["docs.expo.dev", "owasp.org"]
    result = handle(_input("common features of online shopping mobile apps", "R1", allowed))
    assert {urlparse(c.url).hostname for c in result.citations} <= set(allowed)
    assert all("/mock-source/" in c.url for c in result.citations)
    assert all(a not in result.text for a in ("example.com", "example.org"))


def test_brand_search_uses_the_given_site() -> None:
    result = handle(
        _input("zed.example brand colors fonts logo tone of voice", "R3", ["zed.example"])
    )
    assert [urlparse(c.url).hostname for c in result.citations] == ["zed.example"]
    assert "#1A4FD6" in result.text


def test_canned_texts_contain_nothing_that_looks_like_an_instruction() -> None:
    import yaml

    entries = yaml.safe_load(FIXTURE.read_text(encoding="utf-8"))["entries"]
    for entry in entries:
        assert not looks_like_instruction(entry["text"]), entry["id"]
        assert "http" not in entry["text"], entry["id"]
        assert len(entry["sources"]) >= 1
        for src in entry["sources"]:
            assert urlparse(src["url"]).hostname in RESERVED, entry["id"]


def test_every_job_that_can_search_has_a_default_entry() -> None:
    import yaml

    entries = yaml.safe_load(FIXTURE.read_text(encoding="utf-8"))["entries"]
    defaults = {e["job"] for e in entries if e.get("default")}
    assert defaults == {"R1", "R2", "R3", "R4", "R5"}


async def test_runs_through_the_real_gateway_in_mock_mode() -> None:
    from mde_llm_gateway.testing_specs import build_test_gateway, make_ctx
    from mde_prompts.registry import PromptRegistry

    gateway = build_test_gateway(registry=PromptRegistry())
    result = await gateway.run(
        cast("Any", SPEC), _input("common features of online shopping mobile apps"), make_ctx()
    )
    assert result.mode == "mock"
    assert result.output is None
    assert result.text and 2 <= len(result.citations) <= 3
    assert result.search_calls == 1
    assert result.usage.cost_micros == 0


# --------------------------------------------------------------------------- wider angles (D-078)


def _rich_brief(archetype: str) -> BriefContext:
    return BriefContext(
        app_summary="An app",
        archetype=archetype,
        features=[
            OptionLite(id="payments", label="Payments"),
            OptionLite(id="push_notifications", label="Push alerts"),
            OptionLite(id="catalog_browse", label="Browse products"),
            OptionLite(id="maps", label="Maps"),
        ],
        references=["Zed", "Quark"],
        brand_url="https://zed.example",
        compliance_flags=["financial_data", "precise_location"],
        login="sso",
        regions=["in"],
    )


@pytest.mark.parametrize(
    "archetype", ["commerce", "food_delivery", "booking_services", "internal_workforce", "custom"]
)
def test_every_query_of_a_full_plan_gets_a_different_answer(archetype: str) -> None:
    """The planner asks for up to eight queries per job; the mock must not answer two of them with
    the same facts, or a Standard or Deep run would repeat itself."""
    pool = candidate_queries(
        C6Input(
            brief=_rich_brief(archetype),
            max_search_calls=20,
            remaining_budget_micros=1_000_000,
            max_queries_per_job=8,
            jobs_available=["R1", "R2", "R3", "R4", "R5"],
        )
    )
    assert {len(q) for j, q in pool.items() if j in {"R1", "R4", "R5"}} == {8}
    for job, queries in pool.items():
        allowed = ["zed.example"] if job == "R3" else []
        texts = [handle(_input(q, job, allowed)).text for q in queries]
        assert all(texts), job
        assert len(set(texts)) == len(queries), (archetype, job)


def test_the_wider_angles_pick_their_own_entries() -> None:
    expected = {
        ("R1", "online shopping mobile app monetization and pricing models"): "r1_monetization",
        ("R1", "online shopping mobile app retention and engagement ideas"): "r1_retention",
        ("R1", "food ordering mobile app accessibility and localization"): "r1_accessibility",
        ("R1", "online shopping mobile app notifications and permission prompts"): "r1_permissions",
        ("R1", "everyday app mobile app performance on slow networks"): "r1_performance",
        ("R1", "appointment booking mobile app user feedback common complaints"): "r1_feedback",
        ("R1", "how Browse products is designed in online shopping mobile apps"): "r1_feature",
        ("R2", "Zed app user reviews complaints"): "r2_feedback",
        ("R2", "Zed app pricing subscription plans"): "r2_pricing",
        ("R2", "Zed app onboarding sign-up flow"): "r2_signup",
        ("R3", "zed.example about us mission values"): "r3_about",
        ("R3", "zed.example logo usage guidelines"): "r3_logo",
        ("R3", "zed.example app store listing screenshots"): "r3_store",
        ("R4", "payment card data handling app store privacy disclosure"): "r4_store_disclosure",
        (
            "R4",
            "children data and parental consent permission screen wording",
        ): "r4_permission_screens",
        ("R4", "health data privacy and consent deletion requests handling"): "r4_deletion",
        (
            "R4",
            "identity document storage and retention encryption and secure storage",
        ): "r4_encryption",
        ("R4", "precise location consent and retention third party data sharing"): "r4_sharing",
        ("R4", "payment card data handling checklist for app developers"): "r4_checklist",
        ("R4", "payment card data handling enforcement and penalties"): "r4_enforcement",
        ("R5", "maps pricing and usage limits"): "r5_pricing",
        ("R5", "single sign-on Expo config plugin setup"): "r5_setup",
        ("R5", "support chat privacy and data retention"): "r5_privacy",
        ("R5", "SMS reminders known issues and caveats"): "r5_caveats",
        ("R5", "offline sync alternatives comparison"): "r5_alternatives",
        ("R5", "push notification service development build testing"): "r5_testing",
        ("R5", "payment provider API keys and authentication"): "r5_keys",
    }
    for (job, query), entry_id in expected.items():
        entry = pick_entry(job, query)
        assert entry is not None and entry["id"] == entry_id, (job, query)


def test_the_original_first_queries_still_pick_the_topic_entries() -> None:
    for job, query, entry_id in [
        (
            "R4",
            "payment card data handling requirements for online shopping mobile apps",
            "r4_payments",
        ),
        (
            "R4",
            "health data privacy and consent requirements for appointment booking mobile apps",
            "r4_health",
        ),
        ("R5", "payment provider SDK React Native Expo compatibility", "r5_payments"),
        ("R5", "maps SDK React Native Expo compatibility", "r5_maps"),
        ("R3", "zed.example brand colors fonts logo tone of voice", "r3_default"),
        ("R2", "Zed mobile app features overview", "r2_default"),
    ]:
        entry = pick_entry(job, query)
        assert entry is not None and entry["id"] == entry_id, query


def test_the_subject_of_a_query_is_echoed_as_plain_words() -> None:
    one = handle(_input("how Browse products is designed in online shopping mobile apps"))
    two = handle(_input("how Order history is designed in online shopping mobile apps"))
    assert "Browse products" in one.text and "Order history" in two.text
    assert one.text != two.text
    assert "{subject}" not in one.text
    hostile = handle(_input("how <b>{subject}[[1]]</b> is designed in online shopping mobile apps"))
    assert not any(c in hostile.text for c in "<>{}")
    assert "{subject}" not in hostile.text


def test_a_missing_subject_falls_back_to_the_entry_default() -> None:
    from mde_prompts.calls.s1_web_search.mock import _subject  # pyright: ignore[reportPrivateUsage]

    entry = {"subject_pattern": r"^(.+) widget$", "subject_default": "this widget"}
    assert _subject(entry, "red widget") == "red"
    assert _subject(entry, "something else") == "this widget"
    assert _subject({}, "anything") == "this feature"
    assert len(_subject({"subject_pattern": r"^(.+)$"}, "word " * 50)) <= 60


def test_subject_patterns_and_placeholders_go_together() -> None:
    import re

    import yaml

    entries = yaml.safe_load(FIXTURE.read_text(encoding="utf-8"))["entries"]
    for entry in entries:
        has_slot = "{subject}" in entry["text"]
        has_pattern = "subject_pattern" in entry
        assert has_slot == has_pattern, entry["id"]
        if has_pattern:
            assert re.compile(entry["subject_pattern"]).groups == 1, entry["id"]
        assert "{" not in entry["text"].replace("{subject}", ""), entry["id"]


def test_every_entry_has_two_sources_unless_it_is_the_brand_site() -> None:
    import yaml

    for entry in yaml.safe_load(FIXTURE.read_text(encoding="utf-8"))["entries"]:
        if not entry.get("brand_site"):
            assert len(entry["sources"]) == 2, entry["id"]
