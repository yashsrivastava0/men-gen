"""Static rules of the Research console: the files, the strict content-security policy, no HTML injection, no outside URLs,
the size budget, links only through the safe helpers, and the real app serving it with the right headers."""

from __future__ import annotations

import gzip
import re
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
JS_FILES = sorted([ROOT / "app.js", *(ROOT / "lib").glob("*.js"), *(ROOT / "views").glob("*.js")])
ALL_TEXT = [ROOT / "index.html", ROOT / "style.css", *JS_FILES]
REQUIRED = [
    "index.html",
    "app.js",
    "style.css",
    "lib/api.js",
    "lib/budget.js",
    "lib/dom.js",
    "lib/filters.js",
    "lib/format.js",
    "lib/reducer.js",
    "lib/runfeed.js",
    "lib/safe.js",
    "lib/sse.js",
    "lib/store.js",
    "lib/vlist.js",
    "lib/markdown.js",
    "lib/results.js",
    "lib/spotcheck.js",
    "views/inputs.js",
    "views/run.js",
    "views/runs.js",
    "views/results.js",
    "views/budget.js",
    "views/guide.js",
    "views/preflight.js",
    "views/dialogs.js",
    "views/shared.js",
    "views/soon.js",
    "views/item.js",
    "views/results_data.js",
    "views/results_items.js",
    "views/results_norms.js",
    "views/results_notes.js",
    "views/results_quality.js",
    "views/results_report.js",
    "views/results_sources.js",
    "views/spotcheck.js",
]


def code_only(js: str) -> str:
    """The script without comments (a comment may talk about what the code must never do)."""
    js = re.sub(r"/\*.*?\*/", "", js, flags=re.S)
    return re.sub(r"(^|\s)//[^\n]*", r"\1", js)


def test_the_files_exist() -> None:
    missing = [name for name in REQUIRED if not (ROOT / name).is_file()]
    assert missing == []


def test_no_way_to_inject_html_or_run_text_as_code() -> None:
    banned = [
        r"\binnerHTML\b",
        r"\bouterHTML\b",
        r"\binsertAdjacentHTML\b",
        r"document\s*\.\s*write",
        r"\beval\s*\(",
        r"new\s+Function\b",
        r"\bsrcdoc\b",
        r"\bsetTimeout\s*\(\s*[\"'`]",  # a string handed to a timer is eval
        r"\bsetInterval\s*\(\s*[\"'`]",
        r"\bwindow\.open\b",
        r"\bdocument\.cookie\b",
        r"createContextualFragment",
        r"DOMParser",
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


def test_no_inline_styles_or_handlers_in_script_strings() -> None:
    for f in JS_FILES:
        code = code_only(f.read_text(encoding="utf-8"))
        assert not re.search(r"\bstyle\s*=(?!=)", code), f"{f.name}: a style attribute"
        assert not re.search(r"[\"'`]style[\"'`]", code), f"{f.name}: setting a style attribute"
        assert not re.search(r"\sstyle=", code), f"{f.name}: style= in a template string"
        assert not re.search(r"<[a-z][^>]*\son[a-z]+\s*=", code, flags=re.I), (
            f"{f.name}: an inline handler in a string"
        )
        assert not re.search(
            r"<(?:script|style|iframe|img|svg|object|embed)\b", code, flags=re.I
        ), f"{f.name}: markup in a string"


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


def test_all_javascript_is_small() -> None:
    raw = sum(len(f.read_bytes()) for f in JS_FILES)
    zipped = sum(len(gzip.compress(f.read_bytes(), 9)) for f in JS_FILES)
    assert zipped < 100 * 1024, f"{zipped} bytes gzipped"
    assert raw < 400 * 1024, f"{raw} bytes of source"


def test_every_link_is_made_by_the_safe_helpers() -> None:
    """Only lib/dom.js may touch an address attribute or make an anchor; there it goes through safeUrl / safeRoute."""
    dom = (ROOT / "lib" / "dom.js").read_text(encoding="utf-8")
    assert "safeUrl(url)" in dom and "safeRoute(route)" in dom
    link_fn = dom[dom.index("export function link(") : dom.index("export function navLink(")]
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
    # the helper itself refuses anything but its own attribute list and string handlers
    assert "attribute not allowed" in dom and "handlers must be functions" in dom


def test_text_from_outside_goes_in_as_text() -> None:
    """The DOM helper only ever creates text nodes or sets textContent for strings."""
    dom = (ROOT / "lib" / "dom.js").read_text(encoding="utf-8")
    assert "createTextNode" in dom and "textContent" in dom
    assert 'setAttribute("dir", "auto")' in dom
    for f in (ROOT / "views").glob("*.js"):
        code = code_only(f.read_text(encoding="utf-8"))
        assert (
            ".textContent =" in code
            or "untrusted(" in code
            or f.name
            in {"guide.js", "soon.js", "dialogs.js", "results_data.js", "results_report.js"}
        )


def test_the_app_serves_it_with_the_strict_policy_and_script_types() -> None:
    import anyio

    from mde_research_agent.api import create_app
    from mde_research_agent.config import ResearchSettings

    app = create_app(settings=ResearchSettings(), ui_path=ROOT, sweep_seconds=3600)

    async def fetch(path: str) -> httpx.Response:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as c:
            return await c.get(path)

    page = anyio.run(fetch, "/")
    assert page.status_code == 200 and "<title>Research</title>" in page.text
    csp = page.headers["content-security-policy"]
    for part in (
        "default-src 'none'",
        "script-src 'self'",
        "style-src 'self'",
        "connect-src 'self'",
        "img-src 'self' data:",
    ):
        assert part in csp
    assert "unsafe-inline" not in csp and "unsafe-eval" not in csp
    assert page.headers["x-content-type-options"] == "nosniff"
    for path in ("/app.js", "/lib/reducer.js", "/views/run.js"):
        r = anyio.run(fetch, path)
        assert r.status_code == 200 and "javascript" in r.headers["content-type"], path
    assert "text/css" in anyio.run(fetch, "/style.css").headers["content-type"]


def test_the_report_preview_is_built_from_text_nodes_only() -> None:
    """The Markdown preview: the parser returns plain data, and the screen draws it only through h(), link() and text."""
    parser = code_only((ROOT / "lib" / "markdown.js").read_text(encoding="utf-8"))
    assert "document" not in parser and "createElement" not in parser, (
        "the Markdown reader must not touch the page"
    )
    assert "safeUrl" in parser
    view = code_only((ROOT / "views" / "results_report.js").read_text(encoding="utf-8"))
    assert "link(s.url, s.text)" in view and "parseMarkdown(" in view
    assert "innerHTML" not in view and "createElement" not in view
