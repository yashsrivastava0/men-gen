"""Contract tests for the export-format adapter (``formats.py``) and the format-free reader.

``tests/fixtures/formats/`` holds one small, versioned fixture per known Compass export format (copied from Research,
D-175): ``format1_with_baseline``, ``format1_no_baseline``, ``format2_full`` (research view, structure counts, spend) and
``format2_brief_only`` (only the Brief; locales are a sentence).

To support a new Compass version: change ``formats.py`` only, add one trimmed fixture here and one row to ``EXPECTED``.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from mde_compass_intake import ExportRefused, normalise, read_export
from mde_compass_intake.countries import languages
from mde_compass_intake.formats import parse_locales

HERE = Path(__file__).resolve().parent
FORMATS = HERE / "fixtures" / "formats"
SRC = HERE.parent / "src" / "mde_compass_intake"

# name -> (compass format, input_binding, locale codes, country codes, research view present, tier)
EXPECTED = {
    "format1_with_baseline": (1, "verified", ("en", "hi"), [], True, "standard"),
    "format1_no_baseline": (1, "unverified_no_baseline", ("en", "hi"), [], True, "standard"),
    "format2_full": (2, "verified", ("pt-PT", "en"), ["pt"], True, "quick"),
    "format2_brief_only": (2, "verified", ("mr", "gu", "hi", "en"), ["in"], False, "thorough"),
}


def load(name: str) -> dict[str, Any]:
    return json.loads((FORMATS / f"{name}.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_every_known_format_reads_into_a_valid_input(name: str) -> None:
    fmt, binding, locales, countries, has_view, tier = EXPECTED[name]
    inp = read_export(load(name))
    assert inp.compass_format == fmt and inp.input_binding == binding == inp.trust.input_binding
    assert inp.locales.codes == locales
    assert [c.code for c in inp.geography.countries] == countries
    assert f"compass_format_{fmt}" in inp.flags
    assert ("research_view_missing" not in inp.flags) is has_view
    assert inp.idea.summary and inp.features and inp.roles
    assert inp.tier == tier
    inp.model_dump_json()  # serialisable


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_the_adapter_hands_the_reader_one_shape(name: str) -> None:
    norm = normalise(load(name))
    fmt, binding, *_ = EXPECTED[name]
    assert norm.compass_format == fmt and norm.binding == binding
    assert norm.brief_hash and norm.brief_version >= 0 and norm.idea.summary
    assert isinstance(norm.locale_codes, tuple) and isinstance(norm.audience, tuple)
    assert norm.size_band in {"small", "medium", "large"}


def test_the_reader_knows_no_export_layout() -> None:
    """All Compass-format knowledge lives in the adapter: the reader never names an envelope key."""
    text = (SRC / "reader.py").read_text(encoding="utf-8")
    for word in (
        "consumer_views",
        "format_version",
        "approved_baseline",
        '"export"',
        '"spend"',
        '"structure"',
        '"interview"',
    ):
        assert word not in text, word


def test_without_the_research_view_the_same_values_come_from_the_brief() -> None:
    full = load("format1_with_baseline")
    bare = copy.deepcopy(full)
    bare.pop("consumer_views")
    a, b = read_export(full), read_export(bare)
    assert b.idea == a.idea and b.locales.codes == a.locales.codes
    assert b.geography == a.geography and b.audience == a.audience
    assert b.glossary == a.glossary and b.declined == a.declined
    assert b.features == a.features and b.domain_facts == a.domain_facts


def test_a_trimmed_export_builds_its_values_from_the_brief() -> None:
    inp = read_export(load("format2_brief_only"))
    assert (
        len(inp.features) == 13 and len(inp.clean_roles()) == 9
    )  # 10 roles, one is the junk word "Roles"
    assert inp.trust.structure_source == "unknown" and inp.trust.failures == ()
    assert inp.locales.source == "experience" and inp.locales.parsed_from_text
    assert not inp.structure.present and "structure_missing" in inp.flags


# --- tolerance: unknown keys, missing blocks, wrong types ---------------------------------------------------------


def _deep_add(node: Any) -> Any:
    if isinstance(node, dict):
        return {**{k: _deep_add(v) for k, v in node.items()}, "x_future_key": {"a": [1, 2]}}
    if isinstance(node, list):
        return [_deep_add(v) for v in node]
    return node


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_unknown_keys_are_ignored_everywhere(name: str) -> None:
    data = load(name)
    assert read_export(_deep_add(data)).model_dump() == read_export(data).model_dump()


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_a_future_format_version_is_read_and_flagged(name: str) -> None:
    data = load(name)
    data["export"]["format_version"] = 7
    inp = read_export(data)
    assert inp.compass_format == 7 and "compass_format_7" in inp.flags
    assert "unknown_format_version:7" in inp.drift_flags


@pytest.mark.parametrize("name", sorted(EXPECTED))
@pytest.mark.parametrize(
    "key", ["export", "consumer_views", "structure", "spend", "interview", "session"]
)
def test_every_optional_block_may_be_missing(name: str, key: str) -> None:
    data = load(name)
    data.pop(key, None)
    inp = read_export(data)
    assert inp.idea.summary and inp.features
    if key == "export":
        assert inp.compass_format is None and "compass_format_unknown" in inp.flags
        assert inp.input_binding == "unverified_no_baseline"
        assert "format_version_missing" in inp.drift_flags
    if key == "interview":
        assert "quotes_unchecked" in inp.drift_flags


WRONG_TYPES: list[Any] = [None, 5, "text", [], {}, [1, "a", None], {"x": 1}]


@pytest.mark.parametrize("wrong", WRONG_TYPES, ids=repr)
def test_no_section_of_the_brief_can_crash_the_reader(wrong: Any) -> None:
    """Every Brief section set to the wrong type is an empty section: the reader raises nothing but a refusal."""
    base = load("format2_full")
    for key in list(base["brief"]):
        data = copy.deepcopy(base)
        data["brief"][key] = copy.deepcopy(wrong)
        code: str | None = None
        try:
            read_export(data)
        except ExportRefused as refused:
            code = refused.code
        assert code in (None, "unusable_brief"), (key, code)


@pytest.mark.parametrize("wrong", WRONG_TYPES, ids=repr)
def test_no_block_of_the_research_view_or_envelope_can_crash_the_reader(wrong: Any) -> None:
    base = load("format1_with_baseline")
    for key in list(base["export"]):
        data = copy.deepcopy(base)
        data["export"][key] = copy.deepcopy(wrong)
        assert read_export(data).idea.summary, key
    for key in list(base["consumer_views"]["research"]):
        data = copy.deepcopy(base)
        data["consumer_views"]["research"][key] = copy.deepcopy(wrong)
        read_export(data)
    for key in ("consumer_views", "spend", "structure", "interview"):
        data = copy.deepcopy(base)
        data[key] = copy.deepcopy(wrong)
        assert read_export(data).idea.summary, key


def test_a_brief_with_one_stray_record_among_good_ones_keeps_the_good_ones() -> None:
    data = load("format2_full")
    good = len(read_export(data).features)
    data["brief"]["scope"] = ["stray", None, 7, *data["brief"]["scope"]]
    assert len(read_export(data).features) == good


def test_a_wrong_typed_nested_value_inside_a_record_is_an_empty_value() -> None:
    data = load("format2_full")
    for rec in data["brief"]["scope"]:
        rec["apps"], rec["provenance"], rec["priority"] = 5, "text", ["must"]
    for rec in data["brief"]["roles"]:
        rec["goal"], rec["provenance"] = {"x": 1}, [None, 3]
    inp = read_export(data)
    assert len(inp.features) == 7 and not any(f.user_evidenced for f in inp.features)


# --- the binding is a flag, never a refusal ---------------------------------------------------------------------------


def test_no_baseline_a_changed_brief_and_a_changed_export_hash_are_flagged() -> None:
    data = load("format1_with_baseline")
    assert read_export(data).input_binding == "verified"
    changed = copy.deepcopy(data)
    changed["brief"]["_hash"] = "0" * 64
    assert read_export(changed).input_binding == "unverified_hash_mismatch"
    other = copy.deepcopy(data)
    other["export"]["brief_hash"] = "f" * 64
    assert read_export(other).input_binding == "unverified_hash_mismatch"
    unapproved = copy.deepcopy(data)
    unapproved["export"]["approved_baseline"] = {
        "version": 3
    }  # a baseline with no hash is no baseline
    inp = read_export(unapproved)
    assert inp.input_binding == "unverified_no_baseline" and inp.trust.weak_inputs


def test_a_brief_with_no_hash_at_all_gets_a_computed_one_and_is_unverified() -> None:
    data = load("format1_no_baseline")
    data["brief"].pop("_hash", None)
    data["export"].pop("brief_hash", None)
    inp = read_export(data)
    assert len(inp.brief_hash) == 64 and "brief_hash_computed" in inp.flags
    assert inp.input_binding == "unverified_no_baseline"
    assert read_export(data).brief_hash == inp.brief_hash  # stable


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d.pop("brief"),
        lambda d: d.update(brief="a string"),
        lambda d: d.update(brief=[]),
        lambda d: d["brief"].update(idea={}, summary="", research_requests=[], domain_facts=[]),
    ],
)
def test_only_a_genuinely_unusable_brief_is_refused_with_a_plain_reason(mutate: Any) -> None:
    data = load("format2_brief_only")
    mutate(data)
    with pytest.raises(ExportRefused) as err:
        read_export(data)
    assert err.value.code in {"not_an_export", "unusable_brief"} and len(err.value.message) > 20


# --- locales from a sentence ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "codes", "from_text"),
    [
        (["en", "hi"], ["en", "hi"], False),
        (["pt-PT", "en"], ["pt-PT", "en"], False),
        (["pt_pt", "HI-latn"], ["pt-PT", "hi-Latn"], False),
        ("en", ["en"], False),
        (
            ["wo", "ff"],
            ["wo", "ff"],
            False,
        ),  # a well-formed code is kept even when the catalog does not know it
        (
            [
                "Hospitals across Maharashtra and Gujarat, with Marathi, Gujarati, Hindi and English in chat."
            ],
            ["mr", "gu", "hi", "en"],
            True,
        ),
        (["The app is in Portuguese (pt-PT) and English."], ["pt-PT", "en"], True),
        ("en, hi and mr", ["en", "hi", "mr"], True),
        ("Hinglish and English", ["hi-Latn", "en"], True),
        (
            ["The app is used in India or in no other place."],
            [],
            False,
        ),  # "in", "or", "no" are words
        ([], [], False),
        (None, [], False),
        (42, [], False),
    ],
)
def test_locale_codes_are_read_tolerantly_even_from_prose(
    value: Any, codes: list[str], from_text: bool
) -> None:
    got, parsed = parse_locales(value, languages())
    assert got == codes and parsed is from_text
