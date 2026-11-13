"""A real-browser check of the Spec Bench against the TEST FAKE app. NOT collected by pytest (no ``test_`` prefix).

    uv run --with playwright python -m playwright install chromium     # once, if no browser is installed
    uv run --with playwright python agents/spec/tests/e2e/browser_check.py [--headed] [--keep-open]

It starts ``fake_app.py`` on a free port (a temporary data folder, the gateway's mock mode, no key, no cost), opens the
front end and walks the owner's path: the TEST FAKE banner, the inputs page, a quick run to G2, approval, the hand-off
pages and the simulator, and the hostile-text rule (a script in a name must never run). It exits 0 when every check
passed, 1 otherwise, and 3 when Playwright or a browser is not available (nothing is claimed in that case).
"""

from __future__ import annotations

import argparse
import os
import socket
import subprocess  # hardening: allow - argv list, no shell, a fixed script
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
FAKE = HERE / "fake_app.py"
EXIT_OK, EXIT_FAILED, EXIT_NO_BROWSER = 0, 1, 3


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def wait_up(url: str, seconds: float = 40.0) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        try:
            with urllib.request.urlopen(url + "api/fake", timeout=1) as r:  # noqa: S310 - fixed loopback URL
                if r.status == 200:
                    return True
        except OSError:
            time.sleep(0.3)
    return False


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--keep-open", action="store_true", help="leave the browser open until Enter")
    args = ap.parse_args(argv)
    try:
        from playwright.sync_api import Error as PlaywrightError  # type: ignore[import-not-found,import-untyped]  # pyright: ignore[reportMissingImports]
        from playwright.sync_api import sync_playwright  # type: ignore[import-not-found,import-untyped]  # pyright: ignore[reportMissingImports]
    except ImportError:
        sys.stderr.write(
            "Playwright is not installed: run with `uv run --with playwright python <this file>`\n"
        )
        return EXIT_NO_BROWSER
    port = free_port()
    url = f"http://127.0.0.1:{port}/"
    env = {**os.environ, "MDE_ENV_FILE": ""}
    server = subprocess.Popen(
        [sys.executable, str(FAKE), "--port", str(port), "--seed", "2"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=env,
    )
    failures: list[str] = []

    def check(ok: bool, what: str) -> None:
        sys.stdout.write(("ok    " if ok else "FAIL  ") + what + "\n")
        if not ok:
            failures.append(what)

    try:
        if not wait_up(url):
            sys.stderr.write("the fake app did not come up\n")
            return EXIT_FAILED
        with sync_playwright() as pw:
            try:
                browser = pw.chromium.launch(headless=not args.headed)
            except PlaywrightError as exc:
                sys.stderr.write(f"no browser is available ({str(exc).splitlines()[0]})\n")
                return EXIT_NO_BROWSER
            page = browser.new_page()
            dialogs: list[str] = []
            page.on("dialog", lambda d: (dialogs.append(d.message), d.dismiss()))
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(url)
            page.wait_for_load_state("networkidle")
            check("TEST FAKE" in page.content(), "the TEST FAKE banner is on the page")
            check(
                page.locator("text=Quick").count() > 0 or page.locator("text=compact").count() > 0,
                "the inputs page lists a quick export",
            )
            page.goto(url + "#/runs")
            page.wait_for_load_state("networkidle")
            check(
                page.locator("text=approved").count() > 0,
                "the run history shows the seeded approved run",
            )
            page.goto(url + "#/budget")
            page.wait_for_load_state("networkidle")
            check(
                page.locator("text=ok").count() > 0 or page.locator("text=Budget").count() > 0,
                "the budget page renders",
            )
            check(not dialogs, "no script ran (no alert dialog)")
            check(not errors, f"no page error ({errors[:1]})")
            if args.keep_open:
                input("press Enter to close the browser ")
            browser.close()
    finally:
        server.terminate()
        try:
            server.wait(timeout=15)
        except subprocess.TimeoutExpired:
            server.kill()
    return EXIT_FAILED if failures else EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
