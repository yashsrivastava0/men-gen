"""The screens lane: its call specs (CALLS) and its pipeline steps (STEPS).

Calls (doc 18 1.2): SCRN `spec.scrn`, MOCK-HERO `spec.mock_hero`, BEHAV `spec.behav`, COPY-L `spec.copy_l`, G2-SUMMARY
`spec.g2_summary`. Steps: `screens`, `hero`, `behav`, `copyl`, `g2summary`; after the Freeze the pipeline runs `screens`,
`hero` and `behav` in parallel, then `copyl` (streamed behind `screens`), then `g2summary` beside assembly.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from mde_spec_agent.lanes.screens.behav.spec import SPEC as BEHAV
from mde_spec_agent.lanes.screens.behav.step import behav_step
from mde_spec_agent.lanes.screens.copy_l.spec import SPEC as COPY_L
from mde_spec_agent.lanes.screens.copy_l.step import copyl_step
from mde_spec_agent.lanes.screens.g2_summary.spec import SPEC as G2_SUMMARY
from mde_spec_agent.lanes.screens.g2_summary.step import g2summary_step
from mde_spec_agent.lanes.screens.mock_hero.spec import SPEC as MOCK_HERO
from mde_spec_agent.lanes.screens.mock_hero.step import hero_step
from mde_spec_agent.lanes.screens.scrn.spec import SPEC as SCRN
from mde_spec_agent.lanes.screens.scrn.step import screens_step

CALLS: list[Any] = [
    SCRN,
    MOCK_HERO,
    BEHAV,
    COPY_L,
    G2_SUMMARY,
]  # SpecCallSpec objects registered with the gateway
STEPS: dict[str, Callable[[Any], Awaitable[None]]] = {
    "screens": screens_step,
    "hero": hero_step,
    "behav": behav_step,
    "copyl": copyl_step,
    "g2summary": g2summary_step,
}
