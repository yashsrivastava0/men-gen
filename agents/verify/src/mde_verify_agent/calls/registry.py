"""All of Verify's call specs in one place (tests, the schema compile check, catalogs)."""

from __future__ import annotations

from mde_spec_core.verify_port import PortCallSpec
from mde_verify_agent.calls.canary.spec import SPEC as CANARY
from mde_verify_agent.calls.final_critic.spec import SPEC as FINAL_CRITIC
from mde_verify_agent.calls.structure_critic.spec import SPEC as STRUCTURE_CRITIC

ALL_SPECS: tuple[PortCallSpec, ...] = (STRUCTURE_CRITIC, CANARY, FINAL_CRITIC)
BY_ID: dict[str, PortCallSpec] = {s.id: s for s in ALL_SPECS}
