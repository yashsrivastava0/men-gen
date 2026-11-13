"""Provider dossiers: one entry per provider that has provider facts, built by CODE from the checked items (D-159).

The model extracts ONE fact with ONE quote at a time (kind ``provider_fact``); a dossier is the aggregate a front-end
engineer can write an integration from. Per field (the layout is ``data/dossiers.yaml``, per integration need): the value,
the typed value when there is exactly one, every value with its item id and evidence label, and, when nothing can be said,
an explicit ``null_reason``:

* ``not_stated_by_sources``: at least ``absence_after_sources`` distinct pages of the provider were read and none states it
  (or only unverified items do: ``values`` shows them, labelled ``unverified``);
* ``not_researched``: fewer pages of the provider were read, so absence proves nothing;
* ``conflict``: usable values of a ``scalar`` field differ, or two typed figures with the same unit, period, condition,
  currency and country differ. Nothing is defaulted; every value is kept and marked.

A field's ``evidence`` is the WEAKEST label of the values in its ``value`` (a list field is only as strong as its weakest
entry). ``completeness`` counts the layout's required fields that hold a usable value (not unverified, not in conflict);
``official_share`` is the share of filled fields whose evidence is ``official``. Items about another region or entity are not
used. Pure function of the items and the catalog, so a validator recomputes it from ``research.json`` alone (RV-24).
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable
from datetime import date

from mde_research_agent.assemble.capability import need_key_of
from mde_research_agent.assemble.items import canonical_amount, typed_slot
from mde_research_agent.catalog.models import Catalog
from mde_research_agent.core.norm_keys import clean_key
from mde_research_agent.core.sources import own_site
from mde_research_agent.core.text import stem_set
from mde_research_agent.domain.atoms import TypedValue
from mde_research_agent.domain.research import (
    Completeness,
    Dossier,
    DossierField,
    DossierValue,
    Evidence,
    Item,
    NullReason,
)

EVIDENCE_ORDER: dict[str, int] = {
    "official": 0,
    "corroborated": 1,
    "single_source": 2,
    "unverified": 3,
}
SKIP_FLAGS = frozenset({"other_region", "other_entity"})
SEPARATOR = " | "
CLASS_ORDER = {"official": 0, "help": 1}


def usable_facts(items: Iterable[Item]) -> list[Item]:
    """Provider facts a dossier may use: not about another region or entity."""
    return [i for i in items if i.kind == "provider_fact" and not set(i.flags) & SKIP_FLAGS]


def provider_groups(names: Iterable[str]) -> dict[str, str]:
    """``{clean provider name: the key its dossier uses}``. Two names are one provider when the stems of one are all in the
    other ("Acme" and "Acme Payments"); the key is the most common spelling, ties the shorter, then alphabetical."""
    counts = Counter(clean_key(n) for n in names if clean_key(n))
    keys = sorted(counts)
    parent = {k: k for k in keys}

    def find(k: str) -> str:
        while parent[k] != k:
            parent[k] = parent[parent[k]]
            k = parent[k]
        return k

    for i, a in enumerate(keys):
        sa = stem_set(a)
        for b in keys[i + 1 :]:
            sb = stem_set(b)
            small, big = (sa, sb) if len(sa) <= len(sb) else (sb, sa)
            if small and small <= big and len("".join(small)) >= 4 and find(a) != find(b):
                parent[find(b)] = find(a)
    members: dict[str, list[str]] = defaultdict(list)
    for k in keys:
        members[find(k)].append(k)
    out: dict[str, str] = {}
    for group in members.values():
        best = min(group, key=lambda k: (-counts[k], len(k), k))
        for k in group:
            out[k] = best
    return out


def _weakest(labels: Iterable[str]) -> Evidence | None:
    worst = max(labels, key=lambda e: EVIDENCE_ORDER[e], default=None)
    return worst  # type: ignore[return-value]


def _value_of(i: Item) -> DossierValue:
    d = i.data
    typed = TypedValue.model_validate(d["typed"]) if d.get("typed") else None
    return DossierValue(
        value=str(d.get("value", "")),
        typed=typed,
        item_id=i.id,
        evidence=i.evidence,
        newest_source=i.recency.newest_source if i.recency else None,
        domain=i.sources[0].domain if i.sources else None,
        source_ids=[s.id for s in i.sources],
    )


def _order(values: list[DossierValue]) -> list[DossierValue]:
    return sorted(
        values,
        key=lambda v: (
            EVIDENCE_ORDER[v.evidence],
            -(v.newest_source.toordinal() if isinstance(v.newest_source, date) else 0),
            v.item_id,
        ),
    )


def _typed_conflicts(pool: list[tuple[DossierValue, Item]]) -> set[str]:
    """Item ids whose typed figure differs from another value of the same unit, period, condition, currency and country."""
    groups: dict[tuple[str, ...], list[tuple[str, str]]] = defaultdict(list)
    for v, item in pool:
        typed = v.typed.model_dump() if v.typed else None
        amount = canonical_amount(typed)
        if not amount:
            continue
        currency = amount.split(" ", 1)[1] if " " in amount else ""
        where = (
            item.about_region,
            *sorted(clean_key(str(c)) for c in item.data.get("countries", [])),
        )
        key = (str(item.data.get("field")), currency, *typed_slot(typed), *where)
        groups[key].append((amount, v.item_id))
    flagged: set[str] = set()
    for members in groups.values():
        if len({a for a, _ in members}) > 1:
            flagged |= {iid for _, iid in members}
    return flagged


def _field(
    key: str,
    kind: str,
    facts: list[Item],
    *,
    absent: NullReason,
) -> DossierField:
    mine = [i for i in facts if i.data.get("field") == key]
    if not mine:
        return DossierField(null_reason=absent)
    by_id = {i.id: i for i in mine}
    values = _order([_value_of(i) for i in mine])
    usable = [v for v in values if v.evidence != "unverified"]
    pool = usable or values
    conflicted: set[str] = set()
    if kind == "scalar":
        distinct = {clean_key(v.value) for v in pool}
        if len(distinct) > 1:
            conflicted = {v.item_id for v in pool}
    elif kind == "typed":
        conflicted = _typed_conflicts([(v, by_id[v.item_id]) for v in pool])
    values = [v.model_copy(update={"conflict": v.item_id in conflicted}) for v in values]
    item_ids = sorted({v.item_id for v in values})
    if conflicted:
        return DossierField(values=values, item_ids=item_ids, null_reason="conflict")
    chosen = {v.item_id for v in (usable or values)}
    pool = [v for v in values if v.item_id in chosen]
    shown: list[DossierValue] = []
    seen: set[str] = set()
    for v in (
        pool
    ):  # the same words from two sources are one value in the joined text; both stay in ``values``
        sig = clean_key(v.value)
        if sig not in seen:
            seen.add(sig)
            shown.append(v)
    return DossierField(
        value=SEPARATOR.join(v.value for v in shown),
        typed=shown[0].typed if len(shown) == 1 else None,
        values=values,
        item_ids=item_ids,
        evidence=_weakest(v.evidence for v in shown),
    )


def _is_home_of(provider: str, domain: str) -> bool:
    """Is ``domain`` the provider's own site? Any label of the host may carry the name (``docs.acme.example``)."""
    labels = domain.split(".")[:-1] or [domain]
    return any(own_site(provider, f"{label}.x") for label in labels)


