"""Contract tests for the export-format adapter (``intake/formats.py``) and the format-free reader.

``tests/fixtures/formats/`` holds one small, versioned fixture per known Compass export format, trimmed from real
files (bookkeeping such as provenance and history removed; every field Research reads is kept):

* ``format1_with_baseline.json``: a real format-1 export with an approved baseline;
* ``format1_no_baseline.json``: a real format-1 export that was never approved;
* ``format2_full.json``: a format-2 export with the research view, the structure counts and the spend;
* ``format2_brief_only.json``: a format-2 export with only the Brief (no consumer views, no structure, no spend; the
  locales are a sentence).

To support a new Compass version: change ``formats.py`` only, add one trimmed fixture here and one row to ``EXPECTED``.
"""

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from mde_research_agent.catalog import load_catalog
from mde_research_agent.intake import ExportRefused, normalise, read_export
from mde_research_agent.intake.formats import parse_locales

HERE = Path(__file__).resolve().parent
FORMATS = HERE.parent / "fixtures" / "formats"
READER = HERE.parent.parent / "src" / "mde_research_agent" / "intake" / "reader.py"
CAT = load_catalog()

# name -> (compass format, input_binding, load class, locale codes, country codes, research view present)
EXPECTED = {
    "format1_with_baseline": (1, "verified", "standard", ("en", "hi"), [], True),
    "format1_no_baseline": (1, "unverified_no_baseline", "focused", ("en", "hi"), [], True),
    "format2_full": (2, "verified", "focused", ("pt-PT", "en"), ["pt"], True),
    "format2_brief_only": (2, "verified", "standard", ("mr", "gu", "hi", "en"), ["in"], False),
}


