"""The coverage matrix (doc 18 3.1): entity -> screens, role -> screens, flow -> screens, feature -> story and screen.

Computed at Gate 3 and shown at Gate 2. A gap is a row with nothing under it: an entity no screen shows, a role with
no screen, a flow whose stage points at nothing, an `in` feature with no story or no screen.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .graph import SpecGraph
from .render import bound_fields


@dataclass
class Gap:
    kind: str  # entity role flow feature
    id: str
    problem: str  # plain words
    fix: str

    def as_dict(self) -> dict[str, str]:
        return {"kind": self.kind, "id": self.id, "problem": self.problem, "fix": self.fix}


@dataclass
class CoverageMatrix:
    entities: dict[str, dict[str, list[str]]] = field(
        default_factory=dict
    )  # shown_on, via_operations
    roles: dict[str, list[str]] = field(default_factory=dict)
    flows: dict[str, list[str]] = field(default_factory=dict)
    features: dict[str, dict[str, list[str]]] = field(default_factory=dict)  # stories, screens
    gaps: list[Gap] = field(default_factory=list)

    @property
    def rows(self) -> int:
        return len(self.entities) + len(self.roles) + len(self.flows) + len(self.features)

    def as_dict(self) -> dict[str, Any]:
        return {
            "entities": self.entities,
            "roles": self.roles,
            "flows": self.flows,
            "features": self.features,
            "gaps": [g.as_dict() for g in self.gaps],
        }


def coverage_matrix(graph: SpecGraph) -> CoverageMatrix:
    live = {sid: s for sid, s in sorted(graph.screens.items()) if s.status == "in"}
    m = CoverageMatrix()
    ops_entity = {oid: o.entity for oid, o in graph.operations.items() if o.entity}

    shown: dict[str, set[str]] = {}
    via: dict[str, set[str]] = {}
    for sid, s in live.items():
        b = bound_fields(s)
        for oid in b.pop("<ops>", []):
            ent = ops_entity.get(oid)
            if ent:
                via.setdefault(ent, set()).add(sid)
        for eid in b:
            shown.setdefault(eid, set()).add(sid)
    for eid in sorted(graph.entities):
        e = graph.entities[eid]
        if e.later or e.status != "in":
            continue
        m.entities[eid] = {
            "shown_on": sorted(shown.get(eid, ())),
            "via_operations": sorted(via.get(eid, ())),
        }
        if e.klass in ("join", "log"):
            continue
        if not shown.get(eid) and not via.get(eid):
            m.gaps.append(
                Gap(
                    "entity",
                    eid,
                    f"{e.name} is not shown, edited or used by any screen",
                    "add a screen for it, or mark it join or log if it is only data",
                )
            )

    for rid in sorted(graph.roles):
        r = graph.roles[rid]
        sids = sorted(sid for sid, s in live.items() if rid in s.role_ids)
        m.roles[rid] = sids
        if not sids and not r.home_screen:
            m.gaps.append(
                Gap("role", rid, f"{r.name} has no screen", "give the role a home screen")
            )

    for fid in sorted(graph.flows):
        f = graph.flows[fid]
        if f.status != "in":
            continue
        sids = sorted({sid for st in f.stages for sid in st.screen_ids})
        m.flows[fid] = sids
        if not f.stages:
            m.gaps.append(Gap("flow", fid, f"{f.name} has no stages", "write its stages"))
            continue
        for st in f.stages:
            missing = [sid for sid in st.screen_ids if sid not in live]
            if not st.screen_ids or missing:
                m.gaps.append(
                    Gap(
                        "flow",
                        fid,
                        f"stage {st.n} of {f.name} points at "
                        + (", ".join(missing) if missing else "no screen")
                        + ", which is not in scope",
                        "map the stage to a real screen",
                    )
                )

    stories_of: dict[str, list[str]] = {}
    for stid, st in sorted(graph.stories.items()):
        for fid in st.feature_ids:
            stories_of.setdefault(fid, []).append(stid)
    for fid in sorted(graph.features):
        f = graph.features[fid]
        if f.status != "in":
            continue
        sts = sorted(stories_of.get(fid, ()))
        scr = sorted(
            {
                sid
                for stid in sts
                for sid in (
                    list(graph.stories[stid].screen_ids)
                    + [sid for sid, s in live.items() if stid in s.stories]
                )
                if sid in live
            }
        )
        m.features[fid] = {"stories": sts, "screens": scr}
        if not sts:
            m.gaps.append(
                Gap(
                    "feature",
                    fid,
                    f"{f.name} has no user story",
                    "write the story that asks for it",
                )
            )
        if not scr:
            m.gaps.append(
                Gap(
                    "feature",
                    fid,
                    f"{f.name} has no screen",
                    "add the screen that delivers it, or mark it later",
                )
            )
    return m


def plain_words(m: CoverageMatrix) -> list[str]:
    """Owner-readable lines for the Gate 2 page: totals first, then every gap."""
    lines = [
        f"{len(m.entities)} data kinds, {len(m.roles)} roles, {len(m.flows)} journeys and "
        f"{len(m.features)} features were checked; {len(m.gaps)} have nothing behind them."
    ]
    lines += [f"{g.kind} {g.id}: {g.problem}. To fix: {g.fix}." for g in m.gaps]
    return lines
