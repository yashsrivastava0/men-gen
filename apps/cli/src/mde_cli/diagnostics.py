"""``mde budget`` and ``mde doctor``: spend, and what is set up. Neither ever prints a value.

* ``budget`` is ``mde_llm_gateway.budget_cli`` (what ``just budget`` runs): the same numbers, the
  same text and ``--json``.
* ``doctor`` is the logic of ``scripts/doctor.py`` plus the API check: which settings are present
  (names and "present"/"absent" only), which variable NAMES the dotenv file defines (never its
  values), whether the database answers, whether an API answers, and, only in live mode with both
  switches and ``--live``, whether the model is available (a metadata call that costs nothing).
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import httpx

from mde_cli.console import Out
from mde_cli.errors import EXIT_OK, EXIT_PROBLEM

NAME = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=")


def run_budget(scope: str | None, as_json: bool, ledger: Path | None) -> int:
    from mde_llm_gateway import budget_cli

    argv: list[str] = []
    if scope:
        argv += ["--scope", scope]
    if as_json:
        argv.append("--json")
    if ledger is not None:
        argv += ["--ledger", str(ledger)]
    return budget_cli.main(argv)


def env_file_names(path: Path) -> dict[str, int]:
    """Variable NAMES defined in a dotenv file, with how often each is defined (never a value:
    only the part before ``=`` is ever kept)."""
    counts: dict[str, int] = {}
    try:
        with path.open(encoding="utf-8", errors="ignore") as handle:
            for line in handle:
                match = NAME.match(line)
                if match:
                    counts[match.group(1)] = counts.get(match.group(1), 0) + 1
    except OSError:
        return {}
    return counts


def _check_database(url: str) -> bool:
    import psycopg

    try:
        with psycopg.connect(
            url.replace("postgresql+psycopg://", "postgresql://"), connect_timeout=3
        ) as conn:
            conn.execute("select 1")
    except Exception:
        return False
    return True


def _check_api(url: str, out: Out) -> bool:
    try:
        with httpx.Client(base_url=url.rstrip("/"), timeout=3.0, trust_env=False) as http:
            ready = http.get("/readyz")
    except httpx.HTTPError as exc:
        out.line(f"api: NOT reachable at {url} ({type(exc).__name__}); start it with `just dev`")
        return False
    if ready.status_code == 200:
        out.line(f"api: ready at {url}")
        return True
    out.line(
        f"api: reachable at {url} but not ready (HTTP {ready.status_code}): is the database up?"
    )
    return False


def run_doctor(*, live: bool, url: str | None, out: Out) -> int:
    from mde_core.settings import describe_presence, load_settings

    settings = load_settings()
    ok = True
    out.line("settings:")
    for key, state in describe_presence(settings).items():
        out.line(f"  {key}: {state}")

    env_file = os.environ.get("MDE_ENV_FILE", ".env")
    if not env_file:
        out.line("dotenv file: not read (MDE_ENV_FILE is empty)")
    else:
        path = Path(env_file)
        names = env_file_names(path) if path.is_file() else {}
        out.line(
            f"dotenv file {path.name}: present={path.is_file()}; variable names: {sorted(names)}"
        )
        for name, count in names.items():
            if count > 1:
                out.line(f"  note: {name} is defined {count} times (the last one wins)")

    if _check_database(settings.database_url):
        out.line("database: reachable")
    else:
        ok = False
        out.line(
            "database: NOT reachable; run `uv run python scripts/dev_db.py up` (then `just migrate`)"
        )

    if url is not None and not _check_api(url, out):
        ok = False

    if not live:
        out.line("model: not checked (add --live; it also needs LLM_MODE=live)")
    elif not settings.live_enabled:
        out.line("model: skipped (needs LLM_MODE=live)")
    else:
        try:
            from mde_llm_gateway.doctor import probe_model

            out.line(f"model: {probe_model(settings)}")
        except Exception as exc:
            ok = False
            out.line(f"model: probe failed ({type(exc).__name__})")
    return EXIT_OK if ok else EXIT_PROBLEM
