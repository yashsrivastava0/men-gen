"""``CompileAdapter``: targets from the catalog, redaction, value validation, completion of coverage, the floor."""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from mde_compass.calls.base import CallRunner
from mde_compass.calls.compile_brief.adapter import (
    CompileAdapter,
    build_input,
    select_targets,
    to_compilation,
)
from mde_compass.calls.compile_brief.ledger import ledger_ids
from mde_compass.calls.compile_brief.schema import (
    CompileInput,
    CompileOutput,
    ItemWire,
    NotUsedWire,
    PlacementWire,
)
from mde_compass.calls.compile_brief.spec import SPEC
from mde_compass.calls.testing import ScriptedGateway, make_client
from mde_compass.catalog.loader import load_catalog
from mde_compass.domain.interview import FACT_TYPES, Fact
from mde_compass.engine.context_pack import full_ledger
from mde_compass.engine.facts import fact_coverage
from mde_compass.engine.ports import BriefCompilation, CompileRequest, TurnContext
from mde_core.errors import ProviderError
from mde_llm_gateway.render import PromptRenderer

EXAMPLES_DIR = Path(__file__).parents[3] / "src/mde_compass/calls/compile_brief/examples"
EXAMPLES = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(EXAMPLES_DIR.glob("*.json"))]
CATALOG = load_catalog()
CTX = TurnContext(session_id="ses_1", tenant_id="t1")
KEY = "AKIAIOSFODNN7EXAMPLE"  # secret-scan: allow (a published fake key)

LEDGER = "\n".join(
    [
        "# pricing",
        "FCT-002 [rule|pricing] Delivery costs 20 rupees (from Q-03) | conf 0.90 | stated | consumers: backend,qa",
        "# general",
        "FCT-001 [context] A tiffin service in Pune (from evt_001) | conf 0.90 | stated",
        "FCT-003 [number|menu,staff] Two cooks (from evt_003) | consumers: nonsense",
        "FCT-004 [preference|look] Cheerful colours (from Q-07) | consumers: design",
    ]
)


def req(ledger: str = LEDGER, **changes: object) -> CompileRequest:
    base: dict[str, object] = {"language": "en", "ledger": ledger}
    base.update(changes)
    return CompileRequest.model_validate(base)


def item(path: str, value: object, ids: list[str], conf: float = 0.8) -> ItemWire:
    return ItemWire(path=path, value_json=json.dumps(value), fact_ids=ids, confidence=conf)


def out(**changes: object) -> CompileOutput:
    base: dict[str, object] = {
        "items": [],
        "placements": [],
        "not_used": [],
        "summary": "",
        "pitch": "",
    }
    base.update(changes)
    return CompileOutput.model_validate(base)


def conv(output: CompileOutput, ledger: str = LEDGER) -> tuple[BriefCompilation, tuple[str, ...]]:
    return to_compilation(CATALOG, build_input(CATALOG, req(ledger)), output)


def example_request(raw: dict[str, object]) -> CompileRequest:
    inp = CompileInput.model_validate(raw["input"])
    return CompileRequest(
        language=inp.language,
        ledger="\n".join(inp.ledger),
        needs_table="\n".join(inp.needs_table),
        assumptions=list(inp.assumptions),
        draft="\n".join(inp.draft) or None,
    )


# ------------------------------------------------------------------------------------------- targets
def test_targets_come_from_the_catalog_and_skip_generated_and_org_slots() -> None:
    wire = build_input(CATALOG, req())
    paths = [t.path for t in wire.targets]
    assert paths == [
        s
        for s in CATALOG.slot_ids_by_order()
        if CATALOG.slots[s].level != "generated" and CATALOG.slots[s].group != "org"
    ]
    assert "unknowns.research_wanted" not in paths and not any(p.startswith("org.") for p in paths)
    by_path = {t.path: t for t in wire.targets}
    assert by_path["devices.profile"].options == ["phones", "phones_tablets", "low_end"]
    assert by_path["roles.list"].fields == ["name", "goal", "permissions"]
    assert by_path["context.geo"].fields == ["country", "city", "region"]
    assert by_path["locales"].options == [] and by_path["locales"].fields == []  # any language code
    assert by_path["idea.summary"].options == [] and by_path["idea.summary"].value_kind == "text"
    assert {n.need_id for n in wire.needs} <= set(paths) and all(n.sufficient for n in wire.needs)


