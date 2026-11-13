"""The builder-needs matrix BN-01..BN-17 (doc 15 7.1, doc 18 3.1).

Every need is `present`, `default` (the library supplies it, flagged), `assumed` (flagged) or `missing`. A `missing`
need ends the run `partial` with a plain list and NEVER reaches Gate 2. Judged from the graph (and the pack when one is
given): code only, no model. Each row names the pack files that hold the need and the validators that prove it.
"""

from __future__ import annotations

import fnmatch
from collections.abc import Collection, Iterable
from dataclasses import dataclass, field
from typing import Any, Literal

from . import vocab
from .graph import Envelope, SpecGraph
from .render import bound_fields

NeedStatus = Literal["present", "default", "assumed", "missing"]

# facts that mean "an outside provider is involved": such an app with no integration record has a missing need
_PROVIDER_FACTS = ("payments", "provider_side_flow")
_FIRST_PROVENANCE = {"user", "compass", "research", "policy", "code", "model"}


@dataclass
class Need:
    id: str
    title: str
    status: NeedStatus
    why: str  # plain words: what was found
    files: list[str] = field(
        default_factory=list
    )  # pack files that hold it (those present when a pack is given)
    expected_files: list[str] = field(default_factory=list)  # file patterns that should hold it
    validators: list[str] = field(default_factory=list)
    flagged: int = 0  # nodes of this need that are library defaults or assumed

    @property
    def is_flagged(self) -> bool:
        return self.status in ("default", "assumed") or self.flagged > 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "status": self.status,
            "why": self.why,
            "files": self.files,
            "expected_files": self.expected_files,
            "validators": self.validators,
            "flagged": self.flagged,
        }


_SPEC: tuple[tuple[str, str, tuple[str, ...], tuple[str, ...]], ...] = (
    (
        "BN-01",
        "What it is, who uses it, apps, roles",
        ("global/roles-and-access.md", "apps/*/app.md"),
        ("V-08", "V-29"),
    ),
    (
        "BN-02",
        "What first: in, later, out",
        ("global/scope-ledger.json",),
        ("V-16", "V-19", "V-28"),
    ),
    (
        "BN-03",
        "Screens and movement",
        ("global/navigation.md", "global/routes.json", "modules/*/module.md"),
        ("V-04", "V-05", "V-07"),
    ),
    (
        "BN-04",
        "Data: entities, fields, statuses, who sees what",
        ("data-model/*.md",),
        ("V-06", "V-09", "V-11"),
    ),
    (
        "BN-05",
        "Rules, formulas, validations; unknown numbers flagged",
        ("global/rules.md", "global/behaviors.md"),
        ("V-08", "V-36", "V-45"),
    ),
    (
        "BN-06",
        "Contract: operations, errors, pagination, auth",
        ("contract/*",),
        ("V-10", "V-15", "V-30"),
    ),
    (
        "BN-07",
        "Realistic data: names, prices, formats, edge cases",
        ("contract/fixtures/*",),
        ("V-15", "V-41"),
    ),
    (
        "BN-08",
        "Look: tokens, tone, accessibility, dark mode",
        ("global/design-direction.md", "global/tokens.dtcg.json"),
        ("V-12", "V-24"),
    ),
    (
        "BN-09",
        "Per screen: regions, bindings, actions, states, copy, acceptance",
        ("screens/*.md",),
        ("V-06", "V-07", "V-13", "V-14", "V-22", "V-27"),
    ),
    (
        "BN-10",
        "The outside world and what is mocked",
        ("global/integrations.md",),
        ("V-16", "V-36"),
    ),
    ("BN-11", "What is allowed: SDK, packages, kit", ("global/platform.md",), ("V-23",)),
    (
        "BN-12",
        "Build order, ownership, interface freeze",
        ("build/build-plan.json",),
        ("V-26", "V-28"),
    ),
    ("BN-13", "What done means; Locked, Default, Open", ("screens/*.md",), ("V-17",)),
    ("BN-14", "Unknown, assumed, degraded; how to ask", ("INDEX.md",), ("V-17", "V-45")),
    ("BN-15", "Platform and store rules", ("global/platform.md",), ("V-33",)),
    ("BN-16", "Global behaviour", ("global/behaviors.md",), ("V-35",)),
    ("BN-17", "Phases and scope cap", ("global/scope-ledger.json", "apps/*/app.md"), ("V-28",)),
)


