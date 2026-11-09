"""Live batch A (smoke): needs ALLOW_LIVE_LLM=1 in the OUTER environment (the root conftest skips it
otherwise) and OPENAI_API_KEY in .env. Allowance USD 0.10; the Compass budget guard enforces it.

Run (one line, from the repo root):  ALLOW_LIVE_LLM=1 uv run pytest agents/compass/tests/live -m live -q -s
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mde_compass.compose import build_service
from mde_compass.live_smoke import run_smoke, smoke_config

pytestmark = pytest.mark.live
ALLOWANCE_USD = "0.10"


async def test_live_smoke(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COMPASS_LIVE_BUDGET_USD", ALLOWANCE_USD)
    service = build_service(memory=True, compass_config=smoke_config())
    assert service.budget_snapshot()["mode"] == "live"
    report = await run_smoke(service)
    out = Path(__file__).parents[2] / "evals" / "reports"
    out.mkdir(parents=True, exist_ok=True)
    (out / "live-smoke.json").write_text(report.model_dump_json(indent=2), encoding="utf-8")
    print(report.model_dump_json(indent=2))
    assert not report.errors
    assert float(report.spent_usd) <= float(ALLOWANCE_USD)
    assert report.final_state in {"BRIEF_REVIEW", "INTERVIEWING", "PROBING"}
