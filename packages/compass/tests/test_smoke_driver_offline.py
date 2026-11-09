"""The live smoke driver, run offline in mock mode: keeps the batch A code path working without spending."""

from __future__ import annotations

from mde_compass.compose import build_service
from mde_compass.live_smoke import run_smoke


async def test_smoke_driver_exercises_every_call_type_in_mock_mode() -> None:
    report = await run_smoke(build_service(memory=True))
    assert report.mode == "mock" and not report.errors
    assert report.spent_usd == "0.0000"
    expected = {
        "cmp.understand_turn", "cmp.situate", "cmp.write_card", "cmp.seed_research",
        "cmp.probe_handoff", "cmp.compile_text", "cmp.read_chunk", "cmp.merge_claims",
        "cmp.scan_ambiguity", "cmp.answer", "cmp.interpret_change",
    }  # fmt: skip
    assert expected <= set(report.call_types_seen), set(report.call_types_seen)
