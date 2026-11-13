"""Zero-cost derivation of the baseline Spec Graph from a Compass export and a Research view (doc 15 4, doc 18 1.1).

``facts_from_input`` -> ``detail_profile`` -> ``build_inventory`` (R-INV) -> ``plan_release`` -> ``seed_graph``. Plain code;
no model call anywhere; every word list lives in ``derive/data/*.yaml``.
"""

from .facts import facts_from_input
from .inventory import Inventory, PeerGap, ScreenProposal, build_inventory
from .pipeline import Derived, derive_graph
from .profile import ProfileDecision, decide_profile, detail_profile
from .release import ReleasePlan, plan_release
from .seed import SeedResult, research_adapter, seed, seed_graph

__all__ = [
    "Derived",
    "Inventory",
    "PeerGap",
    "ProfileDecision",
    "ReleasePlan",
    "ScreenProposal",
    "SeedResult",
    "build_inventory",
    "decide_profile",
    "derive_graph",
    "detail_profile",
    "facts_from_input",
    "plan_release",
    "research_adapter",
    "seed",
    "seed_graph",
]
