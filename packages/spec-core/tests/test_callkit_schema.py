from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from mde_spec_core.callkit import check_all, compile_check
from mde_spec_core.profiles import ConsumerProfile, SchemaLimits


class _C(BaseModel):
    model_config = ConfigDict(extra="forbid")


def prof(**limits):
    return ConsumerProfile(id="p", capability_class="weak", schema_limits=SchemaLimits(**limits))


FULL = dict(
    max_nullable_fields=16,
    max_union_params=16,
    max_depth=5,
    max_enum_size=100,
    max_grammar_chars=100000,
)


class Card(_C):
    id: str
    kind: Literal["a", "b"]
    note: str | None
    tags: list[str]


class Good(_C):
    items: list[Card]


def codes(issues, errors_only=True):
    return {i.code for i in issues if i.is_error or not errors_only}


def test_good_flat_model_accepted():
    assert codes(compile_check(Good, prof(**FULL))) == set()


def test_unset_limits_are_reported_unchecked_not_failed():
    issues = compile_check(Good, prof())
    assert codes(issues) == set()
    assert {"unchecked_nullable_fields", "unchecked_depth", "unchecked_enum_size"} <= codes(
        issues, errors_only=False
    )


def make_nullable(n):
    ns: dict[str, Any] = {"__annotations__": {f"f{i}": "str | None" for i in range(n)}}
    return type("Nulls", (_C,), ns)


def test_over_16_nullable_fields_flagged():
    class Wrap(_C):
        a: make_nullable(10)  # type: ignore[valid-type]
        b: make_nullable(7)  # type: ignore[valid-type]

    assert "over_nullable_fields" in codes(compile_check(Wrap, prof(**FULL)))
    assert "over_nullable_fields" not in codes(compile_check(make_nullable(16), prof(**FULL)))
    assert "over_nullable_fields" in codes(compile_check(make_nullable(17), prof(**FULL)))


def test_dict_field_rejected():
    class Bad(_C):
        values: dict[str, str]

    assert "open_map" in codes(compile_check(Bad, prof(**FULL)))


def test_any_and_tuple_rejected():
    class A(_C):
        x: Any

    class T(_C):
        t: tuple[int, str]

    assert "any_type" in codes(compile_check(A, prof(**FULL)))
    assert "tuple" in codes(compile_check(T, prof(**FULL)))


def test_open_record_in_array_rejected():
    class Loose(BaseModel):  # extra not forbidden
        id: str

    class Holder(_C):
        rows: list[Loose]

    assert "array_record_open" in codes(compile_check(Holder, prof(**FULL)))


def test_non_flat_record_in_array_flagged_unless_waived():
    class Inner(_C):
        v: str

    class Rec(_C):
        inner: Inner

    class Holder(_C):
        rows: list[Rec]

    assert "array_record_not_flat" in codes(compile_check(Holder, prof(**FULL)))
    assert "array_record_not_flat" not in codes(
        compile_check(Holder, prof(**FULL), require_flat=False)
    )


def test_depth_too_big():
    class L3(_C):
        v: str

    class L2(_C):
        c: list[L3]

    class L1(_C):
        b: list[L2]

    class L0(_C):
        a: list[L1]

    assert "over_depth" in codes(compile_check(L0, prof(**{**FULL, "max_depth": 4})))
    assert "over_depth" not in codes(compile_check(L0, prof(**{**FULL, "max_depth": 7})))


def test_oversized_enum():
    big = Literal[tuple(f"v{i}" for i in range(50))]  # type: ignore[valid-type]

    class E(_C):
        v: big  # type: ignore[valid-type]

    assert "over_enum_size" in codes(compile_check(E, prof(**{**FULL, "max_enum_size": 10})))
    assert "over_enum_size" not in codes(compile_check(E, prof(**FULL)))


def test_recursive_model_rejected():
    class Node(_C):
        children: list["Node"]

    Node.model_rebuild()
    assert "recursive" in codes(compile_check(Node, prof(**FULL)))


def test_check_all_matrix():
    report = check_all({"good": Good, "bad": make_nullable(17)}, [prof(**FULL)])
    assert not report.ok
    assert {f.model for f in report.errors()} == {"bad"}
    assert report.checked == (("good", "p"), ("bad", "p"))
    assert check_all([Good], [prof(**FULL)]).ok
