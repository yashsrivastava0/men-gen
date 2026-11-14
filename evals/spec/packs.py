"""Packs as the tools see them: files, task cards, pilots, and (when known) the graph they came from.

A `ProofPack` can be built from a graph (assembled here), from a pack folder on disk, or from one of the real exports
through the derive pipeline (`baseline`). Card reading is by the card format of `mde_spec_core.render`.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from mde_spec_core.graph import SpecGraph
from mde_spec_core.lint_pack import read_pack_dir
from mde_spec_core.pack import Pack, assemble
from mde_spec_core.render import TokenCounter

ROOT = Path(__file__).resolve().parents[2]
EXPORTS = ROOT / "docs" / "compass" / "research-inputs"
BASELINES = {
    "quick": "quick-yoga-studio-bengaluru",
    "standard": "standard-produce-marketplace-nashik",
    "thorough": "thorough-freight-fleet-india",
}
SIZES = ("quick", "standard", "thorough")
SAMPLE_SIZE = {"quick": 3, "standard": 6, "thorough": 9}  # doc 16 section 4

_COUNTER = TokenCounter(3.0)


class HasFiles(Protocol):
    files: dict[str, str]


@dataclass
class ProofPack:
    name: str
    files: dict[str, str]
    tasks: list[dict[str, Any]]
    manifest: dict[str, Any]
    graph: SpecGraph | None = None
    pilots: dict[str, list[str]] = field(default_factory=dict)
    pack: Pack | None = None

    @property
    def profile(self) -> str:
        return str(self.manifest.get("profile", "standard"))

    @property
    def total_tokens(self) -> int:
        # APP_SPEC.md is the whole pack again as ONE file (the one-file form of a pack small enough to read at once); a
        # reader holds the tree OR the one file, so counting both would make every small pack look twice its size
        return sum(
            count(t)
            for p, t in self.files.items()
            if p not in ("MANIFEST.json", "index.json", "APP_SPEC.md")
        )

    def screen_ids(self) -> list[str]:
        return sorted(
            p.removeprefix("screens/").removesuffix(".md")
            for p in self.files
            if p.startswith("screens/") and p.endswith(".md") and not p.endswith(".could.md")
        )


def count(text: str) -> int:
    return _COUNTER.count(text)


def from_graph(name: str, graph: SpecGraph, **assemble_kw: Any) -> ProofPack:
    pack = assemble(graph, **assemble_kw) if assemble_kw else assemble(graph)
    return ProofPack(name, dict(pack.files), pack.tasks, pack.manifest, graph, pack.pilots, pack)


def from_dir(path: str | Path, name: str | None = None) -> ProofPack:
    """A pack folder as written by `write_pack_dir` (no graph: the graph-based tools report `not_measured`)."""
    root = Path(path)
    files = read_pack_dir(root)
    plan = json.loads(files.get("build/build-plan.json", '{"tasks": []}'))
    manifest = json.loads(files.get("MANIFEST.json", "{}"))
    return ProofPack(name or root.name, files, list(plan.get("tasks", [])), manifest)


def baseline(key: str) -> ProofPack:
    """Quick, standard or thorough: the real export through the derive pipeline, assembled (zero model cost)."""
    from mde_compass_intake import read_export
    from mde_spec_agent.derive import derive_graph

    path = EXPORTS / f"{BASELINES.get(key, key)}.json"
    si = read_export(json.loads(path.read_text(encoding="utf-8")))
    return from_graph(key, derive_graph(si).graph)


# ---------------------------------------------------------------------------------------------------- card reading

_KV = re.compile(r"(\w+): ")
_LIST = re.compile(r"\[([^\]]*)\]")


@dataclass
class Card:
    id: str
    name: str
    app: str
    module: str
    screen_type: str
    leads_to: list[str]
    regions: int
    actions: int
    states: int
    acceptance: list[str]
    text: str  # the card only (no closure)

    @property
    def weight(self) -> int:
        return self.regions + self.actions + self.states + len(self.acceptance)


def card_text(file_text: str) -> str:
    """The card without its generated closure (`## closure` starts the closure)."""
    return file_text.split("\n## closure", 1)[0].rstrip("\n") + "\n"


def _field(line: str, key: str) -> str:
    m = re.search(rf"\b{key}: (.*?)(?:\s{{3}}\w+: |$)", line)
    return m.group(1).strip() if m else ""


def parse_card(file_text: str) -> Card:
    text = card_text(file_text)
    lines = text.splitlines()
    head = " ".join(lines[1:3]) if len(lines) > 2 else ""
    sid = _field(lines[1], "id") if len(lines) > 1 else ""
    name = _field(lines[1], "name") if len(lines) > 1 else ""
    sect = ""
    regions = actions = states = 0
    acceptance: list[str] = []
    leads: list[str] = []
    for ln in lines:
        if ln.startswith("leads_to:") or " leads_to: " in ln:
            m = re.search(r"leads_to: \[([^\]]*)\]", ln)
            if m:
                leads = [x.strip() for x in m.group(1).split(",") if x.strip()]
        if re.match(r"^[a-z_]+:", ln):
            sect = ln.split(":", 1)[0]
            continue
        if ln.startswith("  - "):
            if sect == "regions":
                regions += 1
            elif sect == "actions":
                actions += 1
            elif sect == "states":
                states += 1
            elif sect == "acceptance":
                acceptance.append(ln[4:].strip())
    app = re.search(r"\bapp: (\S+)", head)
    module = re.search(r"\bmodule: (\S+)", head)
    stype = re.search(r"\bscreen_type: (\S+)", head)
    return Card(
        id=sid,
        name=name,
        app=app.group(1) if app else "",
        module=module.group(1) if module else "",
        screen_type=stype.group(1) if stype else "",
        leads_to=leads,
        regions=regions,
        actions=actions,
        states=states,
        acceptance=acceptance,
        text=text,
    )


def cards_of(pack: ProofPack) -> dict[str, Card]:
    return {sid: parse_card(pack.files[f"screens/{sid}.md"]) for sid in pack.screen_ids()}


# ---------------------------------------------------------------------------------------------------- task cards and reads


def read_paths(task: dict[str, Any]) -> list[str]:
    reads = task.get("reads", {}) or {}
    out: list[str] = []
    if reads.get("prefix"):
        out.append(str(reads["prefix"]))
    out += [str(p) for p in reads.get("global", []) or []]
    if reads.get("module"):
        out.append(str(reads["module"]))
    out += [str(p) for p in reads.get("screens", []) or []]
    return list(dict.fromkeys(out))


def task_for_screen(pack: ProofPack, sid: str) -> dict[str, Any] | None:
    want = f"screens/{sid}.md"
    for t in pack.tasks:
        if want in ((t.get("reads", {}) or {}).get("screens", []) or []):
            return t
    return None


def card_reads(pack: HasFiles, task: dict[str, Any]) -> dict[str, str]:
    """EXACTLY the files the task card lists (a listed file the pack lacks is simply missing)."""
    return {p: pack.files[p] for p in read_paths(task) if p in pack.files}


# ---------------------------------------------------------------------------------------------------- deterministic samples

_SLOTS: dict[str, list[tuple[str, ...]]] = {
    "quick": [("list", "detail"), ("form", "wizard"), ("checkout",)],
    "standard": [
        ("list",),
        ("detail",),
        ("form", "wizard"),
        ("checkout",),
        ("status", "confirmation"),
        ("dashboard", "calendar", "search", "report", "notifications", "profile", "settings"),
    ],
}
_SLOTS["thorough"] = [*_SLOTS["standard"], ("@cross_app",), ("@heavy",), ("@any",)]
_LOW_VALUE = ("auth", "permission", "legal", "onboarding")


def pick_samples(pack: ProofPack, size: str | int) -> list[str]:
    """Screens for the consumer probe, chosen by code (no randomness): Quick 3 (a list or detail, a form, a checkout),
    Standard 6 (also a status screen), Thorough 9 (also a cross-app step and the heaviest screen). A missing type is
    filled by the next screen of a type not yet taken (heaviest first), then by id."""
    n = SAMPLE_SIZE[size] if isinstance(size, str) else int(size)
    slots = _SLOTS[size] if isinstance(size, str) else _SLOTS["thorough"][:n]
    cards = cards_of(pack)
    live = dict(cards.items())
    picked: list[str] = []

    def free(pred: Any) -> list[str]:
        cands = [sid for sid, c in live.items() if sid not in picked and pred(c)]
        return sorted(cands, key=lambda sid: (-live[sid].weight, sid))

    def not_low(c: Card) -> bool:
        return c.screen_type not in _LOW_VALUE

    def cross_app(c: Card) -> bool:
        return any(live[t].app != c.app for t in c.leads_to if t in live)

    for slot in slots:
        if len(picked) >= n:
            break
        if slot == ("@cross_app",):
            cands = free(lambda c: cross_app(c))
        elif slot == ("@heavy",):
            cands = free(not_low)
        elif slot == ("@any",):
            cands = free(
                lambda c: not_low(c) and c.screen_type not in {live[p].screen_type for p in picked}
            )
        else:
            cands = free(lambda c, s=slot: c.screen_type in s)
        if cands:
            picked.append(cands[0])
    while len(picked) < n:
        taken_types = {live[p].screen_type for p in picked}
        cands = (
            free(lambda c, taken=taken_types: c.screen_type not in taken and not_low(c))
            or free(not_low)
            or free(lambda c: True)
        )
        if not cands:
            break
        picked.append(cands[0])
    return picked
