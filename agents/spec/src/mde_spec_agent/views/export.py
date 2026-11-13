"""The static export (``GET /api/runs/{id}/export/preview.html``): ONE self-contained offline HTML file for a pack.

It holds no script at all, loads nothing (no link tag, no external address, no image), and every string from the pack or the
graph goes through ``html.escape`` (and has its URL schemes defanged), so a poisoned screen purpose or feature name shows up
as harmless text. Navigation is by in-page anchors. The page can be handed to anyone and works with no network.
"""

from __future__ import annotations

import re
from typing import Any

from mde_spec_core.graph import SpecGraph

from ._data import words
from .data import data_view
from .journey import journey_view
from .names import esc
from .nav import nav_graph
from .needs import needs_view
from .packview import budget_view, pack_tree
from .screen import mock_profiles, screen_view

_CSS = """
:root{--bg:#fff;--fg:#1c1c1e;--muted:#5c5c63;--line:#d9d9de;--card:#f6f6f8;--must:#0b5d3b;--usually:#7a4b00;--could:#4a3b8c;--bad:#a1241a}
@media (prefers-color-scheme: dark){:root{--bg:#141416;--fg:#ececf0;--muted:#a3a3ad;--line:#34343b;--card:#1d1d21;--must:#6fd3a4;--usually:#e6b35a;--could:#b7a8f2;--bad:#ff8a80}}
*{box-sizing:border-box}
body{margin:0;padding:16px;background:var(--bg);color:var(--fg);font:16px/1.5 system-ui,sans-serif;max-width:60rem;margin-inline:auto;overflow-wrap:anywhere}
h1,h2,h3,h4{line-height:1.25}
nav.toc{display:flex;flex-wrap:wrap;gap:.5rem 1rem;padding:.5rem 0;border-bottom:1px solid var(--line)}
a{color:inherit}
section{margin:2rem 0}
article{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:12px 16px;margin:12px 0}
.note{color:var(--muted);font-style:italic}
.voice h5{margin:.75rem 0 .25rem}
.voice-must h5{color:var(--must)}.voice-usually h5{color:var(--usually)}.voice-could h5{color:var(--could)}
.bad{color:var(--bad)}
table{border-collapse:collapse;width:100%}
th,td{border:1px solid var(--line);padding:4px 8px;text-align:left;vertical-align:top}
.scroll{overflow-x:auto}
"""


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "-", text).strip("-") or "x"


def _p(text: object, cls: str = "") -> str:
    attr = f' class="{cls}"' if cls else ""
    return f"<p{attr}>{esc(text)}</p>"


def _ul(items: list[Any]) -> str:
    if not items:
        return ""
    return "<ul>" + "".join(f"<li>{esc(i)}</li>" for i in items) + "</ul>"


