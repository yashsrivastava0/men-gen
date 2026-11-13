"""Static rules of the Spec Bench: the files, a strict content-security policy by construction (no inline script, style
or handler), no HTML injection, no outside URLs, the size budget, links only through the safe helpers, one place for
fetch and storage, and a module graph in which every import resolves and every file is used."""

from __future__ import annotations

import gzip
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JS_FILES = sorted([ROOT / "app.js", *(ROOT / "lib").glob("*.js"), *(ROOT / "views").glob("*.js")])
ALL_TEXT = [ROOT / "index.html", ROOT / "style.css", *JS_FILES]
REQUIRED = [
    "index.html",
    "app.js",
    "style.css",
    "package.json",
    "lib/api.js",
    "lib/dom.js",
    "lib/eventtext.js",
    "lib/format.js",
    "lib/model.js",
    "lib/money.js",
    "lib/reducer.js",
    "lib/routes.js",
    "lib/runfeed.js",
    "lib/safe.js",
    "lib/sse.js",
    "lib/store.js",
    "views/shared.js",
    "views/contentmap.js",
    "views/inputs.js",
    "views/preview.js",
    "views/g15.js",
    "views/run.js",
    "views/g2.js",
    "views/handoff.js",
    "views/handoff_sim.js",
    "views/handoff_probe.js",
    "views/handoff_screens.js",
    "views/handoff_journey.js",
    "views/handoff_graph.js",
    "views/handoff_data.js",
    "views/handoff_needs.js",
    "views/handoff_pack.js",
    "views/runs.js",
    "views/budget.js",
]


def code_only(js: str) -> str:
    """The script without comments (a comment may talk about what the code must never do)."""
    js = re.sub(r"/\*.*?\*/", "", js, flags=re.S)
    return re.sub(r"(^|\s)//[^\n]*", r"\1", js)


def test_the_files_exist() -> None:
    assert [n for n in REQUIRED if not (ROOT / n).is_file()] == []


def test_no_way_to_inject_html_or_run_text_as_code() -> None:
    banned = [
        r"\binnerHTML\b",
        r"\bouterHTML\b",
        r"\binsertAdjacentHTML\b",
        r"document\s*\.\s*write",
        r"\beval\s*\(",
        r"new\s+Function\b",
        r"\bsrcdoc\b",
        r"\bsetTimeout\s*\(\s*[\"'`]",
        r"\bsetInterval\s*\(\s*[\"'`]",
        r"\bwindow\.open\b",
        r"\bdocument\.cookie\b",
        r"createContextualFragment",
        r"DOMParser",
        r"\bimportScripts\b",
        r"\bWorker\s*\(",
        r"\bXMLHttpRequest\b",
        r"\bWebSocket\b",
    ]
    for f in JS_FILES:
        text = f.read_text(
            encoding="utf-8"
        )  # raw, comments included: they must not even mention these
        for pattern in banned:
            assert not re.search(pattern, text), f"{f.name}: {pattern}"


def test_index_html_has_no_inline_script_style_or_handler() -> None:
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    scripts = re.findall(r"<script\b([^>]*)>(.*?)</script>", html, flags=re.S | re.I)
    assert scripts, "the page needs its module script"
    for attrs, body in scripts:
        assert "src=" in attrs and body.strip() == "", "inline script body found"
    assert 'type="module"' in html
    assert not re.search(r"<style\b", html, flags=re.I)
    assert not re.search(r"\sstyle\s*=", html, flags=re.I)
    assert not re.search(r"\son[a-z]+\s*=", html, flags=re.I)
    assert not re.search(r"javascript:", html, flags=re.I)
    assert "<title>Spec Bench</title>" in html
    assert "<noscript>" in html


def test_the_page_has_the_fake_banner_the_money_header_and_the_rail() -> None:
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    assert re.search(r'id="fake-banner"[^>]*\bhidden\b[^>]*>TEST FAKE', html), (
        "the TEST FAKE banner starts hidden"
    )
    for needle in (
        'id="money"',
        'id="money-text"',
        'id="money-meter"',
        'id="rail"',
        'id="mode-pill"',
        'id="view"',
        'id="announcer"',
    ):
        assert needle in html, needle
    app = code_only((ROOT / "app.js").read_text(encoding="utf-8"))
    assert "health.fake" in app and '"fake-banner"' in app and "/api/health" in app


