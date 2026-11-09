"""UI smoke: the console files are served, assets exist, no inline script or style, no markup
injection, the strict CSP holds, every script parses."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import httpx
import pytest

CONSOLE = Path(__file__).resolve().parents[1] / "console"
INDEX = (CONSOLE / "index.html").read_text(encoding="utf-8")
REGISTRY_JS = (CONSOLE / "shared" / "registry.js").read_text(encoding="utf-8")

FILES = sorted(p for p in CONSOLE.rglob("*") if p.is_file())
HTML_FILES = [p for p in FILES if p.suffix == ".html"]
JS_FILES = [p for p in FILES if p.suffix == ".js"]
CSS_FILES = [p for p in FILES if p.suffix == ".css"]
CONTENT_TYPES = {".html": "text/html", ".css": "text/css", ".js": "javascript"}


def rel(path: Path) -> str:
    return path.relative_to(CONSOLE).as_posix()


def text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def registry_entries() -> list[dict[str, str]]:
    """The registry.js entries, read as plain text (its object literals are simple string maps)."""
    return [
        dict(re.findall(r'(\w+):\s*"([^"]*)"', body))
        for body in re.findall(r"\{([^{}]*)\}", REGISTRY_JS)
    ]


def test_the_console_has_the_documented_layout() -> None:
    expected = {
        "index.html",
        "shared/tokens.css",
        "shared/shell.css",
        "shared/components.js",
        "shared/registry.js",
        "shared/shell.js",
        "agents/compass/compass.html",
        "agents/compass/compass.js",
        "agents/compass/compass.css",
    }
    assert {rel(p) for p in FILES} == expected
    assert (CONSOLE.parent / "app" / ".gitkeep").is_file(), "apps/web/app is reserved for React"


async def test_every_console_file_is_served_without_auth_with_csp(anon: httpx.AsyncClient) -> None:
    served = ["/ui/", *(f"/ui/{rel(p)}" for p in FILES)]
    for path in served:
        r = await anon.get(path)
        assert r.status_code == 200, path
        ctype = CONTENT_TYPES[Path(path).suffix] if Path(path).suffix else "text/html"
        assert ctype in r.headers["content-type"], path
        csp = r.headers["content-security-policy"]
        assert "default-src 'self'" in csp and "script-src 'self'" in csp, path
        assert "style-src 'self'" in csp and "unsafe-inline" not in csp, path
        assert "unsafe-eval" not in csp, path
        assert r.headers["x-content-type-options"] == "nosniff", path
    assert "<title>Mobigen console</title>" in (await anon.get("/ui/")).text
    r = await anon.get("/", follow_redirects=False)
    assert r.status_code == 307 and r.headers["location"] == "/ui/"
    assert (await anon.get("/ui/nope.js")).status_code == 404


async def test_every_asset_referenced_by_the_shell_and_registry_exists_and_is_served(
    anon: httpx.AsyncClient,
) -> None:
    refs = re.findall(r"""(?:src|href)=["']([^"'#]+)["']""", INDEX)
    assets = [r for r in refs if not r.startswith(("#", "data:"))]
    entries = registry_entries()
    assert entries, "the registry lists at least one agent"
    for entry in entries:
        assets += [entry["markup"], entry["script"], entry["style"]]
    assert {"shared/tokens.css", "shared/shell.css"} <= set(assets)
    assert {"shared/registry.js", "shared/components.js", "shared/shell.js"} <= set(assets)
    assert {"agents/compass/compass.html", "agents/compass/compass.js"} <= set(assets)
    assert "agents/compass/compass.css" in assets
    for ref in assets:
        assert not re.match(r"^(https?:)?//", ref), f"external asset {ref}"
        assert (CONSOLE / ref).is_file(), ref
        assert (await anon.get(f"/ui/{ref}")).status_code == 200, ref
    assert {rel(p) for p in FILES} == set(assets) | {"index.html"}, "no unreferenced console file"


def test_scripts_load_in_dependency_order() -> None:
    scripts = re.findall(r"""<script\b[^>]*\bsrc=["']([^"']+)["'][^>]*\bdefer\b""", INDEX)
    assert scripts == ["shared/registry.js", "shared/components.js", "shared/shell.js"]


@pytest.mark.parametrize("path", HTML_FILES, ids=rel)
def test_no_inline_script_style_or_handlers_in_html(path: Path) -> None:
    html = text(path)
    scripts = re.findall(r"<script\b([^>]*)>", html, flags=re.I)
    assert all(re.search(r"\bsrc=", attrs) for attrs in scripts), "every <script> has src"
    for m in re.finditer(r"<script\b[^>]*>(.*?)</script>", html, flags=re.I | re.S):
        assert m.group(1).strip() == "", "no inline script body"
    assert not re.search(r"\son[a-z]+\s*=", html, flags=re.I), "no inline event handlers"
    assert not re.search(r"\sstyle\s*=", html, flags=re.I), "no inline style attributes (CSP)"
    assert "<style" not in html.lower()
    assert "javascript:" not in html.lower()
    if path.name != "index.html":
        assert "<script" not in html.lower(), "a screen fragment carries no script of its own"
        assert "<link" not in html.lower() and "<html" not in html.lower()
    else:
        assert scripts, "the shell loads its scripts with <script src defer>"


def test_no_external_resources() -> None:
    for path in CSS_FILES:
        css = text(path)
        assert "@import" not in css and not re.search(r"url\(\s*['\"]?https?:", css), rel(path)
        assert "@font-face" not in css, rel(path)
    for path in HTML_FILES:
        assert not re.search(r"""(?:src|href)\s*=\s*["']https?:""", text(path)), rel(path)
    for path in JS_FILES:
        assert not re.search(r"""fetch\(\s*["']https?:""", text(path)), rel(path)
    everything = "\n".join(text(p) for p in FILES).lower()
    assert "cdn" not in everything and "fonts.googleapis" not in everything
    assert "font-family: var(--font)" in "\n".join(text(p) for p in CSS_FILES)  # system fonts only


@pytest.mark.parametrize("path", JS_FILES, ids=rel)
def test_js_never_assigns_markup_or_evals(path: Path) -> None:
    js = text(path)
    for word in (
        "innerHTML",
        "outerHTML",
        "insertAdjacentHTML",
        "document.write",
        "createContextualFragment",
    ):
        assert word not in js, word
    assert not re.search(r"\beval\s*\(", js)
    assert "new Function" not in js
    assert not re.search(r"""setAttribute\(\s*["']style["']""", js), (
        "inline style is blocked by CSP"
    )
    assert not re.search(r"""setAttribute\(\s*["']on""", js), "no handler attributes"
    if path.name != "registry.js":  # the registry has a data key named style (a stylesheet path)
        assert not re.search(r"""\bstyle\s*:\s*["']""", js), "no style strings (CSP)"
    if path.name in {"compass.js", "components.js"}:
        assert "textContent" in js and "createTextNode" in js


def test_markup_is_parsed_only_by_the_shell() -> None:
    """DOMParser (an inert document, no scripts run) is how the shell mounts an agent's own fragment."""
    users = {rel(p) for p in JS_FILES if "DOMParser" in text(p)}
    assert users == {"shared/shell.js"}
    shell = text(CONSOLE / "shared" / "shell.js")
    assert "script|style|link|iframe|object|embed" in shell, "scripts and styles are dropped"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
@pytest.mark.parametrize("path", JS_FILES, ids=rel)
def test_every_script_parses_with_node(path: Path) -> None:
    out = subprocess.run(
        [shutil.which("node") or "node", "--check", str(path)],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert out.returncode == 0, out.stderr
