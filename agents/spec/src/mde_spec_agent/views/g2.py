"""The G2 view (``GET /api/runs/{id}/views/g2``): summary first, then everything the owner decides on, in plain words
(doc 15 9.5, doc 18 3.4). Pure: objects in, plain dict out. It stays readable after approval (the hand-off views use it).

The content map of every release screen, the confirm list ranked by CODE (money, legal and identity first, the top 20),
capability tiers, store rules, suggestions that are Open and never scope, what differs from the approved Brief, every
assumed node, the repairs the checks made and the risks that remain.
"""

from __future__ import annotations

from typing import Any

from mde_spec_agent.derive.text import humanize
from mde_spec_core.check import CONFIRM_TOP, confirm_list_ranked
from mde_spec_core.graph import NODE_TYPES, Envelope, SpecGraph
from mde_spec_core.platform_data import default_capability_sheet
from mde_spec_core.policy import default_policy_sheet

from ._data import words
from .common import app_map, entities_plain
from .names import join_words, name_of, node_name, plain
from .packview import check_dict
from .phases import release_ids, screen_title
from .screen import mock_profiles, screen_view

MAX_LISTED = 300


# ---------------------------------------------------------------- helpers


def _kind_word(kind: str) -> str:
    return words()["kinds"].get(kind, kind)


def _more(items: list[str], limit: int = MAX_LISTED) -> list[str]:
    if len(items) <= limit:
        return items
    return [*items[:limit], f"... and {len(items) - limit} more"]


def _dict(x: Any) -> dict[str, Any]:
    if x is None:
        return {}
    if isinstance(x, dict):
        return x
    if hasattr(x, "model_dump"):
        return x.model_dump(mode="json")
    return dict(x)


def _live_screens(graph: SpecGraph) -> list[str]:
    return sorted(sid for sid, s in graph.screens.items() if s.status == "in")


# ---------------------------------------------------------------- flows


def _flows(graph: SpecGraph) -> list[dict[str, Any]]:
    out = []
    for fid, f in sorted(graph.flows.items()):
        if f.status != "in":
            continue
        steps = []
        for st in f.stages:
            where = join_words(
                [screen_title(graph, None, s) for s in st.screen_ids if s in graph.screens]
            )
            steps.append(f"{st.n}. {plain(st.goal, graph)}" + (f" (on {where})" if where else ""))
        out.append(
            {
                "id": fid,
                "title": f.name,
                "roles": [name_of(graph, r) for r in f.role_ids],
                "steps": steps,
                "main": bool(f.primary),
            }
        )
    return out


# ---------------------------------------------------------------- differences from the approved Brief


