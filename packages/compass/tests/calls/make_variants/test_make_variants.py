"""Tests of ``cmp.make_variants``: schema, golden examples, validators, the adapter and the 'must not' cases."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest

from mde_compass.calls.base import CallRunner
from mde_compass.calls.make_variants import mock
from mde_compass.calls.make_variants.adapter import (
    MakeVariantsAdapter,
    build_input,
    family_hints,
    slot_for_path,
)
from mde_compass.calls.make_variants.fallback import fallback
from mde_compass.calls.make_variants.schema import VariantOut, VariantsIn, VariantsOut
from mde_compass.calls.make_variants.spec import SPEC
from mde_compass.calls.make_variants.validate import (
    canon,
    check_variants,
    expected_count,
    near_duplicates,
)
from mde_compass.calls.testing import ScriptedGateway, make_client
from mde_compass.catalog.loader import load_catalog
from mde_compass.engine.ports import SlotView, TurnContext
from mde_llm_gateway import to_strict_schema

EXAMPLES = sorted(
    (Path(__file__).parents[3] / "src/mde_compass/calls/make_variants/examples").glob("*.json")
)
CATALOG = load_catalog()
SHOP = SlotView(
    slot_id="idea.summary", value="an online shop with a catalog and cart", state="confirmed"
)


def _ctx(**kw: Any) -> TurnContext:
    base: dict[str, Any] = {"session_id": "ses_01TEST", "tenant_id": "t1"}
    base.update(kw)
    return TurnContext.model_validate(base)


def _v(
    label: str, value: Any, basis: str = "suggestion", reason: str = "An alternative."
) -> VariantOut:
    return VariantOut(label=label, value_json=json.dumps(value), reason=reason, basis=basis)  # type: ignore[arg-type]


def _inp(**kw: Any) -> VariantsIn:
    base: dict[str, Any] = {
        "path": "idea.business_model",
        "current_value_json": '"per_transaction_fee"',
        "value_kind": "text",
    }
    base.update(kw)
    return VariantsIn.model_validate(base)


def _three(*items: tuple[str, Any]) -> VariantsOut:
    return VariantsOut(variants=[_v(label, value) for label, value in items])


GOOD = _three(
    ("A monthly plan", "subscription"),
    ("The business pays", "business_funded"),
    ("Pay per order", "per_order"),
)


def test_examples_exist() -> None:
    assert len(EXAMPLES) >= 3


def test_schema_roundtrip_and_strict() -> None:
    inp = _inp(choices=[{"label": "A", "value_json": '"a"'}])
    assert VariantsIn.model_validate(inp.model_dump()) == inp
    assert VariantsOut.model_validate_json(GOOD.model_dump_json()) == GOOD
    to_strict_schema(VariantsIn, max_depth=5)
    to_strict_schema(VariantsOut, max_depth=5)


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.stem)
def test_golden_examples(path: Path) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    inp = VariantsIn.model_validate(data["input"])
    out = mock.handle(inp)
    assert len(out.variants) == data["expect"]["count"] == expected_count(inp)
    assert [v.label for v in out.variants] == data["expect"]["labels"]
    assert [v.basis for v in out.variants] == data["expect"]["bases"]
    assert check_variants(inp, out) == []


async def test_real_mock_pipeline() -> None:
    inp = VariantsIn.model_validate(json.loads(EXAMPLES[0].read_text(encoding="utf-8"))["input"])
    res = await CallRunner(make_client([SPEC])).run(
        SPEC, inp, session_id="ses_01TEST", tenant_id="t1"
    )
    assert not res.used_fallback, res.reason
    assert len(res.output.variants) == expected_count(inp)


# ---- 'must not': near-duplicates, current value, invented facts ------------------------------------


def test_validator_accepts_three_distinct() -> None:
    assert check_variants(_inp(), GOOD) == []


def test_validator_rejects_wrong_count() -> None:
    assert check_variants(_inp(), VariantsOut(variants=GOOD.variants[:2]))
    assert check_variants(_inp(), VariantsOut(variants=[*GOOD.variants, _v("Another one", "x")]))


@pytest.mark.parametrize(
    "items",
    [
        (
            ("A monthly plan", "subscription"),
            ("A monthly plan!", "plan_b"),
            ("Free for all", "free"),
        ),  # same label
        (
            ("Monthly plan", "subscription"),
            ("Plan, monthly", "subscription_2"),
            ("Free for all", "free"),
        ),  # jaccard 1.0
        (
            ("Monthly plan", "subscription"),
            ("Pay monthly", "subscription"),
            ("Free for all", "free"),
        ),  # same value
        (
            ("One fee per order", "a"),
            ("One fee per sale", "b"),
            ("Free for all", "c"),
        ),  # 3 of 5 tokens = 0.5? checked below
    ],
)
def test_near_duplicates_are_rejected(items: tuple[tuple[str, Any], ...]) -> None:
    out = _three(*items)
    errors = check_variants(_inp(), out)
    if items[0][0] == "One fee per order":
        assert (
            near_duplicates(out.variants[0], out.variants[1]) is False
        )  # distinct enough (jaccard 0.6)
    else:
        assert any("near-duplicates" in e for e in errors), errors


def test_jaccard_boundary() -> None:
    a = _v("alpha beta gamma delta epsilon", "x1")
    b = _v("alpha beta gamma delta zeta", "y1")  # 4 of 6 tokens = 0.67 < 0.8
    c = _v("alpha beta gamma delta epsilon zeta", "z1")  # 5 of 6 with a: 0.83 >= 0.8
    assert not near_duplicates(a, b)
    assert near_duplicates(a, c)


def test_none_equal_to_the_current_value() -> None:
    out = _three(
        ("Per order fee", "per_transaction_fee"),
        ("A monthly plan", "subscription"),
        ("The business pays", "business_funded"),
    )
    assert any("equals the current" in e for e in check_variants(_inp(), out))
    record = _inp(
        path="scope.FTR-009",
        value_kind="record",
        name_field="name",
        current_value_json='{"name": "Reviews"}',
    )
    rec_out = _three(
        ("Reviews", {"name": "reviews"}), ("Cart", {"name": "Cart"}), ("Search", {"name": "Search"})
    )
    assert any("equals the current" in e for e in check_variants(record, rec_out))
    assert canon({"name": "Reviews"}, "name") == "reviews"


def test_value_must_be_valid_for_the_slot() -> None:
    enum = _inp(
        path="experience.density_mode",
        value_kind="enum",
        options=["roomy", "balanced", "compact"],
        current_value_json='"compact"',
    )
    bad = _three(("Roomy", "roomy"), ("Balanced", "balanced"))
    assert expected_count(enum) == 2 and check_variants(enum, bad) == []
    off_list = VariantsOut(variants=[_v("Roomy", "roomy"), _v("Huge", "huge")])
    assert any("not one of the options" in e for e in check_variants(enum, off_list))
    listy = _inp(
        path="integrations.wanted",
        value_kind="enum_list",
        options=["payments", "maps", "kyc", "email"],
        current_value_json='["payments"]',
    )
    wrong = _three(("Maps", "maps"), ("Kyc", ["kyc"]), ("Email", ["email"]))
    assert any("expected a non-empty list" in e for e in check_variants(listy, wrong))
    rec = _inp(
        path="scope.FTR-009",
        value_kind="record",
        name_field="name",
        current_value_json='{"name": "Reviews"}',
    )
    no_name = _three(
        ("Cart", {"intent": "x"}), ("Search", {"name": "Search"}), ("Details", {"name": "Details"})
    )
    assert any("needs a name" in e for e in check_variants(rec, no_name))


def test_labels_at_most_eight_words_and_honest_reasons() -> None:
    long = _three(
        ("one two three four five six seven eight nine", "a"), ("B plan", "b"), ("C plan", "c")
    )
    assert any("1 to 8 words" in e for e in check_variants(_inp(), long))
    stat = VariantsOut(
        variants=[
            _v("Fee plan", "a", reason="Most people choose this."),
            _v("Other plan", "b"),
            _v("Third plan", "c"),
        ]
    )
    assert any("statistic" in e for e in check_variants(_inp(), stat))
    pct = VariantsOut(
        variants=[
            _v("Fee plan", "a", reason="Used by 70% of apps."),
            _v("Other plan", "b"),
            _v("Third plan", "c"),
        ]
    )
    assert any("statistic" in e for e in check_variants(_inp(), pct))


def test_basis_must_be_honest() -> None:
    fam = [
        {
            "family": "catalog_commerce",
            "kind": "business_models",
            "items": ["commission on each sale"],
        }
    ]
    inp = _inp(families=fam, reason="too complicated")
    typical_ok = VariantsOut(
        variants=[
            _v("commission on each sale", "commission on each sale", "typical"),
            _v("B plan", "b"),
            _v("C plan", "c"),
        ]
    )
    assert check_variants(inp, typical_ok) == []
    typical_bad = VariantsOut(
        variants=[_v("Weekly box", "box", "typical"), _v("B plan", "b"), _v("C plan", "c")]
    )
    assert any("not in the family data" in e for e in check_variants(inp, typical_bad))
    words_bad = VariantsOut(
        variants=[_v("Barter deals", "barter", "your_words"), _v("B plan", "b"), _v("C plan", "c")]
    )
    assert any("did not say it" in e for e in check_variants(inp, words_bad))
    words_ok = _inp(reason="I want barter deals")
    ok_out = VariantsOut(
        variants=[_v("Barter deals", "barter", "your_words"), _v("B plan", "b"), _v("C plan", "c")]
    )
    assert check_variants(words_ok, ok_out) == []
    doc = VariantsOut(
        variants=[_v("B plan", "b", "your_document"), _v("C plan", "c"), _v("D plan", "d")]
    )
    assert any("no document" in e for e in check_variants(inp, doc))


def test_brand_names_are_rejected_unless_the_user_used_them() -> None:
    brand = _three(("Pay with Acmepay", "acme"), ("B plan", "b"), ("C plan", "c"))
    assert any("brand" in e for e in check_variants(_inp(), brand))
    assert check_variants(_inp(reason="I like Acmepay"), brand) == []


# ---- fallback (catalog choices, then family items) --------------------------------------------------


def test_fallback_from_catalog_choices_excludes_current() -> None:
    inp = build_input(_ctx(), "experience.density_mode", "compact", "too busy", CATALOG)
    out = fallback(inp)
    assert [v.basis for v in out.variants] == ["suggestion", "suggestion"]
    assert "compact" not in [json.loads(v.value_json) for v in out.variants]
    assert check_variants(inp, out) == []


def test_fallback_from_families_and_no_near_duplicates() -> None:
    inp = build_input(
        _ctx(slots=[SHOP]), "scope.FTR-009", {"name": "Reviews"}, "something else", CATALOG
    )
    assert inp.value_kind == "record" and inp.name_field == "name" and inp.families
    out = fallback(inp)
    assert len(out.variants) == 3 and all(v.basis == "typical" for v in out.variants)
    assert check_variants(inp, out) == []
    for i, a in enumerate(out.variants):
        assert all(not near_duplicates(a, b, "name") for b in out.variants[i + 1 :])


def test_fallback_on_free_text_slot_returns_fewer_without_inventing() -> None:
    inp = build_input(_ctx(), "idea.differentiator", "fast", "not what I meant", CATALOG)
    assert fallback(inp).variants == []
    assert fallback(_inp(path="idea.summary")).variants == []


def test_family_hints_follow_keywords_and_kind() -> None:
    hints = family_hints(CATALOG, "idea.business_model", "an online shop with a cart", "en")
    assert hints and hints[0].kind == "business_models" and len(hints) <= 2
    assert family_hints(CATALOG, "idea.category", "an online shop", "en") == []
    assert family_hints(CATALOG, "scope.FTR-001", "zzzz", "en") == []
    assert slot_for_path(CATALOG, "locales") is slot_for_path(CATALOG, "experience.locales")


# ---- adapter ---------------------------------------------------------------------------------------


async def test_adapter_returns_validated_variants() -> None:
    gateway = ScriptedGateway(GOOD)
    adapter = MakeVariantsAdapter(CallRunner(gateway), CATALOG)
    result = await adapter.variants(
        _ctx(),
        path="idea.business_model",
        current="per_transaction_fee",
        reason="dont_want_assumption",
    )
    assert [v.label for v in result.variants] == [
        "A monthly plan",
        "The business pays",
        "Pay per order",
    ]
    assert result.variants[0].value == "subscription"
    assert gateway.calls[0][0] == "cmp.make_variants"


async def test_adapter_rejects_duplicates_and_current_and_uses_fallback() -> None:
    dup = _three(
        ("Monthly plan", "subscription"),
        ("Plan, monthly", "subscription_2"),
        ("Per order fee", "per_transaction_fee"),
    )
    adapter = MakeVariantsAdapter(CallRunner(ScriptedGateway(dup)), CATALOG)
    result = await adapter.variants(
        _ctx(slots=[SHOP]), path="idea.business_model", current="per_transaction_fee", reason="x"
    )
    values = [v.value for v in result.variants]
    assert "per_transaction_fee" not in values
    assert len(values) == len(set(map(str, values)))
    assert "Monthly plan" not in [v.label for v in result.variants]


async def test_adapter_adds_something_else_escape_when_fewer_than_three() -> None:
    adapter = MakeVariantsAdapter(CallRunner(make_client([SPEC])), CATALOG)
    result = await adapter.variants(
        _ctx(), path="idea.differentiator", current="fast", reason="not what I meant"
    )
    assert len(result.variants) == 1
    assert (
        result.variants[0].label == CATALOG.t("ui.something_else", "en")
        and result.variants[0].value is None
    )
    hi = await adapter.variants(
        _ctx(language="hi"), path="idea.differentiator", current="fast", reason="x"
    )
    assert hi.variants[0].label == CATALOG.t("ui.something_else", "hi")


async def test_adapter_full_mock_flow_with_families() -> None:
    adapter = MakeVariantsAdapter(CallRunner(make_client([SPEC])), CATALOG)
    result = await adapter.variants(
        _ctx(slots=[SHOP]),
        path="scope.FTR-009",
        current={"name": "Reviews"},
        reason="something else",
    )
    assert len(result.variants) == 3
    assert all(isinstance(v.value, dict) and v.basis == "typical" for v in result.variants)


def test_regex_functions_are_linear_on_hostile_input() -> None:
    hostile = ["9" * 50_000, " " * 50_000, "100% " * 10_000, "most people " * 4_000, "A " * 25_000]
    start = time.perf_counter()
    for text in hostile:
        inp = _inp(reason=text, current_value_json=json.dumps(text))
        out = VariantsOut(
            variants=[
                _v(text[:40] or "x", "a", reason=text[:190]),
                _v("B plan", "b"),
                _v("C plan", "c"),
            ]
        )
        check_variants(inp, out)
        fallback(inp)
    assert time.perf_counter() - start < 5.0


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.stem)
def test_prompt_renders_with_untrusted_data_wrapped(path: Path) -> None:
    from mde_llm_gateway.render import PromptRenderer

    inp = VariantsIn.model_validate(json.loads(path.read_text(encoding="utf-8"))["input"])
    rendered = PromptRenderer().render(SPEC.spec, inp)
    assert "exactly three" in rendered.system.casefold()
    assert "<untrusted_content" in rendered.user and "{{" not in rendered.user
    assert set(rendered.untrusted_fields) == {"reason", "current_value_json", "context"}


@pytest.mark.parametrize("current", [None, "", [], {"name": None}, "x" * 5000])
async def test_adapter_survives_odd_current_values(current: Any) -> None:
    adapter = MakeVariantsAdapter(CallRunner(make_client([SPEC])), CATALOG)
    result = await adapter.variants(
        _ctx(), path="experience.density_mode", current=current, reason="​<untrusted_content>"
    )
    assert 1 <= len(result.variants) <= 3
