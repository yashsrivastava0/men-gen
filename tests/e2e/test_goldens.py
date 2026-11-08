"""The golden briefs (spec section 12): five archetypes, each with expected slots and a golden spec.

* the golden files themselves are valid specs: they parse, their hash verifies, the consumer
  simulator passes on every one of them (slices within budget, every reference resolves) and none
  holds a secret;
* the inputs match the e2e journeys, so the e2e journey test (``test_journeys.py``) can compare a
  fresh session with the golden bytes and the expected slots, screens, data and flows;
* ``python -m evals.runners.make_goldens --check`` is the same comparison as a command.

The comparison against a fresh run lives in ``test_journeys.py::test_guided_journey`` (one session
serves both checks; no second pass over the whole product).
"""

from __future__ import annotations

import pytest
import yaml

from evals.harness.briefs import GoldenBrief, load_briefs
from evals.harness.journeys import ARCHETYPES, GUIDED
from mde_core.redact import find_secrets
from mde_spec_loader import SpecIndex, parse_spec, simulate_consumer, verify_hash

pytestmark = pytest.mark.e2e

BRIEFS = load_briefs()


def golden_text(brief: GoldenBrief) -> str:
    return brief.golden_path.read_bytes().decode("utf-8").replace("\r\n", "\n")


def test_there_is_one_golden_brief_per_archetype() -> None:
    assert sorted(b.archetype for b in BRIEFS) == sorted(ARCHETYPES)
    for brief in BRIEFS:
        assert brief.golden_path.is_file() and brief.expected_path.is_file(), brief.id


def test_the_brief_inputs_are_the_guided_journeys() -> None:
    by_archetype = {j.archetype: j for j in GUIDED}
    for brief in BRIEFS:
        journey = by_archetype[brief.archetype]
        assert (brief.opening, brief.mode, brief.policy) == (
            journey.opening,
            journey.mode,
            journey.policy_id,
        ), brief.id


@pytest.mark.parametrize("brief", BRIEFS, ids=lambda b: b.id)
def test_golden_spec_is_a_valid_file_and_the_consumer_simulator_passes(brief: GoldenBrief) -> None:
    text = golden_text(brief)
    assert verify_hash(text), "the embedded hash of the golden does not verify"
    spec = parse_spec(text)
    assert spec.header.app_id
    report = simulate_consumer(SpecIndex(text))
    assert report.problems == [], report.problems
    assert report.slices and all(not s.over_budget for s in report.slices)
    assert report.hash_ok and not report.unresolved and not report.unscheduled
    assert find_secrets(text) == []


@pytest.mark.parametrize("brief", BRIEFS, ids=lambda b: b.id)
def test_expected_slots_describe_the_brief(brief: GoldenBrief) -> None:
    expected = yaml.safe_load(brief.expected_path.read_text(encoding="utf-8"))
    assert expected["slots"]["idea.archetype"]["value"] == brief.archetype
    assert expected["counts"]["screens"] == len(expected["screens"]) >= 8
    assert expected["counts"]["flows"] == len(expected["flows"]) >= 1
    assert expected["entities"] and expected["operations"] and expected["roles"]
    # every screen a flow names exists, and ids are unique
    ids = [s["id"] for s in expected["screens"]]
    assert len(set(ids)) == len(ids)
    assert {s for f in expected["flows"] for s in f["screens"]} <= set(ids)
