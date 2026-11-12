"""Start, stop and watch the Research app (detached, with a PID file and a log).

    uv run python scripts/research_app.py start     # detached; prints the address
    uv run python scripts/research_app.py status
    uv run python scripts/research_app.py logs [-n 80]
    uv run python scripts/research_app.py stop
    uv run python scripts/research_app.py restart
    uv run python scripts/research_app.py credits show     # search credits: what the credit ledger knows
    uv run python scripts/research_app.py credits set tavily --used 93 --limit 1000
    uv run python scripts/research_app.py credits set serper --remaining 2389

The app is ``python -m mde_research_agent`` on ``127.0.0.1:8120`` (API and front end, one origin). It always runs in
live mode; a run spends money only when the person clicks Run and confirms (``docs/runbooks/research-agent.md``).
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess  # hardening: allow - argv list, no shell, a fixed module name
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = Path(os.environ.get("RESEARCH_DATA_DIR") or ROOT / "agents" / "research" / ".runtime")
PID = DATA / "app.pid"
LOG = DATA / "logs" / "process.log"
APP_LOG = DATA / "logs" / "app.log"
PORT = int(os.environ.get("RESEARCH_PORT", "8120"))
URL = f"http://127.0.0.1:{PORT}/"


def _pid() -> int | None:
    try:
        return int(PID.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def _alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _health() -> dict[str, object] | None:
    try:
        with urllib.request.urlopen(f"{URL}api/health", timeout=2) as resp:  # noqa: S310 - fixed loopback URL
            return json.loads(resp.read().decode("utf-8"))  # type: ignore[no-any-return]
    except (urllib.error.URLError, OSError, ValueError):
        return None


def start() -> int:
    if _alive(_pid()):
        sys.stdout.write(f"already running (pid {_pid()}): {URL}\n")
        return 0
    LOG.parent.mkdir(parents=True, exist_ok=True)
    out = LOG.open("ab")
    kwargs: dict[str, object] = {}
    if os.name == "nt":
        kwargs["creationflags"] = (
            0x00000008 | 0x00000200
        )  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    proc = subprocess.Popen(
        [sys.executable, "-m", "mde_research_agent", "--port", str(PORT)],
        cwd=ROOT,
        stdout=out,
        stderr=out,
        stdin=subprocess.DEVNULL,
        **kwargs,  # type: ignore[arg-type]
    )
    PID.parent.mkdir(parents=True, exist_ok=True)
    PID.write_text(str(proc.pid), encoding="utf-8")
    for _ in range(60):
        if _health() is not None:
            sys.stdout.write(f"Research app running (pid {proc.pid}): {URL}\n")
            return 0
        if proc.poll() is not None:
            break
        time.sleep(0.5)
    sys.stderr.write(f"the app did not come up; see {LOG}\n")
    return 1


def stop() -> int:
    pid = _pid()
    if not _alive(pid):
        PID.unlink(missing_ok=True)
        sys.stdout.write("not running\n")
        return 0
    assert pid is not None
    os.kill(pid, signal.SIGTERM)
    for _ in range(60):
        if not _alive(pid):
            break
        time.sleep(0.25)
    else:
        sys.stderr.write("the app did not stop in 15 s; it is still running\n")
        return 1
    PID.unlink(missing_ok=True)
    sys.stdout.write("stopped\n")
    return 0


def status() -> int:
    pid = _pid()
    health = _health()
    if _alive(pid) and health is not None:
        ready = (
            "ready for live runs"
            if health.get("ready")
            else "running, NOT ready for live runs (open Preflight)"
        )
        sys.stdout.write(f"running (pid {pid}): {URL}  {ready}\n")
        return 0
    sys.stdout.write("not running\n")
    return 3


def logs(n: int) -> int:
    for path in (APP_LOG, LOG):
        if path.is_file():
            sys.stdout.write(f"--- {path} (last {n} lines)\n")
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[-n:]
            sys.stdout.write("\n".join(lines) + "\n")
    return 0


def _credits_ledger():  # type: ignore[no-untyped-def]
    """The app's own credit ledger (``<data folder>/provider-credits.json``): the file the running app writes."""
    from mde_research_agent.compose import credit_ledger
    from mde_research_agent.config import load_research_settings

    return credit_ledger(load_research_settings())