def test_enterprise_mode_adds_the_org_slots() -> None:
    guided = {t.path for t in build_input(CATALOG, req(), mode="guided").targets}
    company = {t.path for t in build_input(CATALOG, req(), mode="enterprise").targets}
    assert company - guided == {s for s in CATALOG.slots if s.startswith("org.")}


def test_per_section_mode_restricts_the_targets() -> None:
    audience = build_input(CATALOG, req(sections=["audience"]))
    assert [t.path for t in audience.targets] == [
        "audience.segments",
        "audience.traits",
        "audience.usage_context",
    ]
    assert audience.sections == ["audience"]
    assert {n.need_id for n in audience.needs} == {t.path for t in audience.targets}
    assert [t.path for t in build_input(CATALOG, req(sections=["roles", " "])).targets] == [
        "roles.list"
    ]
    by_group = {t.path for t in build_input(CATALOG, req(sections=["auth"])).targets}
    assert by_group == {"auth.methods", "auth.account_model", "onboarding.approvals"}
    both = build_input(CATALOG, req(sections=["roles", "idea.summary"]))
    assert [t.path for t in both.targets] == ["idea.summary", "roles.list"]
    assert build_input(CATALOG, req(sections=["domain_facts"])).targets == []
    assert select_targets(CATALOG, [], "guided") == select_targets(CATALOG, [" "], "guided")


def test_the_rendered_prompt_says_which_sections_and_lists_only_their_targets() -> None:
    wire = build_input(CATALOG, req(sections=["roles"]))
    user = PromptRenderer().render(SPEC.spec, wire).user
    assert "Per-section compile. Sections: roles" in user
    assert "- roles.list | Roles | object_list | keys: name, goal, permissions" in user
    assert "- idea.summary" not in user
    whole = PromptRenderer().render(SPEC.spec, build_input(CATALOG, req())).user
    assert (
        "Whole Brief" in whole and "Per-section" not in whole and "Draft to reconcile" not in whole
    )


async def test_an_item_outside_the_requested_sections_is_refused() -> None:
    wrong = out(items=[item("idea.summary", "x", ["FCT-001"])])
    request = req(sections=["roles"])
    result = await CompileAdapter(CallRunner(ScriptedGateway(wrong)), CATALOG).compile_brief(
        CTX, request
    )
    assert result.used_fallback and result.items == []


async def test_the_draft_goes_to_the_model_for_a_consistency_pass() -> None:
    gateway = ScriptedGateway(out())
    request = req(draft="idea.summary = old text FCT-001")
    await CompileAdapter(CallRunner(gateway), CATALOG).compile_brief(CTX, request)
    wire = gateway.calls[0][1]
    assert isinstance(wire, CompileInput) and wire.draft == ["idea.summary = old text FCT-001"]
    assert "Draft to reconcile" in PromptRenderer().render(SPEC.spec, wire).user


# ----------------------------------------------------------------------------------------- redaction
def test_secrets_are_redacted_from_everything_that_carries_the_users_words() -> None:
    request = req(
        f"FCT-001 [context] my key is {KEY} (from evt_1)\n{LEDGER}",
        needs_table=f"table {KEY}",
        assumptions=[f"assume {KEY}", "  "],
        asked_list=f"asked {KEY}",
        qa_compact=f"answer {KEY}",
        draft=f"draft {KEY}",
    )
    wire = build_input(CATALOG, request)
    assert KEY not in wire.model_dump_json() and "[REDACTED]" in wire.model_dump_json()
    assert wire.assumptions == ["assume [REDACTED]"]
    prompt = PromptRenderer().render(SPEC.spec, wire)
    assert KEY not in prompt.user and KEY not in prompt.system


# ------------------------------------------------------------------------------------- the happy path
@pytest.mark.parametrize("raw", EXAMPLES, ids=lambda e: e["name"])
async def test_the_examples_compile_with_full_coverage_and_nothing_dropped(
    raw: dict[str, object],
) -> None:
    expected = CompileOutput.model_validate(raw["output"])
    request = example_request(raw)
    result = await CompileAdapter(CallRunner(ScriptedGateway(expected)), CATALOG).compile_brief(
        CTX, request
    )
    assert not result.used_fallback
    assert [i.path for i in result.items] == [i.path for i in expected.items]  # all valid
    assert [i.value for i in result.items] == [json.loads(i.value_json) for i in expected.items]
    ids = ledger_ids(request.ledger)
    cited = {f for i in result.items for f in i.fact_ids}
    placed = {p.fact_id for p in result.placements}
    dropped = {n.fact_id for n in result.not_used}
    assert cited | placed | dropped == set(ids)
    assert not placed & dropped
    assert result.summary == expected.summary and result.pitch == expected.pitch