def load(name: str) -> dict[str, Any]:
    return json.loads((FORMATS / f"{name}.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_every_known_format_reads_into_a_valid_input(name: str) -> None:
    fmt, binding, klass, locales, countries, has_view = EXPECTED[name]
    inp = read_export(load(name), CAT)
    assert inp.quality.compass_format == fmt and inp.quality.input_binding == binding
    assert inp.load is not None and inp.load.load_class == klass
    assert inp.locales.codes == locales
    assert [c.code for c in inp.geography.countries] == countries
    assert (f"compass_format_{fmt}" in inp.quality.flags) is True
    assert ("research_view_missing" not in inp.quality.flags) is has_view
    assert inp.idea.summary and inp.requests and inp.features and inp.roles
    inp.model_dump_json()  # serialisable


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_the_adapter_hands_the_reader_one_shape(name: str) -> None:
    norm = normalise(load(name), CAT.languages)
    fmt, binding, *_ = EXPECTED[name]
    assert norm.compass_format == fmt and norm.binding == binding
    assert norm.brief_hash and norm.brief_version >= 0 and norm.idea.summary
    assert isinstance(norm.locale_codes, tuple) and isinstance(norm.audience, tuple)


def test_the_reader_knows_no_export_layout() -> None:
    """All Compass-format knowledge lives in the adapter: the reader never names an envelope key."""
    text = READER.read_text(encoding="utf-8")
    for word in (
        "consumer_views",
        "format_version",
        "approved_baseline",
        '"export"',
        '"spend"',
        '"structure"',
    ):
        assert word not in text, word


def test_without_the_research_view_the_same_values_come_from_the_brief() -> None:
    """Where the view is missing, idea, languages, geography, audience, glossary and declined come from the Brief."""
    full = load("format1_with_baseline")
    bare = copy.deepcopy(full)
    bare.pop("consumer_views")
    a, b = read_export(full, CAT), read_export(bare, CAT)
    assert b.idea == a.idea and b.locales.codes == a.locales.codes
    assert b.geography == a.geography and b.audience == a.audience
    assert b.glossary == a.glossary and b.declined == a.declined
    assert b.requests == a.requests and b.load == a.load


def test_the_hospital_load_class_is_computed_from_the_brief_not_a_missing_view() -> None:
    inp = read_export(load("format2_brief_only"), CAT)
    assert inp.load is not None and inp.load.components.features == 13
    assert len([r for r in inp.roles if not r.duplicate_of]) == 10
    assert inp.quality.structure_source == "unknown" and inp.quality.failures == ()


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
    assert read_export(_deep_add(data), CAT).model_dump() == read_export(data, CAT).model_dump()


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_a_future_format_version_is_read_and_flagged(name: str) -> None:
    data = load(name)
    data["export"]["format_version"] = 7
    inp = read_export(data, CAT)
    assert inp.quality.compass_format == 7 and "compass_format_7" in inp.quality.flags


@pytest.mark.parametrize("name", sorted(EXPECTED))
@pytest.mark.parametrize(
    "key", ["export", "consumer_views", "structure", "spend", "interview", "session"]
)
def test_every_optional_block_may_be_missing(name: str, key: str) -> None:
    data = load(name)
    data.pop(key, None)
    inp = read_export(data, CAT)
    assert inp.idea.summary and inp.requests
    if key == "export":
        assert inp.quality.compass_format is None and "compass_format_unknown" in inp.quality.flags
        assert inp.quality.input_binding == "unverified_no_baseline"


WRONG_TYPES: list[Any] = [None, 5, "text", [], {}, [1, "a", None], {"x": 1}]


@pytest.mark.parametrize("wrong", WRONG_TYPES, ids=repr)
def test_no_section_of_the_brief_can_crash_the_reader(wrong: Any) -> None:
    """Every Brief section set to the wrong type is an empty section: the reader never raises anything but a refusal."""
    base = load("format2_full")
    for key in list(base["brief"]):
        data = copy.deepcopy(base)
        data["brief"][key] = copy.deepcopy(wrong)
        code: str | None = None
        try:
            inp = read_export(data, CAT)
        except ExportRefused as refused:
            code = refused.code
        else:
            assert inp.load is not None, key
        assert code in (None, "unusable_brief"), (key, code)


@pytest.mark.parametrize("wrong", WRONG_TYPES, ids=repr)
def test_no_block_of_the_research_view_or_envelope_can_crash_the_reader(wrong: Any) -> None:
    base = load("format1_with_baseline")
    for key in list(base["export"]):
        data = copy.deepcopy(base)
        data["export"][key] = copy.deepcopy(wrong)
        assert read_export(data, CAT).load is not None, key
    for key in list(base["consumer_views"]["research"]):
        data = copy.deepcopy(base)
        data["consumer_views"]["research"][key] = copy.deepcopy(wrong)
        assert read_export(data, CAT).load is not None, key
    for key in ("consumer_views", "spend"):
        data = copy.deepcopy(base)
        data[key] = copy.deepcopy(wrong)
        assert read_export(data, CAT).load is not None, key


def test_a_brief_with_one_stray_record_among_good_ones_keeps_the_good_ones() -> None:
    data = load("format2_full")
    good = len(read_export(data, CAT).features)
    data["brief"]["scope"] = ["stray", None, 7, *data["brief"]["scope"]]
    assert len(read_export(data, CAT).features) == good


# --- the binding is a flag, never a refusal ---------------------------------------------------------------------------


def test_no_baseline_a_changed_brief_and_a_changed_export_hash_are_flagged() -> None:
    data = load("format1_with_baseline")
    assert read_export(data, CAT).quality.input_binding == "verified"
    changed = copy.deepcopy(data)
    changed["brief"]["_hash"] = "0" * 64
    assert read_export(changed, CAT).quality.input_binding == "unverified_hash_mismatch"
    other = copy.deepcopy(data)
    other["export"]["brief_hash"] = "f" * 64
    assert read_export(other, CAT).quality.input_binding == "unverified_hash_mismatch"
    unapproved = copy.deepcopy(data)
    unapproved["export"]["approved_baseline"] = {
        "version": 3
    }  # a baseline with no hash is no baseline
    assert read_export(unapproved, CAT).quality.input_binding == "unverified_no_baseline"


def test_a_brief_with_no_hash_at_all_gets_a_computed_one_and_is_unverified() -> None:
    data = load("format1_no_baseline")
    data["brief"].pop("_hash", None)
    data["export"].pop("brief_hash", None)
    inp = read_export(data, CAT)
    assert len(inp.brief_hash) == 64 and "brief_hash_computed" in inp.quality.flags
    assert inp.quality.input_binding == "unverified_no_baseline"
    assert read_export(data, CAT).brief_hash == inp.brief_hash  # stable


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
        read_export(data, CAT)
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
        ),  # "in", "or", "no" are words, not codes
        ([], [], False),
        (None, [], False),
        (42, [], False),
    ],
)
def test_locale_codes_are_read_tolerantly_even_from_prose(
    value: Any, codes: list[str], from_text: bool
) -> None:
    got, parsed = parse_locales(value, CAT.languages)
    assert got == codes and parsed is from_text


# --- the result and the report say it ---------------------------------------------------------------------------------


async def test_an_unverified_brief_is_researched_and_the_result_and_report_say_so() -> None:
    from support.testing import run_engine

    from mde_research_agent.report import render_report

    data = load("format1_no_baseline")
    run = await run_engine(data)
    assert run.research["input_quality"]["input_binding"] == "unverified_no_baseline"
    assert run.research["input_quality"]["compass_format"] == 1
    assert run.research["items"] and run.result.status in ("complete", "partial")
    # only the warning that says so: an unverified Brief is never an error
    assert [(v.rule, v.severity) for v in run.violations] == [("RV-13", "warn")]
    report = render_report(run.research, run.evidence, set(), run.ctx.catalog.lanes.kinds)
    assert "could not be verified against its approval seal (unverified_no_baseline)" in report
    verified = await run_engine(load("format1_with_baseline"))
    assert verified.research["input_quality"]["input_binding"] == "verified"
    assert "approval seal" not in render_report(
        verified.research, verified.evidence, set(), verified.ctx.catalog.lanes.kinds
    )
