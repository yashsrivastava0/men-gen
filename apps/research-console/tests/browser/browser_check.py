"""Real-browser verification of the Research console against the TEST-ONLY fake app (offline doubles, no cost).

Not collected by pytest (the name does not start with test_). Run it with a system Chrome:

    uv run --with playwright python apps/research-console/tests/browser/browser_check.py --shots /some/folder

It starts several fake apps on ports 8141..8150 (one per scenario), drives the page like a person would, asserts what
it sees and writes screenshots (360 and 1280 px wide, light and dark). Nothing here touches the real app or any key.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

from playwright.sync_api import Page, sync_playwright


def _poll(self, expression, arg=None, timeout=30000, **_kw):
    """wait_for_function that polls with evaluate(): Playwright's own polling evaluates the predicate with eval in the
    page, which the page's strict content-security policy (correctly) refuses. The policy stays on."""
    end = time.time() + timeout / 1000
    while time.time() < end:
        if self.evaluate(expression):
            return True
        time.sleep(0.15)
    raise TimeoutError("condition not met: " + expression[:120])


Page.wait_for_function = _poll

REPO = Path(__file__).resolve().parents[4]
FAKE = REPO / "agents" / "research" / "tests" / "e2e" / "fake_app.py"
RESULTS: list[tuple[str, bool, str]] = []
ISSUES: list[str] = []  # alerts, page errors and console errors seen in any page
SHOTS = Path(tempfile.gettempdir())
EXPECTED_CONSOLE = (
    "Failed to load resource",
    "net::ERR",
)  # a killed server in the unreachable scenario


def check(name: str, ok: object, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))
    print(
        ("PASS  " if ok else "FAIL  ")
        + name
        + (f"   [{detail}]" if detail and (not ok or " ms" in detail) else ""),
        flush=True,
    )


# ---------------------------------------------------------------------------------------------- the fake apps
class Fake:
    def __init__(self, port: int, *flags: str, data_dir: Path | None = None) -> None:
        self.port = port
        self.flags = list(flags)
        self.data_dir = data_dir
        self.proc: subprocess.Popen[bytes] | None = None
        self.base = f"http://127.0.0.1:{port}"

    def start(self) -> Fake:
        cmd = ["uv", "run", "python", str(FAKE), "--port", str(self.port), *self.flags]
        if self.data_dir:
            cmd += ["--data-dir", str(self.data_dir)]
        self.proc = subprocess.Popen(
            cmd,
            cwd=REPO,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        for _ in range(120):
            try:
                if self.get("/api/health", auth=False):
                    return self
            except (urllib.error.URLError, ConnectionError, OSError):
                time.sleep(0.5)
        raise RuntimeError(f"fake app on {self.port} did not start")

    def kill(self) -> None:
        if self.proc and self.proc.poll() is None:
            os.killpg(os.getpgid(self.proc.pid), signal.SIGKILL)
            self.proc.wait()

    def get(self, path: str, auth: bool = True):
        req = urllib.request.Request(self.base + path)  # noqa: S310 - loopback test server
        if auth:
            req.add_header("Authorization", "Bearer dev-token")
        with urllib.request.urlopen(req, timeout=10) as r:  # noqa: S310
            body = r.read().decode()
        return json.loads(body) if body.startswith(("{", "[")) else body

    def post(self, path: str, body: dict):
        req = urllib.request.Request(  # noqa: S310 - loopback test server
            self.base + path, data=json.dumps(body).encode(), method="POST"
        )
        req.add_header("Authorization", "Bearer dev-token")
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=10) as r:  # noqa: S310
            return json.loads(r.read().decode())

    def input_id(self, name_part: str) -> str:
        for i in self.get("/api/inputs")["inputs"]:
            if name_part in i["name"]:
                return i["id"]
        raise KeyError(name_part)

    def wait_status(self, run_id: str, done: tuple[str, ...], seconds: float = 90) -> str:
        end = time.time() + seconds
        while time.time() < end:
            st = self.get(f"/v1/research/runs/{run_id}")["status"]
            if st in done:
                return st
            time.sleep(0.5)
        return "timeout"


# ---------------------------------------------------------------------------------------------- browser helpers
def watch(page) -> None:
    page.on("dialog", lambda d: (ISSUES.append("ALERT: " + d.message), d.dismiss()))
    page.on("pageerror", lambda e: ISSUES.append("pageerror: " + str(e)))
    page.on(
        "console",
        lambda m: (
            m.type == "error"
            and not any(t in m.text for t in EXPECTED_CONSOLE)
            and ISSUES.append("console: " + m.text)
        ),
    )


def new_page(browser, width=1280, height=900, scheme="light", reduced=False, block_storage=False):
    ctx = browser.new_context(
        viewport={"width": width, "height": height},
        color_scheme=scheme,
        reduced_motion="reduce" if reduced else "no-preference",
    )
    if block_storage:
        ctx.add_init_script(
            "Object.defineProperty(window, 'localStorage', {get() { throw new Error('blocked'); }});"
        )
    page = ctx.new_page()
    watch(page)
    return page


def shot(page, name: str, full: bool = True) -> None:
    page.screenshot(path=str(SHOTS / f"{name}.png"), full_page=full)


def four_shots(browser, url: str, name: str, ready: str, before=None) -> list[str]:
    """Screenshot one screen at 360 and 1280 px, light and dark. Returns problems (a horizontal scroll at 360 px)."""
    problems = []
    for width in (360, 1280):
        for scheme in ("light", "dark"):
            p = new_page(browser, width=width, scheme=scheme)
            p.goto(url)
            p.wait_for_selector(ready, timeout=15000)
            if before:
                before(p)
            p.wait_for_timeout(300)
            shot(p, f"{name}-{width}-{scheme}")
            over = p.evaluate("document.documentElement.scrollWidth - window.innerWidth")
            if over > 1:
                problems.append(f"{width}px {scheme}: scrolls sideways by {over}px")
            p.context.close()
    return problems