@pytest.mark.parametrize("raw", EXAMPLES, ids=lambda e: e["name"])
def test_examples_have_no_leftovers_for_the_adapter_to_complete(raw: dict[str, object]) -> None:
    request = example_request(raw)
    compilation, leftovers = to_compilation(
        CATALOG, build_input(CATALOG, request), CompileOutput.model_validate(raw["output"])
    )
    assert leftovers == () and compilation.items


# ------------------------------------------------------------------------------- values are checked
def test_values_that_do_not_fit_their_slot_are_dropped_not_fatal() -> None:
    bad = out(
        items=[
            item("idea.summary", "A tiffin service in Pune", ["FCT-001"]),
            item("devices.profile", "tablet_only", ["FCT-002"]),  # not an option
            item("scope.must_not", [], ["FCT-002"]),  # empty list
            item("scope.references", "just text", ["FCT-002"]),  # not a list of objects
            item("roles.list", [{"nope": "x"}], ["FCT-003"]),  # unknown field
            item("idea.category", "", ["FCT-003"]),  # empty text
            item("idea.subcategory", 42, ["FCT-003"]),  # not text
            item("context.geo", {"city": 5}, ["FCT-003"]),  # geo parts must be text
            item("locales", "english", ["FCT-003"]),  # not a list of codes
            item("idea.problem_value", "x" * 4500, ["FCT-003"]),  # too long
            item("goals.primary", "save_time", ["FCT-404"]),  # evidence is not in the ledger
            ItemWire(path="nowhere.nothing", value_json='"x"', fact_ids=["FCT-001"], confidence=1),
            ItemWire(
                path="idea.differentiator", value_json="{", fact_ids=["FCT-001"], confidence=1
            ),
            ItemWire(path="idea.differentiator", value_json='"ok"', fact_ids=[], confidence=1),
            item(
                "unknowns.research_wanted", ["x"], ["FCT-001"]
            ),  # a generated slot is not a target
        ]
    )
    compilation, leftovers = conv(bad)
    assert [i.path for i in compilation.items] == ["idea.summary"]
    assert leftovers == ("FCT-002", "FCT-003", "FCT-004")  # their items died: placed, not lost
    placed = {p.fact_id for p in compilation.placements}
    assert placed == {"FCT-002", "FCT-003", "FCT-004"}


def test_valid_values_are_normalised_and_clamped() -> None:
    compilation, _ = conv(
        out(
            items=[
                item("idea.summary", "  A tiffin service  ", ["FCT-001"], 1.7),
                item("scope.must_not", ["  online payment "], ["FCT-002"], -2),
                item("goals.primary", "save_time", ["FCT-003"], math.nan),
            ]
        )
    )
    by = {i.path: i for i in compilation.items}
    assert by["idea.summary"].value == "A tiffin service" and by["idea.summary"].confidence == 1.0
    assert (
        by["scope.must_not"].value == ["online payment"] and by["scope.must_not"].confidence == 0.0
    )
    assert by["goals.primary"].confidence == 0.5


def test_one_item_per_path_the_newest_evidence_wins() -> None:
    compilation, _ = conv(
        out(
            items=[
                item("idea.summary", "old reading", ["FCT-001"]),
                item("idea.summary", "new reading", ["FCT-001", "FCT-003"]),
                item("idea.summary", "older again", ["FCT-002"]),
            ]
        )
    )
    assert [(i.path, i.value) for i in compilation.items] == [("idea.summary", "new reading")]


def test_unknown_fact_ids_are_dropped_everywhere() -> None:
    compilation, _ = conv(
        out(
            items=[
                item("idea.summary", "x", ["FCT-001", "FCT-404", "FCT-001"]),
                item("idea.category", "y", ["FCT-405"]),
            ],
            placements=[
                PlacementWire(fact_id="FCT-406", topic="ghost", consumers=["all"]),
                PlacementWire(fact_id="FCT-002", topic="pricing", consumers=["qa"]),
            ],
            not_used=[NotUsedWire(fact_id="FCT-407", reason="ghost")],
        )
    )
    assert [(i.path, i.fact_ids) for i in compilation.items] == [("idea.summary", ["FCT-001"])]
    everything = {p.fact_id for p in compilation.placements} | {
        n.fact_id for n in compilation.not_used
    }
    assert everything == {"FCT-002", "FCT-003", "FCT-004"}