def test_no_inline_styles_or_handlers_in_script_strings() -> None:
    for f in JS_FILES:
        code = code_only(f.read_text(encoding="utf-8"))
        assert not re.search(r"\bstyle\s*=(?!=)", code), f"{f.name}: a style attribute"
        if f.name != "dom.js":  # the helper names "style" only to refuse it
            assert not re.search(r"[\"'`]style[\"'`]", code), f"{f.name}: setting a style attribute"
        assert not re.search(r"\sstyle=", code), f"{f.name}: style= in a template string"
        assert not re.search(r"<[a-z][^>]*\son[a-z]+\s*=", code, flags=re.I), (
            f"{f.name}: an inline handler in a string"
        )
        assert not re.search(
            r"<(?:script|style|iframe|img|svg|object|embed|link|meta|base|form)\b", code, flags=re.I
        ), f"{f.name}: markup in a string"
        # strings that look like a tag at all: the pages are built with h() and svg(), never from markup text
        assert not re.search(r"[\"'`]\s*<[a-zA-Z/!]", code), (
            f"{f.name}: a string that starts like markup"
        )


def test_no_outside_addresses_fonts_or_imports() -> None:
    for f in ALL_TEXT:
        text = f.read_text(encoding="utf-8")
        assert not re.search(r"https?://", text, flags=re.I), (
            f"{f.name}: an absolute http(s) address"
        )
        assert "//cdn" not in text and "googleapis" not in text
    css = (ROOT / "style.css").read_text(encoding="utf-8")
    assert "@import" not in css and "url(" not in css and "@font-face" not in css
    for f in JS_FILES:
        for target in re.findall(
            r"import\s*(?:\([\"']|[^;]*?from\s*[\"'])([^\"']+)", f.read_text(encoding="utf-8")
        ):
            assert target.startswith("."), f"{f.name} imports {target}"
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    for ref in re.findall(r"(?:src|href)=\"([^\"]*)\"", html):
        assert ref.startswith("data:") or not re.match(r"[a-z]+:|//|/", ref), f"index.html: {ref}"


def test_all_javascript_is_small() -> None:
    raw = sum(len(f.read_bytes()) for f in JS_FILES)
    zipped = sum(len(gzip.compress(f.read_bytes(), 9)) for f in JS_FILES)
    assert zipped < 100 * 1024, f"{zipped} bytes gzipped"
    assert raw < 400 * 1024, f"{raw} bytes of source"
    for f in JS_FILES:
        assert f.stat().st_size < 32 * 1024, f"{f.name} is {f.stat().st_size} bytes: split it"
    assert (ROOT / "style.css").stat().st_size < 64 * 1024


def test_every_link_is_made_by_the_safe_helpers() -> None:
    """Only lib/dom.js may touch an address attribute or make an anchor; there it goes through safeUrl, safeApiPath or safeRoute."""
    dom = (ROOT / "lib" / "dom.js").read_text(encoding="utf-8")
    assert "safeUrl(url)" in dom and "safeRoute(route)" in dom and "safeApiPath(path)" in dom
    link_fn = dom[dom.index("export function link(") : dom.index("export function fileLink(")]
    assert (
        "safeUrl" in link_fn
        and 'rel", "noopener noreferrer"' in link_fn
        and '"target", "_blank"' in link_fn
    )
    for f in JS_FILES:
        code = code_only(f.read_text(encoding="utf-8"))
        if f.name == "dom.js":
            continue
        assert not re.search(r"\.href\s*=(?!=)|[\"']href[\"']|\bhref\s*:", code), (
            f"{f.name}: an address attribute outside dom.js"
        )
        assert not re.search(
            r"\.src\s*=|\"src\"|\blocation\.(?:href|assign|replace)\b|\.action\s*=", code
        ), f.name
        assert not re.search(r"createElement\(\s*[\"']a[\"']", code), f.name
        assert not re.search(r"\bh\(\s*[\"']a[\"']", code), f.name
        assert not re.search(r"createObjectURL", code), f"{f.name}: blob addresses belong in dom.js"
    assert "attribute not allowed" in dom and "handlers must be functions" in dom
    # the svg() helper refuses the attributes that could carry an address or a style
    svg_fn = dom[dom.index("export function svg(") : dom.index("/** Append children")]
    assert '"style"' in svg_fn and '"href"' in svg_fn and "createElementNS" in svg_fn


