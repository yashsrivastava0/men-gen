"""The whole derivation in one call: intake result in, baseline graph out (zero model cost)."""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass, field

from mde_compass_intake import ResearchView, SpecInput
from mde_spec_core.config import SpecConfig, default_config

from .facts import facts_from_input
from .honesty import GateNotes, apply_and_note
from .inventory import Inventory, build_inventory
from .profile import ProfileDecision, decide_profile
from .release import ReleasePlan, plan_release
from .seed import SeedResult, seed
from .signin import signin_by_role


@dataclass
class Derived:
    facts: set[str]
    decision: ProfileDecision
    inventory: Inventory
    plan: ReleasePlan
    seeded: SeedResult
    notes: GateNotes = field(
        default_factory=GateNotes
    )  # what the gates tell the owner about the input

    @property
    def graph(self):
        return self.seeded.graph


def derive_graph(
    spec_input: SpecInput,
    research_view: ResearchView | None = None,
    config: SpecConfig | None = None,
    *,
    everything_in_one_release: bool = False,
    consented: Collection[str] = (),
) -> Derived:
    cfg = config or default_config()
    facts = facts_from_input(spec_input, research_view)
    decision = decide_profile(spec_input, cfg, facts)
    inventory = build_inventory(spec_input, research_view, facts, decision.profile, cfg)
    plan = plan_release(
        spec_input,
        inventory,
        cfg,
        research_view=research_view,
        everything_in_one_release=everything_in_one_release,
        consented=consented,
    )
    seeded = seed(spec_input, research_view, plan)
    notes = apply_and_note(
        spec_input,
        research_view,
        facts,
        seeded.graph,
        inventory.registry,
        signin_by_role(spec_input),
    )
    return Derived(facts, decision, inventory, plan, seeded, notes)
