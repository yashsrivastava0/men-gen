"""The scripted-provider machinery itself: fixtures parse against the wire schemas, a missing answer and an unused
answer both fail a scenario, and the named SB-QP scenarios are all there."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from mde_compass.calls.audit_questions.schema import AuditOutput
from mde_compass.calls.plan_questions.schema import PlanOutput
from mde_compass.calls.understand_turn.schema import UnderstandOutput
from mde_compass.scenarios import load_scenario, run_scenario
from mde_compass.scenarios.providers import (
    HybridGateway,
    Lazy,
    MockItem,
    ProviderScript,
    fill,
    load_provider,
    parse_provider,
)

DATA = Path(__file__).parent / "data"
IDEA = "I want an app for my tiffin delivery service in Pune"


def test_missing_wire_fields_are_filled_with_the_empty_value_of_their_type() -> None:
    out = fill(UnderstandOutput, {"flags": ["vague"]})
    model = UnderstandOutput.model_validate(out)
    assert model.intent == "answer" and model.facts == [] and model.flags == ["vague"]
    fact = UnderstandOutput.model_validate(
        fill(UnderstandOutput, {"facts": [{"statement": "x", "quote": "x"}]})
    ).facts[0]
    assert fact.type == "context" and fact.consumers == ["all"] and fact.effects == []


def test_a_plan_shorthand_gets_ids_options_and_covered_needs() -> None:
    script = parse_provider(
        {
            "calls": {
                "cmp.plan_questions": [
                    {
                        "questions": [
                            {
                                "serves": ["audience.segments"],
                                "type": "choose_one",
                                "text": "Who orders your tiffins?",
                                "intent": "who",
                                "options": ["A", "B", "C"],
                            }
                        ]
                    }
                ]
            }
        }
    )
    (item,) = script.calls["cmp.plan_questions"]
    assert isinstance(item, PlanOutput)
    q = item.questions[0]
    assert q.id == "Q-001" and [o.label for o in q.options] == ["A", "B", "C"]
    assert item.covered_needs == ["audience.segments"]


def test_queue_markers_are_parsed_and_unknown_things_are_refused() -> None:
    script = parse_provider(
        {
            "calls": {
                "cmp.understand_turn": [{"mock": True}, {"error": "timeout"}],
                "cmp.audit_questions": [{"pass_all": True}],
            },
            "then_mock": ["cmp.plan_questions"],
        }
    )
    q = script.calls["cmp.understand_turn"]
    assert isinstance(q[0], MockItem) and isinstance(q[1], TimeoutError)
    assert isinstance(script.calls["cmp.audit_questions"][0], Lazy)
    assert script.then_mock == {"cmp.plan_questions"}
    with pytest.raises(ValueError, match="not a Compass call"):
        parse_provider({"calls": {"cmp.nope": []}})
    with pytest.raises(ValueError, match="unknown error kind"):
        parse_provider({"calls": {"cmp.understand_turn": [{"error": "wat"}]}})
    with pytest.raises(ValueError, match="does not fit its wire schema"):
        parse_provider({"calls": {"cmp.understand_turn": [{"intent": "teleport"}]}})
    with pytest.raises(ValueError, match="unscripted"):
        parse_provider({"unscripted": "maybe"})


def test_an_auditor_shorthand_resolves_against_the_questions_of_the_request() -> None:
    script = parse_provider(
        {
            "calls": {
                "cmp.audit_questions": [{"pass_all": True, "except": {"Q-003": {"specific": 2}}}]
            }
        }
    )
    lazy = script.calls["cmp.audit_questions"][0]
    assert isinstance(lazy, Lazy)

    class Request:
        questions = [type("Q", (), {"card_id": "Q-002"})(), type("Q", (), {"card_id": "Q-003"})()]

    out = lazy.resolve(Request())  # type: ignore[arg-type]
    assert isinstance(out, AuditOutput)
    assert [(v.card_id, v.specific) for v in out.verdicts] == [("Q-002", 5), ("Q-003", 2)]


def test_every_provider_fixture_in_the_data_directory_loads() -> None:
    files = sorted((DATA / "providers").glob("SB-QP-*.yaml"))
    assert len(files) >= 20
    for path in files:
        script = load_provider(path)
        assert isinstance(script, ProviderScript) and script.total() >= 1, path.name


def _write(tmp_path: Path, plan_items: list[dict], extra_audit: bool = False) -> Path:
    (tmp_path / "providers").mkdir()
    audits: list[dict] = [{"pass_all": True}] + ([{"pass_all": True}] if extra_audit else [])
    provider = {
        "calls": {
            "cmp.understand_turn": [{"mock": True}],
            "cmp.plan_questions": plan_items,
            "cmp.audit_questions": audits,
        }
    }
    (tmp_path / "providers" / "SB-QP-99.yaml").write_text(yaml.safe_dump(provider))
    scn = {
        "id": "SB-QP-99",
        "engines": ["planner"],
        "steps": [{"say": IDEA, "expect": {"batch": {"mode": "initial", "total_min": 1}}}],
    }
    path = tmp_path / "SB-QP-99.yaml"
    path.write_text(yaml.safe_dump(scn))
    return path


GOOD_PLAN = {
    "defer_rest": "later",
    "questions": [
        {
            "serves": ["audience.segments"],
            "type": "text",
            "intent": "learn who orders the tiffins",
            "text": "Who orders your tiffins, and what do they want from the app?",
            "why_we_ask": "The customers decide the screens.",
            "examples": ["Office workers"],
        }
    ],
}


async def test_a_scenario_with_a_complete_script_passes(tmp_path: Path) -> None:
    result = await run_scenario(load_scenario(_write(tmp_path, [GOOD_PLAN])), engine="planner")
    result.raise_if_failed()


async def test_a_scripted_answer_nobody_asked_for_fails_the_scenario(tmp_path: Path) -> None:
    path = _write(tmp_path, [GOOD_PLAN, GOOD_PLAN], extra_audit=True)
    result = await run_scenario(load_scenario(path), engine="planner")
    assert not result.ok and "never used" in (result.failure or "")
    assert "cmp.plan_questions" in (result.failure or "")


async def test_a_call_without_an_answer_left_fails_the_step(tmp_path: Path) -> None:
    path = _write(tmp_path, [])
    result = await run_scenario(load_scenario(path), engine="planner")
    assert not result.ok and result.failed_step == 0


def test_the_hybrid_gateway_refuses_unlisted_calls_when_the_fixture_says_so() -> None:
    script = ProviderScript(Path("<t>"), {}, unscripted="fail")
    gateway = HybridGateway(script, None)
    spec = type("S", (), {"id": "cmp.situate"})()
    import asyncio

    with pytest.raises(AssertionError, match="forbids mock calls"):
        asyncio.run(
            gateway.run(spec, UnderstandOutput.model_validate(fill(UnderstandOutput, {})), None)
        )


def test_the_twenty_named_planner_scenarios_exist_and_are_scripted() -> None:
    ids = {p.stem for p in DATA.glob("SB-QP-*.yaml")}
    for n in range(1, 21):
        sid = f"SB-QP-{n:02d}"
        assert sid in ids, f"{sid} is missing"
        scn = load_scenario(DATA / f"{sid}.yaml")
        assert scn.engines == ["planner"] and scn.title
        assert (DATA / "providers" / f"{sid}.yaml").exists(), f"{sid} has no provider fixture"
    # a variant file pins one unimplemented part of its parent behind a BUG or GAP number
    for path in sorted(DATA.glob("SB-QP-*-b.yaml")):
        scn = load_scenario(path)
        if scn.known_issue:
            assert scn.known_issue.startswith(("BUG-", "GAP-")) and scn.known_issue_note
