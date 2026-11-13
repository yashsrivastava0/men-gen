"""Report which settings are present, whether the DB is reachable, and (live mode) the model.

Never prints a value: only 'present' / 'absent' / counts. Usage: python scripts/doctor.py [--live]
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NAME_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=", re.M)


def env_names() -> dict[str, int]:
    """Variable NAMES in .env with how many times each is defined (never the values)."""
    path = ROOT / ".env"
    if not path.exists():
        return {}
    counts: dict[str, int] = {}
    for name in NAME_RE.findall(path.read_text(encoding="utf-8", errors="ignore")):
        counts[name] = counts.get(name, 0) + 1
    return counts


def main(argv: list[str]) -> int:
    from mde_core.settings import describe_presence, load_settings

    settings = load_settings()
    ok = True
    print("settings:")
    for key, state in describe_presence(settings).items():
        print(f"  {key}: {state}")
    names = env_names()
    print(f".env present: {(ROOT / '.env').exists()}; variable names: {sorted(names)}")
    for name, n in names.items():
        if n > 1:
            print(f"  note: {name} is defined {n} times in .env (last one wins)")

    try:
        import psycopg  # type: ignore[import-not-found]

        url = settings.database_url.replace("postgresql+psycopg://", "postgresql://")
        with psycopg.connect(url, connect_timeout=3) as conn:
            conn.execute("select 1")
        print("database: reachable")
    except Exception as exc:
        ok = False
        print(f"database: NOT reachable ({type(exc).__name__}); run `just db-up`")

    if "--live" in argv:
        if not settings.live_enabled:
            print("model: skipped (needs LLM_MODE=live)")
        else:
            try:
                from mde_llm_gateway.doctor import probe_model  # type: ignore[import-not-found]

                print("model:", probe_model(settings))
            except Exception as exc:
                ok = False
                print(f"model: probe failed ({type(exc).__name__})")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
