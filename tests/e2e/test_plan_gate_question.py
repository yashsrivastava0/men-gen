"""A scripted journey answers the builder's one question and plans again (found by live L3, D-083).

When the repair rounds cannot remove an error, the builder asks ONE plain question (`needs_answer`,
SIT-034) and the session goes back to Discover. The mock never gets there on its own, so the journey
helper used to stop with "planning ended in DISCOVERING". A real model got there in one of seven
live journeys (a screen whose data the repair prompt could not add). A user answers the question and
the plan runs again without redoing the research; the helper now does the same.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from evals.harness.driver import FlowDriver
from evals.harness.journeys import GUIDED, reach_plan_gate
from evals.harness.stack import StackOptions, build_stack
from mde_contracts.calls import C12Output, C13Output, CriticFinding
from mde_contracts.events import CardEvent

pytestmark = pytest.mark.e2e

ERROR = C12Output(
    verdict="fix",
    findings=[
        CriticFinding(
            kind="mismatch",
            severity="error",
            message="the plan does not fit the brief",
            target_ids=[],
        )
    ],
)


async def test_the_journey_helper_answers_the_builder_question_and_reaches_the_plan_gate(
    tmp_path: Path,
) -> None:
    script = {
        "C12.critic": [ERROR, ERROR, ERROR],  # the first check and one after each repair round
        "C13.repair": [C13Output(proposals=[]), C13Output(proposals=[])],  # nothing safe to fix
    }
    stack = build_stack(tmp_path / "projects", StackOptions(script=script))
    driver = FlowDriver.for_user(stack)
    await reach_plan_gate(driver, GUIDED[0])
    snap = await driver.snapshot()
    assert snap.state == "PLAN_REVIEW"
    asked = [e.card.card.id for e in await driver.events() if isinstance(e, CardEvent)]
    assert "Q-build-default" in asked  # the question really was asked, and answered
    assert stack.calls.counts().get("C6.plan_research") == 1  # the research was not redone


async def test_the_plain_mock_path_is_unchanged_by_the_question_handling(
    tmp_path: Path,
) -> None:
    # No script: the plain mock path reaches the plan gate, and the helper changes nothing there.
    stack = build_stack(tmp_path / "projects")
    driver = FlowDriver.for_user(stack)
    await reach_plan_gate(driver, GUIDED[0])
    assert (await driver.snapshot()).state == "PLAN_REVIEW"
