"""Start, stop and watch the Spec Bench app (detached, with a PID file and a log).

    uv run python scripts/spec_app.py start     # detached; prints the address
    uv run python scripts/spec_app.py status
    uv run python scripts/spec_app.py logs [-n 80]
    uv run python scripts/spec_app.py stop
    uv run python scripts/spec_app.py restart

The app is ``python -m mde_spec_agent serve`` on ``127.0.0.1:8130`` (API and front end, one origin). A run spends money
only when the person starts it live; offline runs cost nothing. The money cap comes from the launch environment
(``SPEC_LIVE_BUDGET_USD``, ``SPEC_TRIPWIRE_USD``), never from ``.env``. ``status`` never prints a value, only setting names.
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
DATA = Path(os.environ.get("SPEC_DATA_DIR") or ROOT / "agents" / "spec" / ".runtime")
PID = DATA / "app.pid"
LOG = DATA / "logs" / "process.log"
APP_LOG = DATA / "logs" / "app.log"
PORT = int(os.environ.get("SPEC_PORT", "8130"))
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
        [sys.executable, "-m", "mde_spec_agent", "serve", "--port", str(PORT)],
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
            sys.stdout.write(f"Spec Bench running (pid {proc.pid}): {URL}\n")
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
    os.kill(pid, signal.SIGTERM)  # the app stops every active run gracefully (checkpoint, partial)
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
        if health.get("ready"):
            live = "ready for live runs"
        else:
            reasons = health.get("reasons")
            names = ", ".join(str(r) for r in reasons) if isinstance(reasons, list) else ""
            names = names or "unknown"
            live = f"running, offline runs only; live runs need: {names}"
        sys.stdout.write(f"running (pid {pid}): {URL}  {live}\n")
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="spec_app", description=__doc__.splitlines()[0])  # type: ignore[union-attr]
    parser.add_argument("command", choices=["start", "stop", "status", "logs", "restart"])
    parser.add_argument("-n", type=int, default=80)
    args = parser.parse_args(argv)
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