def _differences(graph: SpecGraph, derived: Any, verify: dict[str, Any]) -> list[str]:
    w = words()
    out: list[str] = []
    inv = getattr(derived, "inventory", None)
    plan = getattr(derived, "plan", None)
    seeded = getattr(derived, "seeded", None)
    if inv is not None:
        for m in inv.merges:
            out.append(
                f"Merged: two screens became one, {screen_title(graph, derived, m.kept)} now covers both ({plain(m.why, graph)})."
            )
        system = [
            p
            for p in inv.screens
            if p.source == "system" and p.id in set(plan.release_screens if plan else [p.id])
        ]
        if system:
            out.append(
                f"Added: {len(system)} screens the app stores or sign-in require: "
                + join_words([p.name for p in system])
                + "."
            )
        for p in inv.screens:
            in_rel = plan is None or p.id in set(plan.release_screens)
            if not in_rel:
                continue
            if p.source == "derived":
                out.append(f"Added: the structure was missing a screen, {p.name}.")
            elif p.source == "peer":
                gap = next((g for g in inv.peers_usually_have if g.screen_id == p.id), None)
                ev = gap.evidence if gap else "research"
                out.append(f"Added: similar apps usually have {p.name} ({ev}).")
        for n in inv.notes:
            low = n.lower()
            tag = "Split" if "split" in low else ("Dropped" if "dropped" in low else "Changed")
            out.append(f"{tag}: {plain(n, graph)}")
    for _, f in sorted(graph.features.items()):
        if f.status == "later":
            reason = f.later_reason or "first_release"
            tag = "Phased" if reason == "first_release" else "Deferred"
            cap = plan.cap if plan is not None else 60
            out.append(
                f"{tag}: {f.name} comes in a later release because "
                + w["later_reasons"].get(reason, w["later_reasons"]["default"]).format(cap=cap)
                + "."
            )
    if plan is not None:
        for p in plan.pulled_in:
            out.append(
                f"Pulled in: {screen_title(graph, derived, p.screen_id)} moved into the first release because "
                + w["pull_in_rules"].get(p.rule, plain(p.because, graph))
                + "."
            )
    if seeded is not None:
        extra = [f"Changed: {plain(x, graph)}" for x in seeded.access_repairs]
        extra += [f"Added link: {plain(x, graph)}" for x in seeded.nav_assumed]
        out.extend(_more_lines(extra, 20))
    for line in verify.get("differs_from_brief", []):
        out.append(f"Changed: {plain(str(line), graph)}")
    if inv is None:  # graph only: what the graph itself shows
        sys_screens = [
            s.name for _, s in sorted(graph.screens.items()) if s.system and s.status == "in"
        ]
        if sys_screens:
            out.append(
                f"Added: {len(sys_screens)} screens the app stores or sign-in require: "
                + join_words(sys_screens)
                + "."
            )
    return out


def _more_lines(items: list[str], limit: int) -> list[str]:
    if len(items) <= limit:
        return items
    return [*items[:limit], f"... and {len(items) - limit} more of the same kind"]


# ---------------------------------------------------------------- assumptions


def _assumed_note(node: Envelope, graph: SpecGraph) -> str:
    for p in node.provenance:
        if p.kind == "assumed" and p.ref:
            return plain(p.ref, graph)
    return "nobody said, so the spec decided"


def _assumptions(graph: SpecGraph) -> tuple[list[str], int]:
    out: list[str] = []
    for kind in NODE_TYPES:
        for _, n in sorted(getattr(graph, kind).items()):
            if n.assumed:
                nm = node_name(n) or "(unnamed)"
                out.append(
                    f'The {_kind_word(kind)} "{plain(nm, graph)}" is assumed: {_assumed_note(n, graph)}.'
                )
    nodes = len(out)
    could = sum(1 for s in graph.screens.values() for c in s.could if c.assumed)
    if could:
        out.append(f"{could} optional ideas rest on an assumed basis.")
    emo = [
        f.name
        for f in graph.flows.values()
        if f.stages and any(st.emotion_assumed for st in f.stages)
    ]
    if emo:
        out.append(
            f"How people feel along {len(emo)} journeys is the spec's own reading, not something the Brief states."
        )
    return _more(out), nodes


# ---------------------------------------------------------------- confirm list


def _confirm(graph: SpecGraph, research: Any) -> list[dict[str, Any]]:
    w = words()
    rows = confirm_list_ranked(
        graph, research, CONFIRM_TOP
    )  # always from the graph: a stored list could be stale
    out = []
    for r in rows:
        kind = r.get("kind", "")
        cat = r.get("category", "other")
        if kind == "rule":
            node = graph.rules.get(r.get("id", ""))
            why = w["confirm_why"][
                "rule_assumed" if node is not None and node.assumed and not node.confirm else "rule"
            ]
        elif kind == "research":
            ev = "unverified" if "(unverified)" in r.get("text", "") else "single_source"
            why = w["confirm_why"]["research"].format(evidence=w["evidence_words"].get(ev, ev))
        else:
            why = w["confirm_why"].get(kind, w["confirm_why"]["screen"])
        extra = w["confirm_why"].get(f"{cat}_extra")
        if extra:
            why = f"{why} {extra}"
        out.append(
            {
                "category": cat,
                "text": plain(r.get("text", ""), graph),
                "why": why,
                "rank": r.get("rank", len(out) + 1),
            }
        )
    return out


