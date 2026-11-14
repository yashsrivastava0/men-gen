"""Shared helpers of the edge-case groups IN, RS, SC, CL, MK, ST, DG, SZ (the real exports, mutations, offline full runs).

Everything here is offline and deterministic. A full run goes through ``service.run_to_end`` in the gateway's mock mode in a
private data folder (about one second for a small app)."""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import Any

from mde_compass_intake import SpecInput, read_export

ROOT = Path(__file__).resolve().parents[3]
EXPORTS = ROOT / "docs" / "compass" / "research-inputs"
RESEARCH_OUTPUT = (
    ROOT
    / "docs"
    / "research-agent"
    / "outputs"
    / "claude-2026-11-13"
    / "medium-produce-marketplace-nashik"
    / "research.json"
)

NAMES = {
    "delhi": "delhi-restaurant-delivery",
    "hospital": "hospital-chain-enterprise",
    "pune": "pune-clinic-appointments",
    "quick": "quick-yoga-studio-bengaluru",
    "standard": "standard-produce-marketplace-nashik",
    "site": "synthetic-big-site-safety-central-america",
    "dakar": "synthetic-medium-school-parents-dakar",
    "bike": "synthetic-small-bike-repair-porto",
    "thorough": "thorough-freight-fleet-india",
}


def raw(key: str) -> dict[str, Any]:
    """A fresh deep copy of one real export (a mutation never touches the file or another test)."""
    path = EXPORTS / f"{NAMES.get(key, key)}.json"
    return json.loads(path.read_text(encoding="utf-8"))


@cache
def real(key: str) -> SpecInput:
    return read_export(raw(key))


def research_raw() -> dict[str, Any]:
    return json.loads(RESEARCH_OUTPUT.read_text(encoding="utf-8"))


def feature(si: SpecInput, name_part: str):
    return next(f for f in si.features if name_part.lower() in f.name.lower())


def clone(data: dict[str, Any]) -> dict[str, Any]:
    return copy.deepcopy(data)


# ---------------------------------------------------------------------------------------------- derivation


def derive(si: SpecInput, research: Any = None, **kw: Any):
    from mde_spec_agent.derive import derive_graph

    return derive_graph(si, research, **kw)


def validate(graph: Any, *, gate: int = 3, pack: Any = None, only: Any = None, **kw: Any):
    from mde_spec_core.validators.base import run_all
    from mde_spec_core.validators.context import make_context

    return run_all(make_context(graph, pack=pack, **kw), gate=gate, only=only)  # type: ignore[arg-type]


def codes(findings: list[Any], severity: str | None = None) -> set[str]:
    return {f.code for f in findings if severity is None or f.severity == severity}


# ---------------------------------------------------------------------------------------------- full offline runs


@dataclass
class Flow:
    result: dict[str, Any]
    data_dir: Path
    graph: Any
    check: dict[str, Any]
    run: dict[str, Any]
    pack_files: dict[str, str] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)

    @property
    def status(self) -> str:
        return str(self.result["status"])

    @property
    def banners(self) -> list[dict[str, Any]]:
        return list(self.check.get("banners", []))

    @property
    def flags(self) -> list[dict[str, Any]]:
        return list(self.check.get("flags", []))

    def g2(self, derived: Any = None, research: Any = None) -> dict[str, Any]:
        from mde_spec_agent.run.store import RunStore
        from mde_spec_agent.views import load_run
        from mde_spec_agent.views.g2 import g2 as g2_view

        loaded = load_run(RunStore(self.data_dir), self.result["run_id"])
        return g2_view(
            loaded.graph,
            loaded.pack,
            loaded.check,
            derived if derived is not None else loaded.derived,
            flags=loaded.flags,
            banners=loaded.banners,
            rcode_rows=loaded.rcode_rows,
            research=research if research is not None else loaded.research,
        )


_FLOWS: dict[str, Flow] = {}


def cached_flow(tmp_path_factory: Any, label: str, build: Any = None, /, **kw: Any) -> Flow:
    """One full run per label per session (a flow takes seconds): ``build()`` returns the export dict."""
    if label not in _FLOWS:
        export = build() if build is not None else None
        flow = run_flow(tmp_path_factory.mktemp(label.replace("/", "_")[:30]), None, export, **kw)
        assert flow.status == "approved", (
            f"flow {label} ended {flow.status}: {flow.result.get('note')}"
        )
        _FLOWS[label] = flow
    return _FLOWS[label]


def run_flow(
    tmp_path: Path,
    monkeypatch: Any,
    export: dict[str, Any] | None = None,
    *,
    synth: str | None = None,
    research: dict[str, Any] | None = None,
    name: str = "input",
) -> Flow:
    """The whole offline flow (both gates approved headlessly) for an export dict, or a synthetic preset."""
    import pytest

    with pytest.MonkeyPatch.context() as own:
        return _run_flow(tmp_path, monkeypatch or own, export, synth, research, name)