def test_pages_are_made_only_through_the_helpers() -> None:
    """Views never call createElement or createElementNS themselves: every element passes through h() or svg()."""
    for f in [*(ROOT / "views").glob("*.js"), *(ROOT / "lib").glob("*.js")]:
        if f.name == "dom.js":
            continue
        code = code_only(f.read_text(encoding="utf-8"))
        assert "createElementNS" not in code, f.name
        if f.parent.name == "views":
            assert "createElement" not in code and "createTextNode" not in code, f.name
            assert "document.body" not in code, f.name


def test_text_from_outside_goes_in_as_text() -> None:
    dom = (ROOT / "lib" / "dom.js").read_text(encoding="utf-8")
    assert "createTextNode" in dom and "textContent" in dom and 'setAttribute("dir", "auto")' in dom
    view_text = {
        f.name: code_only(f.read_text(encoding="utf-8")) for f in (ROOT / "views").glob("*.js")
    }
    for name in (
        "g2.js",
        "contentmap.js",
        "handoff_pack.js",
        "handoff_screens.js",
        "handoff_journey.js",
        "handoff_data.js",
        "handoff_probe.js",
        "inputs.js",
        "run.js",
    ):
        assert any(
            k in view_text[name] for k in ("untrusted(", "genericTable(", "renderContentMap(")
        ), name
    assert (
        'svg("text"' in view_text["handoff_graph.js"]
    )  # drawn labels are text children of svg(), i.e. text nodes
    shared = view_text["shared.js"]
    assert "untrusted(textOf(" in shared  # the generic table puts every cell in as text
    # a pack file is shown in a <pre> through the helper, as a string child (a text node)
    pack = view_text["handoff_pack.js"]
    assert 'h("pre"' in pack and "text)" in pack


def test_fetch_and_storage_have_one_home_each() -> None:
    for f in JS_FILES:
        code = code_only(f.read_text(encoding="utf-8"))
        if f.name not in {"api.js"}:
            assert not re.search(r"\bfetch\s*\(", code), f"{f.name}: fetch outside lib/api.js"
        if f.name not in {"store.js", "app.js"}:
            assert not re.search(r"localStorage|sessionStorage|indexedDB", code), (
                f"{f.name}: storage outside the store"
            )
        if f.name != "store.js":
            assert not re.search(r"\b(?:getItem|setItem|removeItem)\b", code), (
                f"{f.name}: raw storage calls"
            )
    store = code_only((ROOT / "lib" / "store.js").read_text(encoding="utf-8"))
    assert store.count("try {") >= 4 and store.count("catch") >= 4, (
        "every storage access is wrapped"
    )
    app = code_only((ROOT / "app.js").read_text(encoding="utf-8"))
    assert "createStore(() => window.localStorage)" in app, (
        "the storage getter is passed lazily, so a blocked storage cannot throw at load"
    )


def test_the_module_graph_resolves_and_every_file_is_used() -> None:
    graph: dict[Path, set[Path]] = {}
    for f in JS_FILES:
        code = code_only(f.read_text(encoding="utf-8"))
        targets = re.findall(r"import\s*(?:\(\s*[\"']|[^;]*?from\s*[\"'])(\.[^\"']+)", code)
        resolved = set()
        for t in targets:
            p = (f.parent / t).resolve()
            assert p.is_file(), f"{f.name} imports {t}, which does not exist"
            resolved.add(p)
        graph[f.resolve()] = resolved
    seen: set[Path] = set()
    todo = [(ROOT / "app.js").resolve()]
    while todo:
        cur = todo.pop()
        if cur in seen:
            continue
        seen.add(cur)
        todo.extend(graph[cur])
    unused = sorted(p.name for p in graph if p not in seen)
    assert unused == [], f"files nothing imports: {unused}"
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    assert 'src="app.js"' in html and 'href="style.css"' in html


def test_every_view_exports_the_mount_the_shell_calls() -> None:
    for f in (ROOT / "views").glob("*.js"):
        code = code_only(f.read_text(encoding="utf-8"))
        if f.name in {"shared.js", "contentmap.js"}:
            continue
        if f.name.startswith("handoff_"):
            assert "export function mountTab(" in code, f.name
        else:
            assert "export function mount(" in code, f.name
    app = code_only((ROOT / "app.js").read_text(encoding="utf-8"))
    for name in ("inputs", "preview", "g15", "run", "g2", "handoff", "runs", "budget"):
        assert f'import("./views/{name}.js")' in app, name