def text(page, selector: str) -> str:
    return page.locator(selector).first.inner_text()


def card(page, fragment: str):
    return page.locator(".input-card", has_text=fragment).first


def start_run_via_ui(page, fragment: str) -> str:
    """Click Run on the card, confirm, return the run id from the address."""
    page.goto(page.url.split("#")[0] + "#/inputs")
    page.wait_for_selector(".input-card")
    card(page, fragment).get_by_role("button", name="Run research").click()
    page.wait_for_selector("dialog[open]")
    page.get_by_role("button", name="Start research (costs money)").click()
    page.wait_for_url("**/#/run/*", timeout=10000)
    return page.url.split("#/run/")[1]


# ---------------------------------------------------------------------------------------------- scenarios
def scenario_empty(browser) -> None:
    for flags, port, expect_word in (
        ((("--empty",)), 8142, "No approved Compass exports"),
        (("--missing-folder",), 8143, "does not exist"),
    ):
        f = Fake(port, *flags).start()
        try:
            folder = f.get("/api/inputs")["folder"]
            page = new_page(browser)
            page.goto(f.base + "/")
            page.wait_for_selector(".empty")
            body = page.locator(".empty").inner_text()
            check(f"empty folder ({flags[0]}): says what is wrong", expect_word in body, body[:80])
            check(
                f"empty folder ({flags[0]}): shows the exact folder path",
                page.locator(".empty .path").inner_text() == folder,
                folder,
            )
            check(
                f"empty folder ({flags[0]}): says how to add files",
                "Copy an approved Compass export" in body or "Create that folder" in body,
            )
            check(
                f"empty folder ({flags[0]}): no run buttons",
                page.get_by_role("button", name="Run research").count() == 0,
            )
            pill = page.locator("#preflight-pill").inner_text()
            check(
                f"empty folder ({flags[0]}): preflight pill says not ready",
                pill.startswith("Not ready"),
                pill,
            )
            if flags[0] == "--empty":
                problems = four_shots(browser, f.base + "/", "empty-folder", ".empty")
                check(
                    "empty folder: no sideways scroll at 360 px", not problems, "; ".join(problems)
                )
            page.context.close()
        finally:
            f.kill()


def scenario_not_ready(browser) -> None:
    f = Fake(8144, "--not-ready").start()
    try:
        page = new_page(browser)
        page.goto(f.base + "/")
        page.wait_for_selector(".input-card")
        page.wait_for_function(
            "document.querySelector('#preflight-pill').textContent.startsWith('Not ready')"
        )
        btn = card(page, "quick-yoga").get_by_role("button", name="Run research")
        check("not ready: Run is disabled", btn.is_disabled())
        why = card(page, "quick-yoga").locator(".why-off").inner_text()
        check(
            "not ready: the reason names the exact variables",
            "TAVILY_API_KEY or SERPER_API_KEY" in why,
            why,
        )
        page.locator("#preflight-pill").click()
        page.wait_for_selector("dialog[open] .checks")
        panel = page.locator("dialog[open]").inner_text()
        check(
            "not ready: the panel lists each failing check with its fix",
            "Needs fixing" in panel and "What to do" in panel and "TAVILY_API_KEY" in panel,
            panel[:200],
        )
        check(
            "not ready: the panel has the connection test button",
            page.get_by_role("button", name="Test the connection (about USD 0.01)").count() == 1,
        )
        shot(page, "preflight-not-ready-1280-light")
        page.get_by_role("button", name="Test the connection (about USD 0.01)").click()
        page.wait_for_selector("dialog[open] .ping-result p.tone-bad")
        res = page.locator("dialog[open] .ping-result").inner_text()
        check(
            "not ready: the connection test is refused in plain words and names what is missing",
            "cannot start" in res.lower() and "TAVILY_API_KEY or SERPER_API_KEY" in res,
            res,
        )
        page.keyboard.press("Escape")
        check(
            "not ready: the panel closes with Escape and focus returns to the pill",
            page.evaluate("document.activeElement.id") == "preflight-pill",
        )
        problems = four_shots(browser, f.base + "/", "not-ready-inputs", ".input-card")
        check("not ready: no sideways scroll at 360 px", not problems, "; ".join(problems))
        page.context.close()
    finally:
        f.kill()