def _provenance_kinds(n: Envelope) -> set[str]:
    return {p.kind for p in n.provenance}


def _is_flagged(n: Envelope) -> bool:
    kinds = _provenance_kinds(n)
    return n.assumed or "assumed" in kinds or (bool(kinds) and kinds == {"library"})


def _grade(nodes: Collection[Envelope]) -> tuple[NeedStatus, int]:
    """present if any node rests on evidence; default if all are library; assumed if any rests only on assumption."""
    if not nodes:
        return "missing", 0
    flagged = sum(1 for n in nodes if _is_flagged(n))
    evidenced = [n for n in nodes if not _is_flagged(n)]
    if evidenced:
        return "present", flagged
    if any(n.assumed or "assumed" in _provenance_kinds(n) for n in nodes):
        return "assumed", flagged
    return "default", flagged


def _plural(n: int, one: str, many: str | None = None) -> str:
    return f"{n} {one if n == 1 else (many or one + 's')}"


def _live_screens(g: SpecGraph) -> list[Any]:
    return [s for s in g.screens.values() if s.status == "in"]


def _judge(g: SpecGraph, pack: Any) -> dict[str, tuple[NeedStatus, str, int]]:
    out: dict[str, tuple[NeedStatus, str, int]] = {}
    live = _live_screens(g)
    facts = set(g.meta.facts)

    # BN-01
    if not g.apps or not g.roles:
        out["BN-01"] = ("missing", "the graph has no apps or no roles", 0)
    else:
        st, fl = _grade([*g.apps.values(), *g.roles.values()])
        out["BN-01"] = (st, f"{_plural(len(g.apps), 'app')}, {_plural(len(g.roles), 'role')}", fl)

    # BN-02
    feats = list(g.features.values())
    if feats:
        st, fl = _grade(feats)
        out["BN-02"] = (st, f"{_plural(len(feats), 'feature')} in the scope ledger", fl)
    elif live:
        out["BN-02"] = ("assumed", "no feature list: scope is read from the screens", len(live))
    else:
        out["BN-02"] = ("missing", "no features and no screens", 0)

    # BN-03
    if not live:
        out["BN-03"] = ("missing", "no screen is in scope", 0)
    else:
        no_route = [s for s in live if not s.route.path]
        if len(no_route) == len(live):
            out["BN-03"] = ("assumed", "no screen has a route path yet", len(no_route))
        else:
            st, fl = _grade(live)
            out["BN-03"] = (
                st,
                f"{_plural(len(live), 'screen')}, {len(no_route)} without a route",
                max(fl, len(no_route)),
            )

    # BN-04
    bound = {e for s in live for e in bound_fields(s) if e != "<ops>"}
    ents = list(g.entities.values())
    if ents:
        st, fl = _grade(ents)
        out["BN-04"] = (st, f"{_plural(len(ents), 'entity', 'entities')}", fl)
    elif bound:
        out["BN-04"] = ("missing", "screens bind entity fields but the graph has no entities", 0)
    else:
        out["BN-04"] = ("default", "the app keeps no data of its own", 0)

    # BN-05
    rules = list(g.rules.values())
    if rules:
        st, fl = _grade(rules)
        n_conf = sum(1 for r in rules if r.confirm)
        out["BN-05"] = (
            st,
            f"{_plural(len(rules), 'rule')}, {n_conf} flagged confirm",
            max(fl, n_conf),
        )
    elif g.behaviors:
        out["BN-05"] = (
            "default",
            "no business rules; the library behaviours apply",
            len(g.behaviors),
        )
    else:
        out["BN-05"] = ("missing", "no rules and no behaviours", 0)

    # BN-06
    calls = {
        s.target
        for sc in live
        for a in sc.actions
        for s in a.effect
        if s.verb == "call" and s.target
    }
    ops = list(g.operations.values())
    if ops:
        st, fl = _grade(ops)
        no_err = sum(1 for o in ops if not o.errors)
        out["BN-06"] = (
            "missing" if no_err == len(ops) else st,
            f"{_plural(len(ops), 'operation')}, {no_err} without an error model",
            max(fl, no_err),
        )
    elif calls:
        out["BN-06"] = ("missing", "screens call operations but the graph has none", 0)
    else:
        out["BN-06"] = ("default", "the app calls no API", 0)

    # BN-07
    filled = [h for h in g.hero.values() if h.filled]
    mocked = [s for s in live if s.mock_profiles]
    if filled:
        out["BN-07"] = (
            "present",
            f"{_plural(len(filled), 'hero record')} filled",
            len(g.hero) - len(filled),
        )
    elif g.hero or mocked:
        out["BN-07"] = (
            "default",
            "mock profiles are named; hero content comes from seeded pools",
            len(g.hero),
        )
    elif g.meta.profile == "compact":
        out["BN-07"] = ("default", "mock data comes from seeded pools (library)", 0)
    else:
        out["BN-07"] = ("assumed", "no hero records or mock profiles yet", 0)

    # BN-08
    if g.directions:
        with_tokens = [d for d in g.directions if d in g.tokens]
        if with_tokens:
            st, fl = _grade(g.directions.values())
            out["BN-08"] = (st, f"{_plural(len(g.directions), 'direction')} with tokens", fl)
        else:
            out["BN-08"] = (
                "assumed",
                "a direction is written but no tokens are built",
                len(g.directions),
            )
    else:
        out["BN-08"] = ("assumed", "no design direction yet: the library default look applies", 0)

    # BN-09
    if not live:
        out["BN-09"] = ("missing", "no screen cards", 0)
    else:
        thin = [s for s in live if not s.regions or not s.states]
        if len(thin) == len(live):
            out["BN-09"] = ("missing", "no screen has regions and states", 0)
        else:
            st, fl = _grade(live)
            out["BN-09"] = (
                "assumed" if thin else st,
                f"{_plural(len(live), 'screen card')}, {len(thin)} without regions or states",
                max(fl, len(thin)),
            )

    # BN-10
    ints = list(g.integrations.values())
    need_provider = bool(facts & set(_PROVIDER_FACTS))
    if ints:
        st, fl = _grade(ints)
        out["BN-10"] = (st, f"{_plural(len(ints), 'integration')} with a mock plan", fl)
    elif need_provider:
        out["BN-10"] = (
            "missing",
            "the app needs an outside provider but no integration is recorded",
            0,
        )
    else:
        out["BN-10"] = ("default", "the app uses no outside provider", 0)

    # BN-11
    capable = [f for f in g.features.values() if f.capability]
    out["BN-11"] = (
        ("present", f"{_plural(len(capable), 'feature')} with a capability tier", 0)
        if capable
        else ("default", "the standard stack applies (library)", 0)
    )

    # BN-12
    tasks = getattr(pack, "tasks", None)
    if pack is None:
        out["BN-12"] = ("default", "the build plan is produced when the pack is assembled", 0)
    elif tasks:
        out["BN-12"] = ("present", f"{_plural(len(tasks), 'task card')} in the build plan", 0)
    elif live:
        out["BN-12"] = ("missing", "screens are in scope but the build plan has no tasks", 0)
    else:
        out["BN-12"] = ("default", "nothing to build", 0)

    # BN-13
    if not live:
        out["BN-13"] = ("missing", "no screens, so nothing says what done means", 0)
    else:
        without = [s for s in live if not s.acceptance]
        if len(without) == len(live):
            out["BN-13"] = ("missing", "no screen has acceptance scenarios", 0)
        elif without:
            out["BN-13"] = (
                "assumed",
                f"{len(without)} screens have no acceptance scenarios",
                len(without),
            )
        else:
            out["BN-13"] = ("present", "every screen has acceptance scenarios and marked tiers", 0)

    # BN-14
    marked = sum(1 for n in g.all_nodes() if n.assumed or n.degraded)
    index_ok = pack is None or "INDEX.md" in getattr(pack, "files", {})
    out["BN-14"] = (
        ("present", f"{marked} assumed or degraded nodes are marked", marked)
        if index_ok
        else ("missing", "the pack has no INDEX.md to hold the register", 0)
    )

    # BN-15
    cited = [n for n in g.all_nodes() if any(p.kind == "policy" for p in n.provenance)]
    out["BN-15"] = (
        ("present", f"{_plural(len(cited), 'node')} cite a store or policy rule", 0)
        if cited
        else ("default", "the store-policy sheet applies (library)", 0)
    )

    # BN-16
    required = [c.name for c in vocab.behavior_categories_for(g.meta.profile, facts)]
    have = {b.category for b in g.behaviors.values()}
    lacking = [c for c in required if c not in have]
    if not g.behaviors:
        out["BN-16"] = ("missing", "the graph has no behaviours at all", 0)
    elif lacking:
        out["BN-16"] = ("missing", f"behaviour categories missing: {', '.join(sorted(lacking))}", 0)
    else:
        st, fl = _grade(list(g.behaviors.values()))
        out["BN-16"] = (st, f"all {len(required)} required categories are present", fl)

    # BN-17
    cap_default = None
    over = []
    for aid, a in sorted(g.apps.items()):
        n_in = sum(1 for s in live if s.app_id == aid)
        cap = a.scope_cap
        if cap is None:
            cap_default = True
            continue
        if n_in > cap:
            over.append(f"{aid} has {n_in} screens in scope against a cap of {cap}")
    if over:
        out["BN-17"] = ("missing", "; ".join(over), 0)
    elif g.features or any(s.status == "later" for s in g.screens.values()):
        out["BN-17"] = ("present", "scope and phases are recorded", 0)
    else:
        out["BN-17"] = (
            "default",
            "everything is in this release, within the cap"
            + (" (library cap)" if cap_default else ""),
            0,
        )
    return out


