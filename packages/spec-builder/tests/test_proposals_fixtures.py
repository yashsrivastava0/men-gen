"""The deliberately invalid proposals in each call's fixtures are all dropped, with a reason."""

from __future__ import annotations

import json
from importlib import resources

import pytest
from helpers import make_note, make_request

from mde_content.packs import load_pack
from mde_contracts.calls import SpecProposals
from mde_spec_builder.baseline import build_baseline
from mde_spec_builder.proposals import validate_and_apply
from mde_spec_builder.render import render_spec

CALLS = [
    ("c9a_screens_nav", False),
    ("c9b_data_api_roles", False),
    ("c9c_design_tokens", False),
    ("c10_screen_briefs", True),
    ("c13_repair", True),
]
NOTE = make_note()


def invalid(folder: str) -> SpecProposals:
    path = resources.files(f"mde_prompts.calls.{folder}").joinpath(
        "fixtures", "invalid_proposals.json"
    )
    return SpecProposals.model_validate(json.loads(path.read_text("utf-8")))


@pytest.mark.parametrize(("folder", "all_features"), CALLS)
def test_every_invalid_fixture_proposal_is_dropped_and_the_spec_is_untouched(
    folder: str, all_features: bool
) -> None:
    features = [f.id for f in load_pack("commerce").features] if all_features else None
    spec = build_baseline(make_request("commerce", features=features, research=NOTE))
    proposals = invalid(folder)
    result = validate_and_apply(spec, proposals, research=NOTE)
    assert result.applied == [], [a.proposal for a in result.applied]
    assert len(result.dropped) == len(proposals.proposals) >= 4
    assert all(d.reason for d in result.dropped)
    assert render_spec(result.spec) == render_spec(spec)


def test_the_drop_reasons_are_plain_and_specific() -> None:
    spec = build_baseline(make_request("commerce", research=NOTE))
    reasons = {
        d.proposal.kind + ":" + d.code
        for d in validate_and_apply(spec, invalid("c9a_screens_nav"), research=NOTE).dropped
    }
    assert {
        "add_screen:duplicate",
        "add_screen:invalid",
        "add_screen:unknown_target",
        "add_screen:protected",
        "add_screen:research",
    } <= reasons
