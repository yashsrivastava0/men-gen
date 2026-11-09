"""UI smoke: static files are served, assets exist, no inline script, no markup injection, CSP holds."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import httpx
import pytest

from mde_compass.api.middleware import CSP
from mde_compass.api.settings import ApiSettings
from mde_compass.api.testing import ScriptedService
from mde_compass.app import STATIC_DIR, create_app

APP_JS = (STATIC_DIR / "agents/compass/compass.js").read_text(encoding="utf-8")
INDEX = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
CSS = (STATIC_DIR / "shared/tokens.css").read_text(encoding="utf-8") + (
    STATIC_DIR / "agents/compass/compass.css"
).read_text(encoding="utf-8")


@pytest.fixture
async def anon() -> httpx.AsyncClient:
    app = create_app(ScriptedService(), settings=ApiSettings())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        yield c


async def test_ui_served_without_auth_with_csp(anon: httpx.AsyncClient) -> None:
    for path, ctype in (
        ("/ui/", "text/html"),
        ("/ui/index.html", "text/html"),
        ("/ui/shared/tokens.css", "text/css"),
        ("/ui/agents/compass/compass.css", "text/css"),
        ("/ui/shared/components.js", "javascript"),
        ("/ui/agents/compass/compass.js", "javascript"),
    ):
        r = await anon.get(path)
        assert r.status_code == 200, path
        assert ctype in r.headers["content-type"], path
        assert r.headers["content-security-policy"] == CSP
        assert r.headers["x-content-type-options"] == "nosniff"
    assert "<title>Compass</title>" in (await anon.get("/ui/")).text
    r = await anon.get("/", follow_redirects=False)
    assert r.status_code == 307 and r.headers["location"] == "/ui/"
    assert (await anon.get("/ui/nope.js")).status_code == 404


async def test_every_asset_referenced_by_index_exists_and_is_served(
    anon: httpx.AsyncClient,
) -> None:
    refs = re.findall(r"""(?:src|href)=["']([^"'#]+)["']""", INDEX)
    assets = [r for r in refs if not r.startswith(("#", "data:"))]
    assert "agents/compass/compass.js" in assets and "agents/compass/compass.css" in assets
    for ref in assets:
        assert not re.match(r"^(https?:)?//", ref), f"external asset {ref}"
        assert (STATIC_DIR / ref).is_file(), ref
        assert (await anon.get(f"/ui/{ref}")).status_code == 200


def test_no_inline_script_style_or_handlers_in_html() -> None:
    scripts = re.findall(r"<script\b([^>]*)>", INDEX, flags=re.I)
    assert scripts, "the page has a script"
    assert all(re.search(r"\bsrc=", attrs) for attrs in scripts), "every <script> has src"
    for m in re.finditer(r"<script\b[^>]*>(.*?)</script>", INDEX, flags=re.I | re.S):
        assert m.group(1).strip() == "", "no inline script body"
    assert not re.search(r"\son[a-z]+\s*=", INDEX, flags=re.I), "no inline event handlers"
    assert not re.search(r"\sstyle\s*=", INDEX, flags=re.I), "no inline style attributes (CSP)"
    assert "<style" not in INDEX.lower()
    assert "javascript:" not in INDEX.lower()


def test_no_external_resources() -> None:
    assert "@import" not in CSS and not re.search(r"url\(\s*['\"]?https?:", CSS)
    assert not re.search(r"""fetch\(\s*["']https?:""", APP_JS)
    assert not re.search(r"""(?:src|href)\s*=\s*["']https?:""", INDEX)
    assert "cdn" not in INDEX.lower() and "fonts.googleapis" not in (INDEX + CSS).lower()
    assert "font-family: var(--font)" in CSS  # system font stack only
    assert "@font-face" not in CSS


def test_app_js_never_assigns_markup_or_evals() -> None:
    forbidden = (
        "innerHTML",
        "outerHTML",
        "insertAdjacentHTML",
        "document.write",
        "createContextualFragment",
    )
    for word in forbidden:
        assert word not in APP_JS, word
    assert not re.search(r"\beval\s*\(", APP_JS)
    assert "new Function" not in APP_JS
    assert not re.search(r"""setAttribute\(\s*["']style["']""", APP_JS), (
        "inline style is blocked by CSP"
    )
    assert not re.search(r"""\bstyle\s*:\s*["']""", APP_JS), (
        "no style strings (CSP blocks style attributes)"
    )
    assert "textContent" in APP_JS and "createTextNode" in APP_JS


def test_theme_and_responsive_rules_present() -> None:
    assert "prefers-color-scheme: dark" in CSS and '[data-theme="dark"]' in CSS
    assert "max-width: 900px" in CSS and ":focus-visible" in CSS
    assert "prefers-reduced-motion" in CSS


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_app_js_parses_with_node() -> None:
    out = subprocess.run(
        [
            shutil.which("node") or "node",
            "--check",
            str(Path(STATIC_DIR / "agents/compass/compass.js")),
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert out.returncode == 0, out.stderr
