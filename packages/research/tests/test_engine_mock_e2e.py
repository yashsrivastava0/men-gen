"""Offline end to end: every archetype gets a valid, non-empty Research Note with zero network."""

from __future__ import annotations

import ast
import socket
import subprocess
import sys
from pathlib import Path

import pytest
from research_helpers import (
    DirectMockGateway,
    full_slots,
    make_context,
    make_engine,
    make_extras_request,
    make_policy,
    make_request,
)

import mde_research
from mde_content.packs import load_pack
from mde_content.themes import theme_ids
from mde_contracts.research import ResearchNote
from mde_contracts.slots import SLOT_IDS
from mde_research.synthesis import SPEC_SECTIONS

ARCHETYPES = ["commerce", "food_delivery", "booking_services", "internal_workforce", "custom"]
# the searches a run makes with every web job available: the depth cap (the plan reaches it) and,
# at Deep, the one gap the C8 mock reports
SEARCHES = {"light": 6, "standard": 12, "deep": 20 + 1}
SRC = Path(mde_research.__file__).parent


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Any connection to a non-loopback address (or any DNS lookup) fails the test. Loopback is
    allowed because the Windows event loop builds its self-pipe from a loopback socket pair."""
    loopback = {"127.0.0.1", "::1", "localhost"}
    real_connect = socket.socket.connect
    real_getaddrinfo = socket.getaddrinfo

    def connect(self: socket.socket, address: object, *args: object) -> object:
        host = address[0] if isinstance(address, tuple) else address
        if host not in loopback:
            raise AssertionError(f"the offline research run connected to {host}")
        return real_connect(self, address, *args)  # type: ignore[arg-type]

    def getaddrinfo(host: object, *args: object, **kwargs: object) -> object:
        if host not in loopback and host is not None:
            raise AssertionError(f"the offline research run resolved {host}")
        return real_getaddrinfo(host, *args, **kwargs)  # type: ignore[arg-type]

    def create_connection(address: tuple[str, int], *args: object, **kwargs: object) -> object:
        raise AssertionError(f"the offline research run connected to {address[0]}")

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
    monkeypatch.setattr(socket, "create_connection", create_connection)


def valid_target(applies_to: str | None, archetype: str) -> bool:
    if applies_to is None:
        return True
    if applies_to in SPEC_SECTIONS or applies_to in SLOT_IDS or applies_to == "scope.features":
        return True
    prefix, _, feature = applies_to.partition(":")
    return prefix == "scope.features" and feature in {f.id for f in load_pack(archetype).features}


@pytest.mark.parametrize("depth", ["light", "standard", "deep"])
@pytest.mark.parametrize("archetype", ARCHETYPES)
async def test_every_archetype_gets_a_valid_note_offline(archetype: str, depth: str) -> None:
    gw = DirectMockGateway()
    slots = full_slots(archetype)
    req = make_extras_request(
        archetype=archetype,
        depth=depth,
        slots=slots,
        references=["Rival App"],
        brand_url="https://brand.example/",
    )
    note = await make_engine(gw).run(req, make_context()[0])
    assert note.status == "ok", (archetype, depth, note.unavailable_reason)
    assert note.facts
    assert note.suggestions
    assert note.compliance  # the two flags of the slots
    assert note.brand is not None and note.brand.nearest_direction in set(theme_ids())
    assert note.integrations
    assert note.queries_run == gw.counts["S1.web_search"] == SEARCHES[depth]
    assert note.hash == note.compute_hash()
    assert ResearchNote.model_validate_json(note.model_dump_json()) == note

    fact_ids = {f.id for f in note.facts}
    assert all(s.fact_ids and set(s.fact_ids) <= fact_ids for s in note.suggestions)
    assert all(valid_target(s.applies_to, archetype) for s in note.suggestions)
    assert all(f.sources and all(s.url and s.retrieved_at for s in f.sources) for f in note.facts)
    assert {c.flag for c in note.compliance} <= {"financial_data", "precise_location"}
    known_urls = {s.url for f in note.facts for s in f.sources}
    assert all(c.url in known_urls for c in note.compliance)
    if depth == "deep":
        assert gw.counts["C8.synthesize_research"] == 2
    else:
        assert gw.counts["C8.synthesize_research"] == 1


@pytest.mark.parametrize("archetype", ARCHETYPES)
async def test_the_note_differs_per_archetype_and_is_deterministic(archetype: str) -> None:
    def req():
        return make_request(archetype=archetype, slots=full_slots(archetype))

    one = await make_engine(DirectMockGateway()).run(req(), make_context()[0])
    two = await make_engine(DirectMockGateway()).run(req(), make_context()[0])
    assert one == two
    titles = {s.title for s in one.suggestions}
    assert titles


async def test_archetypes_get_different_suggestions() -> None:
    seen: dict[str, set[str]] = {}
    for archetype in ARCHETYPES:
        note = await make_engine(DirectMockGateway()).run(
            make_request(archetype=archetype, slots=full_slots(archetype)), make_context()[0]
        )
        seen[archetype] = {s.title for s in note.suggestions if s.kind in {"feature", "pattern"}}
    assert len({frozenset(v) for v in seen.values()}) == len(ARCHETYPES)


async def test_suggestions_never_repeat_what_the_brief_already_has() -> None:
    from research_helpers import make_brief, make_slots

    brief = make_brief(
        features=[
            ("catalog_browse", "Browse", True),
            ("reviews", "Reviews", True),
            ("favorites", "Favorites", True),
        ]
    )
    slots = make_slots("commerce", features=["catalog_browse", "reviews", "favorites", "payments"])
    note = await make_engine(DirectMockGateway()).run(
        make_request(brief=brief, slots=slots), make_context()[0]
    )
    targets = {s.applies_to for s in note.suggestions}
    assert "scope.features:reviews" not in targets
    assert "scope.features:favorites" not in targets


async def test_the_enterprise_policy_runs_offline_too() -> None:
    from mde_content.policies import resolve_policy

    for policy_id in ("enterprise-it-services-firm", "regulated-bank", "guided-default"):
        policy = resolve_policy(policy_id)
        gw = DirectMockGateway()
        depth = policy.research.depth
        req = make_request(
            archetype="internal_workforce",
            depth=depth,
            slots=full_slots("internal_workforce"),
            policy=policy,
        )
        note = await make_engine(gw).run(req, make_context()[0])
        assert note.status == "ok", policy_id
        # with company names barred only R1, R4 and R5 apply; they still reach the depth ceiling
        assert gw.counts["S1.web_search"] == SEARCHES[depth], policy_id
        if not policy.research.web.allow_company_name_in_queries:
            for call in gw.calls:
                if call.call_id == "S1.web_search":
                    assert policy.org.split("-")[0] not in call.input.query  # type: ignore[attr-defined]
        if policy.research.web.allowed_domains:
            hosts = {s.domain for f in note.facts for s in f.sources}
            assert hosts <= set(policy.research.web.allowed_domains)


async def test_the_budget_never_drives_an_offline_run() -> None:
    note = await make_engine(DirectMockGateway()).run(
        make_request(slots=full_slots(), policy=make_policy(research_budget_usd="0.30")),
        make_context()[0],
    )
    assert note.cost_micros == 0
    assert note.status == "ok"


# --------------------------------------------------------------------------- layering


def imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module)
    return found


def test_the_engine_imports_only_core_contracts_prompts_and_content() -> None:
    forbidden = (
        "mde_llm_gateway",
        "mde_discovery",
        "mde_spec_builder",
        "mde_flow",
        "mde_persistence",
        "mde_spec_loader",
        "openai",
        "anthropic",
        "httpx",
        "requests",
        "sqlalchemy",
        "sqlite3",
        "langchain",
        "langgraph",
    )
    for path in SRC.rglob("*.py"):
        for module in imported_modules(path):
            assert not module.startswith(forbidden), f"{path.name} imports {module}"


def test_no_eval_or_exec_on_text() -> None:
    for path in SRC.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id not in {"eval", "exec", "compile"}, path.name


def test_importing_the_engine_loads_neither_the_gateway_nor_a_model_sdk() -> None:
    code = (
        "import sys, mde_research; "
        "bad = [m for m in sys.modules if m.split('.')[0] in {'mde_llm_gateway', 'openai', 'anthropic'}]; "
        "print(bad)"
    )
    out = subprocess.run(
        [sys.executable, "-I", "-c", code], capture_output=True, text=True, check=True, timeout=120
    )
    assert out.stdout.strip() == "[]"


def test_the_package_ships_its_data_files() -> None:
    assert (SRC / "data" / "progress_labels.yaml").is_file()
