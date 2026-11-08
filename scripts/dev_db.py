"""Local PostgreSQL for development and tests (brief 6.3, decision D-003).

Ladder: (0) already reachable on MDE_PG_PORT -> use it; (1) Docker, only if the daemon answers
(never unpause/restart Docker Desktop); (2) a PRIVATE cluster from local PostgreSQL binaries
(initdb into <main repo>/data/pg, port 54329, trust auth, 127.0.0.1 only); (3) ``pgserver``.
Never SQLite. Never port 5432 (it belongs to other projects on this machine).

Usage: python scripts/dev_db.py [up|down|reset|status|url]
"""

from __future__ import annotations

import contextlib
import glob
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

PORT = int(os.environ.get("MDE_PG_PORT", "54329"))
USER = "mde"
DB = "mde"
HOST = "127.0.0.1"
URL = f"postgresql+psycopg://{USER}:{USER}@{HOST}:{PORT}/{DB}"
IS_WIN = os.name == "nt"


def find_exe(name: str) -> str | None:
    """Absolute path of an executable found on PATH only (never the current directory).

    A bare ``git`` / ``docker`` is resolved by ``subprocess`` and ``shutil.which`` against the
    CURRENT DIRECTORY first on Windows, so a ``git.exe`` planted in a checkout would run. Empty and
    relative PATH entries (which mean "the current directory") are skipped.
    """
    names = (f"{name}.exe", f"{name}.cmd") if IS_WIN else (name,)
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        if not entry or not os.path.isabs(entry):
            continue
        for candidate_name in names:
            candidate = Path(entry) / candidate_name
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return str(candidate)
    return None


GIT = find_exe("git")  # found once; main_root() runs at import time
DOCKER = find_exe("docker")


def main_root() -> Path:
    """The main checkout (so every git worktree shares one cluster)."""
    here = Path(__file__).resolve().parent.parent
    if GIT is None:
        return here
    try:
        out = subprocess.run(
            [GIT, "rev-parse", "--git-common-dir"],
            cwd=here, capture_output=True, text=True, timeout=10, check=True,
        ).stdout.strip()  # fmt: skip
        common = (here / out).resolve()
        return common.parent if common.name == ".git" else here
    except Exception:
        return here


DATA_DIR = Path(os.environ.get("MDE_PG_DATA", str(main_root() / "data" / "pg")))


def is_up(timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((HOST, PORT), timeout=timeout):
            return True
    except OSError:
        return False


def wait_up(seconds: float = 40.0) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if is_up():
            return True
        time.sleep(0.5)
    return False


def find_bin() -> Path | None:
    cands: list[Path] = []
    if os.environ.get("MDE_PG_BIN"):
        cands.append(Path(os.environ["MDE_PG_BIN"]))
    exe = "pg_ctl.exe" if IS_WIN else "pg_ctl"
    on_path = find_exe("pg_ctl")
    if on_path:
        cands.append(Path(on_path).parent)
    patterns = (
        [r"C:\Program Files\PostgreSQL\*\bin"]
        if IS_WIN
        else ["/usr/lib/postgresql/*/bin", "/opt/homebrew/opt/postgresql@*/bin", "/usr/local/opt/postgresql@*/bin"]
    )  # fmt: skip
    versioned: list[tuple[int, Path]] = []
    for pat in patterns:
        for p in glob.glob(pat):
            digits = "".join(ch for ch in Path(p).parent.name if ch.isdigit()) or "0"
            if "@" in p:
                digits = p.split("@")[1].split("/")[0].split("\\")[0]
            versioned.append((int(digits) if digits.isdigit() else 0, Path(p)))
    cands.extend(p for _, p in sorted(versioned, reverse=True))
    for c in cands:
        if (c / exe).exists() and (c / ("initdb.exe" if IS_WIN else "initdb")).exists():
            return c
    return None


def _run(cmd: list[str], **kw: object) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True, **kw)  # type: ignore[call-overload]


def docker_usable() -> bool:
    if DOCKER is None:
        return False
    try:
        # `docker ps` needs a live (and unpaused) daemon; `docker info` can exit 0 when paused.
        r = _run([DOCKER, "ps", "-q"], timeout=15)
    except Exception:
        return False
    return r.returncode == 0


def up_docker() -> bool:
    if DOCKER is None:
        return False
    compose = Path(__file__).resolve().parent.parent / "docker-compose.yml"
    r = _run([DOCKER, "compose", "-f", str(compose), "up", "-d", "postgres"], timeout=180)
    if r.returncode != 0:
        print("docker compose failed:", r.stderr.strip()[:300])
        return False
    return wait_up()