# ------------------------------------------------------------------------- coverage is completed
def test_facts_the_model_forgot_are_placed_with_their_tag_topic_and_line_consumers() -> None:
    compilation, leftovers = conv(out(items=[item("idea.summary", "x", ["FCT-001"])]))
    assert leftovers == ("FCT-002", "FCT-003", "FCT-004")
    placed = {p.fact_id: p for p in compilation.placements}
    assert (placed["FCT-002"].topic, placed["FCT-002"].consumers) == ("pricing", ["backend", "qa"])
    assert (placed["FCT-003"].topic, placed["FCT-003"].consumers) == (
        "menu",
        ["all"],
    )  # no valid ones
    assert (placed["FCT-004"].topic, placed["FCT-004"].consumers) == ("look", ["design"])
    assert "FCT-001" not in placed


def test_an_empty_answer_still_accounts_for_every_fact() -> None:
    compilation, leftovers = conv(out())
    assert set(leftovers) == {"FCT-001", "FCT-002", "FCT-003", "FCT-004"}
    assert [p.fact_id for p in compilation.placements] == [
        "FCT-002",
        "FCT-001",
        "FCT-003",
        "FCT-004",
    ]
    assert compilation.items == [] and compilation.not_used == []


def test_placements_are_merged_and_get_topic_and_consumers_filled_in() -> None:
    compilation, leftovers = conv(
        out(
            placements=[
                PlacementWire(fact_id="FCT-002", topic="", consumers=[]),
                PlacementWire(fact_id="FCT-002", topic="delivery_fee", consumers=["qa", "qa"]),
                PlacementWire(fact_id="FCT-003", topic=" staff ", consumers=["qa", "all"]),
            ]
        )
    )
    placed = {p.fact_id: p for p in compilation.placements}
    assert (placed["FCT-002"].topic, placed["FCT-002"].consumers) == ("delivery_fee", ["qa"])
    assert (placed["FCT-003"].topic, placed["FCT-003"].consumers) == ("staff", ["all"])
    assert [p.fact_id for p in compilation.placements].count("FCT-002") == 1
    assert set(leftovers) == {"FCT-001", "FCT-004"}


def test_not_used_needs_a_reason_and_never_hides_a_fact_that_is_used_or_placed() -> None:
    compilation, leftovers = conv(
        out(
            items=[item("idea.summary", "x", ["FCT-001"])],
            placements=[PlacementWire(fact_id="FCT-002", topic="p", consumers=["qa"])],
            not_used=[
                NotUsedWire(fact_id="FCT-001", reason="small talk"),  # it is used: the item wins
                NotUsedWire(
                    fact_id="FCT-002", reason="small talk"
                ),  # it is placed: the placement wins
                NotUsedWire(fact_id="FCT-003", reason="  "),  # no reason: not accepted, so placed
                NotUsedWire(fact_id="FCT-004", reason="  a   greeting\nonly "),
            ],
        )
    )
    assert [(n.fact_id, n.reason) for n in compilation.not_used] == [("FCT-004", "a greeting only")]
    assert {p.fact_id for p in compilation.placements} == {"FCT-002", "FCT-003"}
    assert leftovers == ("FCT-003",)


def test_summary_is_one_line_and_pitch_is_trimmed() -> None:
    compilation, _ = conv(
        out(summary="  A tiffin\n service  in   Pune ", pitch="\n  Two. Three.  \n")
    )
    assert compilation.summary == "A tiffin service in Pune" and compilation.pitch == "Two. Three."


# ------------------------------------------------------------------- the flagged floor, no call spent
async def test_a_provider_error_returns_the_floor_with_every_fact_placed() -> None:
    gateway = ScriptedGateway(ProviderError("boom"))
    result = await CompileAdapter(CallRunner(gateway), CATALOG).compile_brief(CTX, req())
    assert result.used_fallback and result.items == [] and result.not_used == []
    assert [(p.fact_id, p.topic, p.consumers) for p in result.placements] == [
        ("FCT-002", "pricing", ["all"]),
        ("FCT-001", "general", ["all"]),
        ("FCT-003", "menu", ["all"]),
        ("FCT-004", "look", ["all"]),
    ]
    assert result.summary == "Delivery costs 20 rupees" and result.pitch == ""


