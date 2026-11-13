"""The release planner (doc 15 4.5, 4.6; doc 05 section 5): what is built first, what waits, and why.

One cap per app per phase (``config.scope.scope_cap_per_app_phase``). Phase 1 is the features that are ``in``; Phase 2 the
features that are ``later``, with a reason that is never relabelled: ``user`` (the person's own deferral), ``capacity``
(the cap, only with consent) or ``first_release`` (beyond Compass's approved ``core`` set). Procedure:

1. Compass ``core`` screens, and the system set, are the Phase 1 candidates (a screen that serves only features the person
   deferred is never one, whatever Compass says);
2. PULL-IN: every user-evidenced ``must`` gets a screen; each role that has a screen in Phase 1 gets its core journey end to
   end (the flow with most steps already in); every entity a Phase 1 screen reads has a Phase 1 screen that can produce it;
   repeated until nothing changes. A feature the user deferred is never pulled in;
3. a screen code added (an entity form, a home) is in when its module is;
4. if an app exceeds the cap, the lowest journey-criticality features go ``later`` with their dependents (``capacity``);
   a user-evidenced must is never deferred: it is returned in ``needs_consent`` instead. Consent at G1.5 is mandatory for any
   capacity deferral; ``everything_in_one_release`` is allowed and recorded.

Ordering inside a phase: user-evidenced must first, then journey criticality, dependency depth, peer prevalence, the user's
own ranking. A later feature keeps an OUTLINE and has NO screen nodes. No code here names an app type.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Collection
from dataclasses import dataclass

from mde_compass_intake import ResearchView, SpecInput
from mde_compass_intake.spec_input import Feature as InFeature
from mde_compass_intake.text import stem_set
from mde_spec_core.config import SpecConfig, default_config

from ._data import release_data
from .inventory import Inventory, ScreenProposal, peer_rank


@dataclass(frozen=True)
class Outline:
    """What a later feature would need, kept in the Scope Ledger (no screen nodes while it is later)."""

    intent: str  # the person's own words for it
    screens: tuple[str, ...]  # "name (type)" drafted by Compass; empty when none was drafted
    entities: tuple[str, ...]  # entity ids its screens use, or the entities its words name
    depends_on: tuple[str, ...]  # feature ids

    def text(self) -> str:
        parts = []
        if self.intent:
            parts.append(f"intent: {self.intent}")
        parts.append(
            "screens: " + "; ".join(self.screens)
            if self.screens
            else "screens: none drafted in Compass's structure yet"
        )
        if self.entities:
            parts.append("entities: " + ", ".join(self.entities))
        if self.depends_on:
            parts.append("needs: " + ", ".join(self.depends_on))
        return " | ".join(parts)


@dataclass
class FeatureDecision:
    id: str
    status: str  # in | later
    reason: str | None  # user | capacity | first_release (only when later)
    why: str
    pulled_in: bool = False
    user_evidenced_must: bool = False
    consented: bool = False
    outline: Outline | None = None
    criticality: int = 0
    depth: int = 0
    peer_rank: int = 0


@dataclass(frozen=True)
class PullIn:
    screen_id: str
    compass_id: str | None
    rule: str  # user_must | role_journey | entity_producer
    because: str
    features: tuple[str, ...] = ()


@dataclass(frozen=True)
class Consent:
    """A G1.5 consent the plan needs (mandatory whenever a capacity deferral is proposed)."""

    kind: str  # capacity | user_must | over_cap
    app: str
    feature_ids: tuple[str, ...]
    why: str


@dataclass(frozen=True)
class AppCount:
    app: str
    release: int
    cap: int
    over_cap: bool
    deferred_capacity: int
    before_capacity: int


@dataclass(frozen=True)
class ModuleReport:
    module: str
    name: str
    core: int
    extended: int
    in_release: int  # all release screens of the module (any source)
    in_release_compass: int


@dataclass
class CoreReport:
    """Compass ``core`` against Spec's Phase 1 (PS-3 needs it)."""

    compass_core: int
    compass_extended: int
    core_in_release: int
    core_not_in_release: list[str]
    extended_pulled_in: list[PullIn]
    spec_only_in_release: int  # system, derived: in Phase 1 though Compass had no screen for them