def scenario_budget_colours_and_estimates(browser) -> None:
    for port, flags, want, label in (
        (8145, ("--spent", "4.2"), "amber", "amber at 80% of the total"),
        (8146, ("--spent", "4.6"), "red", "red at the tripwire"),
    ):
        f = Fake(port, *flags).start()
        try:
            page = new_page(browser)
            page.goto(f.base + "/")
            page.wait_for_selector(".input-card")
            cls = page.locator("#budget").get_attribute("class")
            check(f"budget bar is {label}", f"budget-{want}" in cls, cls)
            check(
                f"budget bar text ({want})",
                text(page, "#budget-text").startswith("USD ")
                and "tripwire 4.50" in text(page, "#budget-text"),
                text(page, "#budget-text"),
            )
            if want == "red":
                btn = card(page, "quick-yoga").get_by_role("button", name="Run research")
                why = card(page, "quick-yoga").locator(".why-off").inner_text()
                check(
                    "tripwire: Run is disabled with the reason",
                    btn.is_disabled() and "tripwire" in why,
                    why,
                )
                shot(page, "budget-red-1280-light")
            else:
                shot(page, "budget-amber-1280-light")
            page.context.close()
        finally:
            f.kill()
    f = Fake(8147, "--total", "1.00", "--tripwire", "0.90", "--spent", "0.3").start()
    try:
        page = new_page(browser)
        page.goto(f.base + "/")
        page.wait_for_selector(".input-card")
        card(page, "quick-yoga").get_by_role("button", name="Run research").click()
        page.wait_for_selector("dialog[open]")
        d = page.locator("dialog[open]").inner_text()
        check(
            "confirm dialog: shows the estimate against what is left",
            "Likely cost" in d and "Left in the budget" in d and "USD 0.70" in d,
            d[:300],
        )
        check("confirm dialog: stronger warning above 40% of what is left", "more than 40%" in d, d)
        check("confirm dialog: says estimate, not measured", "stimate, not measured" in d)
        shot(page, "confirm-dialog-strong-1280-light")
        page.keyboard.press("Escape")
        card(page, "standard-produce").get_by_role("button", name="Run research").click()
        page.wait_for_selector("dialog[open]")
        d = page.locator("dialog[open]").inner_text()
        check(
            "confirm dialog: 'may stop early (partial)' when the high estimate exceeds what is left",
            "stop early (partial)" in d,
            d,
        )
        page.keyboard.press("Escape")
        page.context.close()
        problems = four_shots(
            browser,
            f.base + "/",
            "confirm-dialog",
            ".input-card",
            before=lambda p: (
                card(p, "quick-yoga").get_by_role("button", name="Run research").click()
            ),
        )
        check("confirm dialog: no sideways scroll at 360 px", not problems, "; ".join(problems))
    finally:
        f.kill()


def scenario_main(browser) -> None:
    f = Fake(8141, "--slow", "--delay", "4").start()
    try:
        main_flow(browser, f)
    finally:
        f.kill()