def _official_docs(
    provider: str, facts: list[Item], absent: NullReason
) -> tuple[DossierField, str | None]:
    best: tuple[int, str, str] | None = (
        None  # (class rank, url, domain): the best official page of the provider itself
    )
    users: dict[str, list[str]] = defaultdict(list)
    for i in facts:
        for s in i.sources:
            if s.source_class in CLASS_ORDER and _is_home_of(provider, s.domain):
                users[s.url].append(i.id)
                cand = (CLASS_ORDER[s.source_class], s.url, s.domain)
                if best is None or cand[:2] < best[:2]:
                    best = cand
    if best is None:
        return DossierField(null_reason=absent), None
    _, url, domain = best
    ids = sorted(set(users[url]))
    first = next(i for i in facts if i.id == ids[0])
    value = DossierValue(
        value=url,
        item_id=ids[0],
        evidence="official",
        newest_source=first.recency.newest_source if first.recency else None,
        domain=domain,
        source_ids=[s.id for s in first.sources if s.url == url],
    )
    return DossierField(value=url, values=[value], item_ids=ids, evidence="official"), url


def build_dossiers(
    items: list[Item], catalog: Catalog
) -> tuple[list[Dossier], dict[str, list[str]]]:
    """The dossiers (ordered by integration need, then provider) and an index from integration key to dossier ids."""
    cfg = catalog.dossiers
    facts = usable_facts(items)
    if not facts:
        return [], {}
    options = [i for i in items if i.kind == "service_option" and not set(i.flags) & SKIP_FLAGS]
    names = [str(i.data.get("provider", "")) for i in (*facts, *options)]
    canon = provider_groups(names)
    by_provider: dict[str, list[Item]] = defaultdict(list)
    for i in facts:
        by_provider[canon.get(clean_key(str(i.data.get("provider", ""))), "")].append(i)
    option_ids: dict[str, list[str]] = defaultdict(list)
    for i in options:
        option_ids[canon.get(clean_key(str(i.data.get("provider", ""))), "")].append(i.id)
    need_order = {k: n for n, k in enumerate(catalog.needs.needs)}
    built: list[tuple[str, str, Dossier]] = []
    for key, mine in by_provider.items():
        if not key:
            continue
        mine = sorted(mine, key=lambda i: i.id)
        spellings = Counter(str(i.data.get("provider", "")).strip() for i in mine)
        name = min(spellings, key=lambda s: (-spellings[s], len(s), s))
        stated = sorted(
            {str(i.data.get("need", "")).strip() for i in mine if i.data.get("need")},
            key=str.casefold,
        )
        keyed = Counter(
            k for i in mine if (k := need_key_of(str(i.data.get("need", "")), catalog.needs))
        )
        capability = (
            min(keyed, key=lambda k: (-keyed[k], need_order.get(k, 99), k)) if keyed else None
        )
        layout = cfg.layout_for(capability)
        pages = {s.url for i in mine for s in i.sources}
        absent: NullReason = (
            "not_stated_by_sources" if len(pages) >= cfg.absence_after_sources else "not_researched"
        )
        order = [*layout.required, *layout.optional]
        extra = sorted({str(i.data.get("field")) for i in mine} - set(order))
        fields: dict[str, DossierField] = {}
        official_url: str | None = None
        for fkey in (*order, *extra):
            spec = cfg.fields.get(fkey)
            if spec is not None and spec.derived == "official_source":
                fields[fkey], official_url = _official_docs(name, mine, absent)
            else:
                fields[fkey] = _field(fkey, spec.kind if spec else "list", mine, absent=absent)
        filled = sum(
            1
            for f in layout.required
            if fields[f].value is not None and fields[f].evidence not in (None, "unverified")
        )
        unverified = sum(1 for f in layout.required if fields[f].evidence == "unverified")
        usable_fields = [
            f
            for f in fields.values()
            if f.value is not None and f.evidence not in (None, "unverified")
        ]
        official = sum(1 for f in usable_fields if f.evidence == "official")
        integrations = sorted(
            {a.id for i in mine for a in i.applies_to if a.kind == "INT" and a.id}
        )
        built.append(
            (
                capability or "~",
                key,
                Dossier(
                    id="",
                    provider=name,
                    capability=capability,
                    needs=stated,
                    official_url=official_url,
                    fields=fields,
                    required=list(layout.required),
                    completeness=Completeness(
                        filled=filled,
                        required=len(layout.required),
                        share=round(filled / len(layout.required), 3) if layout.required else 0.0,
                        unverified=unverified,
                    ),
                    official_share=round(official / len(usable_fields), 3)
                    if usable_fields
                    else 0.0,
                    item_ids=[i.id for i in mine],
                    service_option_ids=sorted(option_ids.get(key, [])),
                    integrations=integrations,
                ),
            )
        )
    built.sort(key=lambda t: (need_order.get(t[0], 99), t[0], t[1]))
    dossiers = [
        d.model_copy(update={"id": f"DS-{n:03d}"}) for n, (_, _, d) in enumerate(built, start=1)
    ]
    index: dict[str, list[str]] = defaultdict(list)
    for d in dossiers:
        for k in {*d.integrations, *([d.capability] if d.capability else [])}:
            index[k].append(d.id)
    return dossiers, {k: sorted(v) for k, v in sorted(index.items())}
