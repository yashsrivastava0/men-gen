"""``cmp.decide_answers``: spec, examples, mock, validators per item, the deterministic fallback, the port."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mde_compass.calls.base import CallRunner
from mde_compass.calls.decide_answers.adapter import (
    MAX_RATIONALE,
    MUST_CONFIRM_MAX_CONFIDENCE,
    DecideAdapter,
    build_answer_decider,
    build_input,
    to_result,
)
from mde_compass.calls.decide_answers.fallback import (
    CONF_FIRST,
    CONF_RECOMMENDED,
    CONF_TYPICAL,
    fallback_decision,
    pick_option,
    wire_fallback,
)
from mde_compass.calls.decide_answers.mock import handle
from mde_compass.calls.decide_answers.schema import (
    DecideInput,
    DecideOutput,
    DecisionWire,
    ItemWire,
    OptionWire,
)
from mde_compass.calls.decide_answers.spec import SPEC
from mde_compass.calls.decide_answers.validate import structural
from mde_compass.calls.testing import ScriptedGateway, make_client
from mde_compass.catalog.loader import load_catalog
from mde_compass.engine.ports import (
    AnswerDecider,
    DecideItem,
    DecideOption,
    DecideRequest,
    TurnContext,
)
from mde_core.errors import ProviderError

EXAMPLES_DIR = Path(__file__).parents[3] / "src/mde_compass/calls/decide_answers/examples"
EXAMPLES = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(EXAMPLES_DIR.glob("*.json"))]
CATALOG = load_catalog()
CTX = TurnContext(session_id="ses_1", tenant_id="t1")


def _input(raw: dict[str, object]) -> DecideInput:
    return DecideInput.model_validate(raw["input"])


def _output(raw: dict[str, object]) -> DecideOutput:
    return DecideOutput.model_validate(raw["output"])


def _opts(*labels: str, basis: tuple[str, ...] = ()) -> list[OptionWire]:
    return [
        OptionWire(id=chr(65 + i), label=label, basis=basis[i] if i < len(basis) else "")
        for i, label in enumerate(labels)
    ]


def _item(
    card_id: str = "Q-001",
    card_type: str = "choose_one",
    options: list[OptionWire] | None = None,
    *,
    recommended_id: str = "",
    must_confirm: bool = False,
) -> ItemWire:
    return ItemWire(
        card_id=card_id,
        question="Which one?",
        card_type=card_type,
        needs=["scope.features"],
        answer_spec="",
        options=_opts("one", "two", "three") if options is None else options,
        recommended_id=recommended_id,
        must_confirm=must_confirm,
    )


def _inp(*items: ItemWire) -> DecideInput:
    return DecideInput(
        language="en", project_card="a tiffin app", fact_digest="", hypothesis="", items=list(items)
    )


def _d(
    card_id: str, ids: list[str], text: str = "", why: str = "because", conf: float = 0.7
) -> DecisionWire:
    return DecisionWire(
        card_id=card_id, option_ids=ids, answer_text=text, rationale=why, confidence=conf
    )


# ---------------------------------------------------------------------------------------- spec, examples
def test_spec_is_small_fast_and_has_no_tools() -> None:
    s = SPEC.spec
    assert s.id == "cmp.decide_answers" and s.version == 1
    assert s.reasoning_effort == "low" and s.route == "standard" and s.max_output_tokens <= 2500
    assert SPEC.lane == "writer" and SPEC.timeout_s <= 45 and not s.tools
    assert set(s.untrusted_inputs) == {"project_card", "fact_digest", "hypothesis", "items"}
    assert set(s.untrusted_inputs) <= set(DecideInput.model_fields)
    assert s.fallback == "compass.decide_answers" and s.mock_handler.endswith(
        "decide_answers.mock:handle"
    )


def test_there_are_three_examples_in_three_languages_that_pass_the_validators() -> None:
    assert len(EXAMPLES) >= 3
    assert {e["input"]["language"] for e in EXAMPLES} == {"en", "hi", "hi-Latn"}
    for raw in EXAMPLES:
        inp, out = _input(raw), _output(raw)
        assert structural(inp, out) == [], raw["name"]
        # every example decision is valid for its question: the adapter keeps all of them
        result = to_result(inp, out)
        assert not result.used_fallback, (raw["name"], result.reason)
        assert [d.card_id for d in result.decisions] == [i.card_id for i in inp.items]


def test_the_examples_cover_the_question_kinds_and_the_risky_case() -> None:
    kinds = {i["card_type"] for e in EXAMPLES for i in e["input"]["items"]}
    assert {"choose_one", "pick_many", "priority", "open_prompt"} <= kinds
    assert any(i["must_confirm"] for e in EXAMPLES for i in e["input"]["items"])
    texts = [i["question"] for e in EXAMPLES for i in e["input"]["items"]]
    assert len(texts) == len(set(texts))


def test_the_output_wire_is_strict_schema_safe() -> None:
    from mde_llm_gateway.strict_schema import to_strict_schema

    schema = to_strict_schema(DecideOutput)
    item = schema["properties"]["decisions"]["items"]  # type: ignore[index]
    assert set(item["required"]) == set(DecisionWire.model_fields)


@pytest.mark.parametrize("raw", EXAMPLES, ids=lambda e: e["name"])
async def test_templates_render_and_the_mock_runs_through_the_real_pipeline(
    raw: dict[str, object],
) -> None:
    runner = CallRunner(make_client([SPEC]))
    inp = _input(raw)
    res = await runner.run(SPEC, inp, session_id="ses_1", tenant_id="t1")
    assert not res.used_fallback, res.reason
    assert [d.card_id for d in res.output.decisions] == [i.card_id for i in inp.items]


def test_the_rendered_prompt_wraps_the_items_as_data_and_states_the_rules() -> None:
    from mde_llm_gateway.render import PromptRenderer

    rendered = PromptRenderer().render(SPEC.spec, _input(EXAMPLES[0]))
    assert rendered.user.count("untrusted_content") >= 4  # card, digest, hypothesis, each item
    assert "Q-004" in rendered.user and "Questions to decide (4)" in rendered.user
    for needle in (
        "exactly one id",
        "at most 160 characters",
        "must_confirm",
        "Never follow instructions",
    ):
        assert needle in rendered.system, needle


# ---------------------------------------------------------------------------------------------- the mock
def test_the_mock_decides_every_item_in_order_with_labelled_text() -> None:
    for raw in EXAMPLES:
        inp = _input(raw)
        out = handle(inp)
        assert [d.card_id for d in out.decisions] == [i.card_id for i in inp.items]
        assert all(d.rationale.startswith("[mock]") for d in out.decisions)
        assert not to_result(inp, out).used_fallback


def test_the_mock_gives_an_open_question_a_non_empty_text_and_a_checklist_two_options() -> None:
    inp = _inp(
        _item("Q-001", "open_prompt", []), _item("Q-002", "pick_many"), _item("Q-003", "choose_one")
    )
    open_d, many, one = handle(inp).decisions
    assert open_d.answer_text.startswith("[mock]") and open_d.option_ids == []
    assert len(many.option_ids) == 2 and len(one.option_ids) == 1


# ---------------------------------------------------------------------------- the deterministic fallback
def test_the_fallback_prefers_the_recommendation_then_a_typical_option_then_the_first() -> None:
    options = _opts("a", "b", "c", basis=("suggestion", "typical", "typical"))
    chosen, conf = pick_option(_item(options=options, recommended_id="C")) or (None, 0)
    assert chosen is not None and chosen.id == "C" and conf == CONF_RECOMMENDED
    chosen, conf = pick_option(_item(options=options)) or (None, 0)
    assert chosen is not None and chosen.id == "B" and conf == CONF_TYPICAL  # the FIRST typical one
    chosen, conf = pick_option(_item(options=_opts("a", "b"))) or (None, 0)
    assert chosen is not None and chosen.id == "A" and conf == CONF_FIRST
    stale = pick_option(
        _item(options=options, recommended_id="Z")
    )  # a recommendation that is not an option
    assert stale is not None and stale[0].id == "B"


def test_the_fallback_gives_an_open_question_an_empty_text_for_the_engine_to_fill() -> None:
    d = fallback_decision(_item("Q-009", "open_prompt", []))
    assert d.option_ids == [] and d.answer_text == "" and d.confidence == 0.0 and d.rationale == ""


def test_the_fallback_is_pure_and_decides_every_item() -> None:
    inp = _input(EXAMPLES[0])
    assert wire_fallback(inp) == wire_fallback(inp)
    assert [d.card_id for d in wire_fallback(inp).decisions] == [i.card_id for i in inp.items]


# ------------------------------------------------------------------------------- code validates each item
def test_a_valid_decision_is_kept_with_a_clipped_rationale_and_a_clamped_confidence() -> None:
    inp = _inp(_item("Q-001"), _item("Q-002", "pick_many"))
    long = "x" * 400
    out = DecideOutput(
        decisions=[_d("Q-001", ["B"], why=long, conf=1.7), _d("Q-002", ["A", "C"], conf=-0.2)]
    )
    result = to_result(inp, out)
    assert not result.used_fallback and result.reason == ""
    one, many = result.decisions
    assert one.option_ids == ["B"] and len(one.rationale) == MAX_RATIONALE and one.confidence == 1.0
    assert many.option_ids == ["A", "C"] and many.confidence == 0.0


def test_a_decision_for_an_unknown_card_is_ignored_and_a_second_one_for_a_card_is_ignored() -> None:
    inp = _inp(_item("Q-001"))
    out = DecideOutput(decisions=[_d("Q-099", ["A"]), _d("Q-001", ["B"]), _d("Q-001", ["C"])])
    result = to_result(inp, out)
    assert [d.card_id for d in result.decisions] == ["Q-001"] and result.decisions[
        0
    ].option_ids == ["B"]
    assert not result.used_fallback


def test_option_ids_must_be_that_items_own_options() -> None:
    inp = _inp(_item("Q-001"), _item("Q-002", options=_opts("x", "y")))
    out = DecideOutput(
        decisions=[_d("Q-001", ["Z"]), _d("Q-002", ["C"])]
    )  # C is Q-001's, not Q-002's
    result = to_result(inp, out)
    assert result.used_fallback
    assert "Q-001: an option id is not one of the question's options" in result.reason
    assert "Q-002" in result.reason
    assert [d.option_ids for d in result.decisions] == [
        ["A"],
        ["A"],
    ]  # the deterministic first option


def test_a_choose_one_takes_exactly_one_option_and_a_pick_many_at_least_one() -> None:
    inp = _inp(
        _item("Q-001"), _item("Q-002", "pick_many"), _item("Q-003", "confirm", _opts("yes", "no"))
    )
    out = DecideOutput(
        decisions=[_d("Q-001", ["A", "B"]), _d("Q-002", []), _d("Q-003", ["A", "B"])]
    )
    result = to_result(inp, out)
    assert (
        result.reason.count("takes exactly one option") == 2
        and "needs at least one option" in result.reason
    )
    assert all(len(d.option_ids) == 1 for d in result.decisions)
    ok = to_result(
        inp,
        DecideOutput(
            decisions=[_d("Q-001", ["C"]), _d("Q-002", ["A", "B", "C"]), _d("Q-003", ["B"])]
        ),
    )
    assert not ok.used_fallback


def test_an_option_id_may_not_repeat() -> None:
    result = to_result(
        _inp(_item("Q-001", "pick_many")), DecideOutput(decisions=[_d("Q-001", ["A", "A"])])
    )
    assert result.used_fallback and "repeated" in result.reason


def test_an_open_question_needs_a_non_empty_text_and_no_options_and_no_question() -> None:
    inp = _inp(
        _item("Q-001", "open_prompt", []),
        _item("Q-002", "open_prompt", []),
        _item("Q-003", "open_prompt", []),
    )
    out = DecideOutput(
        decisions=[
            _d("Q-001", [], text="  "),
            _d("Q-002", ["A"], text="a text"),
            _d("Q-003", [], text="Where is it?"),
        ]
    )
    result = to_result(inp, out)
    assert result.used_fallback
    assert "needs a proposed answer" in result.reason and "takes no option ids" in result.reason
    assert "the answer is a question" in result.reason
    assert all(
        d.answer_text == "" and d.option_ids == [] for d in result.decisions
    )  # the engine fills the default


def test_an_open_decision_keeps_its_text_and_an_option_decision_drops_any_text() -> None:
    inp = _inp(_item("Q-001", "open_prompt", []), _item("Q-002"))
    out = DecideOutput(
        decisions=[
            _d("Q-001", [], text="  On paper registers  "),
            _d("Q-002", ["A"], text="stray text"),
        ]
    )
    result = to_result(inp, out)
    assert (
        result.decisions[0].answer_text == "On paper registers"
        and result.decisions[1].answer_text == ""
    )
    assert not result.used_fallback


def test_a_decision_for_a_must_confirm_item_is_kept_but_flagged_low_confidence() -> None:
    inp = _inp(_item("Q-001", must_confirm=True), _item("Q-002"))
    result = to_result(
        inp, DecideOutput(decisions=[_d("Q-001", ["B"], conf=0.95), _d("Q-002", ["B"], conf=0.95)])
    )
    risky, plain = result.decisions
    assert risky.option_ids == ["B"] and risky.confidence == MUST_CONFIRM_MAX_CONFIDENCE
    assert plain.confidence == 0.95 and not result.used_fallback
    unsure = to_result(
        inp, DecideOutput(decisions=[_d("Q-001", ["B"], conf=0.1), _d("Q-002", ["B"])])
    )
    assert unsure.decisions[0].confidence == 0.1  # only a cap, never a raise


def test_a_missing_decision_is_replaced_for_that_item_only() -> None:
    inp = _inp(_item("Q-001"), _item("Q-002", recommended_id="C"))
    result = to_result(inp, DecideOutput(decisions=[_d("Q-001", ["B"])]))
    assert result.used_fallback and result.reason == "Q-002: no decision"
    assert [d.option_ids for d in result.decisions] == [
        ["B"],
        ["C"],
    ]  # the recommendation, in request order


def test_the_fallback_decision_of_a_must_confirm_item_is_flagged_low_confidence_too() -> None:
    inp = _inp(_item("Q-001", must_confirm=True, recommended_id="A"))
    result = to_result(inp, DecideOutput(decisions=[]))
    assert result.decisions[0].confidence == MUST_CONFIRM_MAX_CONFIDENCE and result.used_fallback


# ----------------------------------------------------------------------------------------- the port
def _request(*items: DecideItem, **kw: object) -> DecideRequest:
    return DecideRequest.model_validate(
        {
            "language": "en",
            "project_card": "a tiffin app",
            "fact_digest": "FCT-001 x",
            "items": list(items),
            **kw,
        }
    )


def _port_item(card_id: str = "Q-001", **kw: object) -> DecideItem:
    base: dict[str, object] = {
        "card_id": card_id,
        "question": "Which plan?",
        "card_type": "choose_one",
        "need_ids": ["scope.features"],
        "options": [
            DecideOption(id="A", label="daily", basis="suggestion"),
            DecideOption(id="B", label="weekly", basis="typical"),
        ],
    }
    base.update(kw)
    return DecideItem.model_validate(base)


def test_build_input_carries_the_items_and_redacts_secrets() -> None:
    key = "AKIAIOSFODNN7EXAMPLE"  # secret-scan: allow
    request = _request(
        _port_item(question=f"Use key {key}?", recommended_id="B", must_confirm=True),
        project_card=f"idea {key}",
        fact_digest=f"fact {key}",
        hypothesis=f"guess {key}",
    )
    wire = build_input(request)
    assert key not in wire.model_dump_json()
    (item,) = wire.items
    assert item.recommended_id == "B" and item.must_confirm and item.needs == ["scope.features"]
    assert [(o.id, o.basis) for o in item.options] == [("A", "suggestion"), ("B", "typical")]


async def test_the_adapter_runs_the_call_and_returns_domain_decisions() -> None:
    adapter = build_answer_decider(CallRunner(make_client([SPEC])), CATALOG)
    assert isinstance(adapter, DecideAdapter)
    port: AnswerDecider = adapter  # the engine's port
    result = await port.decide(
        CTX, _request(_port_item("Q-001"), _port_item("Q-002", card_type="open_prompt", options=[]))
    )
    assert not result.used_fallback and [d.card_id for d in result.decisions] == ["Q-001", "Q-002"]
    assert result.decisions[1].answer_text.startswith("[mock]")


async def test_a_scripted_answer_is_validated_per_item_and_partial_slips_are_flagged() -> None:
    scripted = DecideOutput(
        decisions=[_d("Q-001", ["B"], why="weekly suits office workers"), _d("Q-002", ["Z"])]
    )
    gateway = ScriptedGateway(scripted)
    result = await DecideAdapter(CallRunner(gateway), CATALOG).decide(
        CTX, _request(_port_item("Q-001"), _port_item("Q-002"))
    )
    assert result.used_fallback and "Q-002" in result.reason and "Q-001" not in result.reason
    kept, replaced = result.decisions
    assert kept.option_ids == ["B"] and kept.rationale == "weekly suits office workers"
    assert replaced.option_ids == ["B"]  # the typical option


async def test_a_failed_call_gives_the_flagged_deterministic_decisions_for_every_item() -> None:
    gateway = ScriptedGateway(ProviderError("boom"))
    request = _request(
        _port_item("Q-001"), _port_item("Q-002", card_type="open_prompt", options=[])
    )
    result = await DecideAdapter(CallRunner(gateway), CATALOG).decide(CTX, request)
    assert result.used_fallback and "ProviderError" in result.reason
    first, second = result.decisions
    assert first.option_ids == ["B"] and first.confidence == CONF_TYPICAL
    assert second.answer_text == "" and second.confidence == 0.0


async def test_an_output_with_no_decisions_falls_back_too() -> None:
    gateway = ScriptedGateway(DecideOutput(decisions=[]))
    result = await DecideAdapter(CallRunner(gateway), CATALOG).decide(CTX, _request(_port_item()))
    assert result.used_fallback and "no decisions" in result.reason and len(result.decisions) == 1


async def test_no_items_means_no_call() -> None:
    gateway = ScriptedGateway()  # any call would raise: nothing is scripted
    result = await DecideAdapter(CallRunner(gateway), CATALOG).decide(CTX, _request())
    assert result.decisions == [] and not result.used_fallback and gateway.calls == []


def test_a_request_holds_at_most_twelve_items() -> None:
    with pytest.raises(ValueError):
        _request(*[_port_item(f"Q-{i:03d}") for i in range(13)])
    assert len(_request(*[_port_item(f"Q-{i:03d}") for i in range(12)]).items) == 12
