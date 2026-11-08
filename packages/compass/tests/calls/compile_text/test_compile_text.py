"""Tests of ``cmp.compile_text``: schema, golden examples, real mock pipeline, adapter, must-not cases."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from mde_compass.calls.base import CallRunner
from mde_compass.calls.compile_text import mock
from mde_compass.calls.compile_text.adapter import CompileTextAdapter, build_input
from mde_compass.calls.compile_text.fallback import fallback
from mde_compass.calls.compile_text.schema import (
    CompileTextInput,
    CompileTextOutput,
    ItemRef,
    ItemText,
    SlotText,
)
from mde_compass.calls.compile_text.spec import SPEC
from mde_compass.calls.compile_text.validators import ungrounded_terms, validate
from mde_compass.calls.testing import ScriptedGateway, make_client
from mde_compass.catalog.loader import load_catalog
from mde_compass.domain.brief import Brief, FeatureRecord
from mde_compass.domain.common import BriefItem
from mde_compass.engine.ports import TurnContext
from mde_core.errors import ProviderError
from mde_llm_gateway import to_strict_schema

EXAMPLES = sorted(
    (Path(__file__).parents[3] / "src/mde_compass/calls/compile_text/examples").glob("*.json")
)


def _load(path: Path) -> tuple[CompileTextInput, CompileTextOutput]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return CompileTextInput.model_validate(data["input"]), CompileTextOutput.model_validate(
        data["output"]
    )


def _ctx(language: str = "en") -> TurnContext:
    return TurnContext(session_id="ses_1", tenant_id="t1", language=language)


def _brief() -> Brief:
    brief = Brief()
    brief.idea["summary"] = BriefItem(
        value="a way for my restaurant in Pune to take orders", status="confirmed"
    )
    brief.context["geo"] = BriefItem(value={"city": "Pune", "country": "India"}, status="confirmed")
    brief.audience["segments"] = BriefItem(value=["regular customers"], status="confirmed")
    brief.scope.append(
        FeatureRecord(id="FTR-001", name="Order tracking", intent="see where my order is")
    )
    return brief


def test_examples_exist() -> None:
    assert len(EXAMPLES) >= 3


def test_schemas_are_strict_safe() -> None:
    to_strict_schema(CompileTextInput, max_depth=5)
    to_strict_schema(CompileTextOutput, max_depth=5)


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.stem)
def test_golden_examples_are_valid_and_grounded(path: Path) -> None:
    inp, out = _load(path)
    assert validate(inp, out) == []
    assert CompileTextOutput.model_validate_json(out.model_dump_json()) == out
    assert CompileTextInput.model_validate_json(inp.model_dump_json()) == inp


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.stem)
def test_mock_handler_is_deterministic_and_grounded(path: Path) -> None:
    inp, _ = _load(path)
    first = mock.handle(inp)
    assert first == mock.handle(inp) == fallback(inp)
    assert validate(inp, first) == []
    assert 0 < len(first.summary) <= 160 and 0 < len(first.pitch) <= 400
    assert [d.item_id for d in first.descriptions] == [i.path for i in inp.items]


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.stem)
async def test_real_mock_pipeline(path: Path) -> None:
    inp, _ = _load(path)
    runner = CallRunner(make_client([SPEC]))
    res = await runner.run(SPEC, inp, session_id="ses_1", tenant_id="t1")
    assert not res.used_fallback, res.reason
    assert res.output == fallback(inp)


def test_fallback_hindi_uses_hindi_templates() -> None:
    inp, _ = _load(next(p for p in EXAMPLES if p.stem == "hi_clinic"))
    out = fallback(inp)
    assert out.summary == "क्लिनिक के लिए अपॉइंटमेंट बुकिंग"
    assert "इंदौर में" in out.pitch and "मरीज़ के लिए है" in out.pitch


def test_fallback_without_slots_never_invents() -> None:
    inp = CompileTextInput(
        language="en", slots=[], app_count=0, role_count=0, feature_count=0, items=[]
    )
    out = fallback(inp)
    assert validate(inp, out) == []
    assert out.descriptions == []


# --- the "must not" cases: compile never adds a fact that is not in the slots ----------------------------


def _inp() -> CompileTextInput:
    return _load(next(p for p in EXAMPLES if p.stem == "en_restaurant"))[0]


@pytest.mark.parametrize(
    ("summary", "pitch"),
    [
        ("An ordering app like Zomato for Pune.", "This app lets customers order."),
        ("An ordering app for Pune.", "This app already has 5000 customers."),
        ("An ordering app for Pune.", "This app lets customers pay with Razorpay."),
        ("An ordering app for Pune.", "Launches in Mumbai next month."),
    ],
)
def test_ungrounded_output_is_rejected(summary: str, pitch: str) -> None:
    out = CompileTextOutput(summary=summary, pitch=pitch, descriptions=[])
    assert ungrounded_terms(_inp(), out)
    assert validate(_inp(), out)


def test_numbers_from_counts_and_slots_are_allowed() -> None:
    inp = _inp()
    ok = CompileTextOutput(
        summary="An ordering app with 2 roles and 1 app.",
        pitch="It has 2 features.",
        descriptions=[],
    )
    assert validate(inp, ok) == []
    bad = CompileTextOutput(summary="An ordering app.", pitch="It has 3 apps.", descriptions=[])
    assert ungrounded_terms(inp, bad) == ["3"]


def test_structure_errors() -> None:
    inp = _inp()
    long = CompileTextOutput(summary="x" * 161, pitch="p", descriptions=[])
    assert any("summary longer" in e for e in validate(inp, long))
    unknown = CompileTextOutput(
        summary="An app.",
        pitch="It helps.",
        descriptions=[ItemText(item_id="scope.FTR-999", text="x")],
    )
    assert any("unknown item" in e for e in validate(inp, unknown))
    dup = CompileTextOutput(
        summary="An app.",
        pitch="It helps.",
        descriptions=[
            ItemText(item_id="scope.FTR-001", text="a"),
            ItemText(item_id="scope.FTR-001", text="b"),
        ],
    )
    assert any("duplicate" in e for e in validate(inp, dup))


_word = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ", min_size=1, max_size=12
)
_phrase = st.lists(_word, min_size=1, max_size=8).map(" ".join)


@settings(max_examples=60, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    idea=_phrase,
    audience=_phrase,
    geo=_phrase,
    feature=_phrase,
    intent=st.one_of(st.just(""), _phrase),
    lang=st.sampled_from(["en", "hi", "hi-Latn"]),
)
def test_fallback_never_adds_facts_property(
    idea: str, audience: str, geo: str, feature: str, intent: str, lang: str
) -> None:
    slots = [
        SlotText(slot_id="idea.category", label="Kind", value=idea, state="assumed"),
        SlotText(slot_id="audience.segments", label="Who", value=audience, state="confirmed"),
        SlotText(slot_id="context.geo", label="Where", value=geo, state="confirmed"),
    ]
    inp = CompileTextInput(
        language=lang,
        slots=slots,
        app_count=1,
        role_count=1,
        feature_count=1,
        items=[ItemRef(path="scope.FTR-001", name=feature, intent=intent)],
    )
    out = fallback(inp)
    assert ungrounded_terms(inp, out) == []
    assert validate(inp, out) == []


# --- the adapter -----------------------------------------------------------------------------------


def _good(inp: CompileTextInput) -> CompileTextOutput:
    return CompileTextOutput(
        summary="An ordering app for my restaurant in Pune.",
        pitch="This app lets regular customers in Pune order.",
        descriptions=[
            ItemText(item_id="scope.FTR-001", text="Order tracking: see where my order is.")
        ],
    )


def test_build_input_reads_brief_and_catalog() -> None:
    inp = build_input(_ctx(), _brief(), load_catalog())
    by_id = {s.slot_id: s for s in inp.slots}
    assert by_id["idea.summary"].state == "confirmed"
    assert by_id["context.geo"].value == "Pune, India"
    assert by_id["audience.segments"].value == "regular customers"
    assert inp.feature_count == 1 and inp.items[0].path == "scope.FTR-001"


async def test_adapter_returns_a_valid_model_answer() -> None:
    inp = build_input(_ctx(), _brief(), load_catalog())
    gw = ScriptedGateway(_good(inp))
    result = await CompileTextAdapter(CallRunner(gw), load_catalog()).compile(_ctx(), _brief())
    assert result.summary.startswith("An ordering app")
    assert result.descriptions[0].item_id == "scope.FTR-001"


@pytest.mark.parametrize(
    "bad_pitch", ["It serves 90000 users in Delhi.", "Built like Swiggy for Pune.", ""]
)
async def test_adapter_falls_back_when_the_validator_rejects(bad_pitch: str) -> None:
    inp = build_input(_ctx(), _brief(), load_catalog())
    bad = _good(inp).model_copy(update={"pitch": bad_pitch})
    runner = CallRunner(ScriptedGateway(bad))
    result = await CompileTextAdapter(runner, load_catalog()).compile(_ctx(), _brief())
    assert result.summary == fallback(inp).summary
    assert "Swiggy" not in result.pitch and "90000" not in result.pitch and result.pitch


async def test_adapter_falls_back_on_gateway_error() -> None:
    runner = CallRunner(ScriptedGateway(ProviderError("boom")))
    result = await CompileTextAdapter(runner, load_catalog()).compile(_ctx(), _brief())
    assert "Pune" in result.summary


async def test_adapter_end_to_end_with_the_real_mock_gateway() -> None:
    runner = CallRunner(make_client([SPEC]))
    result = await CompileTextAdapter(runner, load_catalog()).compile(_ctx(), _brief())
    assert result.summary and result.pitch and len(result.descriptions) == 1


# --- linear time -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "hostile",
    [
        "A" * 50_000,
        "a'" * 25_000,
        "1." * 25_000,
        "Ab " * 16_000,
        "x-" * 25_000,
        ". " * 25_000,
    ],
    ids=["letters", "apostrophes", "numbers", "words", "hyphens", "dots"],
)
def test_linear_time_on_hostile_input(hostile: str) -> None:
    inp = CompileTextInput(
        language="en",
        slots=[SlotText(slot_id="idea.summary", label="Idea", value=hostile, state="confirmed")],
        app_count=1,
        role_count=1,
        feature_count=1,
        items=[ItemRef(path="scope.FTR-001", name=hostile[:200], intent=hostile)],
    )
    started = time.monotonic()
    out = fallback(inp)
    errors = validate(inp, out)
    out2 = CompileTextOutput(summary="An app.", pitch=hostile[:400], descriptions=[])
    validate(inp, out2)
    assert time.monotonic() - started < 3.0
    assert errors == [] and len(out.summary) <= 160