# ---------------------------------------------------------------- capability tiers and store rules


def _capability_tiers(graph: SpecGraph) -> list[dict[str, Any]]:
    w = words()
    sheet = default_capability_sheet()
    rows: dict[str, dict[str, Any]] = {}

    def add(cap_id: str, needed_for: str) -> None:
        try:
            c = sheet.get(cap_id)
        except KeyError:
            return
        r = rows.setdefault(
            cap_id,
            {
                "capability": c.label,
                "tier": c.tier,
                "what_it_means": w["tiers"][c.tier],
                "needed_for": [],
                "flagged": bool(w["tier_flag"][c.tier]),
            },
        )
        if needed_for not in r["needed_for"]:
            r["needed_for"].append(needed_for)

    for _, f in sorted(graph.features.items()):
        if f.capability and f.status == "in":
            add(f.capability, f.name)
    facts = set(graph.meta.facts)
    for fact, caps in sorted(w["fact_capabilities"].items()):
        if fact in facts:
            for cap in caps:
                add(cap, "the app as a whole (" + fact.replace("_", " ") + ")")
    ideas: dict[str, list[str]] = {}
    for _, s in sorted(graph.screens.items()):
        for c in s.could:
            if c.tier != "A":
                ideas.setdefault(c.tier, []).append(s.name)
    for tier, names in sorted(ideas.items()):
        rows[f"ideas-{tier}"] = {
            "capability": "Optional polish ideas that need extra setup",
            "tier": tier,
            "what_it_means": w["tiers"][tier],
            "needed_for": sorted(set(names)),
            "flagged": bool(w["tier_flag"][tier]),
        }
    return sorted(rows.values(), key=lambda r: (-"ABC".index(r["tier"]), r["capability"]))


def _policy_facts(graph: SpecGraph) -> dict[str, bool]:
    m = words()["policy_fact_map"]
    have = set(graph.meta.facts)
    return {pf: any(g in have for g in gfs) for pf, gfs in m.items()}


def _store_rules(graph: SpecGraph) -> list[dict[str, Any]]:
    w = words()
    sheet = default_policy_sheet()
    order = {"apple": 0, "google": 1, "standard": 2}
    rows = []
    for r in sorted(
        sheet.applicable(_policy_facts(graph)),
        key=lambda x: (x.category == "accessibility_floor", order.get(x.platform, 9), x.id),
    ):
        rows.append(
            {
                "store": w["store_names"]["standard"]
                if r.category == "accessibility_floor"
                else w["store_names"].get(r.platform, r.platform),
                "plain": w["store_plain"].get(r.id, r.statement),
                "rule": r.id,
                "checked_on": r.checked_on,
                "source": r.source_url,
            }
        )
    return rows


# ---------------------------------------------------------------- suggestions


