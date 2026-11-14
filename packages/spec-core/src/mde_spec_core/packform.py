"""Pack form and sharding that follow the consumer (doc 18 2.1, 2.3; doc 15 4.8, 5.4). Pure code, no model.

* ``decide_form``: the one-file form (``APP_SPEC.md``) exists only for a consumer whose read budget reaches
  ``one_file_min_consumer_window`` (about 80k) AND only when the whole pack fits that consumer's whole-pack read. A
  consumer below that floor never gets it, except for a pack so small that the design-target consumer could read it at
  once (doc 15 4.8 stands as amended; SPC-SC-01: a tiny app has one whole-pack file and no module folders).
* ``build_whole_file``: the one-file form is the tree's files concatenated in a fixed order; it adds no content.
* ``shard_copy``: a locale's copy is one file while it fits a shard, else one file per module and locale (doc 15 5.4),
  each split by the shard cap. Layout is decided with the fixed reference tokenizer (never a consumer's), so re-packaging
  for another consumer never moves a string.
"""

from __future__ import annotations

from typing import Any

from .canon import canonical_json, normalise_text
from .config import Budgets
from .graph import SpecGraph
from .render import TokenCounter

WHOLE_FILE = "APP_SPEC.md"
COPY_ROOT = "global/copy/"

_LEAD = ("AGENTS.md", "INDEX.md")
_NOT_IN_WHOLE = ("MANIFEST.json", "index.json", "prefix.bundle.md", WHOLE_FILE)
_NOT_IN_WHOLE_PREFIX = ("build/", "evidence/")


def whole_pack_budget(task_read_budget: int, b: Budgets) -> int:
    """What one whole-pack read holds for a consumer: its task budget minus the front end's allowance and the task card."""
    return task_read_budget - b.front_end_allowance - b.task_card_cap


def decide_form(total_tokens: int, task_read_budget: int, b: Budgets) -> str:
    """``one_file_and_tree`` or ``tree`` (the tree is always produced)."""
    if total_tokens > whole_pack_budget(task_read_budget, b):
        return "tree"
    if task_read_budget >= b.one_file_min_consumer_window:
        return "one_file_and_tree"
    return "one_file_and_tree" if total_tokens <= b.whole_pack_tokens else "tree"


def whole_file_paths(files: dict[str, str]) -> list[str]:
    rest = sorted(
        p
        for p in files
        if p not in _NOT_IN_WHOLE and p not in _LEAD and not p.startswith(_NOT_IN_WHOLE_PREFIX)
    )
    return [p for p in _LEAD if p in files] + rest


def build_whole_file(files: dict[str, str]) -> str:
    """The whole pack as one file: each file under a marker comment, in a fixed order (the tree is the source)."""
    return normalise_text("\n\n".join(f"<!-- {p} -->\n{files[p]}" for p in whole_file_paths(files)))


def _module_of(graph: SpecGraph, key: str) -> str:
    sid = key.split(".", 1)[0]
    s = graph.screens.get(sid)
    if s is None:
        return "shared"  # a global string (library standard states, glossary) belongs to no screen
    return s.module_id or "unowned"


def shard_copy(graph: SpecGraph, reference: TokenCounter, cap: int) -> dict[str, dict[str, Any]]:
    """path -> {key: text} for every locale. One file per locale while the biggest locale fits ``cap``; otherwise one
    file per module and locale (``global/copy/<locale>/<module>.json``), a module's file split into ``-2``, ``-3`` ...
    parts when it passes the cap."""
    sharded = any(reference.count(canonical_json(c)) > cap for c in graph.locale_copy.values())
    out: dict[str, dict[str, Any]] = {}
    for loc in sorted(graph.locale_copy):
        copy = graph.locale_copy[loc]
        if not sharded:
            out[f"{COPY_ROOT}{loc}.json"] = dict(copy)
            continue
        by_mod: dict[str, dict[str, str]] = {}
        for key in sorted(copy):
            by_mod.setdefault(_module_of(graph, key), {})[key] = copy[key]
        for mod in sorted(by_mod):
            parts: list[dict[str, str]] = [{}]
            size = 0
            for key, text in by_mod[mod].items():
                t = reference.count(canonical_json({key: text})) + 1
                if parts[-1] and size + t > cap:
                    parts.append({})
                    size = 0
                parts[-1][key] = text
                size += t
            for n, chunk in enumerate(parts, start=1):
                name = mod if n == 1 else f"{mod}-{n}"
                out[f"{COPY_ROOT}{loc}/{name}.json"] = chunk
    return out
