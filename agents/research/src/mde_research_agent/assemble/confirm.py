"""The confirm list, ranked by code (P6).

``_confirm`` (assemble/output.py) decides WHICH items the owner must confirm; this module decides the ORDER and labels each
entry, so a front end can show the money and legal facts first without reading a thousand items. Each entry gets:

* ``class``: ``money`` (a fee, a price, a typed amount with a currency), ``legal`` (a compliance note, a trust topic or a
  legal-adjacent item), ``identity`` (sign-in, KYC, verification) or ``other``; the first class that matches wins, in that order
  (rules in ``data/confirm.yaml``);
* ``group``: what it is about (the provider, the capability, the regime, the rule's subject, else the job);
* ``rank``: 1 is first. Order: class, high impact first, conflicts first, then the strongest evidence, then the item id;
* ``top``: one of the first ``top`` (20) entries.

Display is the front end's job. Pure function of the entries, the items and the catalog.
"""

from __future__ import annotations

import json
from typing import Any

from mde_research_agent.catalog.content import ConfirmClassRule, ConfirmConfig
from mde_research_agent.core.numbers import figures
from mde_research_agent.core.text import stem_set
from mde_research_agent.domain.research import Confirm, Item

EVIDENCE_ORDER = {"official": 0, "corroborated": 1, "single_source": 2, "unverified": 3}
GROUP_KEYS = ("provider", "capability", "regime", "subject", "topic", "entity", "name", "pattern")
_TEXT_FIELDS = (
    "statement",
    "summary",
    "value",
    "rule",
    "convention",
    "what_changed",
    "fees",
    "limits",
)


def _text(item: Item) -> str:
    d = item.data
    parts = [item.label, item.claim, *(str(d[k]) for k in _TEXT_FIELDS if d.get(k))]
    typed = d.get("typed")
    if isinstance(typed, dict):
        parts.append(json.dumps(typed, ensure_ascii=False))
    return " ".join(parts)


def _matches(rule: ConfirmClassRule, item: Item) -> bool:
    d = item.data
    if item.kind in rule.kinds or item.job in rule.jobs or set(item.flags) & set(rule.flags):
        return True
    if item.kind == "provider_fact" and d.get("field") in rule.fields:
        return True
    typed = d.get("typed")
    if rule.typed_currency and isinstance(typed, dict) and typed.get("currency"):
        return True
    if rule.words:
        text = _text(item)
        if stem_set(text) & {s for w in rule.words for s in stem_set(w)} and (
            not rule.needs_figure or figures(text)
        ):
            return True
    return False


def class_of(item: Item, cfg: ConfirmConfig) -> str:
    """The first class of ``data/confirm.yaml`` the item matches; ``other`` when none does."""
    for name, rule in cfg.classes.items():
        if name != "other" and _matches(rule, item):
            return name
    return "other"


def group_of(item: Item) -> str:
    for key in GROUP_KEYS:
        value = item.data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return item.job


def rank_confirm(confirm: list[Confirm], items: list[Item], cfg: ConfirmConfig) -> list[Confirm]:
    """The entries ordered and labelled (class, group, rank, top). An entry whose item is unknown goes last as ``other``."""
    by_id: dict[str, Item] = {i.id: i for i in items}
    order = {name: n for n, name in enumerate(cfg.classes)}
    rows: list[tuple[tuple[Any, ...], Confirm]] = []
    for c in confirm:
        item = by_id.get(c.item_id)
        if item is None:
            rows.append(
                ((order["other"], 1, 1, 9, c.item_id), c.model_copy(update={"class_": "other"}))
            )
            continue
        klass = class_of(item, cfg)
        key = (
            order[klass],
            0 if item.impact == "high" else 1,
            0 if "conflict" in item.flags else 1,
            EVIDENCE_ORDER[item.evidence],
            item.id,
        )
        rows.append(((key), c.model_copy(update={"class_": klass, "group": group_of(item)})))
    rows.sort(key=lambda t: t[0])
    return [
        c.model_copy(update={"rank": n, "top": n <= cfg.top})
        for n, (_, c) in enumerate(rows, start=1)
    ]