@dataclass
class ReleasePlan:
    features: dict[str, FeatureDecision]
    release_screens: list[str]
    deferred_screens: dict[str, str]  # screen id -> reason (first_release | user | capacity)
    per_app: dict[str, AppCount]
    needs_consent: list[Consent]
    pulled_in: list[PullIn]
    core_report: CoreReport
    modules: list[ModuleReport]
    order: dict[int, list[str]]  # phase (1 or 2) -> feature ids, best first
    dependencies: dict[str, set[str]]  # feature -> the features it needs
    journeys: dict[str, str]  # role id -> the flow id chosen as its core journey
    blocked_journeys: list[str]
    notes: list[str]
    cap: int
    everything_in_one_release: bool
    inventory: Inventory | None = None

    @property
    def consent_required(self) -> bool:
        return bool(self.needs_consent)

    def in_features(self) -> list[str]:
        return sorted(f for f, d in self.features.items() if d.status == "in")

    def later_by_reason(self) -> dict[str, list[str]]:
        out: dict[str, list[str]] = defaultdict(list)
        for f, d in sorted(self.features.items()):
            if d.status == "later" and d.reason:
                out[d.reason].append(f)
        return dict(out)


# ---------------------------------------------------------------- helpers


def _user_must(f: InFeature) -> bool:
    return f.user_evidenced and f.priority == "must"


def _user_rank(f: InFeature) -> int:
    if f.priority_trust.grade == "locked":
        return {"must": 0, "should": 1, "nice": 2}.get(f.priority, 3)
    return 3


