"""``to_strict_schema``: strict-subset conversion, rejection of what strict mode cannot express,
every registered output model, and round trips against the model."""

from __future__ import annotations

from typing import Annotated, Any, Literal

import pytest
from pydantic import BaseModel, Field, RootModel, StringConstraints

from mde_contracts.registry import llm_output_models
from mde_llm_gateway.strict_schema import (
    StrictSchemaError,
    check_strict,
    schema_depth,
    schema_hash,
    strict_schema_name,
    to_strict_schema,
)
from mde_llm_gateway.testing import sample_from_schema, schema_violations


class Leaf(BaseModel):
    name: str = Field(description="A name")
    score: float = Field(default=0.5, ge=0.0, le=1.0, description="0 to 1")


class Sample(BaseModel):
    kind: Literal["a", "b"]
    only: Literal["fixed"] = "fixed"
    note: str | None = None
    flag: bool = False
    tags: list[str] = []
    leaf: Leaf
    maybe_leaf: Leaf | None = None
    leaves: list[Leaf] = Field(default_factory=list, max_length=5)
    code: Annotated[str, StringConstraints(pattern=r"^SCR-\d{3}$", min_length=7, max_length=7)] = (
        "SCR-001"
    )


def _props(schema: dict[str, Any]) -> dict[str, Any]:
    return schema["properties"]  # type: ignore[no-any-return]


def test_every_object_is_closed_and_fully_required() -> None:
    schema = to_strict_schema(Sample)
    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    assert schema["required"] == list(_props(schema))
    leaf = _props(schema)["leaf"]
    assert leaf["additionalProperties"] is False
    assert leaf["required"] == ["name", "score"]
    assert check_strict(schema) == []


def test_optional_fields_become_nullable_unions_but_defaults_stay_required_non_null() -> None:
    props = _props(to_strict_schema(Sample))
    assert props["note"]["anyOf"] == [{"type": "string"}, {"type": "null"}]
    assert props["maybe_leaf"]["anyOf"][1] == {"type": "null"}
    assert props["flag"] == {"type": "boolean"}  # default False: required, NOT nullable
    assert props["tags"]["type"] == "array"
    assert "default" not in props["flag"]


def test_literals_become_enums_and_single_literals_stay_enums() -> None:
    props = _props(to_strict_schema(Sample))
    assert props["kind"] == {"type": "string", "enum": ["a", "b"]}
    assert props["only"]["enum"] == ["fixed"]
    assert "const" not in props["only"]


def test_defs_are_inlined_and_titles_and_defaults_dropped() -> None:
    schema = to_strict_schema(Sample)
    assert "$defs" not in schema and "$ref" not in str(schema)
    assert "title" not in str(schema) and "'default'" not in str(schema)


def test_descriptions_are_kept_and_ref_siblings_merged() -> None:
    class Holder(BaseModel):
        leaf: Leaf = Field(description="the leaf")

    props = _props(to_strict_schema(Holder))
    assert props["leaf"]["description"] == "the leaf"
    assert _props(props["leaf"])["name"]["description"] == "A name"


def test_unsupported_string_keywords_are_stripped_supported_ones_kept() -> None:
    code = _props(to_strict_schema(Sample))["code"]
    assert "minLength" not in code and "maxLength" not in code
    assert code["pattern"] == r"^SCR-\d{3}$"
    no_pattern = _props(to_strict_schema(Sample, keep_pattern=False))["code"]
    assert "pattern" not in no_pattern
    score = _props(_props(to_strict_schema(Sample))["leaf"])["score"]
    assert (score["minimum"], score["maximum"]) == (0.0, 1.0)
    assert _props(to_strict_schema(Sample))["leaves"]["maxItems"] == 5


def test_format_is_filtered_to_the_supported_list() -> None:
    import datetime as dt
    from ipaddress import IPv4Address
    from pathlib import Path

    class Fmt(BaseModel):
        when: dt.datetime
        ip: IPv4Address
        where: Path  # emits format "path": not supported

    props = _props(to_strict_schema(Fmt))
    assert props["when"]["format"] == "date-time"
    assert props["ip"]["format"] == "ipv4"
    assert "format" not in props["where"]


def test_one_of_becomes_any_of() -> None:
    class Cat(BaseModel):
        kind: Literal["cat"]
        lives: int

    class Dog(BaseModel):
        kind: Literal["dog"]
        good: bool

    class Pets(BaseModel):
        pet: Annotated[Cat | Dog, Field(discriminator="kind")]

    pet = _props(to_strict_schema(Pets))["pet"]
    assert "oneOf" not in str(pet) and "discriminator" not in str(pet)
    assert len(pet["anyOf"]) == 2
    assert all(b["additionalProperties"] is False for b in pet["anyOf"])


