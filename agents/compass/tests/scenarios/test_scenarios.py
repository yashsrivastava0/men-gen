"""Named edge-case scenarios (``SB-<ID>``): YAML scripts run against the real Service in mock mode.

A scenario marked ``known_issue: BUG-n`` is a strict xfail: the product behaviour is wrong today, the script says
what should happen, and it fails the build when the bug is fixed (remove the mark then).
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from mde_compass.scenarios import Scenario, load_scenario, run_scenario
from mde_compass.scenarios.runner import ENGINES, Engine

DATA = Path(__file__).parent / "data"

# Scripts that assert the legacy frame queue's own behaviour. Every one carries `engines_reason` in its YAML; the
# lead ports or deletes them when the legacy path is removed (ADR 0015 point 7).
LEGACY_ONLY = {"SB-DC-03", "SB-DC-05", "SB-DC-12", "SB-IV-06"}


def _marks(scn: Scenario, engine: Engine) -> list[pytest.MarkDecorator]:
    reasons = []
    if scn.known_issue:
        reasons.append(f"{scn.known_issue}: {scn.known_issue_note}")
    if engine == "planner" and scn.planner_known_issue:
        reasons.append(f"{scn.planner_known_issue}: {scn.planner_known_issue_note}")
    return [pytest.mark.xfail(strict=True, reason=" / ".join(reasons).strip())] if reasons else []


def _params(prefix: str = "SB-") -> list[object]:
    out: list[object] = []
    for path in sorted(DATA.glob(f"{prefix}*.yaml")):
        scn = load_scenario(path)
        for engine in ENGINES:
            if engine in scn.engines:
                out.append(
                    pytest.param(scn, engine, id=f"{scn.id}-{engine}", marks=_marks(scn, engine))
                )
    return out


@pytest.mark.parametrize(("scn", "engine"), _params())
async def test_scenario(scn: Scenario, engine: Engine) -> None:
    result = await run_scenario(
        scn, engine=engine
    )  # a provider fixture, if there is one, is loaded here
    result.raise_if_failed()


def test_engine_limits_are_few_and_always_explained() -> None:
    limited = {}
    for path in sorted(DATA.glob("SB-*.yaml")):
        scn = load_scenario(path)
        if scn.engines != ["legacy", "planner"] and not scn.id.startswith("SB-QP-"):
            limited[scn.id] = scn
    assert set(limited) == LEGACY_ONLY
    for scn in limited.values():
        assert scn.engines == ["legacy"] and len(scn.engines_reason) > 40, scn.id
    for path in sorted(DATA.glob("SB-*.yaml")):
        scn = load_scenario(path)
        if scn.planner_known_issue:
            assert scn.planner_known_issue.startswith("BUG-") and scn.planner_known_issue_note


def test_every_named_scenario_is_implemented_or_deferred() -> None:
    wanted = {
        *(f"IN-{n:02d}" for n in (6, 11, 12, 13)),
        *(f"IV-{n:02d}" for n in range(1, 12)),
        *(f"DC-{n:02d}" for n in range(1, 15)),
    }
    have = {p.stem.removeprefix("SB-") for p in DATA.glob("SB-*.yaml")}
    deferred = {d["id"] for d in yaml.safe_load((DATA / "DEFERRED.yaml").read_text())["deferred"]}
    assert wanted <= have, f"missing scenario files: {sorted(wanted - have)}"
    assert {
        "IN-01",
        "IN-02",
        "IN-04",
        "IN-05",
        "IN-07",
        "IN-08",
    } <= deferred
    assert not (deferred & have), "a deferred id also has a scenario file"
    # IN-10 (private links), IN-14 (corrupt files) and IN-15 (duplicates) are exercised against the real
    # service in test_intake_flow.py, not by a YAML script (the runner has no upload step yet).
    flow = (Path(__file__).parent / "test_intake_flow.py").read_text()
    for needle in ("private_link_is_refused", "corrupt_file_is_reported", "same_file_twice"):
        assert needle in flow, needle
