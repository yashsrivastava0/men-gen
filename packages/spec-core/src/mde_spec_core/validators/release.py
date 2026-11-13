"""V-19, V-28, V-29, V-40, V-41, V-44: scope ledger, release, coverage, stories, mock coverage, journeys."""

from __future__ import annotations

from mde_spec_core.graph import Screen, SpecGraph
from mde_spec_core.render import bound_fields

from .base import Context, Finding, validator


def screen_features(g: SpecGraph, s: Screen) -> set[str]:
    refs = {p.ref for p in s.provenance if p.ref.startswith("FTR-")}
    for st in g.stories.values():
        if s.id in st.screen_ids or st.id in s.stories:
            refs |= set(st.feature_ids)
    return refs


@validator(
    "V-19",
    "Scope ledger: each screen satisfies an in feature or a system requirement; later and out features have no screens",
)
def v19(ctx: Context) -> list[Finding]:
    g, out = ctx.graph, []
    for s in g.screens.values():
        if s.status != "in":
            out.append(
                Finding(
                    "V-19",
                    "blocker",
                    s.id,
                    f"screen node exists for a {s.status} feature",
                    "a later feature keeps an outline in the ledger and has NO screen nodes",
                    "B",
                )
            )
    if not g.features:
        return out
    cited: set[str] = set()
    for s in g.screens.values():
        feats = screen_features(g, s)
        cited |= feats
        if not feats and not s.system and not any(p.kind == "policy" for p in s.provenance):
            out.append(
                Finding(
                    "V-19",
                    "blocker",
                    s.id,
                    "satisfies no in feature and no system-set requirement",
                    "tie it to a feature or remove it",
                    "B",
                )
            )
        for fid in feats:
            f = g.features.get(fid)
            if f and f.status != "in":
                out.append(
                    Finding(
                        "V-19",
                        "blocker",
                        s.id,
                        f"serves {fid}, which is {f.status}",
                        "remove the screen or move the feature in",
                        "B",
                    )
                )
    for f in g.features.values():
        if f.status == "in" and f.id not in cited:
            out.append(
                Finding(
                    "V-19",
                    "blocker",
                    f.id,
                    "in feature without a screen",
                    "add its screen or state why it needs none",
                    "B",
                )
            )
    return out


@validator(
    "V-28",
    "Release consistency: within the cap; no in node depends on a later node; deferrals have reasons and consent",
)
def v28(ctx: Context) -> list[Finding]:
    g, out = ctx.graph, []
    for app_id, app in g.apps.items():
        cap = app.scope_cap or ctx.config.scope.scope_cap_per_app_phase
        n = sum(1 for s in g.screens.values() if s.app_id == app_id and s.status == "in")
        if n > cap:
            out.append(
                Finding(
                    "V-28",
                    "blocker",
                    app_id,
                    f"{n} release screens against a cap of {cap}",
                    "defer the lowest-criticality features with consent at G1.5",
                    "C",
                )
            )
    for f in g.features.values():
        if f.status == "later":
            if f.later_reason is None:
                out.append(
                    Finding(
                        "V-28",
                        "blocker",
                        f.id,
                        "later without a reason (user, capacity or first_release)",
                        "record the reason",
                        "B",
                    )
                )
            if f.later_reason == "capacity" and not (
                f.consent_id and f.consent_id in ctx.consents | {f.consent_id}
            ):
                out.append(
                    Finding(
                        "V-28",
                        "blocker",
                        f.id,
                        "capacity deferral without G1.5 consent",
                        "consent at G1.5 is the I-4 override record",
                        "C",
                    )
                )
            if (
                f.user_evidenced
                and f.user_priority == "must"
                and f.later_reason != "user"
                and not f.consent_id
            ):
                out.append(
                    Finding(
                        "V-28",
                        "blocker",
                        f.id,
                        "a user-evidenced must is deferred without consent",
                        "never defer a must without the user's yes",
                        "C",
                    )
                )
        if f.status == "in":
            for d in f.depends_on:
                dep = g.features.get(d)
                if dep and dep.status == "later":
                    out.append(
                        Finding(
                            "V-28",
                            "blocker",
                            f.id,
                            f"in feature depends on later feature {d}",
                            "pull the dependency in (pull-in rule)",
                            "B",
                        )
                    )
    for s in g.screens.values():
        if s.status != "in":
            continue
        for eid in bound_fields(s):
            e = g.entities.get(eid)
            if e and e.later:
                out.append(
                    Finding(
                        "V-28",
                        "blocker",
                        s.id,
                        f"binds {eid}, which exists only for later features",
                        "an in node must not depend on a later node",
                        "B",
                    )
                )
    return out


_CLASS_RANGE = {"compact": (3, 30), "standard": (10, 80), "deep": (20, 400)}


@validator(
    "V-29",
    "Inventory completeness: no entity, role or flow without a screen; system set present when accounts exist",
)
def v29(ctx: Context) -> list[Finding]:
    g, out = ctx.graph, []
    live = [s for s in g.screens.values() if s.status == "in"]
    bound: set[str] = set()
    for s in live:
        bound |= set(bound_fields(s))
    for e in g.entities.values():
        if e.klass in ("join", "log") or e.later:
            continue
        if e.id not in bound:
            out.append(
                Finding(
                    "V-29",
                    "blocker",
                    e.id,
                    "entity has no screen that shows or edits it",
                    "add a screen, or mark it join/log if it is only data",
                    "B",
                )
            )
    role_screens = {r for s in live for r in s.role_ids}
    for r in g.roles.values():
        if r.id not in role_screens and not r.home_screen:
            out.append(
                Finding(
                    "V-29",
                    "blocker",
                    r.id,
                    "role has no home screen",
                    "every role has a home unless it has a single destination",
                    "B",
                )
            )
    if g.roles:
        types = {s.screen_type for s in live}
        for t in ctx.required_system_types:
            if t not in types:
                out.append(
                    Finding(
                        "V-29",
                        "blocker",
                        "inventory",
                        f"accounts exist but no {t} screen (system set)",
                        "instantiate the system screen from the library",
                        "A",
                    )
                )
    lo, hi = _CLASS_RANGE[ctx.profile]
    if live and not lo <= len(live) <= hi:
        out.append(
            Finding(
                "V-29",
                "warning",
                "inventory",
                f"{len(live)} screens is outside the usual range {lo}-{hi} for {ctx.profile}",
                "ask at G1.5 whether the idea is bigger than its tier; never trimmed silently",
                "C",
            )
        )
    return out