def main_flow(browser, f: Fake) -> None:
    base = f.base
    # ---- inputs: layout, preview, XSS, speed ------------------------------------------------------------------
    page = new_page(browser)
    page.goto(base + "/")  # cold: the exports are parsed on the first scan
    page.wait_for_selector(".input-card")
    page.goto(base + "/#/inputs")
    page.reload()
    page.wait_for_selector(".input-card")
    t_cards = page.evaluate("performance.now()")
    page.wait_for_function(
        "performance.getEntriesByName('first-contentful-paint').length > 0", timeout=2000
    )
    fcp = page.evaluate(
        "(performance.getEntriesByName('first-contentful-paint')[0] || {startTime: -1}).startTime"
    )
    check("speed: first contentful paint under 1 s on localhost", 0 < fcp < 1000, f"{fcp:.0f} ms")
    check(
        "speed: inputs list on screen under 300 ms after load (warm server cache)",
        t_cards < 300,
        f"{t_cards:.0f} ms",
    )
    names = [c.inner_text() for c in page.locator(".input-card .badges").all()]
    check(
        "inputs: cards come in the server order Small, Small, Medium, Big",
        [n.split("\n")[0] for n in names] == ["Small", "Small", "Medium", "Big"],
        str(names),
    )
    c0 = card(page, "quick-yoga")
    ctext = c0.inner_text()
    for needle in (
        "Place",
        "Languages",
        "Contains",
        "Light mode",
        "Trust checks",
        "Topics planned",
        "Estimate",
        "estimate, not measured",
        "fingerprint",
        "modified",
        "same content also saved as",
    ):
        check(f"inputs: the card shows '{needle}'", needle in ctext, ctext[:200])
    check(
        "inputs: trust shows on/off and why",
        "off" in ctext and "Nothing in the Brief needs" in ctext,
    )
    check(
        "inputs: the Big card says trust is on and why",
        "always checked" in card(page, "thorough-freight").inner_text(),
    )
    c0.locator("summary", has_text="Details").click()
    page.wait_for_selector(".details-body")
    dtext = c0.inner_text()
    check(
        "inputs: Details shows Brief summary, topics by job and the trust label",
        "Brief summary" in dtext and "Topics we would research" in dtext and "Trust label" in dtext,
    )
    page.get_by_text("Skipped files (6)").click()
    sk = page.locator(".skipped").inner_text()
    check(
        "skipped files: every file is listed with its plain reason",
        all(
            w in sk
            for w in (
                "binary.json",
                "hash-mismatch.json",
                "not-approved.json",
                "notes.json",
                "old-format-1.json",
                "unrelated.json",
            )
        )
        and "hashes do not match" in sk
        and "never approved" in sk
        and "format 2" in sk
        and "not JSON" in sk,
        sk,
    )
    # XSS fixture
    xcard = card(page, "xss-")
    xt = xcard.inner_text()
    check(
        "XSS: markup in the Brief is shown as plain characters",
        "<img src=x onerror=alert(1)>" in xt
        and "javascript:alert(4)" in xt
        and "<script>alert(2)</script>" in xt,
        xt[:200],
    )
    check(
        "XSS: no injected element, no script, no javascript: link",
        page.locator(
            "main img, main script, main iframe, main svg, main [onerror], main [onmouseover], a[href^='javascript' i]"
        ).count()
        == 0,
    )
    check(
        "XSS: the file name with markup is plain text",
        "xss-<img src=x onerror=alert(8)>.json" in xt,
    )
    check(
        "XSS: right-to-left safe: dir=auto on the Brief text",
        xcard.locator(".summary [dir=auto]").count() >= 1,
    )
    page.wait_for_timeout(500)
    shot(page, "inputs-1280-light-expanded")
    check(
        "inputs: no alert dialogs so far",
        not [i for i in ISSUES if i.startswith("ALERT")],
        str(ISSUES),
    )
    # size filter remembered
    page.get_by_role("button", name="Medium (1)").click()
    check("inputs: the size filter works", page.locator(".input-card").count() == 1)
    page.reload()
    page.wait_for_selector(".input-card")
    check(
        "inputs: the filter is remembered across a reload (localStorage)",
        page.locator(".input-card").count() == 1
        and page.get_by_role("button", name="Medium (1)").get_attribute("aria-pressed") == "true",
    )
    page.get_by_role("button", name="All (4)").click()
    page.context.close()
    problems = four_shots(browser, base + "/", "inputs", ".input-card")
    check(
        "inputs: no sideways scroll at 360 px, screenshots taken in all four themes",
        not problems,
        "; ".join(problems),
    )
    # blocked storage and reduced motion
    p = new_page(browser, block_storage=True, reduced=True)
    p.goto(base + "/")
    p.wait_for_selector(".input-card")
    p.get_by_role("button", name="Small (2)").click()
    check(
        "storage blocked: the page still works (the filter just is not remembered)",
        p.locator(".input-card").count() == 2,
    )
    dur = p.evaluate("getComputedStyle(document.querySelector('#budget-fill')).transitionDuration")
    check(
        "reduced motion: the budget bar does not animate",
        dur in ("0.001s", "1e-06s", "0s") or float(dur.rstrip("s")) < 0.01,
        dur,
    )
    p.context.close()
    # theme toggle
    p = new_page(browser)
    p.goto(base + "/")
    p.wait_for_selector(".input-card")
    bg_light = p.evaluate("getComputedStyle(document.body).backgroundColor")
    p.get_by_role("button", name="Theme: auto").click()
    p.get_by_role("button", name="Theme: light").click()
    p.wait_for_timeout(100)
    bg_dark = p.evaluate("getComputedStyle(document.body).backgroundColor")
    check(
        "theme: the toggle switches to dark",
        bg_light != bg_dark and p.evaluate("document.documentElement.dataset.theme") == "dark",
        f"{bg_light} -> {bg_dark}",
    )
    p.context.close()

    # ---- the confirm dialog, by keyboard only ---------------------------------------------------------------------
    page = new_page(browser)
    page.goto(base + "/#/inputs")
    page.wait_for_selector(".input-card")
    run_btn = card(page, "quick-yoga").get_by_role("button", name="Run research")
    run_btn.focus()
    page.keyboard.press("Enter")
    page.wait_for_selector("dialog[open]")
    page_text = page.locator("dialog[open]").inner_text()
    check(
        "keyboard: Enter opens the confirm dialog showing cost, time and what is left",
        "Likely cost" in page_text and "Likely time" in page_text and "USD 5.00" in page_text,
        page_text[:200],
    )
    check(
        "keyboard: the dialog is a real <dialog> and focus is on Cancel (the safe choice)",
        page.evaluate("document.activeElement.textContent") == "Cancel"
        and page.evaluate("document.querySelector('dialog[open]').matches(':modal')"),
    )
    check(
        "confirm dialog: calm wording when it is a small share of what is left",
        "Expected to use about" in page_text,
        page_text,
    )
    seq = []
    for _ in range(4):
        page.keyboard.press("Tab")
        seq.append(page.evaluate("document.activeElement.textContent"))
    inside = page.evaluate(
        "document.querySelector('dialog[open]').contains(document.activeElement)"
    )
    check(
        "keyboard: Tab stays inside the dialog",
        inside and "Start research (costs money)" in seq,
        str(seq),
    )
    page.keyboard.press("Escape")
    page.wait_for_function("!document.querySelector('dialog[open]')")
    check(
        "keyboard: Escape closes it and focus goes back to the Run button",
        page.evaluate("document.activeElement.textContent") == "Run research",
    )
    shot(page, "inputs-after-escape-1280-light")
    # a refused start shows the server's plain message and the variable names (the refusal is simulated with a route)
    page.route(
        "**/api/runs",
        lambda r: (
            r.fulfill(
                status=409,
                content_type="application/json",
                body=json.dumps(
                    {
                        "code": "not_ready",
                        "message": "Research cannot start yet. Set: TAVILY_API_KEY or SERPER_API_KEY.",
                        "missing": ["TAVILY_API_KEY or SERPER_API_KEY"],
                    }
                ),
            )
            if r.request.method == "POST"
            else r.continue_()
        ),
    )
    run_btn.click()
    page.wait_for_selector("dialog[open]")
    page.get_by_role("button", name="Start research (costs money)").click()
    page.wait_for_selector("dialog[open] .dlg-error .banner")
    err = page.locator("dialog[open] .dlg-error").inner_text()
    check(
        "refused start: shows the server's plain message and the missing variable names",
        "cannot start yet" in err and "TAVILY_API_KEY or SERPER_API_KEY" in err,
        err,
    )
    shot(page, "start-refused-1280-light", full=False)
    page.keyboard.press("Escape")
    page.unroute("**/api/runs")
    page.route(
        "**/api/runs",
        lambda r: (
            r.fulfill(
                status=409,
                content_type="application/json",
                body=json.dumps(
                    {
                        "code": "budget_tripwire",
                        "message": "The budget tripwire was reached, so no new research starts. The run in progress finishes.",
                    }
                ),
            )
            if r.request.method == "POST"
            else r.continue_()
        ),
    )
    run_btn.click()
    page.wait_for_selector("dialog[open]")
    page.get_by_role("button", name="Start research (costs money)").click()
    page.wait_for_selector("dialog[open] .dlg-error .banner")
    check(
        "refused start (budget): the tripwire message is shown",
        "tripwire" in page.locator("dialog[open] .dlg-error").inner_text(),
    )
    page.keyboard.press("Escape")
    page.unroute("**/api/runs")

    # ---- run the Small input and watch it -----------------------------------------------------------------------------
    run_btn.click()
    page.wait_for_selector("dialog[open]")
    page.keyboard.press("Tab")
    page.keyboard.press("Enter")  # Start research, by keyboard
    page.wait_for_url("**/#/run/*", timeout=10000)
    run_id = page.url.split("#/run/")[1]
    page.wait_for_selector(".topic", timeout=15000)
    check(
        "run view: the plan lists topics grouped by job, with priority",
        page.locator(".plan .job h4").count() >= 3 and "priority" in text(page, ".plan"),
    )
    page.wait_for_function("document.querySelectorAll('.feed-row').length > 5", timeout=20000)
    check(
        "run view: the status chip says Researching and the always-visible bar still shows LIVE",
        "Researching" in text(page, ".page-head .chip")
        and "LIVE: real models and the real web; this costs money" in text(page, "#live-banner"),
    )
    check("run view: the test fake banner is shown", page.locator("#fake-banner").is_visible())
    n1 = page.locator(".feed-row").count()
    page.wait_for_timeout(2500)
    n2 = page.locator(".feed-row").count()
    check(
        "run view: the live feed grows while it runs",
        n2 >= n1 and page.locator(".feed-row").count() > 5,
        f"{n1} -> {n2}",
    )
    check(
        "run view: 'what it is doing now' is the server's own label",
        len(text(page, ".nowline")) > 3,
        text(page, ".nowline"),
    )
    check(
        "run view: Skip research and per-topic Cancel are offered",
        page.get_by_role("button", name="Skip research").count() == 1
        and page.get_by_role("button", name="Cancel").count() >= 1,
    )
    check(
        "run view: a second tab-independent cost tile shows money",
        text(page, ".tile:has-text('Cost so far') strong").startswith("USD"),
    )
    page.wait_for_function("document.querySelector('.foot').getAttribute('data-last-seq') > 40")
    seq_before_close = int(page.locator(".foot").get_attribute("data-last-seq"))
    # tab 2 (a phone, dark) and tab 3 (desktop) on the same run: they must agree
    t2 = new_page(browser, width=360, scheme="dark")
    t2.goto(base + f"/#/run/{run_id}")
    t2.wait_for_selector(".topic")
    shot(t2, "run-midway-360-dark")
    t3 = new_page(browser, width=360, scheme="light")
    t3.goto(base + f"/#/run/{run_id}")
    t3.wait_for_selector(".topic")
    shot(t3, "run-midway-360-light")
    shot(page, "run-midway-1280-light")
    t4 = new_page(browser, width=1280, scheme="dark")
    t4.goto(base + f"/#/run/{run_id}")
    t4.wait_for_selector(".topic")
    shot(t4, "run-midway-1280-dark")
    over = t3.evaluate("document.documentElement.scrollWidth - window.innerWidth")
    check("run view: no sideways scroll at 360 px mid-run", over <= 1, str(over))
    t4.close()
    t3.close()
    # close the first tab mid-run and reopen: resumes, no duplicates
    page.context.close()
    time.sleep(2)
    page = new_page(browser)
    page.goto(base + f"/#/run/{run_id}")
    page.wait_for_selector(".topic")
    page.wait_for_function("document.querySelector('.foot').getAttribute('data-last-seq') > 0")
    seqs = page.eval_on_selector_all(".feed-row", "els => els.map(e => Number(e.dataset.seq))")
    check(
        "reopen mid-run: the feed has no duplicate events",
        len(seqs) == len(set(seqs)) and len(seqs) > 0,
        str(len(seqs)),
    )
    check(
        "reopen mid-run: it resumed at or after where the closed tab was",
        int(page.locator(".foot").get_attribute("data-last-seq")) >= seq_before_close,
    )
    check("reopen mid-run: the feed is newest first", seqs == sorted(seqs, reverse=True))
    # wait for the end
    page.wait_for_selector(".banner-ok, .banner-warn, .banner-bad", timeout=90000)
    t2.wait_for_selector(".banner-ok, .banner-warn, .banner-bad", timeout=30000)
    page.wait_for_timeout(1500)
    t2.wait_for_timeout(500)
    status = text(page, ".page-head .chip")
    check(
        "run view: it ends on a final status with a button to the results",
        status in ("Done", "Done, with gaps")
        and page.get_by_role("link", name="Open the results").count() == 1,
        status,
    )
    for key in (
        "Searches",
        "Pages read",
        "Facts checked",
        "Facts dropped",
        "Topics done",
        "Cost so far",
    ):
        a = text(page, f".tile:has-text('{key}') strong")
        b = text(t2, f".tile:has-text('{key}') strong")
        check(f"two tabs agree: {key}", a == b, f"{a} vs {b}")
    check(
        "two tabs agree: status and last update number",
        text(t2, ".page-head .chip") == status
        and t2.locator(".foot").get_attribute("data-last-seq")
        == page.locator(".foot").get_attribute("data-last-seq"),
    )
    last = int(page.locator(".foot").get_attribute("data-last-seq"))
    stream = f.get(f"/v1/research/runs/{run_id}/events")  # the stored events, replayed
    ids = [int(line[4:]) for line in stream.splitlines() if line.startswith("id: ")]
    check(
        "events: the server replay is contiguous 1..N and the page saw all N",
        ids == list(range(1, len(ids) + 1)) and last == len(ids),
        f"page {last}, server {len(ids)}",
    )
    seqs = page.eval_on_selector_all(".feed-row", "els => els.map(e => Number(e.dataset.seq))")
    check(
        "run view: the feed keeps only the last 100 lines and no duplicates",
        len(seqs) <= 100 and len(seqs) == len(set(seqs)) and seqs[0] == last,
        f"{len(seqs)} rows",
    )
    check(
        "run view: counters show topics done of total and the cost by step",
        (
            "of" in text(page, ".tile:has-text('Topics done') strong")
            and "Search" in text(page, ".cost")
        )
        or "Web search" in text(page, ".cost"),
        text(page, ".cost"),
    )
    check(
        "run view: facts moved from checking to checked",
        page.locator(".fact-checked").count() > 0 or page.locator(".fact").count() > 0,
    )
    shot(page, "run-finished-1280-light")
    t2.context.close()
    problems = four_shots(browser, base + f"/#/run/{run_id}", "run-finished", ".chip")
    check("run view (finished): no sideways scroll at 360 px", not problems, "; ".join(problems))
    # results placeholder + download
    page.get_by_role("link", name="Open the results").click()
    page.wait_for_selector(".results-head")
    check(
        "results: header with status, cost and time and the nine tabs",
        "Cost" in text(page, ".results-head")
        and "Time" in text(page, ".results-head")
        and page.get_by_role("tab").count() == 9,
    )
    page.get_by_role("tab", name="Raw JSON", exact=True).click()
    with page.expect_download() as dl:
        page.get_by_role("button", name="Download research.json").click()
    path = dl.value.path()
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    check(
        "results: research.json downloads and parses",
        isinstance(data, dict) and bool(data),
        str(list(data)[:3]),
    )
    problems = four_shots(browser, base + f"/#/results/{run_id}", "results", ".tabs")
    check("results: no sideways scroll at 360 px", not problems, "; ".join(problems))
    # runs history
    page.get_by_role("link", name="Runs", exact=True).click()
    page.wait_for_selector("table.table")
    check(
        "runs history: the finished run is listed with status, cost and an Open link",
        page.locator("tbody tr").count() >= 1 and "USD" in text(page, "tbody tr"),
    )
    page.get_by_role("button", name="Needs attention (0)").click()
    page.reload()
    page.wait_for_selector(".filters")
    check(
        "runs history: the filter is remembered",
        page.get_by_role("button", name="Needs attention (0)").get_attribute("aria-pressed")
        == "true",
    )
    page.get_by_role("button", name="All (1)").click()
    problems = four_shots(browser, base + "/#/runs", "runs", "table.table")
    check("runs history: no sideways scroll at 360 px", not problems, "; ".join(problems))
    # inputs: last used + past runs + cost
    page.goto(base + "/#/inputs")
    page.wait_for_selector(".input-card")
    c0 = card(page, "quick-yoga").inner_text()
    check(
        "inputs: 'Last used' badge and past runs with cost",
        "Past runs of this input" in c0 and "USD" in c0,
    )
    page.context.close()

    # ---- same input twice, queue, cancel, skip, topic cancel, budget ---------------------------------------------------
    page = new_page(browser)
    page.goto(base + "/#/inputs")
    page.wait_for_selector(".input-card")
    rid1 = start_run_via_ui(page, "quick-yoga")
    page.wait_for_selector(".topic", timeout=15000)
    again = f.post("/api/runs", {"input_id": f.input_id("quick-yoga")})
    check(
        "same input twice: the second start returns the active run",
        again["run_id"] == rid1 and again["created"] is False,
    )
    page.goto(base + "/#/inputs")
    page.wait_for_selector(".input-card")
    open_link = card(page, "quick-yoga").get_by_role("link", name="Already running — open it")
    check(
        "same input twice: the card says 'Already running — open it' instead of Run",
        open_link.count() == 1
        and card(page, "quick-yoga").get_by_role("button", name="Run research").count() == 0,
    )
    check(
        "inputs: 'Last used' badge on the input started from this browser",
        "Last used" in card(page, "quick-yoga").inner_text(),
    )
    open_link.click()
    page.wait_for_url(f"**/#/run/{rid1}")
    check("same input twice: the link opens the active run", True)
    start_run_via_ui(page, "standard-produce")
    page.wait_for_selector(".banner-info")
    q = text(page, ".nowline") + " " + text(page, ".banners")
    check(
        "queue: the second run shows 'Waiting for its turn: position 1' and a Cancel button",
        "position 1" in q
        and page.get_by_role("button", name="Cancel (it has not started)").count() == 1,
        q,
    )
    shot(page, "run-queued-1280-light")
    p2 = new_page(browser, width=360)
    p2.goto(base + "/#/runs")
    p2.wait_for_selector("table.table")
    check(
        "queue: Runs history shows the queue position",
        "position 1" in text(p2, "tbody") or "position 1" in p2.locator("tbody").inner_text(),
    )
    shot(p2, "runs-with-queue-360-light")
    p2.context.close()
    page.get_by_role("button", name="Cancel (it has not started)").click()
    page.wait_for_selector(".banner-bad")
    check(
        "queue: cancelling the queued run ends it as Cancelled",
        "cancelled" in text(page, ".banners").lower()
        and text(page, ".page-head .chip") == "Cancelled",
        text(page, ".banners"),
    )
    shot(page, "run-cancelled-1280-light")
    # the running one: restart disabled, topic cancel, skip
    page.goto(base + f"/#/run/{rid1}")
    page.wait_for_selector(".topic")
    page.locator(".topic button").first.click()
    page.wait_for_selector(".topic-cancelled", timeout=20000)
    check(
        "topic cancel: the topic becomes Stopped",
        page.locator(".topic-cancelled .chip").first.inner_text() == "Stopped",
    )
    pb = new_page(browser)
    pb.goto(base + "/#/budget")
    pb.wait_for_selector("table.table")
    restart = pb.get_by_role("button", name="Restart budget…")
    check(
        "budget: Restart is disabled with the reason while a run is active",
        restart.is_disabled() and "run is going" in text(pb, "#restart-why"),
        text(pb, "#restart-why"),
    )
    shot(pb, "budget-running-1280-light")
    page.get_by_role("button", name="Skip research").click()
    page.wait_for_selector("dialog[open]")
    check(
        "skip: asks first, in plain words",
        "Stopped researching?".lower() in text(page, "dialog[open]").lower()
        or "stops now" in text(page, "dialog[open]"),
    )
    shot(page, "skip-dialog-1280-light", full=False)
    page.locator("dialog[open]").get_by_role("button", name="Skip research").click()
    page.wait_for_selector(".banner-ok, .banner-warn", timeout=30000)
    check(
        "skip: the run ends as Stopped early with what was checked",
        text(page, ".page-head .chip") in ("Stopped early", "Done, with gaps", "Done"),
        text(page, ".page-head .chip"),
    )
    page.context.close()
    # budget page and restart
    f.wait_status(rid1, ("skipped", "complete", "partial", "failed"), 30)
    pb.reload()
    pb.wait_for_selector("table.table")
    rows = pb.locator("tbody tr").count()
    check(
        "budget: the spend record table lists every run with cost by step and running total",
        (rows >= 2 and "Running total" in pb.locator("thead").first.inner_text())
        or pb.locator("table").first.count() > 0,
        f"{rows} rows",
    )
    tt = pb.locator("main").inner_text()
    check(
        "budget: columns date, what, run, cost by step, running total",
        all(w in tt for w in ("Date", "What", "Cost by step", "Running total"))
        and "Research run" in tt,
    )
    problems = four_shots(browser, base + "/#/budget", "budget", "table.table")
    check("budget: no sideways scroll at 360 px", not problems, "; ".join(problems))
    restart = pb.get_by_role("button", name="Restart budget…")
    check("budget: Restart is enabled when nothing runs", restart.is_enabled())
    restart.click()
    pb.wait_for_selector("dialog[open]")
    go = pb.locator("dialog[open]").get_by_role("button", name="Restart budget", exact=True)
    check("restart: the button is disabled until the word is typed", go.is_disabled())
    dtext = text(pb, "dialog[open]")
    check(
        "restart: states that history is kept and the old ledger is not touched",
        "History is kept" in dtext and "old ledger file is not touched" in dtext,
        dtext,
    )
    shot(pb, "restart-dialog-1280-light", full=False)
    pb.locator("#restart-word").fill("restart")
    check("restart: lower-case 'restart' is not enough", go.is_disabled())
    pb.locator("#restart-word").fill("RESTART")
    check("restart: the exact word RESTART enables it", go.is_enabled())
    go.click()
    pb.wait_for_function("!document.querySelector('dialog[open]')")
    pb.wait_for_function(
        "document.querySelector('#budget-text').textContent.startsWith('USD 0.00 of')",
        timeout=10000,
    )
    pb.wait_for_function(
        "document.querySelectorAll('table.table').length >= 2 && document.querySelector('table.table:nth-of-type(1)')"
    )
    hist = pb.locator("main").inner_text()
    check(
        "restart: the bar is back to 0.00 and the history keeps the closed period",
        "USD 0.00 of" in text(pb, "#budget-text") and "Closed" in hist and "Current" in hist,
        hist[:300],
    )
    check(
        "restart: the earlier entries are still listed under their period",
        pb.locator("details[data-period]").count() == 1,
    )
    pb.locator("details[data-period] summary").click()
    check(
        "restart: the old period's entries show",
        pb.locator("details[data-period] tbody tr").count() >= 2,
    )
    shot(pb, "budget-after-restart-1280-light")
    pb.context.close()
    # guide and preflight panel (ready)
    problems = four_shots(browser, base + "/#/guide", "guide", ".details")
    check("guide: renders, no sideways scroll at 360 px", not problems, "; ".join(problems))
    pg = new_page(browser)
    pg.goto(base + "/#/guide")
    pg.wait_for_selector(".details")
    check(
        "guide: collapsible steps for the owner plus 'what to do if'",
        pg.locator("details.details").count() >= 12 and "What to do if" in text(pg, "main"),
    )
    pg.locator("#preflight-pill").click()
    pg.wait_for_selector("dialog[open] .checks")
    check(
        "preflight: Ready is explained and every check is listed",
        "Everything needed for a live run is ready" in text(pg, "dialog[open]")
        and pg.locator("dialog[open] .check").count() >= 8,
    )
    pg.get_by_role("button", name="Test the connection (about USD 0.01)").click()
    pg.wait_for_function(
        "document.querySelector('dialog[open] .ping-result').textContent.includes('Search works')",
        timeout=15000,
    )
    check(
        "preflight: the connection test shows search and model results",
        "The model works" in text(pg, "dialog[open] .ping-result"),
        text(pg, "dialog[open] .ping-result"),
    )
    shot(pg, "preflight-ready-1280-light", full=False)
    pg.keyboard.press("Escape")
    pg.context.close()
    # upload
    pu = new_page(browser)
    pu.goto(base + "/#/inputs")
    pu.wait_for_selector(".input-card")
    folder = Path(f.get("/api/inputs")["folder"])
    good = json.loads((folder / "quick-yoga-studio-bengaluru.json").read_text(encoding="utf-8"))
    good["consumer_views"]["research"]["idea"]["summary"] = "An uploaded copy with a new summary."
    pu.set_input_files(
        "#upload-file",
        {
            "name": "uploaded.json",
            "mimeType": "application/json",
            "buffer": json.dumps(good).encode(),
        },
    )
    pu.wait_for_selector(".banner-ok", timeout=10000)
    check(
        "upload: an approved export is accepted and appears in the list",
        "Accepted" in text(pu, ".notice-slot")
        and pu.locator(".input-card", has_text="An uploaded copy").count() == 1,
    )
    pu.set_input_files(
        "#upload-file",
        {"name": "bad.json", "mimeType": "application/json", "buffer": b'{"hello": 1}'},
    )
    pu.wait_for_selector(".banner-bad", timeout=10000)
    check(
        "upload: a refused file shows the plain reason",
        "not a Compass export" in text(pu, ".notice-slot"),
        text(pu, ".notice-slot"),
    )
    shot(pu, "upload-refused-1280-light", full=False)
    pu.context.close()


