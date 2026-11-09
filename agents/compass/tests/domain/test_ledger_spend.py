"""Compass's live spend is recomputed from the gateway ledger, so the cap survives across processes."""

from __future__ import annotations

import json
from pathlib import Path

from mde_compass.budget import CompassBudget
from mde_compass.ledger_spend import compass_spent_micros


def _line(event: str, rid: str, **kw: object) -> str:
    base: dict[str, object] = {
        "event": event,
        "ts": "2026-11-09T10:00:00Z",
        "reservation_id": rid,
        "scopes": ["total"],
    }
    base.update(kw)
    return json.dumps(base)


def test_only_committed_cmp_calls_are_counted(tmp_path: Path) -> None:
    ledger = tmp_path / "ledger.jsonl"
    ledger.write_text(
        "\n".join(
            [
                _line("reserve", "r1", spec_id="cmp.plan_questions", worst_case_micros=9000),
                _line("commit", "r1", cost_micros=5000),
                _line(
                    "reserve", "r2", spec_id="C1", worst_case_micros=9000
                ),  # another agent's call
                _line("commit", "r2", cost_micros=7000),
                _line("reserve", "r3", spec_id="cmp.understand_turn", worst_case_micros=3000),
                _line("commit", "r3", cost_micros=1250),
                _line(
                    "reserve", "r4", spec_id="cmp.audit_questions", worst_case_micros=4000
                ),  # never committed
                "this is not json",
                "",
                _line(
                    "commit", "r5", cost_micros=999
                ),  # a commit with no known spec is not Compass's
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    assert compass_spent_micros(ledger) == 6250


def test_a_missing_ledger_is_zero_and_nothing_is_created(tmp_path: Path) -> None:
    path = tmp_path / "nope" / "ledger.jsonl"
    assert compass_spent_micros(path) == 0 and not path.exists() and not path.parent.exists()


def test_the_budget_adopts_the_ledger_total_and_never_goes_down(tmp_path: Path) -> None:
    ledger = tmp_path / "ledger.jsonl"
    ledger.write_text(
        _line("reserve", "r1", spec_id="cmp.compile_brief", worst_case_micros=9000)
        + "\n"
        + _line("commit", "r1", cost_micros=2_000_000)
        + "\n",
        encoding="utf-8",
    )
    budget = CompassBudget(mode="live")
    budget.reconcile(compass_spent_micros(ledger))
    assert budget.spent_micros == 2_000_000
    budget.reconcile(500)  # a smaller number never lowers it
    assert budget.spent_micros == 2_000_000