def _count(n: int | None) -> str:
    return "-" if n is None else f"{n:,}"


def credits_show() -> int:
    """Print what the credit ledger knows, one line per provider. Reads the file only: no network, no key."""
    from mde_research_agent.spend.credits import describe

    ledger = _credits_ledger()
    sys.stdout.write(f"Search credits (ledger file: {ledger.path})\n")
    sys.stdout.write(f"{'Provider':<9} {'Used':<16} {'Left':<9} {'Source':<26} As of\n")
    words = {"provider": "reported by the provider", "counted": "counted by this app"}
    for r in describe(ledger.snapshot()):
        known = r["used"] is not None or r["remaining"] is not None
        used = _count(r["used"]) + (f" of {_count(r['limit'])}" if r["limit"] else "")
        if r["used"] is None:
            used = "-"
        source = words.get(r["source"], "unverified endpoint") if known else "no count yet"
        when = (r["as_of"] or "-")[:16]
        sys.stdout.write(
            f"{r['label']:<9} {used:<16} {_count(r['remaining']):<9} {source:<26} {when}\n"
        )
        if r["level"] in ("amber", "red"):
            sys.stdout.write(f"          {r['note']}\n")
    sys.stdout.write(
        "Tavily is asked for its own count when the app starts, after each run and from the Budget page.\n"
    )
    return 0


def credits_set(provider: str, used: int | None, remaining: int | None, limit: int | None) -> int:
    """Write the owner's baseline (as of now, UTC). Exactly one of ``used`` and ``remaining``."""
    if (used is None) == (remaining is None):
        sys.stderr.write("give exactly one of --used and --remaining\n")
        return 2
    kind, amount = ("used", used) if used is not None else ("remaining", remaining)
    try:
        _credits_ledger().set_baseline(provider, kind, int(amount or 0), limit)
    except ValueError as exc:
        sys.stderr.write(f"not saved: {exc}\n")
        return 2
    tail = f" of {limit}" if limit is not None else ""
    sys.stdout.write(f"saved: {provider} {kind} {amount}{tail}\n")
    return credits_show()


def credits_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="research_app credits", description="The search-credit ledger (advisory only)."
    )
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("show", help="print what the ledger knows")
    one = sub.add_parser("set", help="enter what a provider account shows; counting restarts now")
    one.add_argument("provider", choices=["tavily", "serper"])
    one.add_argument("--used", type=int, help="credits already used this cycle (Tavily: 93)")
    one.add_argument("--remaining", type=int, help="credits left (Serper: 2389)")
    one.add_argument("--limit", type=int, help="the allowance (Tavily free plan: 1000)")
    args = parser.parse_args(argv)
    if args.action == "show":
        return credits_show()
    return credits_set(args.provider, args.used, args.remaining, args.limit)


def main(argv: list[str] | None = None) -> int:
    given = sys.argv[1:] if argv is None else argv
    if given and given[0] == "credits":
        return credits_main(given[1:])
    parser = argparse.ArgumentParser(prog="research_app", description=__doc__.splitlines()[0])  # type: ignore[union-attr]
    parser.add_argument("command", choices=["start", "stop", "status", "logs", "restart"])
    parser.add_argument("-n", type=int, default=80)
    args = parser.parse_args(given)
    if args.command == "start":
        return start()
    if args.command == "stop":
        return stop()
    if args.command == "status":
        return status()
    if args.command == "logs":
        return logs(args.n)
    code = stop()
    return code or start()


if __name__ == "__main__":
    sys.exit(main())
