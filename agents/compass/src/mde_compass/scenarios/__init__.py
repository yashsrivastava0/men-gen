"""YAML scenario runner for Compass (``SB-<ID>`` edge cases and the persona scripts).

A scenario is a script of user actions run against the real ``Service`` in mock mode over the in-memory store,
through the ``Conversation`` driver. Each step may carry ``expect:`` checks. See ``runner.py``.

Two question engines run the same scripts (``engine="legacy"`` or ``"planner"``); a planner script may come with a
provider fixture of hand-written model outputs (``providers.py``), and ``ledger.py`` / ``metrics.py`` measure a finished
planner session against the targets of build prompt 02 section 6.
"""

from mde_compass.scenarios.models import Expect, Scenario, SlotExpect, Step
from mde_compass.scenarios.runner import (
    ENGINES,
    Engine,
    RunResult,
    ScenarioFailure,
    StepRecord,
    engine_config,
    load_dir,
    load_scenario,
    new_scripted_service,
    new_service,
    run_scenario,
)

__all__ = [
    "ENGINES",
    "Engine",
    "Expect",
    "RunResult",
    "Scenario",
    "ScenarioFailure",
    "SlotExpect",
    "Step",
    "StepRecord",
    "engine_config",
    "load_dir",
    "load_scenario",
    "new_scripted_service",
    "new_service",
    "run_scenario",
]
