import json
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

from mde_spec_agent.config import (
    PERIOD_SCOPE_KIND,
    RUN_SCOPE_KIND,
    guard_cap_usd,
    load_spec_settings,
    spec_budget_config,
)

ROOT = Path(__file__).resolve().parents[4]


def test_defaults_are_the_owners_cap(monkeypatch):
    monkeypatch.delenv("SPEC_LIVE_BUDGET_USD", raising=False)
    s = load_spec_settings()
    assert s.cap_micros == 5_000_000 and s.tripwire_micros == 4_500_000


def test_guard_cap_keeps_the_whole_total_usable():
    assert guard_cap_usd(Decimal("6.00"), Decimal("5.00")) == Decimal("6.50")


def test_budget_config_has_spec_scopes_at_the_total():
    cfg = spec_budget_config("6.50", "5.00")
    assert cfg.scope_caps_micros[RUN_SCOPE_KIND] == 5_000_000
    assert PERIOD_SCOPE_KIND in cfg.scope_kinds


def test_spend_check_script_exits_nonzero_above_cap(tmp_path):
    (tmp_path / "spend-record.json").write_text(
        json.dumps({"spent_micros": 5_000_001, "reservations": {}})
    )
    r = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/spec_spend_check.py"),
            "--data",
            str(tmp_path),
            "--ledgers",
            str(tmp_path / "none"),
        ],
        capture_output=True,
        text=True,
    )
    assert r.returncode == 2
    (tmp_path / "spend-record.json").write_text(
        json.dumps({"spent_micros": 4_000_000, "reservations": {}})
    )
    r = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/spec_spend_check.py"),
            "--data",
            str(tmp_path),
            "--ledgers",
            str(tmp_path / "none"),
        ],
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0
