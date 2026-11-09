"""The offline twin of the live batches E, F and G (``mde_compass.live_batches``).

Everything here is free and deterministic: no live call, no database, no real ledger (a temporary one is
written where a ledger is needed). The batches themselves run end to end in plain mock mode on the real Compass
service, so what is proven is the machinery: the preflight, the plan, the allowance stop, the answer policy, the
checks and the report. Mock questions are labelled ``[mock]`` and their wording is canned, so the quality
numbers they give (project terms, overlap) are computed and reported but not judged here; the live run judges.

Replay tests at the end are skipped, with the reason, until the lead has recorded cassettes for the batch.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from mde_compass import live_batches as lb
from mde_compass.catalog import load_catalog
from mde_compass.compose import build_service
from mde_compass.config import DEFAULT_CONFIG, MODEL_ID, BudgetPolicy
from mde_compass.domain import Brief, Card, StoredEvent
from mde_compass.domain.brief import DomainFact
from mde_compass.domain.cards import CardOption, SkipSpec, YouDecide
from mde_compass.domain.common import BriefItem, Provenance
from mde_compass.domain.interview import Fact, FactStatus, QaRecord
from mde_compass.engine.ports import ExtractedFact, TurnUnderstanding
from mde_compass.engine.service import Service
from mde_core.clock import FakeClock
from mde_core.money import usd_to_micros
from mde_core.settings import load_settings
from mde_llm_gateway.pricing import Pricing

LIVE_ENV = {"LLM_MODE": "live", "ALLOW_LIVE_LLM": "1"}
CAP = 3_000_000
ALLOWANCE = 500_000


# ================================================================================================= helpers
def write_ledger(path: Path, *spend: tuple[str, int]) -> Path:
    """A gateway-shaped ledger: each (spec id, micro-dollars) is a reserve and a commit."""
    lines: list[dict[str, Any]] = []
    for i, (spec, micros) in enumerate(spend):
        rid = f"res_{i}"
        lines.append({"event": "reserve", "reservation_id": rid, "spec_id": spec})
        lines.append({"event": "commit", "reservation_id": rid, "cost_micros": micros})
    path.write_text("".join(json.dumps(x) + "\n" for x in lines), encoding="utf-8")
    return path


def mock_factory() -> Callable[[], Service]:
    """A fresh mock-mode service per session, on a fake clock (as the scenario tests do)."""

    def factory() -> Service:
        return lb.default_service_factory(llm_mode="mock", clock=FakeClock())()

    return factory


def run(data: lb.BatchData, **kwargs: Any) -> dict[str, Any]:
    kwargs.setdefault("allowance_micros", 10_000_000)
    kwargs.setdefault("spend", lambda: 0)
    kwargs.setdefault("service_factory", mock_factory())
    kwargs.setdefault("mode", "mock")
    return asyncio.run(lb.run_batch(data, **kwargs))


def checks_of(report: dict[str, Any]) -> dict[tuple[str, str | None], dict[str, Any]]:
    return {(c["id"], c.get("session")): c for c in report["checks"]}


def make_card(
    card_id: str = "Q-002",
    *,
    type_: str = "choose_one",
    needs: tuple[str, ...] = ("audience.segments",),
    options: int = 3,
    origin: str = "planner",
    question: str | None = None,
    intent: str = "",
    parent: str | None = None,
    depth: int = 0,
    must_confirm: bool = False,
    recommended: int | None = 1,
    skip: bool = True,
    you_decide: bool = True,
) -> Card:
    opts = [
        CardOption(id=chr(65 + i), label=f"Option {chr(65 + i)}", recommended=(i == recommended))
        for i in range(options)
    ]
    return Card.model_validate(
        {
            "id": card_id,
            "frame": "planned",
            "slots": [n for n in needs if not n.startswith("domain:")],
            "type": type_,
            "question": question or f"Question {card_id}?",
            "options": [o.model_dump() for o in opts],
            "need_ids": list(needs),
            "origin": origin,
            "intent": intent,
            "parent_card_id": parent,
            "depth": depth,
            "must_confirm": must_confirm,
            "skip": SkipSpec(enabled=skip).model_dump(),
            "you_decide": YouDecide(enabled=you_decide).model_dump(),
        }
    )


@pytest.fixture(scope="module", autouse=True)
def _offline_env() -> Iterator[None]:
    """Module-scoped fixtures run before the function-scoped guard of the root conftest: pin the same
    offline environment for them (mock mode, forbid-live on, no .env, no second key)."""
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("LLM_MODE", "mock")
        mp.setenv("MDE_FORBID_LIVE", "1")
        mp.setenv("MDE_ENV_FILE", "")
        mp.delenv("ALLOW_LIVE_LLM", raising=False)
        mp.delenv("OPENAI_API_KEY", raising=False)
        yield


@pytest.fixture(scope="module")
def e_report(_offline_env: None, tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    return run(lb.load_batch("E"), out_dir=tmp_path_factory.mktemp("reports_e"))


@pytest.fixture(scope="module")
def f_report(_offline_env: None, tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    return run(lb.load_batch("F"), out_dir=tmp_path_factory.mktemp("reports_f"))


@pytest.fixture(scope="module")
def g_report(_offline_env: None, tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    return run(lb.load_batch("G"), out_dir=tmp_path_factory.mktemp("reports_g"))


# ==================================================================================================== data
def test_the_scripts_load_and_cover_the_agreed_sessions() -> None:
    e, f, g = (lb.load_batch(b) for b in "EFG")
    assert [s.id for s in e.sessions] == [
        "tiffin_pune",
        "clinic_appointments",
        "pharmacy_inventory_b2b",
        "kids_language_learning",
        "senior_ride_sharing",
    ]
    assert all(s.kind == "first_batch" for s in e.sessions)
    assert [s.id for s in f.sessions] == ["tiffin_pune", "pharmacy_inventory_b2b"]
    for s in f.sessions:  # a needle at turn 2, a contradiction and a pivot, in each interview
        assert s.needle is not None and s.needle.turn == 2
        assert s.contradiction is not None and s.pivot is not None
        assert s.contradiction.anchor_turn < s.contradiction.correction_turn < s.pivot.turn
        assert s.budget == "thorough"
    prd, hinglish = g.sessions
    assert prd.document is not None and not prd.idea
    assert hinglish.start_locale == "hi-Latn" and hinglish.document is None
    assert [d.allowance_usd for d in (e, f, g)] == ["0.50", "0.70", "0.40"]


def test_a_script_mistake_is_refused_with_the_file_named(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unknown batch"):
        lb.load_batch("Z")
    with pytest.raises(Exception, match="planted turns"):
        lb.SessionDef.model_validate(
            {
                "id": "x",
                "kind": "interview",
                "label": "x",
                "idea": "an app",
                "pools": {"generic": {"specific": ["a b c d e f"], "vague": ["maybe"]}},
                "needle": {"turn": 2, "text": "t", "probe_terms": ["t"]},
                "contradiction": {
                    "anchor_turn": 2,
                    "anchor_text": "a",
                    "anchor_terms": ["a"],
                    "correction_turn": 5,
                    "correction_text": "c",
                    "correction_terms": ["c"],
                },
            }
        )


@pytest.mark.parametrize("batch", lb.BATCH_IDS)
def test_the_plan_prices_every_planned_call_and_stays_under_the_allowance(batch: str) -> None:
    data = lb.load_batch(batch)
    price = Pricing.load().price_for(MODEL_ID)
    allowance = usd_to_micros(data.allowance_usd)
    plan = lb.build_plan(
        data,
        lb.load_costs(),
        allowance_micros=allowance,
        spent_micros=0,
        policy=BudgetPolicy(),
        price=price,
    )
    assert plan.calls_total > 0 and 0 < plan.expected_micros < plan.ceiling_micros
    assert plan.ceiling_micros <= allowance, "the worst case must fit the allowance of the batch"
    text = lb.format_plan(plan, mode="live")
    assert f"Batch {batch}" in text and "expected cost" in text and "allowance" in text
    assert "planned calls" in text and "remaining USD" in text


def test_an_unpriced_call_in_a_script_is_refused() -> None:
    costs = lb.load_costs()
    price = Pricing.load().price_for(MODEL_ID)
    with pytest.raises(ValueError, match=r"cmp\.not_a_call"):
        lb.estimate_session({"cmp.not_a_call": 1}, costs, price)


# ============================================================================================= preflight
def test_preflight_passes_when_every_condition_is_met(tmp_path: Path) -> None:
    ledger = write_ledger(tmp_path / "ledger.jsonl", ("cmp.plan_questions", 100_000))
    assert lb.preflight(LIVE_ENV, ledger, ALLOWANCE, CAP, api_key_present=True) == []


@pytest.mark.parametrize(
    ("env", "needle"),
    [
        ({"ALLOW_LIVE_LLM": "1"}, "LLM_MODE"),
        ({"LLM_MODE": "mock", "ALLOW_LIVE_LLM": "1"}, "LLM_MODE"),
        ({"LLM_MODE": "live"}, "second key"),
        ({**LIVE_ENV, "ALLOW_LIVE_LLM": "0"}, "second key"),
        ({**LIVE_ENV, "MDE_FORBID_LIVE": "1"}, "MDE_FORBID_LIVE"),
        ({**LIVE_ENV, "COMPASS_INTERVIEW_ENGINE": "legacy"}, "COMPASS_INTERVIEW_ENGINE"),
    ],
)
def test_preflight_refuses_each_missing_switch(
    tmp_path: Path, env: dict[str, str], needle: str
) -> None:
    ledger = write_ledger(tmp_path / "ledger.jsonl")
    reasons = lb.preflight(env, ledger, ALLOWANCE, CAP, api_key_present=True)
    assert len(reasons) == 1 and needle in reasons[0]


def test_preflight_refuses_without_a_key_and_names_only_the_variable(tmp_path: Path) -> None:
    ledger = write_ledger(tmp_path / "ledger.jsonl")
    reasons = lb.preflight(LIVE_ENV, ledger, ALLOWANCE, CAP, api_key_present=False)
    assert len(reasons) == 1 and "OPENAI_API_KEY" in reasons[0]
    assert (
        lb.preflight(LIVE_ENV, ledger, ALLOWANCE, CAP, api_key_present=None) == []
    )  # unknown: not judged


def test_preflight_refuses_an_absent_ledger_and_tells_the_operator_how_to_create_it(
    tmp_path: Path,
) -> None:
    reasons = lb.preflight(LIVE_ENV, tmp_path / "data" / ".budget" / "ledger.jsonl", ALLOWANCE, CAP)
    assert len(reasons) == 1
    assert "budget_cli init" in reasons[0] and "blocks live calls on purpose" in reasons[0]
    # the directory alone is not enough: the ledger file must exist
    (tmp_path / "data" / ".budget").mkdir(parents=True)
    assert lb.preflight(LIVE_ENV, tmp_path / "data" / ".budget" / "ledger.jsonl", ALLOWANCE, CAP)


def test_preflight_collects_every_reason_at_once(tmp_path: Path) -> None:
    reasons = lb.preflight({}, tmp_path / "nope.jsonl", ALLOWANCE, CAP, api_key_present=False)
    assert len(reasons) == 4  # mode, second key, key, ledger


def test_dry_run_needs_no_switches_and_no_ledger(tmp_path: Path) -> None:
    assert lb.preflight({}, tmp_path / "missing.jsonl", ALLOWANCE, CAP, dry_run=True) == []


def test_the_stop_line_and_the_reserve_bound_what_may_be_spent(tmp_path: Path) -> None:
    # ceiling 3.00: the stop line is 95% = 2.85, the 0.30 reserve leaves 2.70, and the smaller one binds
    ok = write_ledger(tmp_path / "a.jsonl", ("cmp.plan_questions", 2_200_000))
    assert (
        lb.preflight(LIVE_ENV, ok, ALLOWANCE, CAP) == []
    )  # 2.20 + 0.50 = 2.70: exactly at the limit
    over = write_ledger(tmp_path / "b.jsonl", ("cmp.plan_questions", 2_300_000))
    reasons = lb.preflight(LIVE_ENV, over, ALLOWANCE, CAP)
    assert len(reasons) == 1 and "reserve" in reasons[0] and "2.7000" in reasons[0]
    spent = write_ledger(tmp_path / "c.jsonl", ("cmp.plan_questions", 2_800_000))
    assert lb.preflight(LIVE_ENV, spent, 10_000, CAP)  # not even a small allowance fits
    # a lower ceiling from COMPASS_LIVE_BUDGET_USD lowers the limit too
    assert lb.preflight(LIVE_ENV, ok, ALLOWANCE, 2_000_000)


def test_only_compass_calls_count_against_the_compass_ceiling_but_the_whole_ledger_has_a_tripwire(
    tmp_path: Path,
) -> None:
    others = write_ledger(tmp_path / "o.jsonl", ("C6.plan_research", 1_000_000))
    assert lb.preflight(LIVE_ENV, others, ALLOWANCE, CAP) == []  # not Compass's money
    heavy = write_ledger(tmp_path / "h.jsonl", ("C6.plan_research", 4_500_000))
    reasons = lb.preflight(LIVE_ENV, heavy, ALLOWANCE, CAP)
    assert len(reasons) == 1 and "80% tripwire" in reasons[0]
    # LLM_BUDGET_USD can only lower the repository cap
    lowered = {**LIVE_ENV, "LLM_BUDGET_USD": "2.00"}
    assert lb.preflight(lowered, others, ALLOWANCE, CAP) == []
    assert lb.preflight({**lowered, "LLM_BUDGET_USD": "9.00"}, heavy, ALLOWANCE, CAP)


def test_a_non_positive_allowance_is_refused(tmp_path: Path) -> None:
    ledger = write_ledger(tmp_path / "l.jsonl")
    assert "positive" in lb.preflight(LIVE_ENV, ledger, 0, CAP)[0]


def test_preflight_reads_the_ledger_without_changing_it(tmp_path: Path) -> None:
    ledger = write_ledger(tmp_path / "l.jsonl", ("cmp.situate", 12_345))
    before = ledger.read_bytes()
    lb.preflight(LIVE_ENV, ledger, ALLOWANCE, CAP)
    assert ledger.read_bytes() == before and not list(tmp_path.glob("*.lock"))


# ====================================================================================================== cli
def boom() -> Service:
    raise AssertionError("no service may be built here")


@pytest.mark.parametrize("batch", lb.BATCH_IDS)
def test_dry_run_prints_the_plan_and_exits_zero_without_building_anything(
    batch: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger = tmp_path / "absent.jsonl"  # no ledger, no switches: a dry run does not need them
    code = lb.main(
        ["--batch", batch, "--dry-run"], env={}, ledger_path=ledger, service_factory=boom
    )
    out = capsys.readouterr().out
    assert code == 0 and f"Batch {batch}" in out and "dry run: nothing was called" in out
    assert "expected cost" in out and "allowance" in out
    assert "does not exist" not in out  # a dry run does not judge the ledger or the switches
    assert not ledger.exists()


def test_a_live_run_without_the_operators_switches_is_refused_and_calls_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger = write_ledger(tmp_path / "l.jsonl")
    code = lb.main(
        ["--batch", "E"], env={}, ledger_path=ledger, service_factory=boom, out_dir=tmp_path
    )
    err = capsys.readouterr().err
    assert code == 2 and "REFUSED" in err and "second key" in err
    assert not list(tmp_path.glob("live-*.json"))


def test_a_bad_allowance_is_a_usage_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = lb.main(
        ["--batch", "E", "--dry-run", "--allowance-usd", "plenty"],
        env={},
        ledger_path=tmp_path / "x",
    )
    assert code == 2 and "not an amount" in capsys.readouterr().err


def test_an_allowance_over_what_preflight_allows_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger = write_ledger(tmp_path / "l.jsonl")
    code = lb.main(
        ["--batch", "E", "--allowance-usd", "9.00"],
        env=LIVE_ENV,
        ledger_path=ledger,
        service_factory=boom,
    )
    assert code == 2 and "limit" in capsys.readouterr().err


def test_the_mock_flag_runs_the_real_batch_offline_and_names_its_report_apart(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = lb.main(
        ["--batch", "E", "--mock"],
        env={},
        ledger_path=tmp_path / "absent.jsonl",
        service_factory=mock_factory(),
        out_dir=tmp_path / "out",
    )
    out = capsys.readouterr().out
    assert code == 0 and "FIRST BATCHES" in out and "cassette" not in out
    assert [p.name for p in (tmp_path / "out").iterdir()] == [
        "live-E-mock.json"
    ]  # never a live name


# ============================================================================================ answer policy
def test_the_tap_follows_the_card_type() -> None:
    assert lb.tap_value(make_card(type_="choose_one", recommended=1)) == "B"
    assert lb.tap_value(make_card(type_="choose_one", recommended=None)) == "A"
    assert lb.tap_value(make_card(type_="pick_many", recommended=2)) == ["C"]
    assert lb.tap_value(make_card(type_="priority")) == ["A", "B", "C"]
    assert lb.tap_value(make_card(type_="open_prompt", options=0)) is None


def policy_for(**overrides: Any) -> lb.AnswerPolicy:
    return lb.AnswerPolicy(lb.load_batch("F").sessions[0].model_copy(update=overrides))


def play(policy: lb.AnswerPolicy, cards: list[Card]) -> list[lb.Action]:
    return [policy.decide(card, turn) for turn, card in enumerate(cards, 1)]


def card_sequence(n: int = 18) -> list[Card]:
    needs = [
        "audience.segments",
        "apps.list",
        "roles.list",
        "idea.business_model",
        "scope.priority",
        "domain:rider_pay",
    ]
    return [
        make_card(
            f"Q-{i + 2:03d}",
            type_="open_prompt" if i % 3 == 1 else "choose_one",
            options=0 if i % 3 == 1 else 3,
            needs=(needs[i % len(needs)],),
        )
        for i in range(n)
    ]


def test_the_answer_policy_is_deterministic() -> None:
    cards = card_sequence()
    first = play(policy_for(), cards)
    assert first == play(policy_for(), cards)
    assert first != play(policy_for(answer_cycle=("vague",)), cards)  # and it does read the data


def test_the_policy_plants_the_needle_the_contradiction_and_the_skips_on_their_turns() -> None:
    sdef = lb.load_batch("F").sessions[0]
    actions = play(lb.AnswerPolicy(sdef), card_sequence(20))
    by_turn = {a.turn: a for a in actions}
    assert by_turn[2].tag == "needle" and by_turn[2].kind == "text"
    assert sdef.needle is not None and by_turn[2].text == sdef.needle.text
    assert sdef.contradiction is not None
    assert by_turn[3].tag == "anchor" and by_turn[3].text == sdef.contradiction.anchor_text
    assert (
        by_turn[12].tag == "correction" and by_turn[12].text == sdef.contradiction.correction_text
    )
    assert by_turn[9].kind == "skip"
    assert {t for t, a in by_turn.items() if a.kind == "you_decide"} == {6, 14}
    assert not any(a.kind == "text" and a.text == "" for a in actions)


def test_typed_answers_alternate_specific_and_vague_and_read_the_pool_of_the_need() -> None:
    sdef = (
        lb.load_batch("F").sessions[0].model_copy(update={"skip_turns": (), "you_decide_turns": ()})
    )
    cards = [
        make_card(f"Q-{i:03d}", type_="open_prompt", options=0, needs=("scope.features",))
        for i in range(20, 29)
    ]
    policy = lb.AnswerPolicy(sdef)
    actions = [policy.decide(c, 30 + i) for i, c in enumerate(cards)]  # turns with nothing planted
    assert [a.specificity for a in actions] == list(
        sdef.answer_cycle
    )  # specific, specific, vague, ...
    pool = sdef.pools.by_need["scope.features"]
    assert actions[0].pool == "scope.features" and actions[0].text in pool.specific
    assert actions[2].text in pool.vague
    # a need with no pool of its own falls back to the generic pool
    generic = lb.AnswerPolicy(sdef).decide(
        make_card("Q-099", type_="open_prompt", options=0, needs=("domain:rider_pay",)), 31
    )
    assert generic.pool == "generic" and generic.text in sdef.pools.generic.specific


def test_a_followup_is_answered_with_a_specific_line_and_a_vague_line_cannot_end_up_on_it() -> None:
    policy = policy_for(answer_cycle=("vague",))
    follow = make_card(
        "Q-020", type_="open_prompt", options=0, origin="followup", parent="Q-004", depth=1
    )
    action = policy.decide(follow, 31)
    assert action.tag == "followup" and action.specificity == "specific"


def test_a_card_that_must_be_confirmed_is_never_skipped_and_hands_the_skip_to_the_next_card() -> (
    None
):
    sdef = lb.load_batch("F").sessions[0]  # a skip is due at turn 9, "you decide" at turns 6 and 14
    locked = make_card("Q-020", must_confirm=True)
    policy = lb.AnswerPolicy(sdef)
    assert policy.decide(locked, 6).kind == "tap"  # cannot take the "you decide" owed at turn 6
    assert policy.decide(make_card("Q-021"), 7).kind == "you_decide"  # the next card takes it
    assert policy.decide(make_card("Q-022"), 8).kind in (
        "tap",
        "text",
    )  # never two in a row (fatigue)
    assert (
        policy.decide(make_card("Q-023", skip=False), 9).kind == "tap"
    )  # no skip button on this one
    assert policy.decide(make_card("Q-024"), 10).kind == "skip"  # the skip due at turn 9 comes now
    assert policy.decide(make_card("Q-025"), 11).kind in ("tap", "text")
    assert policy.decide(make_card("Q-026"), 14).kind == "you_decide"
    assert policy.decide(make_card("Q-027"), 15).kind in ("tap", "text")  # nothing owed any more


def test_a_choice_card_is_typed_only_where_the_pool_has_a_line_for_its_need() -> None:
    sdef = lb.load_batch("F").sessions[0]  # text_on_choice_every = 4
    on_pool = make_card("Q-020", needs=("audience.segments",))
    off_pool = make_card("Q-021", needs=("domain:rider_pay",))
    assert lb.AnswerPolicy(sdef).decide(on_pool, 4).kind == "text"
    assert lb.AnswerPolicy(sdef).decide(off_pool, 4).kind == "tap"
    assert lb.AnswerPolicy(sdef).decide(on_pool, 5).kind == "tap"


def test_the_pivot_message_comes_once_when_its_turn_is_reached() -> None:
    sdef = lb.load_batch("F").sessions[0]
    policy = lb.AnswerPolicy(sdef)
    assert sdef.pivot is not None
    assert policy.pivot_before(sdef.pivot.turn - 1) is None
    assert policy.pivot_before(sdef.pivot.turn) == sdef.pivot.text
    assert policy.pivot_before(sdef.pivot.turn) is None


def test_after_the_scripted_answers_a_tired_person_says_you_decide_to_the_rest() -> None:
    hinglish = lb.load_batch("G").sessions[1]
    assert hinglish.max_answers == 8 and hinglish.tail == "you_decide"
    policy = lb.AnswerPolicy(hinglish)
    assert policy.decide(make_card("Q-020"), 9).tag == "tail"
    assert policy.decide(make_card("Q-021"), 9).kind == "you_decide"
    assert policy.decide(make_card("Q-022", must_confirm=True), 10).kind == "tap"


# ======================================================================================= check machinery
def f_session(**changes: Any) -> lb.SessionData:
    sd = lb.SessionData(sdef=lb.load_batch("F").sessions[0], catalog=load_catalog())
    for key, value in changes.items():
        setattr(sd, key, value)
    return sd


def act(turn: int, card: Card, kind: str, **kw: Any) -> lb.ActionRecord:
    return lb.ActionRecord(lb.Action(turn, card.id, kind, **kw), "INTERVIEWING", 0, 0)  # type: ignore[arg-type]


def test_followups_are_counted_after_vague_and_after_specific_answers() -> None:
    base_vague = make_card("Q-002", needs=("audience.segments",))  # impact 5
    child = make_card(
        "Q-009", origin="followup", parent="Q-002", depth=1, needs=("audience.segments",)
    )
    base_missed = make_card("Q-003", needs=("scope.priority",))  # impact 4, no follow-up came
    base_low = make_card("Q-004", needs=("brand.name",))  # impact 2: no follow-up is owed
    specific = make_card("Q-005", needs=("roles.list",))
    specific_child = make_card(
        "Q-010", origin="followup", parent="Q-005", depth=1, needs=("roles.list",)
    )
    planted = make_card("Q-006", needs=("locales",))
    qa = [
        QaRecord(card_id=c.id, card=c)
        for c in (base_vague, child, base_missed, base_low, specific, specific_child, planted)
    ]
    sd = f_session(
        qa_all=qa,
        actions=[
            act(1, base_vague, "text", specificity="vague", pool="generic"),
            act(2, base_missed, "text", specificity="vague", pool="generic"),
            act(3, base_low, "text", specificity="vague", pool="generic"),
            act(4, specific, "text", specificity="specific", pool="roles.list"),
            act(5, planted, "text", specificity="specific", pool="needle", tag="needle"),
            act(6, child, "text", specificity="specific", pool="generic", tag="followup"),
        ],
    )
    stats, detail = lb.followup_stats(sd, high_impact_at=4)
    assert stats["vague_total"] == 3 and stats["vague_fired"] == 1
    assert (
        stats["eligible_vague"] == 2 and stats["eligible_fired"] == 1
    )  # the impact-2 need is not owed one
    assert detail["vague_missed"] == ["Q-003"]
    assert stats["specific_text_total"] == 1 and stats["specific_text_fired"] == 1
    assert detail["specific_fired"] == ["Q-005"]  # planted answers and follow-ups are not judged


def test_a_second_vague_answer_for_the_same_need_is_not_owed_another_followup() -> None:
    first = make_card("Q-002", needs=("audience.segments",))
    child = make_card(
        "Q-009", origin="followup", parent="Q-002", depth=1, needs=("audience.segments",)
    )
    again = make_card("Q-003", needs=("audience.segments",))
    sd = f_session(
        qa_all=[QaRecord(card_id=c.id, card=c) for c in (first, child, again)],
        actions=[
            act(1, first, "text", specificity="vague", pool="generic"),
            act(2, again, "text", specificity="vague", pool="generic"),
        ],
    )
    stats, _ = lb.followup_stats(sd, high_impact_at=4)
    assert stats["eligible_vague"] == 1 and stats["eligible_fired"] == 1


def test_repeated_questions_are_found_by_text_and_by_intent_but_a_followup_is_no_repeat() -> None:
    a = make_card("Q-002", question="[mock] Who orders lunch?", intent="who orders")
    b = make_card("Q-003", question="Who  ORDERS lunch?", intent="something else")
    c = make_card("Q-004", question="Which people place orders?", intent="Who orders")
    follow = make_card(
        "Q-005",
        question="Who orders lunch?",
        intent="who orders",
        origin="followup",
        parent="Q-002",
    )
    assert lb.repeated_questions([a, b, c, follow]) == [
        {"card": "Q-003", "repeats": "Q-002", "by": "text"},
        {"card": "Q-004", "repeats": "Q-002", "by": "intent"},
    ]
    assert lb.repeated_questions([a, a]) == []  # the same card twice is one question


def test_questions_must_serve_a_need_or_a_domain_topic() -> None:
    catalog = load_catalog()
    good = [
        make_card("Q-002", needs=("roles.list",)),
        make_card("Q-003", needs=("domain:pickup_window",)),
    ]
    bad = [
        make_card("Q-004", needs=("not.a.need",)),
        make_card("Q-005", needs=("domain:",)),
        make_card("Q-006", needs=()),
    ]
    assert lb.unmapped_questions(good, catalog) == []
    assert lb.unmapped_questions(bad, catalog) == ["Q-004", "Q-005", "Q-006"]


def test_a_need_that_was_satisfied_when_a_question_was_shown_counts_as_asked_again() -> None:
    asked = make_card("Q-002", needs=("roles.list",))
    confirm = make_card("Q-003", type_="confirm", needs=("roles.list",))
    follow = make_card("Q-004", origin="followup", needs=("roles.list",), parent="Q-001")
    fine = make_card("Q-005", needs=("apps.list",))
    sd = f_session(
        shown=[
            lb.ShownCard(asked, 1, {"roles.list": "satisfied"}),
            lb.ShownCard(confirm, 2, {"roles.list": "satisfied"}),
            lb.ShownCard(follow, 3, {"roles.list": "satisfied"}),
            lb.ShownCard(fine, 4, {"apps.list": "missing"}),
        ]
    )
    assert lb.reasked_needs(sd) == [{"card": "Q-002", "needs": ["roles.list"], "turn": 1}]
    assert lb.reasked_needs(
        f_session(shown=[lb.ShownCard(fine, 1, {"apps.list": "missing"})]), closed=["apps.list"]
    )


def brief_with(*facts: DomainFact, **sections: dict[str, BriefItem]) -> Brief:
    brief = Brief(domain_facts=list(facts))
    for name, items in sections.items():
        getattr(brief, name).update(items)
    return brief


def test_the_needle_is_found_in_a_domain_fact_or_in_an_items_evidence_and_nowhere_else_fails() -> (
    None
):
    sd = f_session()
    needle = sd.sdef.needle
    assert needle is not None
    holder = Fact(
        id="FCT-003",
        type="rule",
        statement="Lunch boxes leave Aundh by 11:40 am",
        quote="must leave the Aundh kitchen by 11:40 am sharp",
        origin="stated",
    )
    sd.facts = [holder]
    sd.brief = brief_with(
        DomainFact(id="FCT-003", type="rule", statement=holder.statement, quote=holder.quote)
    )
    check = lb.needle_check(sd)
    assert check.passed is True and check.value == ["domain_facts:FCT-003"]
    # in an item's evidence quote only
    item = BriefItem(
        value="Office workers",
        provenance=[Provenance(type="user_text", ref="FCT-003", quote=holder.quote)],
    )
    sd.brief = brief_with(audience={"segments": item})
    assert lb.needle_check(sd).passed is True
    # present only as plain text somewhere: not enough
    sd.brief = brief_with(idea={"summary": BriefItem(value="boxes leave by 11:40 am")})
    assert lb.needle_check(sd).passed is False
    sd.brief = Brief()
    check = lb.needle_check(sd)
    assert (
        check.passed is False and "FCT-003" in check.note
    )  # the ledger still holds it: the note says so


def test_the_contradiction_check_wants_the_old_fact_inactive_and_the_correction_active() -> None:
    spec = lb.load_batch("F").sessions[0].contradiction
    assert spec is not None
    anchor = make_card("Q-003", needs=("locales",))
    correction = make_card("Q-012", needs=("scope.priority",))
    actions = [act(3, anchor, "text", tag="anchor"), act(12, correction, "text", tag="correction")]

    def facts(old: FactStatus, new: FactStatus) -> list[Fact]:
        return [
            Fact(
                id="FCT-007",
                type="rule",
                statement="Riders are salaried staff",
                quote="salaried staff",
                origin="stated",
                qa_card_id="Q-003",
                status=old,
            ),
            Fact(
                id="FCT-021",
                type="rule",
                statement="Riders are independent partners",
                quote="independent partners",
                origin="stated",
                qa_card_id="Q-012",
                status=new,
            ),
        ]

    assert (
        lb.contradiction_check(
            f_session(actions=actions, facts=facts("superseded", "active"))
        ).passed
        is True
    )
    assert (
        lb.contradiction_check(f_session(actions=actions, facts=facts("disputed", "active"))).passed
        is True
    )
    assert (
        lb.contradiction_check(f_session(actions=actions, facts=facts("active", "active"))).passed
        is False
    )
    assert (
        lb.contradiction_check(
            f_session(actions=actions, facts=facts("superseded", "superseded"))
        ).passed
        is False
    )


def test_a_locked_item_needs_a_verified_quote_and_an_unverified_one_never_counts() -> None:
    good = Fact(id="FCT-001", type="context", statement="s", quote="my own words", origin="stated")
    shaky = Fact(
        id="FCT-002",
        type="context",
        statement="s",
        quote="words not in the source",
        origin="stated",
        unverified_quote=True,
    )

    def locked(ref: str, quote: str = "q") -> BriefItem:
        return BriefItem(
            value="v",
            tier="Locked",
            provenance=[Provenance(type="user_text", ref=ref, quote=quote)],
        )

    brief = brief_with(
        idea={
            "ok": locked("FCT-001"),
            "event": locked("evt_0007"),
            "shaky": locked("FCT-002"),
            "bare": locked("FCT-001", ""),
        },
        audience={
            "open": BriefItem(
                value="v", provenance=[Provenance(type="user_text", ref="FCT-002", quote="q")]
            )
        },
    )
    report = lb.locked_report(brief, [good, shaky])
    assert report["locked_items"] == 4
    assert sorted(v["path"] for v in report["violations"]) == ["idea.bare", "idea.shaky"]  # type: ignore[index,union-attr]


def test_the_first_batch_latency_is_the_gap_between_the_playback_and_the_first_planned_batch() -> (
    None
):
    t0 = datetime(2026, 11, 9, 9, 0, 0, tzinfo=UTC)

    def ev(idx: int, type_: str, at: datetime | None, **payload: Any) -> StoredEvent:
        return StoredEvent(idx=idx, direction="out", type=type_, payload=payload, at=at)

    events = [
        ev(1, "card", t0, card={"frame": "welcome"}),
        ev(2, "playback", t0 + timedelta(seconds=1)),
        ev(3, "card", t0 + timedelta(seconds=2), card={"frame": "welcome"}),  # not a planned card
        ev(4, "question_batch", t0 + timedelta(seconds=7, milliseconds=250)),
    ]
    assert lb.first_batch_latency_ms(events) == 6250
    assert lb.first_batch_latency_ms(events[:3]) is None
    assert lb.first_batch_latency_ms([ev(1, "playback", None), events[3]]) is None


def test_side_by_side_puts_the_columns_next_to_each_other_and_wraps_to_the_width() -> None:
    text = lb.side_by_side(
        {
            "first": ["1. a rather long question that needs wrapping", "   - an option"],
            "second": ["1. short"],
        },
        width=24,
        per_row=2,
    )
    lines = text.splitlines()
    assert lines[0].startswith("first") and "second" in lines[0]
    assert all(len(line) <= 24 * 2 + 3 for line in lines)
    assert any("an option" in line for line in lines)


# ============================================================================================== batch E
def test_batch_e_runs_the_five_ideas_to_their_first_batch_and_answers_nothing(
    e_report: dict[str, Any],
) -> None:
    assert e_report["batch"] == "E" and e_report["mode"] == "mock" and e_report["stopped"] is None
    assert [s["id"] for s in e_report["sessions"]] == [
        "tiffin_pune",
        "clinic_appointments",
        "pharmacy_inventory_b2b",
        "kids_language_learning",
        "senior_ride_sharing",
    ]
    for s in e_report["sessions"]:
        assert s["state"] == "INTERVIEWING" and s["answers"] == []
        assert len(s["questions"]) >= 3, f"{s['id']} got no first batch"
        assert s["questions"][0]["question"].startswith(
            "[mock]"
        )  # a mock session is never taken for the product
        assert all(q["need_ids"] and q["origin"] == "planner" for q in s["questions"])
        assert s["first_message_wall_ms"] is not None and s["spend_micros"] == 0


def test_batch_e_computes_and_reports_every_quality_check(e_report: dict[str, Any]) -> None:
    checks = checks_of(e_report)
    share = checks[("project_term_share", None)]
    assert isinstance(share["value"], float) and 0.0 <= share["value"] <= 1.0
    assert share["threshold"] == 0.9 and isinstance(share["passed"], bool)
    overlap = checks[("cross_idea_overlap", None)]
    assert isinstance(overlap["value"], float) and overlap["threshold"] == 0.5
    assert "uncalibrated" in overlap["note"] and "pairs" in overlap["note"]
    latency = checks[("first_batch_latency_ms", None)]
    assert latency["passed"] is None and latency["threshold"] == 10_000  # reported, never judged
    for session in ("tiffin_pune", "kids_language_learning"):
        for check_id in (
            "fallback_count",
            "repeated_questions",
            "questions_map_to_a_need",
            "first_batch_produced",
        ):
            assert isinstance(checks[(check_id, session)]["passed"], bool), (check_id, session)
    assert checks[("no_session_crashed", None)]["passed"] is True
    assert e_report["checks_passed"] + e_report["checks_failed"] == sum(
        1 for c in e_report["checks"] if c["passed"] is not None
    )


def test_a_declined_idea_is_a_failed_check_not_a_silent_gap() -> None:
    data = lb.load_batch("E")
    games = data.sessions[3].model_copy(
        update={"idea": "A language-learning app for children with short games and stories"}
    )
    report = run(data.model_copy(update={"sessions": (games,)}))
    session = report["sessions"][0]
    assert session["questions"] == [] and session["state"] == "WELCOME"
    check = checks_of(report)[("first_batch_produced", "kids_language_learning")]
    assert check["passed"] is False and "declined_category" in check["note"]


def test_batch_e_report_has_the_per_call_table_and_the_blocks_the_owner_asked_for(
    e_report: dict[str, Any],
) -> None:
    s = e_report["sessions"][0]
    assert {
        "spec_id",
        "latency_ms",
        "input_tokens",
        "output_tokens",
        "reasoning_tokens",
        "cost_micros",
        "fallback",
        "attempts",
        "repaired",
        "outcome",
    } <= set(s["calls"][0])
    assert {"cmp.understand_turn", "cmp.plan_questions", "cmp.audit_questions"} <= {
        c["spec_id"] for c in s["calls"]
    }
    assert all(c["fallback"] is False for c in s["calls"])
    q = s["questions"][0]
    assert {
        "question",
        "options",
        "group",
        "why_we_ask",
        "intent",
        "need_ids",
        "origin",
        "parent_card_id",
        "unlocks",
        "type",
        "language",
        "fallback",
        "shown",
    } <= set(q)
    assert q["group"]  # the model-written title (the id when there is none)
    assert s["brief"]["meta_interview"]["batches"] >= 1 and s["batches"][0]["mode"] == "initial"
    assert s["hypothesis"] is not None and s["needs"]["audience.segments"]["state"] in {
        "missing",
        "partial",
        "assumed",
        "satisfied",
    }


def test_batch_e_prints_all_five_first_batches_side_by_side(e_report: dict[str, Any]) -> None:
    text = lb.render_all(e_report)
    assert "FIRST BATCHES" in text
    for idea in (
        "tiffin_pune",
        "clinic_appointments",
        "pharmacy_inventory_b2b",
        "kids_language_learning",
        "senior_ride_sharing",
    ):
        assert idea in text
    assert "checks" in text and "calls by spec" in text
    if lb.BEFORE_EVIDENCE.is_file():
        assert "BEFORE" in text  # the old frame-driven questions, from the committed evidence


def test_the_report_is_plain_json_written_under_a_mock_name(e_report: dict[str, Any]) -> None:
    path = Path(e_report["report_path"])
    assert path.name == "live-E-mock.json"
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert {
        "batch",
        "title",
        "mode",
        "started_at",
        "finished_at",
        "stopped",
        "allowance_micros",
        "allowance_usd",
        "spent_delta_micros",
        "sessions",
        "checks",
        "log_warnings",
    } <= set(loaded)
    assert datetime.fromisoformat(loaded["started_at"]).tzinfo is not None
    assert isinstance(loaded["spent_delta_micros"], int) and loaded["spent_delta_usd"] == "0.0000"
    assert "sk-" not in path.read_text(encoding="utf-8")


def test_the_reports_directory_is_covered_by_a_gitignore() -> None:
    assert (lb.REPORT_DIR / ".gitignore").read_text(encoding="utf-8").strip().startswith("*")


# ============================================================================================== batch F
def test_batch_f_plays_both_interviews_with_the_planted_turns(f_report: dict[str, Any]) -> None:
    assert [s["id"] for s in f_report["sessions"]] == ["tiffin_pune", "pharmacy_inventory_b2b"]
    for s in f_report["sessions"]:
        assert s["state"] == "BRIEF_REVIEW" and s["stopped"] is None
        tags = {a["turn"]: a["tag"] for a in s["answers"]}
        assert tags[2] == "needle" and tags[3] == "anchor" and tags[12] == "correction"
        assert {a["kind"] for a in s["answers"]} >= {"tap", "text", "skip", "you_decide"}
        assert [m["tag"] for m in s["messages"]] == ["pivot"]
        assert (
            15 <= len(s["answers"]) <= 25
        )  # the thorough budget in mock mode ends at its 25 questions
        assert s["brief"]["domain_facts"] and s["facts"]


def test_batch_f_carries_the_needle_into_the_final_brief(f_report: dict[str, Any]) -> None:
    checks = checks_of(f_report)
    for session, term in (("tiffin_pune", "11:40"), ("pharmacy_inventory_b2b", "9:30")):
        needle = checks[("needle_in_brief", session)]
        assert needle["passed"] is True and needle["threshold"] == [term]
        assert any(w.startswith(("domain_facts", "evidence")) for w in needle["value"])
        assert checks[("needle_in_consumer_views", session)]["passed"] is True


def test_batch_f_computes_every_check_it_promises(f_report: dict[str, Any]) -> None:
    checks = checks_of(f_report)
    for session in ("tiffin_pune", "pharmacy_inventory_b2b"):
        for check_id in (
            "fact_coverage",
            "locked_have_verified_quotes",
            "quote_verification_rate",
            "followup_after_vague",
            "no_followup_after_specific",
            "answered_need_not_reasked",
            "qa_count_in_range",
            "contradiction_newest_wins",
            "pivot_handled",
            "brief_reaches_review",
            "handoff_probes",
            "consumer_views_clean",
            "fallback_count",
            "repeated_questions",
        ):
            assert (check_id, session) in checks, (check_id, session)
        assert checks[("fact_coverage", session)]["value"]["coverage"] == 1.0
        assert checks[("locked_have_verified_quotes", session)]["passed"] is True
        assert checks[("brief_reaches_review", session)]["passed"] is True
        assert checks[("handoff_probes", session)]["value"][
            "probes"
        ]  # the engine's own probes, reported
        stats = json.loads(checks[("followup_after_vague", session)]["note"])
        assert stats["vague_total"] >= 1


def pivot_aware(factory: Callable[[], Service]) -> Callable[[], Service]:
    """A service whose reader flags a pivot (what the real model does and the mock cannot), as the engine tests do."""

    def build() -> Service:
        svc = factory()
        assert svc.ports is not None
        real = svc.ports.understander

        class Pivoting:
            async def understand(self, ctx: Any, text: str, **kw: Any) -> TurnUnderstanding:
                if text.startswith("Actually"):
                    return TurnUnderstanding(
                        intent="answer",
                        flags=["pivot"],
                        facts=[
                            ExtractedFact(
                                type="context",
                                statement="The customers are corporate canteens that order lunch in bulk",
                                quote="corporate canteens that order lunch in bulk",
                                topics=["idea"],
                            )
                        ],
                    )
                return await real.understand(ctx, text, **kw)

        object.__setattr__(svc.ports, "understander", Pivoting())
        return svc

    return build


def test_the_pivot_check_passes_when_the_engine_handles_the_pivot() -> None:
    data = lb.load_batch("F")
    one = data.model_copy(update={"sessions": (data.sessions[0],)})
    report = run(one, service_factory=pivot_aware(mock_factory()))
    check = checks_of(report)[("pivot_handled", "tiffin_pune")]
    assert check["passed"] is True, check
    assert (
        check["value"]["notice"]
        and check["value"]["replanned"]
        and check["value"]["facts_lost"] == []
    )
    assert check["value"]["superseded_by_pivot"], (
        "the earlier facts about the idea are superseded, not deleted"
    )


def test_batch_f_prints_the_interviews_as_questions_and_answers(f_report: dict[str, Any]) -> None:
    text = lb.render_all(f_report)
    assert "[text/specific/needle]" in text and "[skip/" in text and "follow-up of Q-" in text


# ============================================================================================== batch G
def test_batch_g_prd_led_session_turns_claims_into_verified_facts(g_report: dict[str, Any]) -> None:
    prd = next(s for s in g_report["sessions"] if s["id"] == "prd_led")
    checks = checks_of(g_report)
    assert prd["state"] == "BRIEF_REVIEW" and prd["questions"]
    assert checks[("claims_become_facts", "prd_led")]["passed"] is True
    assert checks[("document_quotes_verified", "prd_led")]["passed"] is True
    assert checks[("document_quotes_verified", "prd_led")]["value"]["facts"] >= 5
    assert isinstance(checks[("few_questions_after_a_prd", "prd_led")]["passed"], bool)
    assert isinstance(checks[("answered_need_not_reasked", "prd_led")]["passed"], bool)
    assert checks[("needs_closed_by_document", "prd_led")]["value"] == prd["closed_by_document"]
    assert prd["closed_by_document"], "the document answers some needs, and the report lists them"
    assert checks[("locked_have_verified_quotes", "prd_led")]["passed"] is True
    doc_facts = [f for f in prd["facts"] if f["origin"] == "document"]
    assert doc_facts and all(f["quote_verified"] and f["quote"] for f in doc_facts)
    assert not any(c["id"] in lb.FOLLOWUP_CHECKS for c in g_report["checks"])  # batch F's question


def test_batch_g_hinglish_session_is_checked_for_language_and_verbatim_quotes(
    g_report: dict[str, Any],
) -> None:
    hi = next(s for s in g_report["sessions"] if s["id"] == "hinglish")
    checks = checks_of(g_report)
    language = checks[("question_language_matches_session", "hinglish")]
    assert (
        language["passed"] is True and language["value"]["session_locale"] == hi["locale"] != "en"
    )
    assert set(language["value"]["card_languages"]) == {hi["locale"]}
    assert all(q["language"] == hi["locale"] for q in hi["questions"] if q["status"] != "dropped")
    leaks = checks[("english_looking_questions", "hinglish")]
    assert (
        leaks["passed"] is None and "count" in leaks["value"]
    )  # reported for a human read, never judged
    assert checks[("facts_keep_verbatim_quotes", "hinglish")]["passed"] is True
    assert checks[("qa_count_in_range", "hinglish")]["value"] == len(hi["answers"])
    assert hi["state"] == "BRIEF_REVIEW", (
        "tired answers end the interview and the Brief is compiled"
    )
    assert any(a["tag"] == "tail" for a in hi["answers"])


def test_batch_g_prints_the_transcripts(g_report: dict[str, Any]) -> None:
    text = lb.render_all(g_report)
    assert "prd_led" in text and "hinglish" in text and "FIRST BATCHES" not in text


# =================================================================================== the allowance stop
def counting_factory(
    inner: Callable[[], Service], counter: dict[str, int]
) -> Callable[[], Service]:
    """A service that counts the events it handles (stand-in for the ledger moving as calls are paid)."""

    def build() -> Service:
        svc = inner()
        real = svc.handle_event

        async def counted(*args: Any, **kwargs: Any) -> Any:
            counter["events"] += 1
            return await real(*args, **kwargs)

        object.__setattr__(svc, "handle_event", counted)
        return svc

    return build


def test_the_run_stops_at_the_allowance_skips_the_rest_and_still_writes_the_report(
    tmp_path: Path,
) -> None:
    counter = {"events": 0}
    report = run(
        lb.load_batch("F"),
        allowance_micros=300_000,
        spend=lambda: 40_000 * counter["events"],  # 40 thousand micro-dollars per event handled
        service_factory=counting_factory(mock_factory(), counter),
        out_dir=tmp_path,
    )
    first = report["sessions"][0]
    assert report["stopped"] == "allowance" and first["stopped"] == "allowance"
    assert report["spent_delta_micros"] >= 300_000 > report["spent_delta_micros"] - 40_000 * 2
    assert len(first["answers"]) < 10, "stopped in the middle of the first interview"
    assert report["sessions_skipped"] == ["pharmacy_inventory_b2b"] and len(report["sessions"]) == 1
    assert (tmp_path / "live-F-mock.json").is_file()
    assert first["answers"], "what was already paid for is in the report"
    assert first["spend_micros"] == report["spent_delta_micros"]
    assert checks_of(report)[("no_session_crashed", None)]["passed"] is True


def test_the_allowance_is_read_from_the_ledger_delta_not_from_an_estimate(tmp_path: Path) -> None:
    """The reader is called between turns, and only its difference from the start counts."""
    reads: list[int] = []
    counter = {"events": 0}

    def spend() -> int:
        value = (
            1_000_000 + 25_000 * counter["events"]
        )  # money already spent before this batch started
        reads.append(value)
        return value

    report = run(
        lb.load_batch("E"),
        allowance_micros=60_000,
        spend=spend,
        service_factory=counting_factory(mock_factory(), counter),
    )
    assert report["stopped"] == "allowance" and reads[0] == 1_000_000
    assert report["spent_delta_micros"] == reads[-1] - reads[0]
    assert len(report["sessions"]) == 3 and len(report["sessions_skipped"]) == 2


def test_a_session_that_crashes_is_reported_and_does_not_lose_the_others() -> None:
    data = lb.load_batch("E")
    two = data.model_copy(update={"sessions": data.sessions[:2]})
    calls = {"n": 0}

    def flaky() -> Service:
        calls["n"] += 1
        svc = mock_factory()()
        if calls["n"] == 1:

            async def broken(*a: Any, **k: Any) -> Any:
                raise RuntimeError("the model host went away")

            object.__setattr__(svc, "handle_event", broken)
        return svc

    report = run(two, service_factory=flaky)
    assert (
        report["sessions"][0]["stopped"] == "error"
        and "went away" in report["sessions"][0]["error"]
    )
    assert report["sessions"][1]["questions"], "the next idea still ran"
    assert checks_of(report)[("no_session_crashed", None)]["passed"] is False


def test_a_service_in_the_wrong_mode_stops_the_run_before_any_call() -> None:
    with pytest.raises(RuntimeError, match="refusing to go on"):
        run(lb.load_batch("E"), mode="live")  # a live run on a mock service must not start


def test_the_legacy_engine_cannot_be_measured_by_mistake(monkeypatch: pytest.MonkeyPatch) -> None:
    def legacy() -> Service:
        interview = dataclasses.replace(DEFAULT_CONFIG.interview, engine="legacy")
        config = dataclasses.replace(DEFAULT_CONFIG, interview=interview)
        return build_service(memory=True, clock=FakeClock(), compass_config=config)

    monkeypatch.delenv("COMPASS_INTERVIEW_ENGINE", raising=False)
    with pytest.raises(RuntimeError, match="planner engine"):
        run(lb.load_batch("E"), service_factory=legacy)


# ====================================================================================== the live guards
def test_this_module_cannot_switch_live_mode_on_by_itself() -> None:
    """Mock and replay settings are forced whatever the process environment says; nothing sets a switch."""
    source = Path(lb.__file__).read_text(encoding="utf-8")
    assert "ALLOW_LIVE_LLM" in source and "os.environ[" not in source and "putenv" not in source
    assert "setenv" not in source and "import openai" not in source
    for mode in ("mock", "replay"):
        svc = lb.default_service_factory(llm_mode=mode, clock=FakeClock())()  # type: ignore[arg-type]
        assert svc.mode_label == "mock"
    assert (
        load_settings().allow_live_llm is False
    )  # the offline guard of the root conftest is in force


# ============================================================================================ replay twin
def cassettes_recorded() -> bool:
    directory = load_settings().cassette_dir
    return directory.is_dir() and any(directory.glob("cmp.*__v*/*.json"))


@pytest.mark.parametrize("batch", lb.BATCH_IDS)
def test_replaying_the_recorded_live_batch_needs_no_cassette_that_is_missing(batch: str) -> None:
    if not cassettes_recorded():
        pytest.skip(
            f"no Compass cassettes recorded yet for batch {batch}: the lead records them in the live "
            "batch (MDE_RECORD_CASSETTES=1 in the outer environment), runs `just scan-secrets`, and "
            "this test then replays the batch offline"
        )
    report = run(
        lb.load_batch(batch),
        mode="replay",
        service_factory=lambda: lb.default_service_factory(llm_mode="replay", clock=FakeClock())(),
    )
    calls = [c for s in report["sessions"] for c in s["calls"]]
    missing = [c for c in calls if "CassetteMissing" in (c["reason"] or "")]
    if calls and len(missing) == len(calls):
        pytest.skip(f"cassettes exist, but none for batch {batch} yet")
    assert not missing, (
        f"batch {batch}: {len(missing)} of {len(calls)} calls have no cassette (a prompt changed since "
        f"the recording?): {sorted({c['spec_id'] for c in missing})}"
    )
    assert report["stopped"] is None


# =============================================================================== both fallback numbers
def test_the_fallback_check_counts_every_call_log_fallback_per_spec_not_only_the_blocks_count() -> (
    None
):
    from mde_compass.domain.interview import InterviewBlock
    from mde_compass.store.ports import CallLogEntry

    def entry(spec: str, outcome: str) -> CallLogEntry:
        return CallLogEntry(call_id=f"call_{spec}_{outcome}", spec_id=spec, outcome=outcome)  # type: ignore[arg-type]

    brief = Brief()
    brief.meta.interview = InterviewBlock(fallback_count=0)  # the block sees none of these
    sd = f_session(
        brief=brief,
        call_log=[
            entry("cmp.understand_turn", "fallback"),
            entry("cmp.understand_turn", "fallback"),
            entry("cmp.probe_handoff", "fallback"),
            entry("cmp.plan_questions", "ok"),
        ],
    )
    counts = lb.fallback_counts(sd)
    assert counts["interview_block"] == 0 and counts["calls"] == 3
    assert counts["calls_by_spec"] == {"cmp.probe_handoff": 1, "cmp.understand_turn": 2}
    check = next(c for c in lb.common_checks(sd, []) if c.id == "fallback_count")
    assert check.passed is False and check.value["calls_by_spec"]["cmp.understand_turn"] == 2
    clean = f_session(brief=brief, call_log=[entry("cmp.plan_questions", "ok")])
    assert next(c for c in lb.common_checks(clean, []) if c.id == "fallback_count").passed is True


def test_every_report_check_for_fallbacks_shows_both_numbers(e_report: dict[str, Any]) -> None:
    for c in e_report["checks"]:
        if c["id"] == "fallback_count":
            assert {"interview_block", "calls", "calls_by_spec"} <= set(c["value"])
    s = e_report["sessions"][0]
    assert s["brief"]["meta_interview"]["fallback_count"] == 0 and all(
        not c["fallback"] for c in s["calls"]
    )


# ================================================================================ "Ask me more" when ended early
def test_the_user_presses_ask_me_more_when_the_engine_ends_before_the_planted_turns() -> None:
    sdef = lb.load_batch("F").sessions[0].model_copy(update={"budget": "quick", "ask_more_max": 2})
    report = run(lb.load_batch("F").model_copy(update={"sessions": (sdef,)}))
    session = report["sessions"][0]
    assert [m["tag"] for m in session["messages"]] == [
        "ask_more",
        "pivot",
    ]  # the pivot turn was reached
    quiet = sdef.model_copy(update={"ask_more_max": 0})
    plain = run(lb.load_batch("F").model_copy(update={"sessions": (quiet,)}))
    assert len(session["answers"]) >= len(plain["sessions"][0]["answers"])