def plan_release(
    spec_input: SpecInput,
    inventory: Inventory,
    config: SpecConfig | None = None,
    *,
    research_view: ResearchView | None = None,
    everything_in_one_release: bool = False,
    consented: Collection[str] = (),
) -> ReleasePlan:
    cfg = config or default_config()
    cap = cfg.scope.scope_cap_per_app_phase
    rd = release_data()
    si = spec_input
    notes: list[str] = []

    feats = {f.id: f for f in si.features if f.duplicate_of is None and f.scope_status != "out"}
    user_deferred = {fid for fid, f in feats.items() if f.deferred_by_user}
    later_by_compass = {fid for fid, f in feats.items() if f.scope_status == "later"}
    screens = {p.id: p for p in inventory.screens}
    has_phase = any(p.phase in (rd.core_phase, rd.extended_phase) for p in screens.values())

    def only_deferred(p: ScreenProposal) -> bool:
        return bool(p.feature_ids) and all(f in user_deferred for f in p.feature_ids)

    # --- 1. the candidates ---------------------------------------------------------------------------------------
    release: set[str] = set()
    for p in screens.values():
        if p.phase == "brief":
            # no Compass structure, so no first-release split: every screen drafted from the in-scope features is a candidate
            if not only_deferred(p):
                release.add(p.id)
            continue
        if p.source in ("derived", "peer"):
            continue  # decided after the pull-in, from their module
        if only_deferred(p):
            continue
        if p.system or not has_phase or p.phase == rd.core_phase:
            release.add(p.id)
    for p in screens.values():
        if only_deferred(p) and p.phase == rd.core_phase:
            notes.append(
                f"screen {p.id} {p.name!r} is core in Compass but serves only features the person deferred: kept out (reason user)"
            )
    pulled: list[PullIn] = []
    pulled_ids: set[str] = set()

    def pull(p: ScreenProposal, rule: str, because: str) -> bool:
        if p.id in release or only_deferred(p):
            return False
        release.add(p.id)
        pulled_ids.add(p.id)
        pulled.append(PullIn(p.id, p.compass_id, rule, because, p.feature_ids))
        return True

    flows = list(si.structure.flows)

    def flow_role(fl) -> str | None:
        rid: str | None = fl.role
        seen: set[str] = set()
        while rid is not None and rid in inventory.role_alias and rid not in seen:
            seen.add(rid)
            rid = inventory.role_alias[rid]
        return rid if rid in inventory.roles else None

    def flow_screens(fl) -> list[ScreenProposal]:
        out: list[ScreenProposal] = []
        for st in fl.steps:
            ids = inventory.compass_instances.get(st.screen)
            pid = ids[0] if ids else st.screen
            pid = inventory.screen_alias.get(pid, pid)
            p = screens.get(pid)
            if p is not None and p not in out:
                out.append(p)
        return out

    def flow_ignored(fl) -> bool:
        fs = {inventory.feature_alias.get(x, x) for x in fl.features}
        return bool(fs) and fs <= user_deferred

    producers: dict[str, list[ScreenProposal]] = defaultdict(list)
    for p in screens.values():
        for e, u in p.entity_uses:
            if u in ("create", "edit"):
                producers[e].append(p)

    journeys: dict[str, str] = {}
    blocked: list[str] = []

    def feature_in_release(fid: str) -> bool:
        return any(fid in screens[i].feature_ids for i in release)

    # --- 2a. every user-evidenced must gets a screen -----------------------------------------------------------------
    for fid, f in sorted(feats.items()):
        if fid in user_deferred or not _user_must(f):
            continue
        if feature_in_release(fid) or not any(fid in p.feature_ids for p in screens.values()):
            continue
        cands = [
            fl for fl in flows if fid in {inventory.feature_alias.get(x, x) for x in fl.features}
        ]
        for fl in sorted(cands, key=lambda x: x.id)[:1]:
            for p in flow_screens(fl):
                pull(
                    p,
                    "user_must",
                    f"{fid} is a user-evidenced must with no screen in Phase 1: its journey {fl.id}",
                )
        if not feature_in_release(fid):
            mine = sorted((p for p in screens.values() if fid in p.feature_ids), key=lambda p: p.id)
            if mine:
                pull(
                    mine[0],
                    "user_must",
                    f"{fid} is a user-evidenced must with no screen in Phase 1",
                )

    # --- 2b/2c. role journeys and entity producers, to a fixpoint ------------------------------------------------------
    changed = True
    while changed:
        changed = False
        roles_in = sorted(
            {r for i in release for r in screens[i].role_ids if not screens[i].system}
        )
        for rid in roles_in:
            cands = [fl for fl in flows if flow_role(fl) == rid and not flow_ignored(fl)]
            if not cands:
                continue

            def score(fl) -> tuple[int, int, str]:
                scr = flow_screens(fl)
                have = sum(1 for p in scr if p.id in release)
                return (-have, len(scr) - have, fl.id)

            best = min(cands, key=score)
            journeys[rid] = best.id
            for p in flow_screens(best):
                if p.id in release:
                    continue
                if pull(
                    p,
                    "role_journey",
                    f"the core journey {best.id} {best.name!r} of role {rid} must run end to end",
                ):
                    changed = True
                elif only_deferred(p):
                    tag = f"{best.id}:{p.id}"
                    if tag not in blocked:
                        blocked.append(tag)
        consumed = sorted({e for i in release for e, _ in screens[i].entity_uses})
        for eid in consumed:
            prods = producers.get(eid, [])
            if not prods or any(p.id in release for p in prods):
                continue
            ent = next((e for e in si.structure.entities if e.id == eid), None)
            prods = sorted(
                (p for p in prods if p.source not in ("derived", "peer")),
                key=lambda p: (0 if ent and p.module == ent.module else 1, p.id),
            )
            for p in prods:
                if pull(
                    p,
                    "entity_producer",
                    f"{eid} is shown in Phase 1 and nothing in Phase 1 can create or change it",
                ):
                    changed = True
                    break

    # --- 3. screens code added follow their module ---------------------------------------------------------------------
    modules_with_release = {screens[i].module for i in release}
    for p in screens.values():
        if (
            p.source in ("derived", "peer")
            and p.module in modules_with_release
            and not only_deferred(p)
        ):
            release.add(p.id)

    # --- feature facts: dependencies, journeys, criticality ----------------------------------------------------------------
    # a feature needs the features whose Phase 1 screens produce an entity it shows (one producer is enough: the others
    # are alternatives, not needs)
    in_release_features = {f for i in release for f in screens[i].feature_ids}
    prod_features: dict[str, set[str]] = defaultdict(set)
    for e, ps in producers.items():
        for p in ps:
            if p.id in release:
                prod_features[e] |= set(p.feature_ids)
    deps: dict[str, set[str]] = {f: set() for f in feats}
    for p in screens.values():
        for e, u in p.entity_uses:
            if u != "view":
                continue
            for f in p.feature_ids:
                if f not in feats or f in prod_features.get(e, set()):
                    continue
                deps[f].update(
                    x
                    for x in prod_features.get(e, set()) - {f}
                    if x in feats and x in in_release_features
                )
    for f in sorted(
        deps
    ):  # cycles are cut: of two features that need each other, the later id keeps the dependency
        for g in sorted(deps[f]):
            if f in deps.get(g, set()) and f < g:
                deps[f].discard(g)

    def depth(f: str, seen: tuple[str, ...] = ()) -> int:
        if f in seen:
            return 0
        d = deps.get(f, set())
        return 0 if not d else 1 + max(depth(x, (*seen, f)) for x in d)

    on_journey: dict[str, int] = defaultdict(int)
    for flid in journeys.values():
        fl = next(x for x in flows if x.id == flid)
        seen_f: set[str] = set()
        for p in flow_screens(fl):
            seen_f |= set(p.feature_ids)
        for f in seen_f:
            on_journey[f] += 1

    # --- 4. capacity ----------------------------------------------------------------------------------------------------
    needs: list[Consent] = []
    capacity_deferred: set[str] = set()
    before: dict[str, int] = {}
    apps = [a.id for a in inventory.apps]

    def app_release(app: str) -> list[ScreenProposal]:
        return [screens[i] for i in sorted(release) if screens[i].app == app]

    for app in apps:
        before[app] = len(app_release(app))
        if before[app] <= cap:
            continue
        if everything_in_one_release:
            notes.append(
                f"{app}: {before[app]} screens against a cap of {cap}: everything in one release was chosen "
                "(recorded; a plain warning about time and quality is shown at G1.5)"
            )
            continue
        deferred_here: list[str] = []
        unresolved: set[str] = set()
        while len(app_release(app)) > cap:
            fcount: dict[str, int] = defaultdict(int)
            for p in app_release(app):
                if p.system:
                    continue
                for f in p.feature_ids:
                    fcount[f] += 1
            order = sorted(
                (f for f in fcount if f not in unresolved),
                key=lambda f: (
                    on_journey.get(f, 0),
                    peer_rank(feats[f].name, research_view) if f in feats else 0,
                    -fcount[f],
                    f,
                ),
            )
            if not order:
                break
            chosen = order[0]
            group = {chosen}
            grew = True
            while grew:
                grew = False
                for f, d in deps.items():
                    if f not in group and d & group:
                        group.add(f)
                        grew = True
            blockers = sorted(f for f in group if f in feats and _user_must(feats[f]))
            if blockers:
                unresolved.update(blockers)
                unresolved.add(chosen)  # leave it: deferring it would drag a must along
                needs.append(
                    Consent(
                        "user_must",
                        app,
                        tuple(blockers),
                        f"{app} is over its cap of {cap}; deferring {chosen} would defer a user-evidenced must, which needs the person's yes",
                    )
                )
                continue
            victims = [
                p
                for p in app_release(app)
                if not p.system and p.feature_ids and set(p.feature_ids) <= group
            ]
            if not victims:
                unresolved.add(chosen)
                continue
            for p in victims:
                release.discard(p.id)
                capacity_deferred.add(p.id)
                pulled_ids.discard(p.id)
            deferred_here.extend(sorted(group & set(fcount)))
        if deferred_here:
            needs.append(
                Consent(
                    "capacity",
                    app,
                    tuple(sorted(set(deferred_here))),
                    f"{app} had {before[app]} screens against a cap of {cap}: the lowest-criticality features are proposed "
                    "for Phase 2 (reason capacity); the person's consent at G1.5 is mandatory",
                )
            )
        if len(app_release(app)) > cap:
            needs.append(
                Consent(
                    "over_cap",
                    app,
                    (),
                    f"{app} still has {len(app_release(app))} screens against a cap of {cap}: G1.5 shows the conflict and "
                    "the options (accept, move named features, everything in one release)",
                )
            )

    needs = list(dict.fromkeys(needs))  # one consent per (kind, app, features)
    if inventory.profile == "deep":
        moved = sorted(
            {f for x in pulled if x.screen_id in release for f in x.features if f in feats}
        )
        moved = [
            f
            for f in moved
            if not any(
                screens[i].phase == rd.core_phase for i in release if f in screens[i].feature_ids
            )
        ]
        if moved:
            needs.append(
                Consent(
                    "pull_in",
                    "",
                    tuple(moved),
                    "G1.5 is a hard gate for deep apps: these features had no core screen in Compass but the first release "
                    "needs them (a role's core journey, or an entity nothing else can create); the person's yes is the "
                    "record of the move",
                )
            )

    # --- feature decisions --------------------------------------------------------------------------------------------------
    feature_screens: dict[str, list[ScreenProposal]] = defaultdict(list)
    for p in screens.values():
        for f in p.feature_ids:
            feature_screens[f].append(p)
    consented_set = set(consented)
    decisions: dict[str, FeatureDecision] = {}
    for fid, f in sorted(feats.items()):
        mine = feature_screens.get(fid, [])
        in_rel = [p for p in mine if p.id in release]
        pulled_f = any(p.id in pulled_ids for p in in_rel)
        if fid in user_deferred:
            status, reason, why = "later", "user", "the person deferred it (their own words)"
        elif in_rel or not mine:
            if fid in later_by_compass and not in_rel:
                status, reason, why = "later", "first_release", "Compass marks it later"
            else:
                status, reason, why = (
                    "in",
                    None,
                    "has a screen in Phase 1" if in_rel else "has no screen in Compass's structure",
                )
        elif any(p.id in capacity_deferred for p in mine):
            status, reason, why = (
                "later",
                "capacity",
                "deferred for capacity (consent at G1.5 is mandatory)",
            )
        else:
            status, reason, why = (
                "later",
                "first_release",
                "none of its screens is in Compass's first-release (core) set and no journey needs one",
            )
        outline = None
        if status == "later":
            out_screens = tuple(sorted(f"{p.name} ({p.screen_type})" for p in mine))
            ents_set = {e for p in mine for e, _ in p.entity_uses}
            if not ents_set:  # no screen was drafted: the entities its own words name
                want = stem_set(f"{f.name} {f.intent}")
                ents_set = {
                    e.id
                    for e in si.structure.entities
                    if want & stem_set(f"{e.name} {e.key.replace('_', ' ')}")
                }
            outline = Outline(
                f.intent, out_screens, tuple(sorted(ents_set)), tuple(sorted(deps.get(fid, set())))
            )
        decisions[fid] = FeatureDecision(
            id=fid,
            status=status,
            reason=reason,
            why=why,
            pulled_in=pulled_f and status == "in",
            user_evidenced_must=_user_must(f),
            consented=fid in consented_set,
            outline=outline,
            criticality=on_journey.get(fid, 0),
            depth=depth(fid),
            peer_rank=peer_rank(f.name, research_view),
        )

    for fid, d in decisions.items():
        if d.status == "in":
            for g in sorted(deps.get(fid, set())):
                if decisions[g].status == "later" and decisions[g].reason != "user":
                    notes.append(
                        f"{fid} (in) depends on {g} (later): a dependency left out of the release"
                    )

    # --- ordering -------------------------------------------------------------------------------------------------------------
    def key(fid: str) -> tuple[int, int, int, int, int, str]:
        d = decisions[fid]
        return (
            0 if d.user_evidenced_must else 1,
            -d.criticality,
            d.depth,
            -d.peer_rank,
            _user_rank(feats[fid]),
            fid,
        )

    order = {
        1: sorted((f for f, d in decisions.items() if d.status == "in"), key=key),
        2: sorted((f for f, d in decisions.items() if d.status == "later"), key=key),
    }

    # --- reports ----------------------------------------------------------------------------------------------------------------
    deferred_screens: dict[str, str] = {}
    for p in screens.values():
        if p.id in release:
            continue
        if p.id in capacity_deferred:
            deferred_screens[p.id] = "capacity"
        elif only_deferred(p):
            deferred_screens[p.id] = "user"
        else:
            deferred_screens[p.id] = "first_release"

    per_app: dict[str, AppCount] = {}
    for app in apps:
        n = len(app_release(app))
        per_app[app] = AppCount(
            app=app,
            release=n,
            cap=cap,
            over_cap=n > cap,
            deferred_capacity=sum(1 for i in capacity_deferred if screens[i].app == app),
            before_capacity=before.get(app, n),
        )

    core = [p for p in screens.values() if p.phase == rd.core_phase and p.compass_id]
    ext = [p for p in screens.values() if p.phase == rd.extended_phase and p.compass_id]
    core_report = CoreReport(
        compass_core=len(core),
        compass_extended=len(ext),
        core_in_release=sum(1 for p in core if p.id in release),
        core_not_in_release=sorted(p.id for p in core if p.id not in release),
        extended_pulled_in=[x for x in pulled if screens[x.screen_id].phase == rd.extended_phase],
        spec_only_in_release=sum(
            1 for i in release if screens[i].phase not in (rd.core_phase, rd.extended_phase)
        ),
    )
    mod_rows: list[ModuleReport] = []
    names = {m.id: m.name for m in si.modules}
    if inventory.system_module:
        names[inventory.system_module[0]] = inventory.system_module[1]
    for mid, name in sorted(names.items()):
        mine_m = [p for p in screens.values() if p.module == mid]
        mod_rows.append(
            ModuleReport(
                module=mid,
                name=name,
                core=sum(1 for p in mine_m if p.phase == rd.core_phase),
                extended=sum(1 for p in mine_m if p.phase == rd.extended_phase),
                in_release=sum(1 for p in mine_m if p.id in release),
                in_release_compass=sum(1 for p in mine_m if p.id in release and p.compass_id),
            )
        )
    return ReleasePlan(
        features=decisions,
        release_screens=sorted(release),
        deferred_screens=deferred_screens,
        per_app=per_app,
        needs_consent=needs,
        pulled_in=pulled,
        core_report=core_report,
        modules=mod_rows,
        order=order,
        dependencies={f: set(d) for f, d in deps.items()},
        journeys=journeys,
        blocked_journeys=blocked,
        notes=notes,
        cap=cap,
        everything_in_one_release=everything_in_one_release,
        inventory=inventory,
    )