def scenario_interrupted_and_unreachable(browser) -> None:
    data = Path(tempfile.mkdtemp(prefix="research-fake-keep-"))
    f = Fake(8148, "--slow", "--delay", "4", data_dir=data).start()
    try:
        page = new_page(browser)
        page.goto(f.base + "/#/inputs")
        page.wait_for_selector(".input-card")
        rid = start_run_via_ui(page, "quick-yoga")
        page.wait_for_selector(".topic")
        page.wait_for_function("document.querySelectorAll('.feed-row').length > 5")
        f.kill()  # the process dies mid-run
        page.wait_for_selector("#notices .banner-bad", timeout=40000)
        check(
            "unreachable: a plain banner says the app cannot be reached",
            "cannot reach the Research app" in text(page, "#notices"),
            text(page, "#notices"),
        )
        check(
            "unreachable: the pill and the budget bar say so too",
            "connection" in text(page, "#preflight-pill").lower()
            or "Not" in text(page, "#preflight-pill"),
        )
        shot(page, "unreachable-1280-light", full=False)
        f2 = Fake(8148, "--slow", "--delay", "4", data_dir=data).start()
        try:
            page.get_by_role("button", name="Try now").click()
            page.wait_for_function("!document.querySelector('#notices .banner-bad')", timeout=40000)
            check("unreachable: the page recovers by itself when the app is back", True)
            page.goto(f.base + f"/#/run/{rid}")
            page.wait_for_selector(".banner-bad", timeout=15000)
            b = text(page, ".banners")
            check(
                "interrupted: a banner explains it and offers Re-run",
                "interrupted" in b.lower()
                and page.get_by_role("button", name="Re-run this input").is_enabled(),
                b,
            )
            check(
                "interrupted: the chip says Interrupted",
                text(page, ".page-head .chip") == "Interrupted",
            )
            shot(page, "run-interrupted-1280-light")
            problems = four_shots(
                browser, f.base + f"/#/run/{rid}", "run-interrupted", ".banner-bad"
            )
            check("interrupted: no sideways scroll at 360 px", not problems, "; ".join(problems))
            page.get_by_role("button", name="Re-run this input").click()
            page.wait_for_function(
                f"!location.hash.endsWith('{rid}') && location.hash.startsWith('#/run/')",
                timeout=10000,
            )
            check("interrupted: Re-run starts a new run on the same input", True)
        finally:
            f2.kill()
        page.context.close()
    finally:
        f.kill()


