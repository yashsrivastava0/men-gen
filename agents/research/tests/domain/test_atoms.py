import pytest
from pydantic import BaseModel, create_model

from mde_llm_gateway import to_strict_schema
from mde_research_agent.domain.atoms import (
    COMMON_FIELDS,
    KINDS,
    NON_WIRE,
    PAYLOADS,
    payload_from_wire,
    validate_payload,
    wire_atom_model,
)


def _root(kind: str) -> type[BaseModel]:
    return create_model("Root", atoms=(list[wire_atom_model(kind)], ...))  # type: ignore[valid-type]


@pytest.mark.parametrize("kind", KINDS)
def test_every_kind_is_strict_schema_safe_inside_a_list(kind: str) -> None:
    schema = to_strict_schema(_root(kind), max_depth=5)
    assert schema["type"] == "object"


@pytest.mark.parametrize("kind", KINDS)
def test_wire_atoms_have_no_url_action_or_tool_fields(kind: str) -> None:
    fields = wire_atom_model(kind).model_fields
    names = set(fields)
    forbidden = ("url", "link", "href", "tool", "command", "fetch", "navigate")
    assert not {n for n in names if any(f in n for f in forbidden)}, names
    if (
        "actions" in names
    ):  # what users can do on a screen: plain strings, never something the pipeline runs
        assert fields["actions"].annotation == list[str]
    assert set(COMMON_FIELDS) <= names
    assert not (names & NON_WIRE.get(kind, frozenset()))


def test_kinds_and_payloads_agree() -> None:
    assert set(KINDS) == set(PAYLOADS)


def test_payload_round_trip_for_an_entity_pattern() -> None:
    wire = wire_atom_model("entity_pattern")(
        norm_key="booking",
        label="Booking record",
        claim="A booking links a member to a class",
        quote="A booking links a member to a class session",
        impact="normal",
        entity="booking",
        fields=[
            {
                "name": "status",
                "type": "enum",
                "required": True,
                "values": "booked|cancelled",
                "validation": None,
            }
        ],
        relations=["member", "class"],
        kpis=[],
    )
    data = payload_from_wire("entity_pattern", wire)
    assert data["fields"][0]["values"] == "booked|cancelled"
    assert validate_payload("entity_pattern", data) == []
    assert "norm_key" not in data


def test_validate_payload_reports_a_wrong_shape_and_unknown_kinds() -> None:
    assert validate_payload("rule", {"statement": "x"})  # subject missing
    assert validate_payload("nope", {})