def test_open_objects_are_rejected_with_a_path() -> None:
    class Bad(BaseModel):
        meta: dict[str, str]

    with pytest.raises(StrictSchemaError) as exc:
        to_strict_schema(Bad)
    assert exc.value.path == "$.meta"
    assert "open object" in str(exc.value)


def test_any_is_rejected() -> None:
    class Bad(BaseModel):
        blob: Any

    with pytest.raises(StrictSchemaError, match="unconstrained"):
        to_strict_schema(Bad)


def test_tuples_are_rejected() -> None:
    class Bad(BaseModel):
        pair: tuple[int, str]

    with pytest.raises(StrictSchemaError, match="prefixItems"):
        to_strict_schema(Bad)


def test_recursive_models_are_rejected() -> None:
    class Node(BaseModel):
        child: Node | None = None

    with pytest.raises(StrictSchemaError, match="recursive"):
        to_strict_schema(Node)


def test_depth_over_the_limit_raises_a_clear_error() -> None:
    class L5(BaseModel):
        x: int

    class L4(BaseModel):
        n: L5

    class L3(BaseModel):
        n: L4

    class L2(BaseModel):
        n: L3

    class L1(BaseModel):
        n: L2

    assert schema_depth(to_strict_schema(L1, max_depth=5)) == 5
    with pytest.raises(StrictSchemaError, match="nests 5 levels deep; the limit is 4"):
        to_strict_schema(L1, max_depth=4)

    class Wrap(BaseModel):
        top: list[L1]

    with pytest.raises(StrictSchemaError, match="levels deep"):  # arrays count as a level
        to_strict_schema(Wrap, max_depth=5)


def test_root_must_be_an_object() -> None:
    root_list = RootModel[list[int]]  # a RootModel over a list is not an object schema
    with pytest.raises(StrictSchemaError):
        to_strict_schema(root_list)


def test_conversion_is_deterministic_and_does_not_mutate_the_model_schema() -> None:
    first, second = to_strict_schema(Sample), to_strict_schema(Sample)
    assert first == second and schema_hash(first) == schema_hash(second)
    assert list(_props(first)) == list(Sample.model_fields)  # field order preserved


def test_strict_schema_name_is_api_safe() -> None:
    assert strict_schema_name("C9a.screens_nav") == "C9a_screens_nav"
    assert len(strict_schema_name("x" * 100)) == 64


def test_check_strict_reports_violations() -> None:
    assert check_strict([]) == ["root must be an object"]
    bad = {
        "type": "object",
        "properties": {"a": {"type": "string", "minLength": 1}},
        "required": [],
        "default": {},
    }
    problems = check_strict(bad)
    assert any("required" in p for p in problems)
    assert any("additionalProperties" in p for p in problems)
    assert any("minLength" in p for p in problems)
    assert any("default" in p for p in problems)


# ---------------------------------------------------------------------------- registry + round trip
@pytest.mark.parametrize("call_id", sorted(llm_output_models()))
def test_every_registered_output_model_converts(call_id: str) -> None:
    model = llm_output_models()[call_id]
    schema = to_strict_schema(model)
    assert check_strict(schema) == [], call_id
    assert schema_depth(schema) <= 5
    # A schema-minimal instance is accepted by the schema and by the model (round trip).
    sample = sample_from_schema(schema)
    assert schema_violations(schema, sample) == []
    parsed = model.model_validate(sample)
    # A fully populated dump of the model validates against the produced schema.
    assert schema_violations(schema, parsed.model_dump(mode="json")) == []


def test_round_trip_model_dump_validates_against_schema_and_back() -> None:
    schema = to_strict_schema(Sample)
    obj = Sample(kind="a", leaf=Leaf(name="n"), leaves=[Leaf(name="m", score=1.0)])
    dumped = obj.model_dump(mode="json")
    assert schema_violations(schema, dumped) == []
    assert Sample.model_validate(dumped) == obj


def test_schema_violations_catches_what_strict_mode_would_reject() -> None:
    schema = to_strict_schema(Sample)
    good = Sample(kind="a", leaf=Leaf(name="n")).model_dump(mode="json")
    assert schema_violations(schema, {**good, "extra": 1})  # additional property
    missing = {k: v for k, v in good.items() if k != "flag"}
    assert schema_violations(schema, missing)  # every property is required
    assert schema_violations(schema, {**good, "kind": "z"})  # enum
    assert schema_violations(schema, {**good, "flag": None})  # defaulted field is not nullable
    assert schema_violations(schema, {**good, "code": "nope"})  # pattern
    assert schema_violations(schema, {**good, "leaves": [{"name": "x", "score": 2.0}]})  # maximum
    assert schema_violations(schema, {**good, "note": 7})  # type
