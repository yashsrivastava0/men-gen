"""Tests of ``cmp.interpret_change``: schema, golden examples, rules, validators, the adapter, 'must not' cases."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest

from mde_compass.calls.base import CallRunner
from mde_compass.calls.interpret_change import mock
from mde_compass.calls.interpret_change.adapter import (
    InterpretChangeAdapter,
    build_input,
    to_proposal,
)
from mde_compass.calls.interpret_change.digest import locked_paths, target_items
from mde_compass.calls.interpret_change.fallback import fallback
from mde_compass.calls.interpret_change.schema import (
    USE_VARIANTS,
    ChangeIn,
    ChangeOut,
    DigestItem,
    OpOut,
)
from mde_compass.calls.interpret_change.spec import SPEC
from mde_compass.calls.interpret_change.validate import check_change, parse_path, value_ok
from mde_compass.calls.testing import ScriptedGateway, make_client
from mde_compass.catalog.loader import load_catalog
from mde_compass.domain.brief import Brief, FeatureRecord
from mde_compass.domain.common import BriefItem, Provenance
from mde_compass.engine.ports import ChangeInput, HistoryLine, ResolvedTarget, TurnContext
from mde_llm_gateway import to_strict_schema

EXAMPLES = sorted(
    (Path(__file__).parents[3] / "src/mde_compass/calls/interpret_change/examples").glob("*.json")
)
CATALOG = load_catalog()
LOCKED_EV = [Provenance(type="user_text", ref="evt_01A", quote="x")]


def _item(
    path: str,
    label: str,
    value: Any,
    tier: str = "Default",
    kind: str = "text",
    options: list[str] | None = None,
) -> DigestItem:
    return DigestItem(
        path=path,
        label=label,
        value_json=json.dumps(value),
        tier=tier,  # type: ignore[arg-type]
        status="assumed",
        value_kind=kind,
        options=options or [],
    )


def _feature(
    fid: str, name: str, *, priority: str = "must", status: str = "in", tier: str = "Default"
) -> DigestItem:
    value = {"name": name, "intent": "", "apps": [], "priority": priority, "scope_status": status}
    return _item(f"scope.{fid}", name, value, tier=tier, kind="record")


def _inp(text: str, items: list[DigestItem], **kw: Any) -> ChangeIn:
    base: dict[str, Any] = {
        "text": text,
        "items": items,
        "target_kind": "section",
        "target_id": "scope",
        "add_sections": ["scope"],
    }
    base.update(kw)
    return ChangeIn.model_validate(base)


def _ctx() -> TurnContext:
    return TurnContext(session_id="ses_01TEST", tenant_id="t1")


def _brief() -> Brief:
    brief = Brief()
    brief.scope = [
        FeatureRecord(
            id="FTR-004",
            name="Group ordering",
            priority="must",
            tier="Locked",
            provenance=LOCKED_EV,
            status="confirmed",
        ),
        FeatureRecord(id="FTR-005", name="Order tracking", priority="nice"),
    ]
    brief.experience["locales"] = BriefItem(
        value=["en", "hi"], tier="Locked", provenance=LOCKED_EV, status="confirmed"
    )
    brief.idea["differentiator"] = BriefItem(value="fast", tier="Default", status="assumed")
    return brief


def test_examples_exist() -> None:
    assert len(EXAMPLES) >= 3


def test_schema_roundtrip_and_strict() -> None:
    inp = _inp("x", [_feature("FTR-001", "A")])
    assert ChangeIn.model_validate(inp.model_dump()) == inp
    out = ChangeOut(understood=False, ops=[], clarification="?", options=[], confidence=0.0)
    assert ChangeOut.model_validate_json(out.model_dump_json()) == out
    to_strict_schema(ChangeIn, max_depth=5)
    to_strict_schema(ChangeOut, max_depth=5)


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.stem)
def test_golden_examples(path: Path) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    wire = ChangeIn.model_validate(data["input"])
    out = mock.handle(wire)
    assert check_change(wire, out) == []
    proposal = to_proposal(out, wire)
    exp = data["expect"]
    assert proposal.understood is exp["understood"]
    assert [[o.op, o.path, o.value] for o in proposal.ops] == [
        [op, p, v] if v is not None else [op, p, None] for op, p, v in exp["ops"]
    ]
    assert proposal.touches_locked == exp["touches_locked"]


async def test_real_mock_pipeline() -> None:
    runner = CallRunner(make_client([SPEC]))
    wire = _inp("remove order tracking", [_feature("FTR-005", "Order tracking")])
    res = await runner.run(SPEC, wire, session_id="ses_01TEST", tenant_id="t1")
    assert not res.used_fallback, res.reason
    assert res.output.ops[0].op == "remove"
    assert res.output.ops[0].path == "scope.FTR-005"


# ---- rule-based interpretation --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "language"),
    [
        ("remove order tracking", "en"),
        ("I don't want order tracking", "en"),
        ("order tracking nahi chahiye", "hi-Latn"),
        ("order tracking हटा दो", "hi"),
    ],
)
def test_remove_in_three_languages(text: str, language: str) -> None:
    items = [_feature("FTR-004", "Group ordering"), _feature("FTR-005", "Order tracking")]
    out = fallback(_inp(text, items, language=language))
    assert out.understood and [(o.op, o.path) for o in out.ops] == [("remove", "scope.FTR-005")]


@pytest.mark.parametrize("level", ["later", "must", "nice"])
def test_mark_priority(level: str) -> None:
    items = [
        _feature(
            "FTR-004",
            "Group ordering",
            priority="nice",
            status="later" if level != "later" else "in",
        )
    ]
    text = {
        "later": "move group ordering to later",
        "must": "mark group ordering as must have",
        "nice": "make group ordering nice to have",
    }[level]
    out = fallback(_inp(text, items))
    assert out.understood
    assert (out.ops[0].op, out.ops[0].path, json.loads(out.ops[0].value_json)) == (
        "set",
        "scope.FTR-004.priority",
        level,
    )
    assert check_change(_inp(text, items), out) == []


def test_add_feature_uses_the_users_words() -> None:
    out = fallback(_inp("add a wishlist to the app", []))
    assert out.understood and out.ops[0].op == "add" and out.ops[0].path == "scope"
    assert json.loads(out.ops[0].value_json) == {"name": "Wishlist"}
    hi = fallback(_inp("wishlist add karo", [], language="hi-Latn"))
    assert hi.understood and json.loads(hi.ops[0].value_json) == {"name": "Wishlist"}
    assert not fallback(_inp("add", [])).understood


def test_make_locale_first() -> None:
    loc = _item("experience.locales", "App languages", ["en", "hi"], kind="locales")
    out = fallback(
        _inp("make the app Hindi first", [loc], target_kind="row", target_id="experience.locales")
    )
    assert out.understood and json.loads(out.ops[0].value_json) == ["hi", "en"]
    nested = _item(
        "experience.locales",
        "App languages",
        {"chat": "en", "app": ["en"], "right_to_left": False},
        kind="locales",
    )
    out2 = fallback(_inp("हिंदी पहले रखो", [nested], language="hi"))
    assert out2.understood and json.loads(out2.ops[0].value_json)["app"] == ["hi", "en"]


def test_dislike_assumption_asks_for_variants() -> None:
    item = _item("assumptions.ASM-001", "Phones only", {"claim": "Phones only"}, kind="record")
    for text, reason in (("I don't want this assumption", ""), ("hmm", "dont_want_assumption")):
        out = fallback(
            _inp(text, [item], target_kind="assumption", target_id="ASM-001", reason=reason)
        )
        assert not out.understood and out.ops == [] and out.options == [USE_VARIANTS]
        assert out.clarification


def test_too_much_on_a_section_asks_which_to_remove() -> None:
    items = [_feature("FTR-004", "Group ordering"), _feature("FTR-005", "Order tracking")]
    out = fallback(_inp("this is too much", items))
    assert not out.understood and out.ops == []
    assert out.options == ["Group ordering", "Order tracking"]


def test_unknown_text_is_not_guessed() -> None:
    for text in ("I didn't like the features list", "change it", "hmm", ""):
        out = fallback(
            _inp(
                text, [_feature("FTR-004", "Group ordering"), _feature("FTR-005", "Order tracking")]
            )
        )
        assert not out.understood and out.ops == []
        assert out.clarification == CATALOG.t("fallback.not_sure_rephrase", "en")


def test_ambiguous_target_never_returns_ops() -> None:
    items = [_feature("FTR-004", "Order tracking"), _feature("FTR-005", "Order history")]
    out = fallback(
        _inp("remove order tracking and order history", items, target_kind="unknown", target_id="")
    )
    assert not out.understood and out.ops == []
    assert out.options == ["Order tracking", "Order history"]
    assert out.clarification == 'Do you mean "Order tracking" or "Order history"?'
    # an unknown target with an unrelated request
    out2 = fallback(_inp("get rid of it", items, target_kind="unknown", target_id=""))
    assert not out2.understood and out2.ops == []


# ---- validators -----------------------------------------------------------------------------------


def _ok_out(*ops: OpOut) -> ChangeOut:
    return ChangeOut(understood=True, ops=list(ops), clarification="", options=[], confidence=0.9)


def test_parse_path_grammar() -> None:
    assert parse_path("idea.category")
    assert parse_path("experience.brand.name")
    assert parse_path("scope.FTR-004.priority")
    assert parse_path("scope")
    assert parse_path("nope.x") is None
    assert parse_path("idea") is None
    assert parse_path("scope.FTR-004.priority.extra") is None
    assert parse_path("scope.FTR 004") is None
    assert parse_path("idea." + "a" * 200) is None


def test_validator_rejects_bad_ops() -> None:
    feat = _feature("FTR-004", "Group ordering")
    cat = _item(
        "idea.category", "Kind of app", "commerce", kind="enum", options=["commerce", "booking"]
    )
    inp = _inp("x", [feat, cat])

    def op(o: str, path: str, value: Any = None) -> OpOut:
        return OpOut(op=o, path=path, value_json=json.dumps(value), old_value_json="null")  # type: ignore[arg-type]

    bad_cases = [
        _ok_out(op("set", "scope.FTR-999.priority", "later")),  # not among the items
        _ok_out(op("set", "scope.FTR-004.priority", "urgent")),  # not an allowed enum value
        _ok_out(op("set", "scope.FTR-004.tier", "Locked")),  # not an editable field
        _ok_out(op("set", "idea.category", "gaming")),  # not in options
        _ok_out(op("set", "scope.FTR-004", {"name": "x"})),  # whole record
        _ok_out(op("remove", "idea.category")),  # remove needs a list record
        _ok_out(op("clear", "scope.FTR-004")),  # clear a record
        _ok_out(op("add", "apps", {"name": "x"})),  # section not allowed here
        _ok_out(op("add", "scope", {"name": ""})),  # empty name
        _ok_out(op("add", "scope", {"id": "FTR-9", "name": "x"})),  # id is assigned by code
        _ok_out(op("set", "bogus.path", "x")),
        _ok_out(*[op("set", "scope.FTR-004.priority", "later")] * 2),  # duplicate
    ]
    for out in bad_cases:
        assert check_change(inp, out), out
    good = _ok_out(
        op("set", "idea.category", "booking"), op("set", "scope.FTR-004.priority", "later")
    )
    assert check_change(_inp("x", [feat, cat], target_kind="section"), good) == []
    assert any(
        "ambiguous target" in e
        for e in check_change(_inp("x", [feat, cat], target_kind="unknown"), good)
    )
    too_many = _ok_out(*[op("set", "idea.category", "booking")] * 9)
    assert any("more than 8" in e for e in check_change(inp, too_many))


def test_validator_not_understood_rules() -> None:
    inp = _inp("x", [])
    assert (
        check_change(
            inp,
            ChangeOut(
                understood=False,
                ops=[],
                clarification="Which one?",
                options=["a", "b"],
                confidence=0.1,
            ),
        )
        == []
    )
    assert check_change(
        inp, ChangeOut(understood=False, ops=[], clarification="", options=[], confidence=0.0)
    )
    ops = [OpOut(op="clear", path="idea.x", value_json="null", old_value_json="null")]
    assert check_change(
        inp, ChangeOut(understood=False, ops=ops, clarification="?", options=[], confidence=0.0)
    )
    assert check_change(
        inp, ChangeOut(understood=True, ops=[], clarification="", options=[], confidence=0.5)
    )
    four = ChangeOut(
        understood=False, ops=[], clarification="?", options=list("abcd"), confidence=0.0
    )
    assert check_change(inp, four)


def test_value_ok_by_kind() -> None:
    assert value_ok("enum", ["a", "b"], "a") is None
    assert value_ok("enum", ["a", "b"], "c")
    assert value_ok("enum_list", ["a", "b"], ["a", "b"]) is None
    assert value_ok("enum_list", ["a"], ["z"])
    assert value_ok("text", [], "hello") is None and value_ok("text", [], 5)
    assert value_ok("bool", [], True) is None and value_ok("bool", [], "yes")
    assert value_ok("locales", [], ["hi", "en"]) is None and value_ok("locales", [], {"bogus": 1})
    assert value_ok("object_list", [], [{"a": 1}]) is None and value_ok("object_list", [], ["a"])


# ---- digest and adapter ---------------------------------------------------------------------------


def test_digest_targets() -> None:
    brief = _brief()
    section = target_items(brief, ResolvedTarget(kind="section", id="features"), CATALOG)
    assert [i.path for i in section] == ["scope.FTR-004", "scope.FTR-005"]
    row = target_items(brief, ResolvedTarget(kind="feature", id="FTR-005"), CATALOG)
    assert [i.path for i in row] == ["scope.FTR-005"]
    slot = target_items(brief, ResolvedTarget(kind="slot", id="locales"), CATALOG)
    assert [i.path for i in slot] == ["experience.locales"] and slot[0].tier == "Locked"
    assert slot[0].value_kind == "locales"
    broad = target_items(brief, ResolvedTarget(kind="unknown"), CATALOG)
    assert {"scope.FTR-004", "experience.locales", "idea.differentiator"} <= {i.path for i in broad}
    assert target_items(brief, ResolvedTarget(kind="row", id="nope.x"), CATALOG) == []


def test_locked_paths_by_code() -> None:
    items = [_feature("FTR-004", "A", tier="Locked"), _feature("FTR-005", "B")]
    paths = locked_paths(
        [("set", "scope.FTR-004.priority"), ("remove", "scope.FTR-005"), ("add", "scope")], items
    )
    assert paths == ["scope.FTR-004"]


async def test_adapter_computes_touches_locked_even_if_model_omits_it() -> None:
    model_out = _ok_out(
        OpOut(
            op="set", path="scope.FTR-004.priority", value_json='"later"', old_value_json='"must"'
        )
    )
    adapter = InterpretChangeAdapter(CallRunner(ScriptedGateway(model_out)), CATALOG)
    change = ChangeInput(
        text="move group ordering to later", target=ResolvedTarget(kind="feature", id="FTR-004")
    )
    proposal = await adapter.interpret(_ctx(), change, _brief())
    assert proposal.understood and proposal.touches_locked == ["scope.FTR-004"]
    assert proposal.ops[0].old_value == "must"  # filled from the digest, not from the model
    assert proposal.ops[0].value == "later"


async def test_adapter_rejected_output_uses_fallback_and_never_applies() -> None:
    bad = _ok_out(OpOut(op="set", path="idea.made_up", value_json='"x"', old_value_json="null"))
    gateway = ScriptedGateway(bad)
    adapter = InterpretChangeAdapter(CallRunner(gateway), CATALOG)
    change = ChangeInput(
        text="I didn't like the features list", target=ResolvedTarget(kind="section", id="scope")
    )
    proposal = await adapter.interpret(_ctx(), change, _brief())
    assert not proposal.understood and proposal.ops == [] and proposal.clarification
    assert gateway.calls[0][0] == "cmp.interpret_change"


async def test_adapter_ambiguous_target_returns_clarification_no_ops() -> None:
    adapter = InterpretChangeAdapter(CallRunner(make_client([SPEC])), CATALOG)
    change = ChangeInput(text="change it", target=ResolvedTarget(kind="unknown"))
    proposal = await adapter.interpret(_ctx(), change, _brief())
    assert not proposal.understood and proposal.ops == [] and proposal.clarification


async def test_adapter_full_mock_flow_marks_locked() -> None:
    adapter = InterpretChangeAdapter(CallRunner(make_client([SPEC])), CATALOG)
    ctx = TurnContext(session_id="ses_01TEST", tenant_id="t1", language="en")
    change = ChangeInput(
        text="make the app Hindi first",
        target=ResolvedTarget(kind="slot", id="locales"),
        history_slice=[
            HistoryLine(
                turn_id="evt_01A",
                role="user",
                text="English and Hindi",
                path="experience.locales",
                value=["en", "hi"],
            )
        ],
    )
    proposal = await adapter.interpret(ctx, change, _brief())
    assert proposal.understood
    assert (proposal.ops[0].op, proposal.ops[0].path, proposal.ops[0].value) == (
        "set",
        "experience.locales",
        ["hi", "en"],
    )
    assert proposal.touches_locked == ["experience.locales"]
    wire = build_input(ctx, change, _brief(), CATALOG)
    assert wire.history[0].value_json == '["en", "hi"]'


async def test_adapter_use_variants_marker_passes_through() -> None:
    adapter = InterpretChangeAdapter(CallRunner(make_client([SPEC])), CATALOG)
    brief = _brief()
    change = ChangeInput(
        text="I don't want this assumption",
        target=ResolvedTarget(kind="slot", id="idea.differentiator"),
        reason="dont_want_assumption",
    )
    proposal = await adapter.interpret(_ctx(), change, brief)
    assert not proposal.understood and proposal.options == [USE_VARIANTS] and proposal.ops == []


def test_regex_functions_are_linear_on_hostile_input() -> None:
    items = [_feature("FTR-004", "Group ordering")]
    hostile = [
        "add " + " " * 50_000,
        "add" + " a" * 25_000,
        "x" * 50_000,
        "हटा " * 12_500,
        ". " * 25_000,
        "to" * 25_000,
    ]
    start = time.perf_counter()
    for text in hostile:
        fallback(_inp(text, items))
        fallback(_inp(text, items, target_kind="unknown"))
        parse_path(text)
    assert time.perf_counter() - start < 5.0


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.stem)
def test_prompt_renders_with_untrusted_data_wrapped(path: Path) -> None:
    from mde_llm_gateway.render import PromptRenderer

    wire = ChangeIn.model_validate(json.loads(path.read_text(encoding="utf-8"))["input"])
    rendered = PromptRenderer().render(SPEC.spec, wire)
    assert "never apply" in rendered.system.casefold()
    assert rendered.user.count("<untrusted_content") >= 2 and "{{" not in rendered.user
    assert set(rendered.untrusted_fields) == {"text", "history", "items"}


@pytest.mark.parametrize(
    "text",
    ["I want video calls", "I want it simpler", "don't add reviews", "do not include a cart"],
)
def test_loose_wants_are_not_turned_into_added_features(text: str) -> None:
    out = fallback(_inp(text, [], target_kind="unknown", target_id=""))
    assert not out.understood and out.ops == []
