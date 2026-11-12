"""D-162: the output stays ``research.v5`` and is extended ADDITIVELY. Nothing that validated before stops validating; the new keys
and kinds are in the committed schema; money is a string."""

from __future__ import annotations

import asyncio
import copy
import json
from typing import Any

import jsonschema
import pytest
from support.content import combine, compliance_extractor, fact_extractor, platform_extractor
from support.testing import load_fixture, rule_extractor, run_engine

from mde_research_agent import SCHEMA_VERSION
from mde_research_agent.domain.research import SCHEMA
from mde_research_agent.schemas import CONTRACTS_DIR, RESEARCH_FILE

NEW_KEYS = (
    "dossiers",
    "dossiers_by_integration",
    "platform_constraints",
    "compliance_register",
    "lane_decisions",
)


@pytest.fixture(scope="module")
def schema() -> dict[str, Any]:
    return json.loads((CONTRACTS_DIR / RESEARCH_FILE).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def rich() -> dict[str, Any]:
    run = asyncio.run(
        run_engine(
            load_fixture("standard-produce-marketplace-nashik"),
            overrides={
                "rsch.extract.services": fact_extractor(),
                "rsch.extract.platform": platform_extractor(),
                "rsch.extract.data": combine(rule_extractor(), compliance_extractor()),
            },
        )
    )
    return run.research


def test_the_schema_id_does_not_change(schema: dict[str, Any]) -> None:
    assert SCHEMA == SCHEMA_VERSION == "research.v5" == schema["$id"]
    assert (CONTRACTS_DIR / RESEARCH_FILE).name == "research.v5.schema.json"
    assert not list(CONTRACTS_DIR.glob("research.v6*")), "no v6 and no compatibility view (D-162)"


def test_the_new_keys_are_in_the_schema_and_none_is_required(schema: dict[str, Any]) -> None:
    props, required = schema["properties"], set(schema["required"])
    for key in NEW_KEYS:
        assert key in props, key
        assert key not in required, f"{key} must be optional so an older output still validates"
    assert "variants" not in set(schema["$defs"]["Item"]["required"])
    assert "digest_tokens_cap_per_lane" not in set(schema["$defs"]["Profile"].get("required", []))


def test_an_output_without_the_new_keys_still_validates(
    rich: dict[str, Any], schema: dict[str, Any]
) -> None:
    old = copy.deepcopy(rich)
    for key in NEW_KEYS:
        old.pop(key)
    for item in old["items"]:
        item.pop("variants", None)
    old["profile"].pop("digest_tokens_cap_per_lane", None)
    for c in old["confirm"]:
        for k in ("rank", "group", "class", "top"):
            c.pop(k, None)
    jsonschema.Draft202012Validator(schema).validate(old)


def test_a_real_output_with_every_new_key_validates(
    rich: dict[str, Any], schema: dict[str, Any]
) -> None:
    for key in NEW_KEYS:
        assert rich[key], key
    jsonschema.Draft202012Validator(schema).validate(rich)


def test_the_new_kinds_are_in_the_kind_enum_with_their_own_data_schema(
    schema: dict[str, Any],
) -> None:
    kinds = schema["$defs"]["Item"]["properties"]["kind"]["enum"]
    assert {"provider_fact", "platform_constraint", "service_option"} <= set(kinds), (
        "service_option keeps working"
    )
    then = {c["if"]["properties"]["kind"]["const"] for c in schema["$defs"]["Item"]["allOf"]}
    assert {"provider_fact", "platform_constraint"} <= then


def test_old_compliance_data_still_validates_and_the_register_fields_are_optional(
    rich: dict[str, Any], schema: dict[str, Any]
) -> None:
    sub = {"$ref": "#/$defs/data__compliance_note", "$defs": schema["$defs"]}
    jsonschema.validate(
        {
            "topic": "t",
            "summary": "s",
            "authority": None,
            "effective_date": None,
            "legal_adjacent": True,
        },
        sub,
    )
    required = set(schema["$defs"]["data__compliance_note"]["required"])
    assert required.isdisjoint({"regime", "country", "applies_to", "penalty"})


def test_money_in_a_typed_value_is_a_string_never_a_number(schema: dict[str, Any]) -> None:
    typed = schema["$defs"]["provider_fact__TypedValue"]["properties"]["amount"]
    assert {"type": "string"} in typed["anyOf"] and all(
        t.get("type") != "number" for t in typed["anyOf"]
    )
    sub = {"$ref": "#/$defs/provider_fact__TypedValue", "$defs": schema["$defs"]}
    jsonschema.validate({"amount": "2.9"}, sub)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"amount": 2.9}, sub)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(
            {"amount": "2.9", "url": "https://x.example"}, sub
        )  # extra keys are refused


def test_per_kind_data_is_enforced_for_the_new_kinds(
    rich: dict[str, Any], schema: dict[str, Any]
) -> None:
    validator = jsonschema.Draft202012Validator(schema)
    for kind, bogus in (
        ("provider_fact", {"provider": "x"}),
        (
            "platform_constraint",
            {"capability": "x", "effect_on_build": "makes_coffee", "rule": "r"},
        ),
    ):
        bad = copy.deepcopy(rich)
        item = next(i for i in bad["items"] if i["kind"] == kind)
        item["data"] = bogus
        with pytest.raises(jsonschema.ValidationError):
            validator.validate(bad)


def test_the_quotes_still_never_reach_the_file(rich: dict[str, Any]) -> None:
    assert '"quote"' not in json.dumps(rich)