@validator(
    "V-40",
    "Story coverage: every in feature has a story, every screen cites a story, every story cites an in feature or a policy and has acceptance",
)
def v40(ctx: Context) -> list[Finding]:
    g, out = ctx.graph, []
    with_story = {fid for st in g.stories.values() for fid in st.feature_ids}
    for f in g.features.values():
        if f.status == "in" and f.id not in with_story:
            out.append(
                Finding(
                    "V-40", "blocker", f.id, "in feature without a story", "write its story", "B"
                )
            )
    cited = {sid for st in g.stories.values() for sid in st.screen_ids} | {
        s.id for s in g.screens.values() if s.stories
    }
    for s in g.screens.values():
        if s.status == "in" and s.id not in cited and not s.system:
            out.append(
                Finding("V-40", "blocker", s.id, "screen cites no story", "cite a story", "B")
            )
    for st in g.stories.values():
        feats = [g.features.get(f) for f in st.feature_ids]
        if (
            not st.policy_id
            and not any(f is not None and f.status == "in" for f in feats)
            and g.features
        ):
            out.append(
                Finding(
                    "V-40",
                    "blocker",
                    st.id,
                    "story cites no in feature and no policy id",
                    "keep it only as a backlog story or a suggestion",
                    "B",
                )
            )
        if not st.acceptance and ctx.profile != "compact":
            out.append(
                Finding(
                    "V-40",
                    "warning",
                    st.id,
                    "story has no acceptance (GIVEN WHEN THEN)",
                    "add acceptance",
                    "B",
                )
            )
        if st.assumed and st.voice == "must":
            out.append(
                Finding(
                    "V-40",
                    "blocker",
                    st.id,
                    "an assumed story is never voice must",
                    "make it usually or ground it",
                    "A",
                )
            )
    return out


@validator(
    "V-41",
    "Mock coverage: every lifecycle state has a hero record; every media field has an asset need; shared entities coherent",
)
def v41(ctx: Context) -> list[Finding]:
    g, out = ctx.graph, []
    if not g.hero:
        return [
            Finding("V-41", "info", "hero", "no hero skeleton yet", "minted at the Freeze", "A")
        ]
    by_state: dict[str, set[str]] = {}
    for h in g.hero.values():
        by_state.setdefault(h.entity, set())
        if h.state:
            by_state[h.entity].add(h.state)
        if h.entity not in g.entities:
            out.append(
                Finding(
                    "V-41",
                    "blocker",
                    h.id,
                    f"hero record for missing entity {h.entity}",
                    "fix the skeleton",
                    "A",
                )
            )
        if not h.synthetic:
            out.append(
                Finding(
                    "V-41",
                    "blocker",
                    h.id,
                    "hero record not marked synthetic",
                    "mark it synthetic",
                    "A",
                )
            )
    for e in g.entities.values():
        if e.klass == "primary":
            for st in e.lifecycle.states:
                if st not in by_state.get(e.id, set()):
                    out.append(
                        Finding(
                            "V-41",
                            "blocker",
                            e.id,
                            f"no hero record in state {st}",
                            "the skeleton gives every lifecycle state a record",
                            "A",
                        )
                    )
        for f in e.fields:
            if f.media and not any(f"{e.id}.{f.key}" in a.used_by for a in g.assets.values()):
                out.append(
                    Finding(
                        "V-41",
                        "blocker",
                        f"{e.id}.{f.key}",
                        "media field without an asset need",
                        "data-bound asset needs are made by code",
                        "A",
                    )
                )
    return out


@validator(
    "V-44",
    "Journey completeness: primary journeys have stages with an emotion line, moments that matter and unhappy paths",
)
def v44(ctx: Context) -> list[Finding]:
    out = []
    if ctx.profile == "compact" and not any(f.primary for f in ctx.graph.flows.values()):
        return [
            Finding("V-44", "blocker", "flows", "no primary journey", "write the main journey", "B")
        ]
    for f in ctx.graph.flows.values():
        if ctx.profile == "compact" and not f.primary:
            continue
        if not f.stages:
            out.append(Finding("V-44", "blocker", f.id, "journey has no stages", "write them", "B"))
        if f.primary:
            if not f.moments_that_matter:
                out.append(
                    Finding(
                        "V-44",
                        "blocker",
                        f.id,
                        "primary journey names no moments that matter",
                        "name them",
                        "B",
                    )
                )
            if not f.unhappy_paths:
                out.append(
                    Finding(
                        "V-44",
                        "blocker",
                        f.id,
                        "primary journey has no unhappy path",
                        "write the failure paths",
                        "B",
                    )
                )
        for st in f.stages:
            for sid in st.screen_ids:
                if sid not in ctx.graph.screens:
                    out.append(
                        Finding(
                            "V-44",
                            "blocker",
                            f.id,
                            f"stage {st.n} names missing screen {sid}",
                            "fix the stage",
                            "B",
                        )
                    )
    return out
