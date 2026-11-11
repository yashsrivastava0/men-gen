"""The two public contracts: committed JSON Schemas equal what the models generate, and real output validates against them."""

import json
from pathlib import Path

import jsonschema
import pytest

from mde_research_agent.schemas import (
    CONTRACTS_DIR,
    EVENT_FILE,
    RESEARCH_FILE,
    dumps,
    event_schema,
    research_v4_schema,
)
from support.testing import load_fixture, run_engine


def test_committed_schemas_have_not_drifted() -> None:
    assert (CONTRACTS_DIR / RESEARCH_FILE).read_text(encoding="utf-8") == dumps(
        research_v4_schema()
    )
    assert (CONTRACTS_DIR / EVENT_FILE).read_text(encoding="utf-8") == dumps(event_schema())


def test_the_schemas_are_valid_draft_2020_12() -> None:
    for name in (RESEARCH_FILE, EVENT_FILE):
        jsonschema.Draft202012Validator.check_schema(
            json.loads((CONTRACTS_DIR / name).read_text(encoding="utf-8"))
        )


@pytest.mark.parametrize("name", ["quick-yoga-studio-bengaluru", "dental-clinic-dubai-ar-en"])
async def test_real_output_and_events_validate_against_the_committed_schemas(name: str) -> None:
    run = await run_engine(load_fixture(name))
    research = json.loads((Path(CONTRACTS_DIR) / RESEARCH_FILE).read_text(encoding="utf-8"))
    events = json.loads((Path(CONTRACTS_DIR) / EVENT_FILE).read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator(research).validate(run.research)
    ev_validator = jsonschema.Draft202012Validator(events)
    for e in run.events:
        ev_validator.validate(json.loads(e.model_dump_json(by_alias=True)))


async def test_per_kind_data_is_enforced_by_the_schema() -> None:
    run = await run_engine(load_fixture("quick-yoga-studio-bengaluru"))
    schema = json.loads((CONTRACTS_DIR / RESEARCH_FILE).read_text(encoding="utf-8"))
    bad = json.loads(json.dumps(run.research))
    bad["items"][0]["data"] = {"nonsense": 1}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.Draft202012Validator(schema).validate(bad)
    text = json.dumps(run.research)
    assert '"quote"' not in text, "quotes never reach research.json"
