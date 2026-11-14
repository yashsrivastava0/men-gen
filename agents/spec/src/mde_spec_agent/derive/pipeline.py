"""The whole derivation in one call: intake result in, baseline graph out (zero model cost)."""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass

from mde_compass_intake import ResearchView, SpecInput
from mde_spec_core.config import SpecConfig, default_config

from .facts import facts_from_input
from .inventory import Inventory, build_inventory
from .profile import ProfileDecision, decide_profile
from .release import ReleasePlan, plan_release
from .seed import SeedResult, seed


@dataclass
class Derived:
    facts: set[str]
    decision: ProfileDecision
    inventory: Inventory
    plan: ReleasePlan
    seeded: SeedResult

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
    moves: Collection[tuple[str, str]] = (),
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
        moves=moves,
    )
    return Derived(facts, decision, inventory, plan, seed(spec_input, research_view, plan))
