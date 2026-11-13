"""The VIEW DATA layer of the Spec Bench: pure functions that turn a finished (or paused) run's graph, pack and check
report into the JSON the Bench front end reads (``docs/api/spec-agent.md``, 'Response shapes the Bench front end reads').

No I/O except ``views.load`` (it reads a run folder through the run store), no model, deterministic. Owner-facing fields
are plain words: ids are always shown with their names, store rules are explained, regions carry importance and no position.
"""

from .data import data_view
from .estimate import PlanCounts, estimate, plan_calls, prices_from_profile
from .export import preview_html
from .g2 import g2
from .g15 import g15
from .journey import journey_view
from .load import LoadedRun, load_run, read_pack_folder
from .nav import nav_graph
from .needs import needs_view
from .packview import budget_view, check_dict, checks_view, diff_packs, pack_tree, tasks_view
from .preview import preview
from .screen import mock_profiles, screen_view

__all__ = [
    "LoadedRun",
    "PlanCounts",
    "budget_view",
    "check_dict",
    "checks_view",
    "data_view",
    "diff_packs",
    "estimate",
    "g2",
    "g15",
    "journey_view",
    "load_run",
    "mock_profiles",
    "nav_graph",
    "needs_view",
    "pack_tree",
    "plan_calls",
    "preview",
    "preview_html",
    "prices_from_profile",
    "read_pack_folder",
    "screen_view",
    "tasks_view",
]