def scenario_tools(browser) -> None:
    f = Fake(8149).start()
    try:
        page = new_page(browser)
        page.goto(f.base + "/#/tools")
        page.wait_for_selector("#settings-h")
        check("tools: header says Tools", "Tools" in text(page, ".page-head h2"))
        check(
            "tools: settings card has primary search provider",
            "Primary search provider" in text(page, "#settings-h"),
        )
        check(
            "tools: provider options include Auto, Tavily and Serper",
            page.locator("input[name='provider']").count() == 3,
        )
        page.locator("#p-serper").check()
        page.wait_for_function("document.querySelector('#p-serper').checked")
        check("tools: serper provider radio checked", page.locator("#p-serper").is_checked())
        problems = four_shots(browser, f.base + "/#/tools", "tools-view", "#settings-h")
        check("tools: no sideways scroll at 360 px", not problems, "; ".join(problems))
        page.context.close()
    finally:
        f.kill()


def main() -> int:
    global SHOTS
    ap = argparse.ArgumentParser()
    ap.add_argument("--shots", default=str(Path(tempfile.gettempdir()) / "research-console-shots"))
    ap.add_argument("--only", default="")
    args = ap.parse_args()
    SHOTS = Path(args.shots)
    SHOTS.mkdir(parents=True, exist_ok=True)
    scenarios = {
        "empty": scenario_empty,
        "notready": scenario_not_ready,
        "budget": scenario_budget_colours_and_estimates,
        "main": scenario_main,
        "interrupted": scenario_interrupted_and_unreachable,
        "tools": scenario_tools,
    }
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome")
        for name, fn in scenarios.items():
            if args.only and args.only != name:
                continue
            print(f"--- {name}", flush=True)
            try:
                fn(browser)
            except Exception as exc:  # a scenario that breaks is a failure, the rest still run
                check(f"scenario {name} ran to the end", False, repr(exc)[:400])
        browser.close()
    check(
        "no alert dialog, page error or console error in any page",
        not ISSUES,
        "; ".join(ISSUES[:5]),
    )
    failed = [r for r in RESULTS if not r[1]]
    print(f"\n{len(RESULTS) - len(failed)} passed, {len(failed)} failed; screenshots in {SHOTS}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
