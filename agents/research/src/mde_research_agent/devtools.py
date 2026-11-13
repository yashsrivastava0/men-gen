"""For the developer scripts only (headless run, bake-off, batch runs): build a service for them.

The app never imports this module. Offline modes (mock and replay) need the test doubles, which live ONLY in the
repository's ``tests/support/`` folder; they are loaded from there on demand. A live script run builds the real
service over a throw-away file store under the system temp folder (a script is not the app and keeps nothing).
"""

from __future__ import annotations

import sys
import tempfile
from datetime import date
from pathlib import Path
from typing import Any

from mde_core.settings import Settings
from mde_research_agent.service import ResearchService

TESTS_DIR = Path(__file__).resolve().parents[2] / "tests"


def load_doubles() -> Any:
    """``tests/support/compose.py`` (the offline composition); a plain exit if the test tree is not there."""
    if not (TESTS_DIR / "support").is_dir():
        raise SystemExit(
            "this offline mode needs the repository's agents/research/tests/support folder"
        )
    if str(TESTS_DIR) not in sys.path:
        sys.path.insert(0, str(TESTS_DIR))
    import support.compose as compose  # type: ignore[import-not-found]  # a test-tree module, loaded on demand

    return compose


def fake_world(**kwargs: Any) -> Any:
    """A ``FakeWorld`` (offline search and fetch content) from the test support folder."""
    load_doubles()
    from support.fakes import FakeWorld  # type: ignore[import-not-found]

    return FakeWorld(**kwargs)


def fake_search(name: str) -> Any:
    """An offline ``SearchPort`` double named like a provider (the bake-off's mock mode)."""
    world = fake_world()
    from support.fakes import FakeSearch  # type: ignore[import-not-found]

    search = FakeSearch(world)
    search.name = name
    return search


def script_service(
    *,
    core: Settings,
    mock_or_replay: bool,
    provider: str | None = None,
    world: Any = None,
    wrap_search: Any = None,
    wrap_fetch: Any = None,
    as_of: date | None = None,
    live_budget_micros: int | None = None,
) -> ResearchService:
    if mock_or_replay or core.llm_mode != "live":
        return load_doubles().build_service(  # type: ignore[no-any-return]
            core_settings=core,
            provider=provider,
            world=world,
            wrap_search=wrap_search,
            wrap_fetch=wrap_fetch,
            as_of=as_of,
        )
    from mde_research_agent.compose import build_live_service
    from mde_research_agent.store.files import FileStore

    scratch = Path(tempfile.mkdtemp(prefix="mde-research-script-"))
    return build_live_service(
        store=FileStore(scratch, lock=False),
        core=core,
        provider=provider,
        wrap_search=wrap_search,
        wrap_fetch=wrap_fetch,
        as_of=as_of,
        live_budget_micros=live_budget_micros,
    )
