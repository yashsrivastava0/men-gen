"""The console shell: the agent switcher and registry, the design tokens, the shared helpers and the
Compass fragment that the shell mounts."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import httpx
import pytest

CONSOLE = Path(__file__).resolve().parents[1] / "console"


def text(rel: str) -> str:
    return (CONSOLE / rel).read_text(encoding="utf-8")


INDEX = text("index.html")
REGISTRY_JS = text("shared/registry.js")
SHELL_JS = text("shared/shell.js")
COMPONENTS_JS = text("shared/components.js")
COMPASS_JS = text("agents/compass/compass.js")
COMPASS_HTML = text("agents/compass/compass.html")
TOKENS = text("shared/tokens.css")

ENTRY_KEYS = {"id", "title", "base", "markup", "helper", "script", "style"}


def registry_entries() -> list[dict[str, str]]:
    return [
        dict(re.findall(r'(\w+):\s*"([^"]*)"', body))
        for body in re.findall(r"\{([^{}]*)\}", REGISTRY_JS)
    ]


# ---- the agent switcher and the registry -----------------------------------------------------------


def test_the_shell_has_an_agent_switcher_and_the_mount_points() -> None:
    assert re.search(r'<select\s+id="agent-select"', INDEX)
    assert re.search(
        r'<header class="topbar"[^>]*>.*<select\s+id="agent-select"', INDEX, flags=re.S
    )
    label = re.search(r'aria-labelledby="(agent-label)"', INDEX)
    assert label and f'id="{label.group(1)}"' in INDEX, "the switcher has an accessible name"
    for mount in ('id="agent-topbar"', 'id="agent-mount"', 'id="toasts"'):
        assert mount in INDEX, mount
    assert "<option" not in INDEX, "the options come from the registry, never from the page"


def test_the_registry_lists_compass_and_only_compass_today() -> None:
    entries = registry_entries()
    assert [e["id"] for e in entries] == ["compass"]
    entry = entries[0]
    assert set(entry) == ENTRY_KEYS
    assert entry["title"] == "Compass" and entry["base"] == "/v1/compass"
    assert entry["script"] == "agents/compass/compass.js"
    assert entry["style"] == "agents/compass/compass.css"
    assert entry["markup"] == "agents/compass/compass.html"
    assert entry["helper"] == "agents/compass/interview.js"
    for e in entries:
        assert re.fullmatch(r"[a-z][a-z0-9_-]*", e["id"]), e["id"]
        for key in ("markup", "helper", "script", "style"):
            assert (CONSOLE / e[key]).is_file(), e[key]
            assert e[key].startswith(f"agents/{e['id']}/"), "an agent's files live in its folder"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_registry_evaluates_to_what_the_tests_read() -> None:
    prog = (
        "global.window = {}; require(process.argv[1]); "
        "process.stdout.write(JSON.stringify(window.MobigenRegistry));"
    )
    out = subprocess.run(
        [shutil.which("node") or "node", "-e", prog, str(CONSOLE / "shared" / "registry.js")],
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    assert json.loads(out.stdout) == registry_entries()


async def test_the_registered_api_base_is_the_one_the_agent_serves(
    anon: httpx.AsyncClient,
) -> None:
    for entry in registry_entries():
        r = await anon.get(f"{entry['base']}/strings/en")
        assert r.status_code == 200 and r.json()["language"] == "en", entry["id"]


def test_the_shell_loads_the_helper_before_the_screen_script() -> None:
    """The optional helper (pure functions the screen uses) is loaded first, then the screen's own script."""
    helper = SHELL_JS.index("loadScript(active.helper)")
    script = SHELL_JS.index("loadScript(active.script)")
    assert SHELL_JS.index("mountMarkup(done[1])") < helper < script
    assert "active.helper ?" in SHELL_JS, "an agent without a helper still loads"


def test_the_shell_knows_no_agent_by_name() -> None:
    """Agents come from the registry only: nothing in the shell or the shared helpers names one."""
    for name, body in (("shell.js", SHELL_JS), ("components.js", COMPONENTS_JS), ("index", INDEX)):
        assert "compass" not in body.lower(), name
    assert "MobigenRegistry" in SHELL_JS and "MobigenAgent" in SHELL_JS


def test_compass_reads_its_api_base_from_the_registry_entry() -> None:
    m = re.search(r'var BASE = AGENT\.base \|\| "([^"]+)";', COMPASS_JS)
    assert m, "BASE comes from window.MobigenAgent.base"
    assert m.group(1) == registry_entries()[0]["base"], "the fallback equals the registered base"
    assert "window.MobigenAgent" in COMPASS_JS


# ---- design tokens ---------------------------------------------------------------------------------

THEME_VARS = [
    "bg",
    "surface",
    "surface-2",
    "border",
    "text",
    "muted",
    "accent",
    "accent-text",
    "accent-soft",
    "ok",
    "ok-soft",
    "warn",
    "warn-soft",
    "danger",
    "danger-soft",
    "shadow",
]
LAYOUT_VARS = ["radius", "radius-sm", "s1", "s2", "s3", "s4", "font", "mono"]


def declarations(block: str) -> dict[str, str]:
    return {name: value.strip() for name, value in re.findall(r"--([\w-]+)\s*:\s*([^;]+);", block)}