def _run_flow(
    tmp_path: Path,
    monkeypatch: Any,
    export: dict[str, Any] | None,
    synth: str | None,
    research: dict[str, Any] | None,
    name: str,
) -> Flow:
    from mde_spec_agent.service import run_to_end

    data = tmp_path / "spec-data"
    monkeypatch.setenv("SPEC_DATA_DIR", str(data))
    monkeypatch.setenv("MDE_ENV_FILE", "")
    monkeypatch.setenv("LLM_MODE", "mock")
    monkeypatch.setenv("RESEARCH_DATA_DIR", str(tmp_path / "no-research"))
    cpath = rpath = None
    if export is not None:
        cpath = tmp_path / f"{name}.json"
        cpath.write_text(json.dumps(export), encoding="utf-8")
    if research is not None:
        rpath = tmp_path / f"{name}.research.json"
        rpath.write_text(json.dumps(research), encoding="utf-8")
    result = run_to_end(compass_path=cpath, synth=synth, research_path=rpath, offline=True)
    run_dir = Path(result["run_dir"])
    graph_path = run_dir / "graph.json"
    from mde_spec_core.graph import SpecGraph

    graph = (
        SpecGraph.model_validate_json(graph_path.read_text(encoding="utf-8"))
        if graph_path.exists()
        else None
    )
    chk = run_dir / "evidence" / "self-check.json"
    check = json.loads(chk.read_text(encoding="utf-8")) if chk.exists() else {}
    run = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    pack_dir = run_dir / "pack"
    files = (
        {
            p.relative_to(pack_dir).as_posix(): p.read_text(encoding="utf-8")
            for p in sorted(pack_dir.rglob("*"))
            if p.is_file()
        }
        if pack_dir.exists()
        else {}
    )
    ev = run_dir / "events.jsonl"
    events = (
        [json.loads(line) for line in ev.read_text(encoding="utf-8").splitlines() if line.strip()]
        if ev.exists()
        else []
    )
    return Flow(result, data, graph, check, run, files, events)


# ---------------------------------------------------------------------------------------------- fixtures and mutations

FIXTURES = ROOT / "tests" / "edge" / "fixtures"


def fixture_path(case_id: str, name: str) -> Path:
    return FIXTURES / case_id / name


def fixture_json(case_id: str, name: str) -> Any:
    return json.loads(fixture_path(case_id, name).read_text(encoding="utf-8"))


def _walk(obj: Any, path: str) -> tuple[Any, str | int]:
    parts = path.split(".")
    for p in parts[:-1]:
        obj = obj[int(p)] if isinstance(obj, list) else obj[p]
    last = parts[-1]
    return obj, (int(last) if isinstance(obj, list) else last)


def mutate(data: dict[str, Any], ops: list[dict[str, Any]]) -> dict[str, Any]:
    """Apply ``{"set": "a.b.0.c", "value": v}``, ``{"del": path}`` or ``{"append": path, "value": v}`` to a deep copy."""
    out = copy.deepcopy(data)
    for op in ops:
        if "set" in op:
            holder, key = _walk(out, op["set"])
            holder[key] = op["value"]
        elif "del" in op:
            holder, key = _walk(out, op["del"])
            del holder[key]
        elif "append" in op:
            holder, key = _walk(out, op["append"])
            holder[key].append(op["value"])
        else:
            raise ValueError(f"unknown mutation {op}")
    return out


def mutated(case_id: str, base: str) -> dict[str, Any]:
    """A real export with the mutation recorded in the case's fixture folder (``mutation.json``)."""
    return mutate(raw(base), fixture_json(case_id, "mutation.json"))


# ---------------------------------------------------------------------------------------------- small hand-made inputs


