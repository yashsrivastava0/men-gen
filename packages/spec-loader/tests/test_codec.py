from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from mde_contracts.spec import (
    Entity,
    FieldDef,
    ScreenAction,
    ScreenBlock,
    StateMachine,
    Transition,
)
from mde_spec_loader import (
    SpecParseError,
    block_data,
    dump_yaml,
    load_yaml,
    model_data,
)
from mde_spec_loader.codec import block_model

# --------------------------------------------------------------------------- dump / load


@settings(max_examples=150, deadline=None)
@given(
    st.dictionaries(
        st.text(min_size=1, max_size=12),
        st.one_of(
            st.text(max_size=60),
            st.integers(),
            st.booleans(),
            st.lists(st.text(max_size=20), max_size=5),
        ),
        max_size=6,
    )
)
def test_dump_load_round_trips_any_text(data: dict[str, object]) -> None:
    sorted_data = {k: data[k] for k in sorted(data)}
    assert load_yaml(dump_yaml(sorted_data)) == sorted_data


def test_dump_is_deterministic_and_ends_with_one_newline() -> None:
    data = {"a": [1, 2, 3], "b": {"x": "y"}}
    assert dump_yaml(data) == dump_yaml(data)
    assert dump_yaml(data).endswith("\n") and not dump_yaml(data).endswith("\n\n")


@pytest.mark.parametrize(
    "value",
    ["abc\n", "abc\n\n", "a\x85b", "a b", "x\r\ny", "  lead", "trail  ", "a\tb", "a\x00b", ""],
)
def test_awkward_strings_survive(value: str) -> None:
    assert load_yaml(dump_yaml({"k": value})) == {"k": value}


def test_short_leaf_mappings_and_lists_use_flow_style() -> None:
    text = dump_yaml({"sections": ["a", "b"], "copy": {"cta": "Add"}, "n": {"deep": {"x": 1}}})
    assert "sections: [a, b]" in text
    assert "copy: {cta: Add}" in text
    assert "n:\n  deep: {x: 1}" in text  # a mapping holding a mapping stays block style


def test_long_lists_fall_back_to_block_style() -> None:
    items = [f"a fairly long list item number {i}" for i in range(8)]
    text = dump_yaml({"checks": items})
    assert text.startswith("checks:\n  - a fairly long list item number 0")


def test_a_list_of_mappings_is_written_in_one_style() -> None:
    short = {"id": "S1", "name": "A"}
    long = {"id": "S2", "name": "x" * 120}
    mixed = dump_yaml({"steps": [short, long]})
    assert "{id: S1" not in mixed  # not every item is short: all block style
    uniform = dump_yaml({"steps": [short, {"id": "S3", "name": "B"}]})
    assert "- {id: S1, name: A}" in uniform


def test_multiline_strings_use_literal_blocks() -> None:
    text = dump_yaml({"notes": "line one\nline two"})
    assert "notes: |-\n  line one\n  line two" in text
    assert load_yaml(text) == {"notes": "line one\nline two"}


def test_no_aliases_even_for_shared_objects() -> None:
    shared = ["a", "b", "c", "d" * 40, "e" * 40, "f" * 40]
    text = dump_yaml({"x": shared, "y": shared})
    assert "&" not in text and "*id" not in text


def test_unicode_is_kept_as_is() -> None:
    text = dump_yaml({"title": "नमस्ते दुनिया"})
    assert "नमस्ते दुनिया" in text


def test_load_yaml_wraps_errors() -> None:
    with pytest.raises(SpecParseError, match="invalid YAML"):
        load_yaml("a: [unclosed", line=7)


# --------------------------------------------------------------------------- model <-> data


def test_defaults_and_nones_are_omitted_and_order_is_declared_order() -> None:
    screen = ScreenBlock(id="SCR-001", route="/", recipe="home", purpose="Home")
    data = model_data(screen)
    assert list(data) == ["id", "route", "recipe", "purpose"]  # surface/auth/... are defaults


def test_free_form_maps_are_sorted() -> None:
    screen = ScreenBlock(
        id="SCR-001", route="/", recipe="r", purpose="p", texts={"b": "2", "a": "1"}
    )
    assert list(model_data(screen)["copy"]) == ["a", "b"]  # type: ignore[arg-type]


def test_screen_body_renames_texts_to_copy_and_omits_id() -> None:
    screen = ScreenBlock(id="SCR-014", route="/x", recipe="r", purpose="p", texts={"cta": "Go"})
    data = block_data("screen", screen)
    assert "copy" in data and "texts" not in data and "id" not in data


def test_actions_are_compact_and_round_trip() -> None:
    screen = ScreenBlock(
        id="SCR-001",
        route="/",
        recipe="r",
        purpose="p",
        actions=[
            ScreenAction(name="add", calls="API-addItem", on_success="toast"),
            ScreenAction(name="see", go="SCR-002"),
            ScreenAction(name="noop"),
        ],
    )
    body = block_data("screen", screen)
    assert body["actions"] == [
        {"add": {"calls": "API-addItem", "on_success": "toast"}},
        {"see": {"go": "SCR-002"}},
        {"noop": {}},
    ]
    back = block_model("screen", load_yaml(dump_yaml(body)), "SCR-001")
    assert back == screen


def test_expanded_action_form_is_also_accepted() -> None:
    data = {
        "route": "/",
        "recipe": "r",
        "purpose": "p",
        "actions": [{"name": "go_home", "go": "SCR-001"}, {"bare": None}],
    }
    screen = block_model("screen", data, "SCR-002")
    assert isinstance(screen, ScreenBlock)
    assert [a.name for a in screen.actions] == ["go_home", "bare"]


def test_transition_source_is_written_as_from() -> None:
    entity = Entity(
        id="ENT-Order",
        name="Order",
        plain_name="Orders",
        fields=[FieldDef(name="status")],
        state_machine=StateMachine(
            field="status",
            states=["a", "b"],
            transitions=[Transition(source="a", target="b", on="go")],
        ),
    )
    text = dump_yaml(model_data(entity))
    assert "from: a" in text and "source" not in text
    from mde_contracts.spec import DataBlock

    body = {"entities": [load_yaml(text)]}
    data = block_model("data", body, "DATA")
    assert isinstance(data, DataBlock)
    assert data.entities[0] == entity


def test_block_model_rejects_bad_input() -> None:
    with pytest.raises(SpecParseError, match="unknown block kind"):
        block_model("nope", {}, "X")
    with pytest.raises(SpecParseError, match="must be a YAML mapping"):
        block_model("app", ["a"], "APP")
    with pytest.raises(SpecParseError, match="block APP"):
        block_model("app", {"name": "x"}, "APP")  # purpose and audience missing
    with pytest.raises(SpecParseError, match="differs from the fence id"):
        block_model(
            "screen", {"id": "SCR-009", "route": "/", "recipe": "r", "purpose": "p"}, "SCR-001"
        )
    with pytest.raises(SpecParseError, match="differs from fence id"):
        block_model("flow", {"id": "FLW-009"}, "FLW-001")


def test_empty_body_is_an_empty_mapping() -> None:
    behavior = block_model("behavior", None, "BEHAVIOR")
    assert behavior.model_dump() == block_model("behavior", {}, "BEHAVIOR").model_dump()