def ensure_local_cluster(bin_dir: Path) -> bool:
    exe = ".exe" if IS_WIN else ""
    if not (DATA_DIR / "PG_VERSION").exists():
        DATA_DIR.parent.mkdir(parents=True, exist_ok=True)
        print(f"initdb -> {DATA_DIR}")
        r = _run([str(bin_dir / f"initdb{exe}"), "-D", str(DATA_DIR), "-U", USER, "-A", "trust",
                  "-E", "UTF8", "--locale=C"])  # fmt: skip
        if r.returncode != 0:
            print("initdb failed:", (r.stderr or r.stdout).strip()[:400])
            return False
    opts = (
        f"-p {PORT} -c listen_addresses={HOST} -c max_connections=80 -c shared_buffers=64MB "
        "-c fsync=off -c synchronous_commit=off -c full_page_writes=off -c max_wal_size=256MB"
    )
    log = DATA_DIR / "server.log"
    r = subprocess.run(
        [str(bin_dir / f"pg_ctl{exe}"), "-D", str(DATA_DIR), "-o", opts, "-l", str(log), "-w", "-t", "40", "start"],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )  # fmt: skip
    if r.returncode != 0 and not is_up():
        print(f"pg_ctl start failed; see {log}")
        return False
    return wait_up(10)


def ensure_database(bin_dir: Path | None) -> None:
    """Create database ``mde`` if missing (idempotent)."""
    try:
        import psycopg  # type: ignore[import-not-found]
    except ImportError:
        return
    try:
        with psycopg.connect(host=HOST, port=PORT, user=USER, dbname="postgres", autocommit=True,
                             connect_timeout=5) as conn:  # fmt: skip
            exists = conn.execute("select 1 from pg_database where datname=%s", (DB,)).fetchone()
            if not exists:
                conn.execute(f'create database "{DB}"')
                print(f"created database {DB}")
    except Exception as exc:
        print(f"could not ensure database {DB}: {type(exc).__name__}")


def cmd_up() -> int:
    if is_up():
        print(f"postgres already reachable on {HOST}:{PORT}")
        ensure_database(None)
        return 0
    if docker_usable():
        print("docker is usable: docker compose up -d postgres")
        if up_docker():
            ensure_database(None)
            print(f"ready: {URL}")
            return 0
    else:
        print("docker unavailable or paused (left untouched); using local binaries")
    bin_dir = find_bin()
    if bin_dir and ensure_local_cluster(bin_dir):
        ensure_database(bin_dir)
        print(f"ready (private local cluster): {URL}")
        return 0
    try:
        import pgserver  # type: ignore[import-not-found]

        srv = pgserver.get_server(DATA_DIR.parent / "pgserver", cleanup_mode=None)
        print("pgserver URI (set DATABASE_URL accordingly):", srv.get_uri())
        return 0
    except ImportError:
        print(
            "No PostgreSQL available: install Docker, PostgreSQL 16+, or `uv pip install pgserver`."
        )
        return 1


def cmd_down() -> int:
    bin_dir = find_bin()
    if bin_dir and (DATA_DIR / "PG_VERSION").exists():
        exe = ".exe" if IS_WIN else ""
        r = _run([str(bin_dir / f"pg_ctl{exe}"), "-D", str(DATA_DIR), "-m", "fast", "-w", "stop"])
        print(
            (r.stdout or r.stderr).strip().splitlines()[-1] if (r.stdout or r.stderr) else "stopped"
        )
    elif is_up():
        print("postgres is up but not managed by this script (docker?); leaving it alone")
    return 0


def cmd_reset() -> int:
    cmd_down()
    if DATA_DIR.exists():
        for _ in range(5):
            with contextlib.suppress(OSError):
                shutil.rmtree(DATA_DIR)
                break
            time.sleep(1)
    return cmd_up()


def main(argv: list[str]) -> int:
    action = argv[1] if len(argv) > 1 else "up"
    if action == "up":
        return cmd_up()
    if action == "down":
        return cmd_down()
    if action == "reset":
        return cmd_reset()
    if action == "status":
        print(f"{HOST}:{PORT} {'UP' if is_up() else 'DOWN'}; data dir {DATA_DIR}")
        return 0 if is_up() else 1
    if action == "url":
        print(URL)
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