async def test_a_structurally_bad_answer_returns_the_floor_too() -> None:
    bad = out(items=[item("idea.summary", "x", ["FCT-404"])])
    result = await CompileAdapter(CallRunner(ScriptedGateway(bad)), CATALOG).compile_brief(
        CTX, req()
    )
    assert result.used_fallback and len(result.placements) == 4 and result.items == []


async def test_the_real_mock_pipeline_gives_the_floor_without_being_flagged() -> None:
    compiler = CompileAdapter(CallRunner(make_client([SPEC])), CATALOG)
    result = await compiler.compile_brief(CTX, req())
    assert not result.used_fallback and result.items == []
    assert [p.fact_id for p in result.placements] == ["FCT-002", "FCT-001", "FCT-003", "FCT-004"]


async def test_an_empty_ledger_spends_no_call() -> None:
    gateway = ScriptedGateway()
    result = await CompileAdapter(CallRunner(gateway), CATALOG).compile_brief(CTX, req(""))
    assert result == BriefCompilation() and gateway.calls == []
    for text in ("  \n ", "# only a header\nno fact line here"):
        blank = await CompileAdapter(CallRunner(gateway), CATALOG).compile_brief(CTX, req(text))
        assert blank == BriefCompilation() and gateway.calls == []


# ------------------------------------------------------------------------------- nothing is silently lost
def _big_facts(n: int) -> list[Fact]:
    facts = [
        Fact(
            id="FCT-001",
            type="rule",
            statement="Orders for the next day close at 9 pm sharp, no exceptions for festivals",
            topics=["cutoff"],
            quote="the cut-off is nine pm sharp and it never moves, even on festival days",
        )
    ]
    for i in range(2, n + 1):
        facts.append(
            Fact(
                id=f"FCT-{i:03d}",
                type=FACT_TYPES[i % len(FACT_TYPES)],  # type: ignore[arg-type]
                statement=f"Detail number {i} about the {('menu', 'staff', 'zone', 'fees')[i % 4]}",
                topics=[("menu", "staff", "zone", "fees")[i % 4]],
                quote="what the person said about this detail, in their own words " * 3,
            )
        )
    return facts


async def test_the_needle_a_fact_stated_early_in_a_300_line_ledger_is_still_referenced() -> None:
    facts = _big_facts(300)
    ledger = full_ledger(facts)
    assert len(ledger) > 60_000 and "FCT-300" in ledger
    # the model cites only the last fact and forgets everything else, including the needle
    forgetful = out(items=[item("idea.summary", "A tiffin service", ["FCT-300"])])
    gateway = ScriptedGateway(forgetful)
    result = await CompileAdapter(CallRunner(gateway), CATALOG).compile_brief(CTX, req(ledger))
    assert not result.used_fallback
    referenced = {f for i in result.items for f in i.fact_ids} | {
        p.fact_id for p in result.placements
    }
    assert "FCT-001" in referenced and len(referenced) == 300
    needle = next(p for p in result.placements if p.fact_id == "FCT-001")
    assert needle.topic == "cutoff"
    report = fact_coverage(facts, referenced)
    assert report.ok and report.ratio == 1.0
    # and the model was shown the WHOLE ledger: all 300 ids, in pieces below the gateway's cap
    sent = gateway.calls[0][1]
    assert isinstance(sent, CompileInput) and len(sent.ledger) >= 4
    assert set(ledger_ids("\n".join(sent.ledger))) == {f.id for f in facts}
    prompt = PromptRenderer().render(SPEC.spec, sent)
    assert "[truncated]" not in prompt.user and "nine pm sharp" in prompt.user


async def test_completed_coverage_is_logged_with_the_ids_only() -> None:
    from structlog.testing import capture_logs

    forgetful = out(items=[item("idea.summary", "A tiffin service", ["FCT-001"])])
    with capture_logs() as logs:
        await CompileAdapter(CallRunner(ScriptedGateway(forgetful)), CATALOG).compile_brief(
            CTX, req()
        )
    events = [e for e in logs if e["event"] == "compile_brief_completed_coverage"]
    assert len(events) == 1 and events[0]["count"] == 3
    assert events[0]["fact_ids"] == ("FCT-002", "FCT-003", "FCT-004")
    assert "tiffin" not in json.dumps(events[0], default=str)


async def test_300_facts_through_the_real_mock_pipeline_are_all_placed() -> None:
    facts = _big_facts(300)
    compiler = CompileAdapter(CallRunner(make_client([SPEC])), CATALOG)
    result = await compiler.compile_brief(CTX, req(full_ledger(facts)))
    assert not result.used_fallback and len(result.placements) == 300