def _suggestions(graph: SpecGraph, derived: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    inv = getattr(derived, "inventory", None)
    if inv is not None:
        for g in inv.peers_usually_have:
            if not g.added:
                out.append(
                    {
                        "id": f"peer-{g.item_ids[0] if g.item_ids else len(out) + 1}",
                        "text": f"Add a {g.name.lower()} screen",
                        "why": f"Similar apps usually have it ({g.evidence}): {plain(g.reason, graph)}",
                        "included": False,
                    }
                )
    for _, s in sorted(graph.screens.items()):
        for c in s.could:
            if c.kind == "alternative":
                out.append(
                    {
                        "id": f"could-{c.id}",
                        "text": f"{s.name}: {plain(c.effect, graph)}",
                        "why": f"Basis: {plain(c.basis, graph)}",
                        "included": False,
                    }
                )
    return out


# ---------------------------------------------------------------- checks, repairs, risks


def _degraded(graph: SpecGraph, verify: dict[str, Any], flags: list[Any]) -> list[str]:
    out = []
    for kind in NODE_TYPES:
        for _, n in sorted(getattr(graph, kind).items()):
            if n.degraded:
                out.append(
                    f'The {_kind_word(kind)} "{plain(node_name(n), graph)}" was written by a fallback and needs a look.'
                )
    for d in verify.get("degraded_nodes", []):
        line = f'"{name_of(graph, str(d))}" had no repair round left and needs a look.'
        if line not in out:
            out.append(line)
    for f in flags:
        fd = _dict(f)
        if fd.get("kind") == "degraded":
            out.append(plain(str(fd.get("why") or fd.get("where") or "a part needs a look"), graph))
    return _more(out)


def _code_fixes(graph: SpecGraph, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rw = words()["rcode_words"]
    by: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        by.setdefault(str(r.get("kind", "")), []).append(r)
    out = []
    for kind in sorted(by):
        rs = by[kind]
        where = sorted({name_of(graph, str(r.get("node_id", ""))) for r in rs})
        out.append(
            {
                "fix": rw.get(kind, humanize(kind).lower()),
                "count": len(rs),
                "where": join_words(where[:3])
                + (f" and {len(where) - 3} more" if len(where) > 3 else ""),
                "what": plain(str(rs[0].get("what", "")), graph),
            }
        )
    return out


def _flag_rows(graph: SpecGraph, flags: list[Any]) -> list[dict[str, Any]]:
    out = []
    for f in flags:
        d = _dict(f)
        out.append(
            {
                "what": plain(str(d.get("why") or d.get("what") or d.get("kind") or ""), graph),
                "where": plain(str(d.get("where") or d.get("node_id") or ""), graph),
                "fix": plain(str(d.get("fix") or ""), graph),
                "severity": d.get("severity", "warning"),
                "check": d.get("code", ""),
            }
        )
    return out[:MAX_LISTED]


def _verify_block(verify: dict[str, Any]) -> dict[str, Any]:
    if not verify:
        return {"enabled": False}
    findings = verify.get("findings", [])
    open_blockers = [f for f in findings if f.get("severity") == "blocker"]
    canaries = verify.get("canaries", [])
    return {
        "enabled": True,
        "findings": len(findings),
        "open_blockers": len(open_blockers),
        "patches": len(verify.get("patches", [])),
        "rounds_used": verify.get("rounds_used", 0),
        "canaries": len(canaries),
        "canary_blocking_questions": sum(len(c.get("blocking_questions", [])) for c in canaries),
        "critic_vendor": verify.get("critic_vendor", "same"),
        "summary": verify.get("summary", ""),
    }


def _risks(
    graph: SpecGraph,
    check: dict[str, Any],
    tiers: list[dict[str, Any]],
    degraded: list[str],
    n_assumed: int,
    confirm: list[dict[str, Any]],
    derived: Any,
    banners: list[Any],
    verify: dict[str, Any],
    research: Any,
    critic_vendor: Any,
) -> list[dict[str, str]]:
    r = words()["risks"]
    out: list[dict[str, str]] = []
    for n in check.get("builder_needs", {}).get("needs", []):
        if n.get("status") == "missing":
            out.append(
                {"text": r["missing_need"].format(title=n.get("title", "")), "severity": "blocker"}
            )
    sev = check.get("counts", {}).get("by_severity", {})
    if sev.get("blocker"):
        out.append({"text": r["blockers"].format(n=sev["blocker"]), "severity": "blocker"})
    if sev.get("warning"):
        out.append({"text": r["warnings"].format(n=sev["warning"]), "severity": "warning"})
    nb = sum(1 for t in tiers if t["tier"] == "B")
    nc = sum(1 for t in tiers if t["tier"] == "C")
    if nc:
        out.append({"text": r["tier_c"].format(n=nc), "severity": "blocker"})
    if nb:
        out.append({"text": r["tier_b"].format(n=nb), "severity": "warning"})
    if degraded:
        out.append({"text": r["degraded"].format(n=len(degraded)), "severity": "warning"})
    if verify and verify.get("findings"):
        open_n = sum(1 for f in verify["findings"] if f.get("severity") == "blocker")
        if open_n:
            out.append({"text": r["verify_open"].format(n=open_n), "severity": "blocker"})
    if len(confirm) >= 5:
        out.append({"text": r["confirm_many"].format(n=len(confirm)), "severity": "info"})
    if n_assumed:
        out.append(
            {
                "text": r["assumed"].format(n=n_assumed),
                "severity": "info",
            }
        )
    nr = graph.meta.languages[1:] if len(graph.meta.languages) > 1 else []
    if nr:
        out.append({"text": r["native_review"].format(langs=join_words(nr)), "severity": "info"})
    if research is not None and getattr(research, "thin", False):
        out.append({"text": r["thin_research"], "severity": "warning"})
    if research is None:
        out.append({"text": r["thin_research"], "severity": "info"})
    for b in banners:
        bd = _dict(b)
        if bd.get("message"):
            out.append({"text": r["banner"].format(message=bd["message"]), "severity": "warning"})
    plan = getattr(derived, "plan", None)
    if plan is not None and plan.needs_consent:
        out.append({"text": r["consent"], "severity": "info"})
    if critic_vendor == "same" and verify:
        out.append({"text": r["same_critic"], "severity": "info"})
    return out


# ---------------------------------------------------------------- the summary


def _summary(
    graph: SpecGraph,
    derived: Any,
    check: dict[str, Any],
    stored: Any,
    n_release: int,
    n_later: int,
    n_confirm: int,
    n_assumed: int,
) -> dict[str, Any]:
    w = words()["summary"]
    tables = [
        {
            "title": "What is in this spec",
            "rows": [
                {"what": "Apps", "number": len(graph.apps)},
                {"what": "Kinds of user", "number": len(graph.roles)},
                {"what": "Screens in the first release", "number": n_release},
                {"what": "Screens in later releases", "number": n_later},
                {
                    "what": "Kinds of information",
                    "number": len([e for e in graph.entities.values() if e.status == "in"]),
                },
                {
                    "what": "Journeys",
                    "number": len([f for f in graph.flows.values() if f.status == "in"]),
                },
            ],
        }
    ]
    text: str | None = None
    if isinstance(stored, dict):
        text = stored.get("text") or stored.get("paragraph")
    elif isinstance(stored, str):
        text = stored
    if text and text.strip():
        return {"text": plain(text.strip(), graph), "source": "model", "tables": tables}
    apps = [a.name for _, a in sorted(graph.apps.items())]
    apps_text = (
        f"{len(apps)} app{'s' if len(apps) != 1 else ''} ({join_words(apps)})" if apps else "an app"
    )
    parts = [w["opening"].format(apps_text=apps_text, roles=len(graph.roles), screens=n_release)]
    if n_later:
        parts.append(w["later"].format(n=n_later))
    feats = [f for f in graph.features.values() if f.status == "in"]
    parts.append(w["features"].format(n=len(feats)) if feats else "No features were listed.")
    ents = [e for e in graph.entities.values() if e.status == "in"]
    if ents:
        parts.append(w["data"].format(n=len(ents)))
    flows = [f for f in graph.flows.values() if f.status == "in"]
    if flows:
        parts.append(w["flows"].format(n=len(flows)))
    ints = [i.name for _, i in sorted(graph.integrations.items()) if i.status == "in"]
    if ints:
        parts.append(w["integrations"].format(text=join_words(ints)))
    if len(graph.meta.languages) > 1:
        parts.append(w["languages"].format(text=join_words(graph.meta.languages)))
    missing = [
        n["title"]
        for n in check.get("builder_needs", {}).get("needs", [])
        if n.get("status") == "missing"
    ]
    blockers = check.get("counts", {}).get("by_severity", {}).get("blocker", 0)
    if missing:
        parts.append(w["blocked"].format(text=join_words(missing)))
    elif check:
        parts.append(w["checks_open"].format(n=blockers) if blockers else w["checks_ok"])
    if n_confirm:
        parts.append(w["confirm"].format(n=n_confirm).replace("1 things need", "1 thing needs"))
    if n_assumed:
        parts.append(w["assumptions"].format(n=n_assumed))
    return {"text": " ".join(parts), "source": "template", "tables": tables}


# ---------------------------------------------------------------- the view


def g2(
    graph: SpecGraph,
    pack: Any,
    check: Any,
    derived: Any = None,
    *,
    flags: list[Any] | None = None,
    banners: list[Any] | None = None,
    rcode_rows: list[dict[str, Any]] | None = None,
    verify_report: Any = None,
    summary: Any = None,
    research: Any = None,
    mock: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """The documented G2 shape. ``check`` is a ``CheckReport`` or the dict of ``evidence/self-check.json``;
    ``summary`` is the stored model paragraph (a string or ``{text}``), a template paragraph is written from code when absent."""
    cd = check_dict(check) if check is not None else {}
    verify = _dict(verify_report)
    flags = list(flags if flags is not None else cd.get("flags", []))
    banners = list(banners if banners is not None else cd.get("banners", []))
    rows = list(rcode_rows if rcode_rows is not None else cd.get("rcode", {}).get("rows", []))
    mocks = mock if mock is not None else mock_profiles(graph)
    release, deferred = release_ids(graph, derived)
    live = _live_screens(graph)
    maps = [screen_view(graph, sid, "typical", mock=mocks) for sid in live]
    diffs = _differences(graph, derived, verify)
    assumptions, n_assumed = _assumptions(graph)
    confirm = _confirm(graph, research)
    tiers = _capability_tiers(graph)
    degraded = _degraded(graph, verify, flags)
    manifest = getattr(pack, "manifest", {}) if pack is not None else {}
    checked_by = list(manifest.get("checked_by", ["self_check"]))
    critic = manifest.get("critic_vendor")
    if critic is None and verify:
        critic = verify.get("critic_vendor")
    blocking = [
        f"{n.get('title', '')}: {n.get('why', '')}"
        for n in cd.get("builder_needs", {}).get("needs", [])
        if n.get("status") == "missing"
    ]
    blocking_checks = [
        {
            "code": str(e.get("code", "")),
            # plain words for the owner: no bare id reaches the page (the raw ids stay in evidence/self-check.json)
            "where": plain(str(e.get("where", "")), graph),
            "why": plain(str(e.get("why", "")), graph),
            "fix": plain(str(e.get("fix", "")), graph),
        }
        for e in cd.get("errors", [])
        if isinstance(e, dict) and e.get("severity") == "blocker"
    ]
    content_hash = (
        getattr(pack, "content_hash", None) or manifest.get("content_hash") or graph.content_hash()
    )
    return {
        "summary": _summary(
            graph,
            derived,
            cd,
            summary,
            len(release),
            len(deferred),
            len(confirm),
            n_assumed,
        ),
        "content_hash": content_hash,
        "app_map": app_map(graph, derived),
        "flows": _flows(graph),
        "content_maps": maps,
        "data": [{"entity": e["name"], "plain": e["plain"]} for e in entities_plain(graph)],
        "differences_from_brief": diffs,
        "assumptions": assumptions,
        "confirm_list": confirm,
        "capability_tiers": tiers,
        "store_rules": _store_rules(graph),
        "suggestions": _suggestions(graph, derived),
        "degraded_nodes": degraded,
        "code_fixes": _code_fixes(graph, rows),
        "code_fixes_total": len(rows),
        "flags": _flag_rows(graph, flags),
        "risks": _risks(
            graph,
            cd,
            tiers,
            degraded,
            n_assumed,
            confirm,
            derived,
            banners,
            verify,
            research,
            critic,
        ),
        "checked_by": checked_by,
        "critic_vendor": critic,
        "banners": [
            {"code": _dict(b).get("code", ""), "message": _dict(b).get("message", "")}
            for b in banners
        ],
        "verify": _verify_block(verify),
        "blocking_needs": blocking,
        # checks that failed with blocker status: approving needs an explicit, recorded acknowledgement
        "blocking_checks": blocking_checks[:MAX_LISTED],
        "blocking_checks_total": len(blocking_checks),
    }