def token_blocks() -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    light = re.search(r"^:root\s*\{(.*?)^\}", TOKENS, flags=re.S | re.M)
    media = re.search(
        r"@media \(prefers-color-scheme: dark\)\s*\{\s*:root:not\(\[data-theme=\"light\"\]\)\s*\{(.*?)\}\s*\}",
        TOKENS,
        flags=re.S,
    )
    attr = re.search(r':root\[data-theme="dark"\]\s*\{(.*?)\}', TOKENS, flags=re.S)
    assert light and media and attr, "light, dark-by-preference and dark-by-attribute blocks"
    return declarations(light.group(1)), declarations(media.group(1)), declarations(attr.group(1))


def test_tokens_define_the_theme_variables_for_light_and_dark() -> None:
    light, dark_media, dark_attr = token_blocks()
    for name in THEME_VARS:
        assert name in light, f"--{name} missing from the light theme"
        assert name in dark_media, f"--{name} missing from the dark theme (preference)"
        assert name in dark_attr, f"--{name} missing from the dark theme (data-theme)"
    assert dark_media == dark_attr, "both dark rules define the same palette"
    for name in ("bg", "surface", "text", "accent"):
        assert dark_attr[name] != light[name], f"--{name} differs between light and dark"
    for name in LAYOUT_VARS:
        assert name in light, f"--{name} missing"
    assert "prefers-color-scheme: dark" in TOKENS and ':root[data-theme="dark"]' in TOKENS
    assert ':root:not([data-theme="light"])' in TOKENS, "an explicit light choice beats the OS"


def test_tokens_are_only_defined_in_tokens_css_and_every_use_resolves() -> None:
    light, _, _ = token_blocks()
    defined = set(light)
    for rel in ("shared/shell.css", "agents/compass/compass.css"):
        css = text(rel)
        assert not re.search(r"(?<![\w-])--[\w-]+\s*:", css), f"{rel} defines a custom property"
        used = set(re.findall(r"var\(--([\w-]+)", css))
        assert used, rel
        assert used <= defined, f"{rel} uses undefined tokens: {sorted(used - defined)}"


def test_tokens_hold_no_selectors_other_than_the_theme_rules() -> None:
    css = re.sub(r"/\*.*?\*/", "", TOKENS, flags=re.S)
    css = re.sub(r"@media[^{]*\{", "", css)  # the dark-by-preference wrapper
    selectors = {s.strip() for s in re.findall(r"([^{}]+)\{", css)}
    assert selectors == {
        ":root",
        ':root:not([data-theme="light"])',
        ':root[data-theme="dark"]',
    }


def test_responsive_focus_and_motion_rules_stay_with_the_screens() -> None:
    css = text("agents/compass/compass.css")
    assert "max-width: 900px" in css and ":focus-visible" in css
    assert "prefers-reduced-motion" in css


# ---- shared helpers --------------------------------------------------------------------------------


def exported_helpers() -> set[str]:
    m = re.search(r"root\.MobigenUI\s*=\s*\{(.*?)\};", COMPONENTS_JS, flags=re.S)
    assert m, "components.js exposes window.MobigenUI"
    return set(re.findall(r"(\w+|\$):", m.group(1)))


def test_the_shared_helpers_keep_their_api() -> None:
    assert exported_helpers() == {
        "store",
        "$",
        "h",
        "appendChild",
        "clear",
        "randomId",
        "toast",
    }
    # h() understands the same attribute keys it always did
    for key in ('"class"', '"text"', '"on"', '"data"', '"css"'):
        assert f"k === {key}" in COMPONENTS_JS, key
    assert "function h(tag, attrs)" in COMPONENTS_JS
    assert "function clear(el) { el.replaceChildren(); }" in COMPONENTS_JS


def test_compass_uses_only_helpers_that_the_shared_module_exports() -> None:
    used = set(re.findall(r"\bUI\.([\w$]+)", COMPASS_JS))
    assert used and used <= exported_helpers(), sorted(used - exported_helpers())
    # no second copy of a shared helper is left behind
    for name in ("h", "clear", "randomId", "toast", "appendChild"):
        assert not re.search(rf"^\s*function {re.escape(name)}\(", COMPASS_JS, flags=re.M), name
    assert not re.search(r"^\s*var store = \{", COMPASS_JS, flags=re.M)


# ---- the Compass fragment --------------------------------------------------------------------------


def test_the_fragment_marks_what_goes_into_the_shared_top_bar() -> None:
    slotted = re.findall(r'<(?:button|div) id="([\w-]+)"[^>]*data-slot="topbar"', COMPASS_HTML)
    assert slotted == ["brand", "topbar-session", "meter"]
    assert 'class="topbar-actions" data-slot="topbar"' in COMPASS_HTML
    tags = re.findall(r'<[a-z]+\b[^>]*\bdata-slot="topbar"', COMPASS_HTML)
    assert len(tags) == 4, "brand, session label, spend meter and the actions group"
    assert '<main id="main"' in COMPASS_HTML, "the skip link in the shell points at #main"
    assert 'href="#main"' in INDEX


def test_every_element_compass_looks_up_by_id_exists_exactly_once() -> None:
    markup_ids = re.findall(r'\bid="([\w-]+)"', INDEX + COMPASS_HTML)
    assert len(markup_ids) == len(set(markup_ids)), "ids are unique across the shell and fragment"
    looked_up = set(re.findall(r'\$\("([\w-]+)"\)', COMPASS_JS))
    built = set(re.findall(r'\bid:\s*"([\w-]+)"', COMPASS_JS))  # elements compass.js creates itself
    assert len(looked_up) > 40
    missing = looked_up - set(markup_ids) - built
    assert not missing, f"compass.js looks up ids that no markup defines: {sorted(missing)}"
    assert "toasts" in markup_ids and "toasts" in INDEX, "toast() needs #toasts in the shell"
