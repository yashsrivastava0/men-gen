"""The consumption simulator core (doc 19 4.4): what a front-end worker would read, counted from the pack's files.

Three modes against a consumer: all at once (only if the whole pack fits), one by one (prefix, card and closure of one
screen) and in parallel (task cards by wave, worker slots, a dry-run timeline). Totals are recomputed from the files
and the build plan with the same rules the packager uses, so they equal the packager's task totals (tested).

A "pack" is anything with `.files`, `.manifest` and `.tasks`: the `Pack` of `assemble`, or one read back from a folder.
"""

from __future__ import annotations

import re
from typing import Any

from .config import SpecConfig, default_config
from .packform import WHOLE_FILE
from .render import TokenCounter

_USE_BLOCK = re.compile(r"use_block: (BLK-S\d+)@")
_RELATIVE_COST = {
    "S": 1,
    "M": 2,
    "L": 3,
}  # relative effort units for the dry-run timeline (not a time estimate)
_NOT_READ = ("MANIFEST.json", "index.json", WHOLE_FILE)  # the one-file form repeats the tree


def counter_for(pack: Any) -> TokenCounter:
    return TokenCounter(float(pack.manifest["tokenizer_chars_per_token"]))


def block_bodies(files: dict[str, str]) -> dict[str, str]:
    """BLK id -> body, read back from global/blocks.md (`BLK-S001: body` per line)."""
    out: dict[str, str] = {}
    for line in files.get("global/blocks.md", "").splitlines():
        bid, sep, body = line.partition(": ")
        if sep and bid.startswith("BLK-"):
            out[bid] = body
    return out


def screen_blocks(text: str) -> list[str]:
    """Block ids a screen file uses, in file order, each once."""
    return list(dict.fromkeys(_USE_BLOCK.findall(text)))


def _screen_of(path: str) -> str:
    return path.removeprefix("screens/").removesuffix(".md")


def task_tokens(
    pack: Any,
    task: dict[str, Any],
    counter: TokenCounter | None = None,
    config: SpecConfig | None = None,
) -> dict[str, int]:
    """The read total of one task card, recomputed from the files: prefix, shards, module, slices, front end."""
    cfg = config or default_config()
    c = counter or counter_for(pack)
    files: dict[str, str] = pack.files
    allowance = task.get("read_tokens", {}).get("front_end", cfg.budgets.front_end_allowance)
    prefix = c.count(files["prefix.bundle.md"])
    if task.get("wave") == "W0":
        parts = {"prefix": prefix, "front_end": allowance}
        return {
            **parts,
            "total": sum(parts.values()),
            "budget": int(pack.manifest["task_read_budget"]),
        }
    reads = task["reads"]
    shards = sum(c.count(files[p]) for p in sorted(set(reads.get("global", []))) if p in files)
    mod = reads.get("module")
    module = c.count(files[mod]) if mod and mod in files else 0
    bodies = block_bodies(files)
    seen: set[str] = set()
    dedupe = 0
    slices = 0
    for sp in reads.get("screens", []):
        text = files.get(sp, "")
        slices += c.count(text)
        for bid in screen_blocks(text):
            if bid in seen and bid in bodies:
                dedupe += c.count(bodies[bid])
            seen.add(bid)
    parts = {
        "prefix": prefix,
        "shards": shards,
        "module": module,
        "slices": slices - dedupe,
        "front_end": allowance,
    }
    return {**parts, "total": sum(parts.values()), "budget": int(pack.manifest["task_read_budget"])}


def _utf8(text: str) -> int:
    return len(text.encode("utf-8"))


def simulate_all(
    pack: Any, profile: Any = None, config: SpecConfig | None = None
) -> dict[str, Any]:
    """All at once: allowed only if the whole pack fits `whole_pack_tokens` (task budget minus the front end's
    allowance minus the task card); otherwise the plain reason. `profile` (a consumer profile) is only named."""
    cfg = config or default_config()
    b = cfg.budgets
    budget = int(pack.manifest["task_read_budget"])
    whole = budget - b.front_end_allowance - b.task_card_cap
    c = counter_for(pack)
    read = sorted(p for p in pack.files if p not in _NOT_READ and not p.startswith("evidence/"))
    total = sum(c.count(pack.files[p]) for p in read)
    who = getattr(profile, "id", None)
    out: dict[str, Any] = {
        "mode": "all_at_once",
        "consumer": who or pack.manifest.get("consumer"),
        "total_tokens": total,
        "whole_pack_tokens": whole,
        "allowed": total <= whole,
        "files": read if total <= whole else [],
        "bytes": sum(_utf8(pack.files[p]) for p in read) if total <= whole else 0,
    }
    out["reason"] = (
        "the whole pack fits one read"
        if out["allowed"]
        else f"the pack is {total} tokens and one read holds {whole} (task budget {budget} minus the "
        f"front end's {b.front_end_allowance} and the task card's {b.task_card_cap}); read it by screen or by task card"
    )
    return out


