import json

from hypothesis import given, settings
from hypothesis import strategies as st

from mde_spec_core.callkit import TopLevelShape, tolerant_parse

SHAPE = TopLevelShape(required_keys=("version",), array_key="items")


def _items(n):
    return [{"id": f"I-{i}", "name": f"item {i}"} for i in range(n)]


def test_clean_json_ok():
    r = tolerant_parse(json.dumps({"version": 1, "items": _items(2)}), SHAPE)
    assert r.ok and not r.truncated and r.value["items"][1]["id"] == "I-1"


def test_fenced_json():
    text = "```json\n" + json.dumps({"version": 1, "items": _items(1)}) + "\n```"
    r = tolerant_parse(text, SHAPE)
    assert r.ok and "fence" in r.stripped


def test_prose_around_json():
    body = json.dumps({"version": 1, "items": _items(1)})
    r = tolerant_parse(
        f"Sure! Here is the result you asked for:\n{body}\nHope it helps {{really}}.", SHAPE
    )
    assert r.ok and r.value["version"] == 1


def test_think_blocks_removed_and_their_json_ignored():
    fake = json.dumps({"version": 0, "items": [{"id": "WRONG"}]})
    real = json.dumps({"version": 1, "items": _items(1)})
    r = tolerant_parse(f"<think>maybe {fake} hmm</think>\n{real}", SHAPE)
    assert r.ok and r.value["items"][0]["id"] == "I-0" and "think" in r.stripped


def test_unclosed_think_block_is_dropped():
    r = tolerant_parse('{"version": 1, "items": []}<think>and then I kept thinking', SHAPE)
    assert r.ok


def test_reasoning_line_style():
    fake = json.dumps({"version": 0, "items": [{"id": "WRONG"}]})
    real = json.dumps({"version": 1, "items": _items(1)})
    r = tolerant_parse(
        f"Reasoning: the answer could be {fake}\nstill reasoning here\n\n{real}", SHAPE
    )
    assert r.ok and r.value["items"][0]["id"] == "I-0"


def test_first_object_that_fits_wins_and_misfits_are_skipped():
    a = json.dumps({"other": 1})
    b = json.dumps({"version": 1, "items": _items(1)})
    c = json.dumps({"version": 2, "items": _items(2)})
    r = tolerant_parse(f"{a} then {b} then {c}", SHAPE)
    assert r.ok and r.value["version"] == 1


def test_nested_braces_inside_strings():
    obj = {"version": 1, "items": [{"id": "A", "name": 'a } b { c "quoted" \\ end'}]}
    r = tolerant_parse("text {not json} " + json.dumps(obj), SHAPE)
    assert r.ok and r.value == obj


def test_truncated_array_salvages_seven_of_ten():
    full = json.dumps({"version": 1, "items": _items(10)})
    # cut in the middle of item index 7 (items 0..6 are finished)
    cut = full.index('"id": "I-7"') + 5
    r = tolerant_parse(full[:cut], SHAPE)
    assert r.ok and r.truncated and r.unfinished
    assert r.salvaged_count == 7
    assert [i["id"] for i in r.value["items"]] == [f"I-{i}" for i in range(7)]
    assert r.value["version"] == 1


def test_truncated_inside_string_with_braces():
    items = [{"id": "A", "name": "x { y"}, {"id": "B", "name": "long text that is cut off { of"}]
    full = json.dumps({"version": 1, "items": items})
    r = tolerant_parse(full[: len(full) - 12], SHAPE)
    assert r.ok and r.truncated and r.salvaged_count == 1


def test_truncated_after_fence_and_prose():
    full = json.dumps({"version": 1, "items": _items(4)})
    r = tolerant_parse("Here:\n```json\n" + full[:-30], SHAPE)
    assert r.ok and r.truncated and 0 < r.salvaged_count < 4


def test_truncated_with_nothing_finished_is_not_ok():
    r = tolerant_parse('{"version": 1, "items": [{"id": "I-0", "na', SHAPE)
    assert not r.ok and r.truncated and r.salvaged_count == 0 and r.value is None


def test_truncated_array_missing_required_key_after_it():
    shape = TopLevelShape(required_keys=("version", "summary"), array_key="items")
    r = tolerant_parse('{"version": 1, "items": [{"id": "a"}, {"id": "b"}], "summary": "cut', shape)
    assert not r.ok and r.truncated


def test_closed_array_then_truncated_optional_key_is_ok():
    r = tolerant_parse('{"version": 1, "items": [{"id": "a"}], "note": "cu', SHAPE)
    assert r.ok and r.truncated and r.salvaged_count == 1


def test_malformed_but_not_truncated_is_not_salvaged():
    r = tolerant_parse('{"version": 1, "items": [{"id": "a"} {"id": "b"}]}', SHAPE)
    assert not r.ok and not r.truncated


def test_wrong_shape_reports_reason():
    r = tolerant_parse('{"other": 1}', SHAPE)
    assert not r.ok and "shape" in r.reason


def test_array_key_not_an_array():
    r = tolerant_parse('{"version": 1, "items": "nope"}', SHAPE)
    assert not r.ok


def test_nan_is_rejected():
    r = tolerant_parse('{"version": NaN, "items": []}', SHAPE)
    assert not r.ok


def test_deep_nesting_does_not_raise():
    r = tolerant_parse("{" + '"a":[' * 5000, SHAPE)
    assert not r.ok


def test_garbage_is_not_ok():
    for junk in ["", "   ", "no json here", "{{{{", "}}}}", "[1,2,3]", "null", "<think>", "```"]:
        r = tolerant_parse(junk, SHAPE)
        assert not r.ok and r.reason, junk


def test_no_shape_means_any_object():
    r = tolerant_parse('prose {"a": 1}')
    assert r.ok and r.value == {"a": 1}


@settings(max_examples=300, deadline=None)
@given(st.text(max_size=400))
def test_never_raises_on_any_text(text):
    r = tolerant_parse(text, SHAPE)
    assert isinstance(r.ok, bool)
    assert (r.value is not None) == r.ok


@settings(max_examples=200, deadline=None)
@given(
    st.text(alphabet='{}[]",:\\ \nabc<>/think`123', max_size=300),
    st.integers(min_value=0, max_value=300),
)
def test_never_raises_on_jsonish_junk_and_cuts(text, cut):
    full = json.dumps({"version": 1, "items": _items(5)})
    for candidate in (text, full[:cut], text + full[:cut]):
        r = tolerant_parse(candidate, SHAPE)
        if r.ok:
            assert SHAPE.fits(r.value)


@settings(max_examples=100, deadline=None)
@given(st.integers(min_value=0, max_value=len(json.dumps({"version": 1, "items": _items(10)}))))
def test_every_truncation_point_is_safe_and_consistent(cut):
    full = json.dumps({"version": 1, "items": _items(10)})
    r = tolerant_parse(full[:cut], SHAPE)
    if r.ok and r.truncated:
        assert r.value["items"] == _items(10)[: r.salvaged_count]
