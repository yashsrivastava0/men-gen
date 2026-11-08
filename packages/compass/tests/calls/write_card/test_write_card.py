"""cmp.write_card: schema, goldens, the real mock pipeline, the card rules, fallback for every frame."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from mde_compass.calls.base import CallRunner
from mde_compass.calls.testing import ScriptedGateway, make_client
from mde_compass.calls.write_card.adapter import WriteCardAdapter, build_input
from mde_compass.calls.write_card.fallback import fallback
from mde_compass.calls.write_card.mock import handle
from mde_compass.calls.write_card.schema import (
    FrameWire,
    OptionWire,
    ProfileWire,
    SlotDefWire,
    WriteCardInput,
    WriteCardOutput,
)
from mde_compass.calls.write_card.spec import SPEC
from mde_compass.calls.write_card.validate import structural, validate_card
from mde_compass.catalog.loader import load_catalog
from mde_compass.engine.ports import CardRequest, SlotView, TurnContext
from mde_core.errors import ProviderError
from mde_llm_gateway import to_strict_schema

C = load_catalog()
EXAMPLES = sorted(
    (Path(__file__).parents[3] / "src/mde_compass/calls/write_card/examples").glob("*.json")
)


def load(path: Path) -> dict:  # type: ignore[type-arg]
    return json.loads(path.read_text(encoding="utf-8"))


HINTS = ["Browse items", "Cart", "Track orders", "Saved list"]


def mk(
    frame: str,
    lang: str = "en",
    hints: list[str] | None = None,
    evidence: list[str] | None = None,
    **kw: object,
) -> WriteCardInput:
    f = C.frames[frame]
    return WriteCardInput(
        frame=FrameWire(
            id=f.id,
            purpose=f.purpose,
            guidance=f.guidance,
            card_type=f.card_type,
            option_count=f.option_count if f.card_type in ("choose_one", "look_signals") else None,
            max_question_words=f.max_question_words,
            max_option_words=f.max_option_words,
        ),
        slots=[
            SlotDefWire(
                slot_id=s,
                label=C.slots[s].label.en,
                value_kind=C.slots[s].value_schema.kind,
                options=C.slots[s].value_schema.options,
                fields=C.slots[s].value_schema.fields,
            )
            for s in f.slots
        ],
        option_source=str(kw.get("option_source", f.option_source)),
        hints=hints or [],
        evidence=evidence or [],
        must_confirm=False,
        language=lang,
        avoid_labels=list(kw.get("avoid", [])),  # type: ignore[call-overload]
        profile=ProfileWire(expertise="non_technical", formality="casual", language=lang),
        known_slots=[],
        question_number=2,
    )


def opt(i: str, label: str, **kw: object) -> OptionWire:
    base: dict[str, object] = {
        "basis": "suggestion",
        "recommended": False,
        "reason": "",
        "sets_json": "{}",
        "ticked": False,
    }
    base.update(kw)
    return OptionWire(id=i, label=label, **base)  # type: ignore[arg-type]


def card(
    *options: OptionWire,
    question: str = "How will it earn money?",
    why: str = "It decides the plan screens.",
) -> WriteCardOutput:
    return WriteCardOutput(question=question, why_we_ask=why, options=list(options), placeholder="")


def good3() -> list[OptionWire]:
    return [
        opt("A", "A small fee on each sale", recommended=True, reason="A simple place to start."),
        opt("B", "A monthly plan", sets_json='{"idea.business_model": "subscription"}'),
        opt("C", "Free to use, business pays"),
    ]


# --------------------------------------------------------------------------- schema, goldens, pipeline


def test_strict_schemas() -> None:
    to_strict_schema(WriteCardInput, max_depth=5)
    to_strict_schema(WriteCardOutput, max_depth=5)


def test_spec_matches_profile() -> None:
    s = SPEC.spec
    assert s.id == "cmp.write_card" and s.reasoning_effort == "high" and s.max_output_tokens == 2500
    assert s.fallback == "compass.write_card" and not s.tools and "evidence" in s.untrusted_inputs


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.stem)
def test_golden_examples(path: Path) -> None:
    data = load(path)
    inp = WriteCardInput.model_validate(data["input"])
    out = WriteCardOutput.model_validate(data["output"])
    assert handle(inp) == out
    assert validate_card(inp, out, C) == []


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.stem)
async def test_real_mock_pipeline_renders_templates(path: Path) -> None:
    data = load(path)
    inp = WriteCardInput.model_validate(data["input"])
    res = await CallRunner(make_client([SPEC])).run(
        SPEC, inp, session_id="ses_01", tenant_id="ten_01"
    )
    assert not res.used_fallback, res.reason
    assert res.output == WriteCardOutput.model_validate(data["output"])


# --------------------------------------------------------------------------- the fallback card, every frame


@pytest.mark.parametrize("frame", sorted(C.frames))
@pytest.mark.parametrize("lang", ["en", "hi", "hi-Latn"])
@pytest.mark.parametrize("hints", [[], HINTS])
def test_fallback_satisfies_every_rule(frame: str, lang: str, hints: list[str]) -> None:
    inp = mk(frame, lang, hints=hints)
    out = fallback(inp, C)
    assert validate_card(inp, out, C) == [], (frame, lang)
    ctype = C.frames[frame].card_type
    if ctype in ("choose_one", "look_signals"):
        assert len(out.options) == (C.frames[frame].option_count or 3)
    if out.options:
        assert sum(o.recommended for o in out.options) == 1
    if lang == "hi":
        assert any("ऀ" <= ch <= "ॿ" for ch in out.question)


def test_fallback_uses_typical_hints_honestly() -> None:
    inp = mk("features", "en", hints=HINTS, evidence=["I need to track orders"])
    out = fallback(inp, C)
    assert [o.label for o in out.options] == HINTS
    assert {o.label for o in out.options if o.ticked} == {"Track orders"}
    assert all(o.basis in ("typical", "your_words") for o in out.options)


def test_fallback_never_repeats_avoided_labels() -> None:
    first = fallback(mk("business_model"), C)
    again = fallback(mk("business_model", avoid=[first.options[0].label]), C)
    assert first.options[0].label not in [o.label for o in again.options]


# --------------------------------------------------------------------------- rule violations (the must-not cases)


def test_good_card_passes() -> None:
    assert validate_card(mk("business_model"), card(*good3()), C) == []


@pytest.mark.parametrize(
    ("label", "needle"),
    [
        ("Most people choose a monthly plan", "statistic"),
        ("A monthly plan, 80% pick it", "statistic"),
        ("Studies show a fee works best", "statistic"),
        ("Pay with PhonePe", "names the user did not use"),
        ("Real-time live location tracking", "capability"),
        ("You should pick the fee", "leading"),
        ("A very long label that goes well over eight words in total", "words"),
    ],
)
def test_bad_option_wording_is_rejected(label: str, needle: str) -> None:
    opts = good3()
    opts[1] = opt("B", label)
    errors = validate_card(mk("business_model"), card(*opts), C)
    assert errors and any(needle in e for e in errors), errors


def test_option_count_and_recommended_rules() -> None:
    inp = mk("business_model")
    assert any("exactly 3" in e for e in validate_card(inp, card(*good3()[:2]), C))
    two_rec = [o.model_copy(update={"recommended": True, "reason": "x"}) for o in good3()]
    assert any("exactly one recommended" in e for e in validate_card(inp, card(*two_rec), C))
    no_reason = good3()
    no_reason[0] = no_reason[0].model_copy(update={"reason": ""})
    assert any("reason" in e for e in validate_card(inp, card(*no_reason), C))
    dup = [
        good3()[0],
        good3()[1].model_copy(update={"label": "a small fee on each sale"}),
        good3()[2],
    ]
    assert any("distinct" in e for e in validate_card(inp, card(*dup), C))
    ticked = good3()
    ticked[2] = ticked[2].model_copy(update={"ticked": True})
    assert any("pre-ticked" in e for e in validate_card(inp, card(*ticked), C))


def test_question_length_and_language() -> None:
    inp = mk("business_model")
    long_q = " ".join(["word"] * 25) + "?"
    assert any("question has" in e for e in validate_card(inp, card(*good3(), question=long_q), C))
    assert any(
        "language" in e for e in validate_card(mk("business_model", "hi"), card(*good3()), C)
    )


def test_basis_must_be_honest() -> None:
    inp = mk("business_model")
    for basis in ("typical", "your_words", "your_document"):
        opts = good3()
        opts[1] = opts[1].model_copy(update={"basis": basis})
        assert any("basis" in e for e in validate_card(inp, card(*opts), C)), basis
    typical = mk("business_model", hints=["A monthly plan"])
    opts = good3()
    opts[1] = opts[1].model_copy(update={"basis": "typical"})
    assert validate_card(typical, card(*opts), C) == []


def test_user_named_brand_and_capability_are_allowed() -> None:
    inp = mk("business_model", evidence=["I want to take PhonePe payments and push notifications"])
    opts = good3()
    opts[1] = opt("B", "Pay with PhonePe", sets_json='{"idea.business_model": "subscription"}')
    assert validate_card(inp, card(*opts), C) == []


def test_checklist_conservative_ticks() -> None:
    inp = mk("features", hints=HINTS)
    opts = [opt(str(i), h, basis="typical", ticked=True) for i, h in enumerate(HINTS)]
    assert any(
        "too many pre-ticked" in e
        for e in validate_card(inp, card(*opts, question="Which do you want?"), C)
    )
    sug = [opt(str(i), h, basis="suggestion") for i, h in enumerate(HINTS)]
    sug[0] = sug[0].model_copy(update={"ticked": True})
    assert any(
        "suggestion must not be pre-ticked" in e
        for e in validate_card(inp, card(*sug, question="Which do you want?"), C)
    )


def test_sets_must_stay_inside_the_card() -> None:
    opts = good3()
    opts[1] = opt("B", "A monthly plan", sets_json='{"auth.methods": ["phone_otp"]}')
    assert any(
        "outside this card" in e for e in validate_card(mk("business_model"), card(*opts), C)
    )
    opts[1] = opt("B", "A monthly plan", sets_json="not json")
    assert structural(mk("business_model"), card(*opts))


def test_free_text_cards_need_a_placeholder_and_no_options() -> None:
    inp = mk("idea_in_your_words")
    assert any("placeholder" in e for e in validate_card(inp, card(question="What is it?"), C))
    assert any(
        "no options" in e
        for e in validate_card(
            inp, card(*good3(), question="What is it?").model_copy(update={"placeholder": "x"}), C
        )
    )


# --------------------------------------------------------------------------- adapter


def ctx(**kw: object) -> TurnContext:
    return TurnContext(session_id="ses_01", tenant_id="ten_01", **kw)  # type: ignore[arg-type]


def req(frame: str = "business_model", **kw: object) -> CardRequest:
    f = C.frames[frame]
    return CardRequest(
        frame_id=frame,
        slots=list(f.slots),
        card_type=f.card_type,
        option_source=f.option_source,
        **kw,
    )  # type: ignore[arg-type]


async def test_adapter_returns_a_domain_draft_from_the_model() -> None:
    gw = ScriptedGateway(card(*good3()))
    draft = await WriteCardAdapter(CallRunner(gw), C).write(ctx(), req())
    assert draft.question == "How will it earn money?" and len(draft.options) == 3
    assert (
        draft.options[1].sets == {"idea.business_model": "subscription"} and draft.language == "en"
    )


async def test_adapter_replaces_a_card_that_breaks_the_rules() -> None:
    bad = good3()
    bad[1] = opt("B", "Most people pick a monthly plan")
    draft = await WriteCardAdapter(CallRunner(ScriptedGateway(card(*bad))), C).write(ctx(), req())
    assert all("most people" not in o.label.lower() for o in draft.options)
    assert sum(o.recommended for o in draft.options) == 1 and len(draft.options) == 3


async def test_adapter_falls_back_on_gateway_error_in_hindi() -> None:
    draft = await WriteCardAdapter(CallRunner(ScriptedGateway(ProviderError("x"))), C).write(
        ctx(language="hi"), req()
    )
    assert draft.language == "hi" and any("ऀ" <= ch <= "ॿ" for ch in draft.question)


async def test_adapter_end_to_end_mock_for_every_frame() -> None:
    for frame in C.frames:  # one gateway per frame: the gateway rate-limits a busy scope
        adapter = WriteCardAdapter(CallRunner(make_client([SPEC])), C)
        draft = await adapter.write(
            ctx(), req(frame, hints=HINTS, evidence=["I sell cakes in Pune"])
        )
        assert draft.question and (draft.options or draft.placeholder)


def test_input_builder_redacts_and_compacts() -> None:
    secret = "sk-" + "A1b2C3d4" * 6
    c = ctx(
        slots=[SlotView(slot_id="idea.summary", value="a bakery", state="confirmed")], question_n=3
    )
    wire = build_input(C, c, req(evidence=[f"my key {secret}"]))
    assert secret not in wire.evidence[0] and wire.question_number == 4
    assert [k.slot_id for k in wire.known_slots] == ["idea.summary"]
    assert wire.frame.option_count == 3 and [s.slot_id for s in wire.slots] == [
        "idea.business_model"
    ]


# --------------------------------------------------------------------------- linearity


@pytest.mark.parametrize(
    "text",
    [
        "A" * 50_000,
        ("Word " * 10_000),
        "x. " * 20_000,
        "Pay " * 12_000 + "PhonePe",
        "\n" * 50_000,
        "जैसा " * 10_000,
    ],
)
def test_validators_are_linear(text: str) -> None:
    inp = mk("business_model", evidence=[text[:20_000]])
    opts = good3()
    opts[1] = opt("B", text[:60])
    start = time.perf_counter()
    validate_card(inp, card(*opts, question=text[:50_000], why=text[:50_000]), C)
    assert time.perf_counter() - start < 2.0