def _table(headers: list[str], rows: list[list[Any]]) -> str:
    if not rows:
        return _p("None.", "note")
    head = "".join(f'<th scope="col">{esc(h)}</th>' for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{esc(c)}</td>" for c in r) + "</tr>" for r in rows)
    return f'<div class="scroll"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'


def _screen_html(v: dict[str, Any]) -> str:
    w = words()
    out = [f'<article id="screen-{_slug(v["id"])}">', f"<h3>{esc(v['title'])}</h3>"]
    if v["purpose"]:
        out.append(_p(v["purpose"]))
    out.append(_p(w["fixed_copy"]["illustrative"] + f" Showing: {v['mock_profile_note']}.", "note"))
    for key in ("must", "usually", "could"):
        regs = [r for r in v["regions"] if r["importance"] == key]
        out.append(f'<div class="voice voice-{key}"><h5>{esc(w["voices"][key])}</h5>')
        if regs:
            out.append("<ul>")
            for r in regs:
                out.append(
                    f"<li><strong>{esc(r['name'])}</strong>: {esc(r['text'])}{_ul(r['items'])}</li>"
                )
            out.append("</ul>")
        else:
            out.append(_p("None.", "note"))
        out.append("</div>")
    if v["actions"]:
        out.append("<h5>Actions</h5><ul>")
        for a in v["actions"]:
            out.append(
                f"<li><strong>{esc(a['label'])}</strong> to {esc(a['intent'])}; then {esc(a['effect'])}</li>"
            )
        out.append("</ul>")
    if v["states"]:
        out.append("<h5>States</h5><ul>")
        for s in v["states"]:
            out.append(f"<li><strong>{esc(s['name'])}</strong>: {esc(s['text'])}</li>")
        out.append("</ul>")
    if v["copy"]:
        out.append("<h5>Words on the screen (source language)</h5><ul>")
        for c in v["copy"]:
            out.append(f"<li>{esc(c['key'])}: {esc(c['text'])}</li>")
        out.append("</ul>")
    if v["hero_records"]:
        out.append("<h5>Example records</h5>" + _ul(v["hero_records"]))
    out.append("</article>")
    return "".join(out)


def _journey_html(j: dict[str, Any]) -> str:
    out = [f'<article id="journey-{_slug(j["id"])}">', f"<h3>{esc(j['title'])}</h3>"]
    if j["role"]:
        out.append(_p(f"As {j['role']}."))
    if j["stories"]:
        out.append("<h5>Stories</h5>" + _ul(j["stories"]))
    if j["emotion_line"]:
        out.append(
            "<h5>How the person feels</h5>"
            + _ul([f"{e['label']}: {e['feeling']}" for e in j["emotion_line"]])
        )
    if j["moments_that_matter"]:
        out.append("<h5>Moments that matter</h5>" + _ul(j["moments_that_matter"]))
    if j["unhappy_paths"]:
        out.append(
            "<h5>When things go wrong</h5>"
            + _ul(
                [
                    u["what"] + (f" Then: {u['recovery']}" if u["recovery"] else "")
                    for u in j["unhappy_paths"]
                ]
            )
        )
    if j["steps"]:
        out.append("<h5>Steps</h5><ol>")
        for s in j["steps"]:
            exits = "".join(
                f'<li><a href="#screen-{_slug(x["to"])}">{esc(x["label"])}</a></li>'
                for x in s["exits"]
            )
            out.append(
                f'<li><a href="#screen-{_slug(s["screen_id"])}">{esc(s["title"])}</a>: {esc(s["says"])}'
                + (f"<ul>{exits}</ul>" if exits else "")
                + "</li>"
            )
        out.append("</ol>")
    out.append("</article>")
    return "".join(out)


def preview_html(
    pack: Any,
    graph: SpecGraph,
    *,
    g2_view: dict[str, Any] | None = None,
    derived: Any = None,
    profile: str = "typical",
    mock: dict[str, dict[str, Any]] | None = None,
    title: str | None = None,
) -> str:
    """One offline HTML page: summary, content map of every screen, journeys, navigation, data and the pack's files."""
    mocks = mock if mock is not None else mock_profiles(graph)
    live = sorted(sid for sid, s in graph.screens.items() if s.status == "in")
    screens = (
        {m["id"]: m for m in g2_view["content_maps"]}
        if g2_view is not None and profile == "typical"
        else {sid: screen_view(graph, sid, profile, mock=mocks) for sid in live}
    )
    nav = nav_graph(graph, None, derived)
    data = data_view(graph, mock=mocks)
    needs = needs_view(pack, graph)
    tree = pack_tree(pack)
    bud = budget_view(pack)
    page_title = title or (
        "Spec preview: " + (", ".join(a.name for a in graph.apps.values()) or "app")
    )
    parts: list[str] = [
        "<!doctype html>",
        '<html lang="en"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f"<title>{esc(page_title)}</title>",
        f"<style>{_CSS}</style></head><body>",
        f"<h1>{esc(page_title)}</h1>",
        _p(
            "A self-contained preview of one Spec Pack. It works offline and holds no script. "
            + words()["fixed_copy"]["illustrative"],
            "note",
        ),
        _table(
            ["Fact", "Value"],
            [
                ["Content hash", pack.manifest.get("content_hash", "")],
                ["Package hash", pack.manifest.get("package_hash", "")],
                ["Size of the app", pack.manifest.get("profile", graph.meta.profile)],
                ["Files", len(pack.files)],
                ["Tokens in the whole pack", bud["total_tokens"]],
            ],
        ),
        '<nav class="toc" aria-label="Sections">'
        + "".join(
            f'<a href="#{a}">{esc(t)}</a>'
            for a, t in (
                ("summary", "Summary"),
                ("screens", "Screens"),
                ("journeys", "Journeys"),
                ("navigation", "Navigation"),
                ("data", "Data"),
                ("needs", "Needs"),
                ("files", "Files"),
            )
        )
        + "</nav>",
    ]
    # summary
    parts.append('<section id="summary"><h2>Summary</h2>')
    if g2_view is not None:
        parts.append(_p(g2_view["summary"]["text"]))
        if g2_view["confirm_list"]:
            parts.append(
                "<h3>Please confirm</h3><ol>"
                + "".join(
                    f"<li>{esc(c['category'].title())}: {esc(c['text'])}</li>"
                    for c in g2_view["confirm_list"]
                )
                + "</ol>"
            )
        if g2_view["store_rules"]:
            parts.append(
                "<h3>App store rules that apply</h3>"
                + _ul([f"{r['store']}: {r['plain']}" for r in g2_view["store_rules"]])
            )
        if g2_view["differences_from_brief"]:
            parts.append(
                "<h3>What differs from the Brief</h3>" + _ul(g2_view["differences_from_brief"])
            )
    else:
        parts.append(
            _p(
                f"{len(graph.apps)} app(s), {len(graph.roles)} role(s), {len(live)} screens in the first release."
            )
        )
    parts.append("</section>")
    # screens
    parts.append(f'<section id="screens"><h2>Screens ({len(live)})</h2>')
    parts.extend(_screen_html(screens[sid]) for sid in live if sid in screens)
    parts.append("</section>")
    # journeys
    flows = sorted(fid for fid, f in graph.flows.items() if f.status == "in")
    parts.append(f'<section id="journeys"><h2>Journeys ({len(flows)})</h2>')
    parts.extend(_journey_html(journey_view(graph, fid, None, profile)) for fid in flows)
    parts.append("</section>")
    # navigation
    titles = {n["id"]: n["title"] for n in nav["nodes"]}
    parts.append('<section id="navigation"><h2>Navigation</h2>')
    parts.append(
        _table(
            ["From", "Action", "To"],
            [
                [titles.get(e["from"], e["from"]), e["label"], titles.get(e["to"], e["to"])]
                for e in nav["edges"]
            ],
        )
    )
    parts.append(
        "<h3>Screens nobody can reach</h3>"
        + (_ul([titles.get(x, x) for x in nav["unreachable"]]) or _p("None.", "note"))
    )
    parts.append(
        "<h3>Screens with no way out</h3>"
        + (_ul([titles.get(x, x) for x in nav["dead_ends"]]) or _p("None.", "note"))
    )
    parts.append("</section>")
    # data
    parts.append('<section id="data"><h2>Data</h2>')
    parts.extend(
        f"<article><h3>{esc(e['name'])}</h3>{_p(e['plain'])}</article>" for e in data["entities"]
    )
    parts.append("<h3>Integrations</h3>")
    parts.append(
        _table(
            ["Service", "What happens", "If interrupted", "If pending too long"],
            [
                [i["name"], i["what"], i["if_interrupted"], i["pending_timeout"]]
                for i in data["integrations"]
            ],
        )
    )
    parts.append("</section>")
    # needs
    summary_line = f"{len(needs['needs'])} needs, {len(needs['blocks'])} shared blocks, {len(needs['assets'])} assets."
    parts.append(f'<section id="needs"><h2>Needs</h2>{_p(summary_line)}</section>')
    # files
    parts.append('<section id="files"><h2>Files</h2>')
    parts.append(_table(["File", "Tokens"], [[f["path"], f["tokens"]] for f in tree["files"]]))
    parts.append("<h3>What each task reads</h3>")
    parts.append(
        _table(
            ["Task", "Title", "Reads (tokens)", "Budget"],
            [[t["id"], t["title"], t["read_tokens"], t["budget"]] for t in tree["tasks"]],
        )
    )
    parts.append("</section></body></html>")
    return "\n".join(parts)
