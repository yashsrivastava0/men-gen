"""``pack(research, need_card)`` (copied from Research, D-175): the reference implementation of how Spec reads ``research.json`` (D-159, doc 13 section 9.3).

Pure code over the parsed file: no model, no embeddings, no similarity, no I/O. Selection is by STRUCTURE:

1. **direct**: every item whose ``applies_to`` names a Brief id of the need card (``FTR-001``, ``INT:payments``, or ``NEW``);
2. **hop**: one hop along ``applies_to``: items that share a Brief id with a direct item (a feature's items name the entity
   they need; the entity's other items come along);
3. **lane**: the lane's essentials (when the card names a lane): the best items of every kind of the lane's slice, the items of
   the lane's decisions, and conflicts;
4. everything else is a title-only **index** (id, kind, label, evidence): nothing is left out of the file, and Spec never asks
   a model to fetch more.

Selected content (the decisions of the lane, items, dossiers, platform constraints, confirm entries, conflicts) is filled into
**packs** in that order. A pack that would pass ``token_budget`` is SPLIT: the next pack starts, nothing is truncated or cut
mid-item. One unit that is larger than the budget on its own becomes its own pack, flagged ``over_budget``. The file is read
whole, appendix included: a Brief-linked item in the appendix is still in the pack, after the main-store items (rank order).

The need card is ``{"brief_ids": [...], "lane": "integrations" | null, "token_budget": 8000, "kinds": [...] | null,
"essentials_per_kind": 2}``; only ``brief_ids`` is required. The result is deterministic: the same file and card always give the
same packs, whatever the order of ``items`` in the file. Contract tests: ``tests/test_pack.py`` (and ``tests/contract/test_intake_parity.py`` against Research's copy); the note for Spec is
``docs/research-agent/spec-contract.md``.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

DATA_DIR = Path(__file__).resolve().parent / "data"


@lru_cache(maxsize=1)
def load_default_notes() -> dict[str, str]:
    raw = yaml.safe_load((DATA_DIR / "use_notes.yaml").read_text(encoding="utf-8")) or {}
    return {str(k): str(v) for k, v in raw.items()}


# --- the compact view of an item and a dossier (copied from Research's assemble/store.py, D-175) -----------------------
# Pack content is part of the contract with Research: the parity test compares the output of both copies.


def tokens_of(obj: Any) -> int:
    """The token estimate used everywhere: characters over 4 of the compact JSON."""
    return len(json.dumps(obj, ensure_ascii=False, separators=(",", ":"), default=str)) // 4


def compact_item(item: dict[str, Any], default_notes: dict[str, str]) -> dict[str, Any]:
    """One item in the compact view: sources by id, empty fields dropped, ``use_note`` only when it is not the default."""
    out: dict[str, Any] = {
        "id": item["id"],
        "job": item.get("job"),
        "kind": item.get("kind"),
        "label": item.get("label"),
        "claim": item.get("claim"),
        "impact": item.get("impact"),
        "evidence": item.get("evidence"),
        "tier": item.get("tier"),
        "confidence": item.get("confidence"),
    }
    note = item.get("use_note") or ""
    if note and note != default_notes.get(item.get("kind", ""), ""):
        out["use_note"] = note
    if item.get("flags"):
        out["flags"] = item["flags"]
    if item.get("applies_to"):
        out["applies_to"] = [
            f"{a['kind']}:{a['id']}:{a['relation']}"
            if a.get("id")
            else f"{a['kind']}::{a['relation']}"
            for a in item["applies_to"]
        ]
    if item.get("sources"):
        out["sources"] = [s["id"] for s in item["sources"]]
    if item.get("independent_domains"):
        out["independent"] = item["independent_domains"]
    prev = item.get("prevalence")
    if prev:
        out["prevalence"] = f"{prev['seen_in']}/{prev['of']} {prev['level']}"
    rec = item.get("recency") or {}
    if rec.get("newest_source"):
        out["newest_source"] = rec["newest_source"]
    if item.get("about_region") not in (None, "unknown"):
        out["region"] = item["about_region"]
    data = {k: v for k, v in (item.get("data") or {}).items() if v not in (None, "", [], {})}
    if data:
        out["data"] = data
    return out


def _present(value: Any) -> Any:
    """``value`` without null, empty text, empty list and empty object entries (a nested walk), for the compact view."""
    if isinstance(value, dict):
        out = {k: _present(v) for k, v in value.items()}
        return {k: v for k, v in out.items() if v not in (None, "", [], {})}
    if isinstance(value, list):
        return [_present(v) for v in value]
    return value


def _compact_field(f: dict[str, Any]) -> dict[str, Any]:
    if f.get("value") is None and not f.get("values"):
        return {"null_reason": f.get("null_reason")}  # an explicit unknown (R27)
    out = _present(f)
    values = f.get("values") or []
    if len(values) == 1 and not values[0].get("conflict"):
        out.pop(
            "values", None
        )  # the single value is already ``value``; its item is in ``item_ids``
    return out


def compact_dossier(d: dict[str, Any]) -> dict[str, Any]:
    """A dossier in the compact view: empty parts are dropped, a field that is empty keeps its ``null_reason`` (an explicit
    unknown, R27), a field with one value does not repeat it, and a conflict or several values keep every value."""
    out = _present({k: v for k, v in d.items() if k != "fields"})
    out["fields"] = {key: _compact_field(f) for key, f in d.get("fields", {}).items()}
    return out


SCHEMA = "research.pack.v1"
DEFAULT_TOKEN_BUDGET = 8000
MIN_TOKEN_BUDGET = 500
DEFAULT_ESSENTIALS = 2
DECISION_ITEMS = 3  # the best items of each lane decision that come along as lane essentials
_BUCKETS = ("lane_decisions", "items", "dossiers", "platform_constraints", "confirm", "conflicts")
_WORST = 10**9


def _normalize(card: Mapping[str, Any]) -> dict[str, Any]:
    ids = [str(x).strip() for x in card.get("brief_ids", []) if str(x).strip()]
    budget = int(card.get("token_budget") or DEFAULT_TOKEN_BUDGET)
    kinds = card.get("kinds")
    return {
        "brief_ids": sorted(set(ids)),
        "lane": card.get("lane") or None,
        "token_budget": max(MIN_TOKEN_BUDGET, budget),
        "kinds": sorted({str(k) for k in kinds}) if kinds else None,
        "essentials_per_kind": max(0, int(card.get("essentials_per_kind", DEFAULT_ESSENTIALS))),
    }


def _entries(item: Mapping[str, Any]) -> list[tuple[str, str | None]]:
    return [(str(a.get("kind")), a.get("id")) for a in item.get("applies_to", [])]


def _names(entry: tuple[str, str | None], named: set[str]) -> bool:
    kind, ident = entry
    if kind == "NEW":
        return "NEW" in named
    return ident is not None and (ident in named or f"{kind}:{ident}" in named)


def _rank(item: Mapping[str, Any]) -> tuple[int, str]:
    return (int(item.get("rank") or _WORST), str(item.get("id", "")))


def _source_table(items: list[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    table: dict[str, dict[str, Any]] = {}
    for i in items:
        for s in i.get("sources", []):
            table[s["id"]] = {
                "domain": s.get("domain"),
                "class": s.get("source_class"),
                "url": s.get("url"),
                "published": s.get("published_on"),
            }
    return table


def pack(
    research: Mapping[str, Any],
    need_card: Mapping[str, Any],
    *,
    default_notes: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Select, rank and split what applies to ``need_card``. See the module docstring for the contract."""
    if default_notes is None:
        default_notes = load_default_notes()
    card = _normalize(need_card)
    named = set(card["brief_ids"])
    items: list[Mapping[str, Any]] = sorted(research.get("items", []), key=_rank)
    by_id = {i["id"]: i for i in items}
    allowed = (lambda i: i.get("kind") in card["kinds"]) if card["kinds"] else (lambda i: True)

    # 1. direct, 2. one hop, 3. the lane's essentials --------------------------------------------------------------
    why: dict[str, str] = {}
    for i in items:
        if allowed(i) and any(_names(e, named) for e in _entries(i)):
            why[i["id"]] = "direct"
    hop_ids = {
        f"{k}:{ident}"
        for iid, w in why.items()
        if w == "direct"
        for k, ident in _entries(by_id[iid])
        if k != "NEW" and ident is not None and not _names((k, ident), named)
    }
    for i in items:
        if (
            i["id"] not in why
            and allowed(i)
            and any(e[1] is not None and f"{e[0]}:{e[1]}" in hop_ids for e in _entries(i))
        ):
            why[i["id"]] = "hop"
    lane = card["lane"]
    if lane:
        in_lane = [
            by_id[x]
            for x in research.get("slices", {}).get(lane, {}).get("item_ids", [])
            if x in by_id
        ]
        in_lane.sort(key=_rank)
        seen: dict[str, int] = {}
        essentials: list[str] = []
        for i in in_lane:
            k = str(i.get("kind"))
            if seen.get(k, 0) < card["essentials_per_kind"]:
                seen[k] = seen.get(k, 0) + 1
                essentials.append(i["id"])
            elif "conflict" in i.get("flags", []):
                essentials.append(i["id"])
        for d in research.get("lane_decisions", []):
            if d.get("lane") == lane:
                essentials += sorted(
                    (x for x in d.get("item_ids", []) if x in by_id), key=lambda x: _rank(by_id[x])
                )[:DECISION_ITEMS]
        for iid in essentials:
            if iid not in why and allowed(by_id[iid]):
                why[iid] = "lane"
    order = {"direct": 0, "hop": 1, "lane": 2}
    selected = sorted(why, key=lambda x: (order[why[x]], *_rank(by_id[x])))
    chosen = set(selected)

    # the units, in the order they fill the packs ----------------------------------------------------------------
    units: list[tuple[str, dict[str, Any], list[str]]] = []  # (bucket, payload, source ids)
    for d in research.get("lane_decisions", []):
        if lane and d.get("lane") == lane:
            units.append(("lane_decisions", dict(d), []))
    for iid in selected:
        item = by_id[iid]
        payload = compact_item(dict(item), dict(default_notes))
        payload["why"] = why[iid]
        units.append(("items", payload, [s["id"] for s in item.get("sources", [])]))
    wanted_int = {x.split(":", 1)[1] if x.startswith("INT:") else x for x in named}
    for d in research.get("dossiers", []):
        if set(d.get("item_ids", [])) & chosen or wanted_int & {
            *d.get("integrations", []),
            d.get("capability"),
        }:
            units.append(("dossiers", compact_dossier(dict(d)), []))
    for row in research.get("platform_constraints", []):
        if row.get("item_id") in chosen:
            units.append(("platform_constraints", dict(row), []))
    for c in research.get("confirm", []):
        if c.get("item_id") in chosen:
            units.append(("confirm", dict(c), []))
    for c in research.get("conflicts", []):
        if set(c.get("item_ids", [])) & chosen:
            units.append(("conflicts", dict(c), []))

    # split, never truncate -----------------------------------------------------------------------------------------
    table = _source_table(items)
    packs: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None

    def new_pack() -> dict[str, Any]:
        return {
            "no": len(packs) + 1,
            "tokens": 0,
            "over_budget": False,
            "sources": {},
            **{b: [] for b in _BUCKETS},
        }

    for bucket, payload, source_ids in units:
        if current is None:
            current = new_pack()

        def cost(
            p: dict[str, Any], payload: dict[str, Any] = payload, source_ids: list[str] = source_ids
        ) -> int:
            extra = {s: table[s] for s in source_ids if s in table and s not in p["sources"]}
            return tokens_of(payload) + tokens_of(extra)

        if current["tokens"] and current["tokens"] + cost(current) > card["token_budget"]:
            packs.append(current)
            current = new_pack()
        current["tokens"] += cost(current)
        current[bucket].append(payload)
        for s in source_ids:
            if s in table:
                current["sources"][s] = table[s]
        if current["tokens"] > card["token_budget"]:
            current["over_budget"] = True  # one unit larger than the budget: it stands alone, whole
    if current is not None:
        packs.append(current)
    for n, p in enumerate(packs, start=1):
        p["of"] = len(packs)
        p["no"] = n

    index = [
        {
            "id": i["id"],
            "kind": i.get("kind"),
            "label": i.get("label"),
            "evidence": i.get("evidence"),
            **({"store": "appendix"} if i.get("store") == "appendix" else {}),
        }
        for i in items
        if i["id"] not in chosen
    ]
    return {
        "schema": SCHEMA,
        "program_id": research.get("program_id"),
        "brief_hash": research.get("brief_hash"),
        "need_card": card,
        "packs": packs,
        "index": index,
        "stats": {
            "items_total": len(items),
            "direct": sum(1 for w in why.values() if w == "direct"),
            "hop": sum(1 for w in why.values() if w == "hop"),
            "lane": sum(1 for w in why.values() if w == "lane"),
            "indexed": len(index),
            "packs": len(packs),
            "tokens": sum(p["tokens"] for p in packs),
            "index_tokens": tokens_of(index),
            "token_budget": card["token_budget"],
        },
    }