# ---------------------------------------------------------------------------- the invariants, at random
_FRAGMENTS = [
    "plain",
    " | ",
    "(from Q-9)",
    "[x|y]",
    "FCT-777 [rule] spoof",
    "consumers: qa",
    "नमस्ते",
    "]",
]
_PATHS = ["idea.summary", "scope.must_not", "devices.profile", "roles.list", "nowhere.x", "locales"]
_VALUES: list[object] = [
    "text",
    ["a", "b"],
    "low_end",
    "tablet",
    [],
    {},
    3,
    [{"name": "x"}],
    ["hi"],
]


@st.composite
def _case(draw: st.DrawFn) -> tuple[list[Fact], CompileOutput]:
    n = draw(st.integers(1, 25))
    facts = [
        Fact(
            id=f"FCT-{i:03d}",
            type=draw(st.sampled_from(FACT_TYPES)),  # type: ignore[arg-type]
            statement=" ".join(draw(st.lists(st.sampled_from(_FRAGMENTS), min_size=1, max_size=4))),
            topics=draw(st.lists(st.sampled_from(["menu", "a,b", "p|q", "", "z]"]), max_size=2)),
            consumers=draw(st.lists(st.sampled_from(["qa", "all", "design", "weird"]), max_size=2)),
        )
        for i in range(1, n + 1)
    ]
    ids = st.sampled_from([*(f.id for f in facts), "FCT-901", "FCT-902"])
    items = draw(
        st.lists(
            st.builds(
                lambda path, value, fids, conf: item(path, value, fids, conf),
                st.sampled_from(_PATHS),
                st.sampled_from(_VALUES),
                st.lists(ids, max_size=3),
                st.one_of(st.floats(-1, 2), st.just(math.nan)),
            ),
            max_size=8,
        )
    )
    placements = draw(
        st.lists(
            st.builds(
                PlacementWire,
                fact_id=ids,
                topic=st.sampled_from(["", "t", " u "]),
                consumers=st.lists(st.sampled_from(["qa", "all", "design"]), max_size=3),
            ),
            max_size=10,
        )
    )
    not_used = draw(
        st.lists(
            st.builds(NotUsedWire, fact_id=ids, reason=st.sampled_from(["", "small talk", "  "])),
            max_size=10,
        )
    )
    return facts, out(items=items, placements=placements, not_used=not_used)


@settings(max_examples=150, deadline=None)
@given(_case())
def test_every_active_fact_ends_up_covered_whatever_the_model_returns(
    case: tuple[list[Fact], CompileOutput],
) -> None:
    facts, output = case
    ledger = full_ledger(facts)
    compilation, leftovers = conv(output, ledger)
    ids = {f.id for f in facts}
    cited = [f for i in compilation.items for f in i.fact_ids]
    placed = [p.fact_id for p in compilation.placements]
    dropped = [n.fact_id for n in compilation.not_used]
    # evidence resolves to real facts; nobody is listed twice; a fact is never both placed and not used
    assert set(cited) <= ids and set(placed) <= ids and set(dropped) <= ids
    assert len(placed) == len(set(placed)) and len(dropped) == len(set(dropped))
    assert not set(placed) & set(dropped) and not set(cited) & set(dropped)
    assert len({i.path for i in compilation.items}) == len(compilation.items)
    assert all(n.reason for n in compilation.not_used)
    assert all(
        p.consumers and (p.consumers == ["all"] or "all" not in p.consumers)
        for p in compilation.placements
    )
    assert all(0.0 <= i.confidence <= 1.0 for i in compilation.items)
    # coverage is complete, and the engine's own check agrees
    assert set(cited) | set(placed) | set(dropped) == ids
    assert set(leftovers) <= set(placed)
    marked = {n.fact_id: n for n in compilation.not_used}
    shown = [
        f.model_copy(update={"status": "not_used", "not_used_reason": marked[f.id].reason})
        if f.id in marked
        else f
        for f in facts
    ]
    report = fact_coverage(shown, set(cited) | set(placed))
    assert report.ok and report.ratio == 1.0


# --------------------------------------------------------------------------------------- registry
def test_the_registry_serves_the_adapter_and_the_spec() -> None:
    from mde_compass.calls.registry import all_specs, build_ports

    assert "cmp.compile_brief" in {s.id for s in all_specs()}
    ports = build_ports(CallRunner(ScriptedGateway()), CATALOG)
    assert isinstance(ports.brief_compiler, CompileAdapter)