def _match(files: Iterable[str], patterns: Iterable[str]) -> list[str]:
    fs = sorted(files)
    return sorted({f for pat in patterns for f in fs if fnmatch.fnmatch(f, pat)})


def builder_needs(graph: SpecGraph, pack: Any = None) -> list[Need]:
    """BN-01..BN-17, each with its status, plain reason, holding files and proving validators."""
    judged = _judge(graph, pack)
    files = list(getattr(pack, "files", {})) if pack is not None else []
    out: list[Need] = []
    for nid, title, patterns, validators in _SPEC:
        status, why, flagged = judged[nid]
        out.append(
            Need(
                nid,
                title,
                status,
                why,
                _match(files, patterns) if pack is not None else [],
                list(patterns),
                list(validators),
                flagged,
            )
        )
    return out


def missing_needs(needs: Iterable[Need]) -> list[Need]:
    """These end the run `partial` and never reach Gate 2."""
    return [n for n in needs if n.status == "missing"]


def summary(needs: Iterable[Need]) -> dict[str, int]:
    c = {"present": 0, "default": 0, "assumed": 0, "missing": 0}
    for n in needs:
        c[n.status] += 1
    return c


def plain_lines(needs: Iterable[Need]) -> list[str]:
    """One line per need that is not `present`, in plain words (for the Gate 2 page and the CLI)."""
    return [
        f"{n.id} {n.title}: {n.status.upper()}: {n.why}" for n in needs if n.status != "present"
    ]