def tiny_si(**over: Any) -> SpecInput:
    """A one-app input: two roles, one entity the owner manages, three core screens, one flow (the shape of the derive tests)."""
    from mde_compass_intake.spec_input import (
        App,
        Entity,
        EntityField,
        Feature,
        FlowStep,
        Idea,
        Locales,
        Module,
        Role,
        Screen,
        ScreenUse,
        Setting,
        StructureProposal,
        Trust,
    )
    from mde_compass_intake.spec_input import (
        Flow as InFlow,
    )

    locked = Trust(grade="locked", verified_quote=True)
    base: dict[str, Any] = dict(
        program_id="prog-edge",
        tier="quick",
        idea=Idea(summary="A small booking app"),
        locales=Locales(codes=("en",)),
        auth_methods=Setting(values=("email_password",), trust=Trust(grade="locked")),
        features=(
            Feature(
                id="FTR-001",
                name="Book a slot",
                intent="Customers book a slot",
                priority="must",
                user_evidenced=True,
                quote_ids=("FCT-001",),
                trust=locked,
            ),
            Feature(
                id="FTR-002",
                name="Manage slots",
                intent="The owner manages slots",
                priority="nice",
                user_evidenced=True,
                quote_ids=("FCT-002",),
                trust=locked,
            ),
        ),
        roles=(
            Role(id="ROL-001", name="Customer", goal="book", trust=Trust(grade="locked")),
            Role(id="ROL-002", name="Owner", goal="run it", trust=Trust(grade="locked")),
        ),
        apps=(App(id="APP-001", name="The app", roles=("ROL-001", "ROL-002")),),
        modules=(
            Module(
                id="MOD-001",
                name="Slots",
                apps=("APP-001",),
                features=("FTR-001", "FTR-002"),
                entry="SCR-001",
            ),
        ),
        structure=StructureProposal(
            present=True,
            screens=(
                Screen(
                    id="SCR-001",
                    name="Slots",
                    module="MOD-001",
                    apps=("APP-001",),
                    roles=("ROL-001", "ROL-002"),
                    serves=("FTR-001",),
                    entities=(ScreenUse(entity="ENT-001", use="view"),),
                    pattern="list",
                    phase="core",
                ),
                Screen(
                    id="SCR-002",
                    name="Create slot",
                    module="MOD-001",
                    apps=("APP-001",),
                    roles=("ROL-002",),
                    serves=("FTR-002",),
                    entities=(ScreenUse(entity="ENT-001", use="create"),),
                    pattern="form",
                    phase="core",
                ),
                Screen(
                    id="SCR-003",
                    name="Edit slot",
                    module="MOD-001",
                    apps=("APP-001",),
                    roles=("ROL-002",),
                    serves=("FTR-002",),
                    entities=(ScreenUse(entity="ENT-001", use="edit"),),
                    pattern="form",
                    phase="core",
                ),
            ),
            flows=(
                InFlow(
                    id="FLW-001",
                    name="Book",
                    role="ROL-001",
                    steps=(FlowStep(screen="SCR-001"),),
                    features=("FTR-001",),
                ),
            ),
            entities=(
                Entity(
                    id="ENT-001",
                    key="slot",
                    name="Slot",
                    module="MOD-001",
                    fields=(
                        EntityField(key="starts", type="datetime", required=True),
                        EntityField(key="price", type="money"),
                    ),
                    states=("open", "booked", "cancelled"),
                    access={"ROL-001": "view", "ROL-002": "manage"},
                ),
            ),
        ),
    )
    base.update(over)
    return SpecInput(**base)


def big_si(
    n_screens: int,
    *,
    tier: str = "quick",
    per_feature: int = 3,
    must: bool = True,
    n_modules: int = 3,
):
    """One app with ``n_screens`` core screens over many user-evidenced features (list, detail, form per entity)."""
    from mde_compass_intake.spec_input import (
        Entity,
        EntityField,
        Feature,
        Module,
        Screen,
        ScreenUse,
        StructureProposal,
        Trust,
    )

    base = tiny_si(tier=tier)
    n_feat = max(2, n_screens // per_feature)
    feats, mods, screens, ents = [], [], [], []
    locked = Trust(grade="locked", verified_quote=True)
    for k in range(n_feat):
        fid, mid, eid = f"FTR-{k + 1:03d}", f"MOD-{k % n_modules + 1:03d}", f"ENT-{k + 1:03d}"
        feats.append(
            Feature(
                id=fid,
                name=f"Manage thing {k + 1}",
                intent=f"The owner manages thing {k + 1}",
                priority="must" if must else "nice",
                user_evidenced=True,
                quote_ids=(f"FCT-{k + 1:03d}",),
                trust=locked,
            )
        )
        if k < n_modules:
            mods.append(
                Module(
                    id=mid,
                    name=f"Thing {k + 1}",
                    apps=("APP-001",),
                    features=tuple(f"FTR-{j + 1:03d}" for j in range(k, n_feat, n_modules)),
                    entry=f"SCR-{k * per_feature + 1:03d}",
                )
            )
        ents.append(
            Entity(
                id=eid,
                key=f"thing{k + 1}",
                name=f"Thing {k + 1}",
                module=mid,
                fields=(EntityField(key="title", type="text", required=True),),
                states=("open", "closed"),
                access={"ROL-001": "view", "ROL-002": "manage"},
            )
        )
        for j, (pat, use) in enumerate((("list", "view"), ("detail", "view"), ("form", "create"))):
            if len(screens) >= n_screens:
                break
            screens.append(
                Screen(
                    id=f"SCR-{k * per_feature + j + 1:03d}",
                    name=f"Thing {k + 1} {pat}",
                    module=mid,
                    apps=("APP-001",),
                    roles=("ROL-001", "ROL-002") if pat != "form" else ("ROL-002",),
                    serves=(fid,),
                    entities=(ScreenUse(entity=eid, use=use),),
                    pattern=pat,
                    phase="core",
                )
            )
    return base.model_copy(
        update=dict(
            features=tuple(feats),
            modules=tuple(mods),
            structure=StructureProposal(present=True, screens=tuple(screens), entities=tuple(ents)),
        )
    )
