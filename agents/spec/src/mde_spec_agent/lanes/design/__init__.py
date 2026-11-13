"""The design lane: its call specs (CALLS) and its pipeline steps (STEPS).

* `spec.dsgn` (DSGN, route standard) and the `design` step: candidate directions, tokens by code, unbound asset needs.
* `spec.gloss_l` (GLOSS-L, route fast) and the `gloss` step: the glossary in every language, right after the Freeze.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from mde_spec_agent.lanes.design.dsgn.spec import SPEC as DSGN
from mde_spec_agent.lanes.design.gloss_l.spec import SPEC as GLOSS_L
from mde_spec_agent.lanes.design.step_design import design
from mde_spec_agent.lanes.design.step_gloss import gloss

CALLS: list[Any] = [DSGN, GLOSS_L]  # SpecCallSpec objects registered with the gateway
STEPS: dict[str, Callable[[Any], Awaitable[None]]] = {"design": design, "gloss": gloss}