def simulate_one(
    pack: Any, screen_id: str, counter: TokenCounter | None = None, config: SpecConfig | None = None
) -> dict[str, Any]:
    """One by one: the files and bytes a worker reads for one screen (prefix, then the screen file), block bodies once."""
    cfg = config or default_config()
    c = counter or counter_for(pack)
    path = f"screens/{screen_id}.md"
    if path not in pack.files:
        return {
            "mode": "one_by_one",
            "screen": screen_id,
            "found": False,
            "reason": f"{screen_id} has no screen file in this pack (not in scope, or unknown)",
        }
    files = ["prefix.bundle.md", path]
    text = pack.files[path]
    prefix_t = c.count(pack.files["prefix.bundle.md"])
    slice_t = c.count(text)
    total = prefix_t + slice_t
    budget = int(pack.manifest["task_read_budget"])
    allowance = cfg.budgets.front_end_allowance
    return {
        "mode": "one_by_one",
        "screen": screen_id,
        "found": True,
        "files": files,
        "bytes": sum(_utf8(pack.files[p]) for p in files),
        "token_total": total,
        "tokens": {"prefix": prefix_t, "screen": slice_t},
        "front_end_allowance": allowance,
        "budget": budget,
        "fits": total + allowance <= budget,
        "blocks": screen_blocks(text),  # each body is listed once in the closure of a task bundle
    }


def default_workers(pack: Any, profile: Any = None) -> int:
    """Worker slots: the profile's `max_parallel` when it has one, else the pack's own hint, else 4."""
    mp = getattr(getattr(profile, "limits", None), "max_parallel", None)
    if mp:
        return int(mp)
    hints = [t.get("parallelism_hint") for t in pack.tasks if t.get("parallelism_hint")]
    return int(max(hints)) if hints else 4


def simulate_parallel(
    pack: Any, k: int | None = None, profile: Any = None, config: SpecConfig | None = None
) -> dict[str, Any]:
    """Task cards by wave, `k` worker slots, and a dry-run timeline in relative effort units (S=1, M=2, L=3).

    A task starts when a slot is free and all its `depends_on` tasks have ended; each row carries the task's read
    tokens. Deterministic: ties are broken by task id."""
    slots = k or default_workers(pack, profile)
    if slots < 1:
        raise ValueError("k must be at least 1")
    c = counter_for(pack)
    by_id = {t["id"]: t for t in pack.tasks}
    waves: dict[str, list[str]] = {}
    for t in pack.tasks:
        waves.setdefault(t["wave"], []).append(t["id"])
    free = [0] * slots
    end: dict[str, int] = {}
    rows: list[dict[str, Any]] = []
    order = sorted(pack.tasks, key=lambda t: (t["wave"], t["id"]))
    pending = list(order)
    while pending:
        ready = [t for t in pending if all(d in end for d in t.get("depends_on", []) if d in by_id)]
        if not ready:  # a dependency cycle: report it plainly rather than loop
            return {
                "k": slots,
                "waves": waves,
                "timeline": rows,
                "makespan": max(end.values(), default=0),
                "error": "tasks depend on each other in a loop: "
                + ", ".join(t["id"] for t in pending),
            }
        t = ready[0]
        pending.remove(t)
        slot = min(range(slots), key=lambda i: (free[i], i))
        dep_end = max((end[d] for d in t.get("depends_on", []) if d in end), default=0)
        start = max(free[slot], dep_end)
        stop = start + _RELATIVE_COST.get(t.get("complexity", "M"), 2)
        free[slot] = stop
        end[t["id"]] = stop
        tt = task_tokens(pack, t, c, config)
        rows.append(
            {
                "task": t["id"],
                "wave": t["wave"],
                "slot": slot,
                "start": start,
                "end": stop,
                "read_tokens": tt["total"],
                "budget": tt["budget"],
            }
        )
    return {
        "k": slots,
        "unit": "relative effort (S=1, M=2, L=3); not a time estimate",
        "waves": waves,
        "timeline": rows,
        "makespan": max(end.values(), default=0),
    }
