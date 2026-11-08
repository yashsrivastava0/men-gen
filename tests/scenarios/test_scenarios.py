"""The situation catalog as executable scenarios (spec section 9, brief 9.1).

One YAML file per row, played against the real engines in mock mode (see ``runner.py`` for the
file format). ``test_scenarios_match_the_spec_catalog`` pins the files to the spec table 1:1.
A handful also run on PostgreSQL (``@pytest.mark.integration``).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from runner import Scenario, ScenarioFailed, load_scenario, run_scenario, scenario_files

from evals.harness.catalog import parse_situation_table
from evals.harness.stack import StoreFactory, network_guard

FILES = scenario_files()
FILE_SCENARIOS = [load_scenario(p) for p in FILES]
SCENARIOS = [sc for group in FILE_SCENARIOS for sc in group]


def _param(sc: Scenario) -> pytest.ParameterSet:
    marks = (
        [pytest.mark.xfail(strict=True, reason=sc.known_issue, raises=ScenarioFailed)]
        if sc.known_issue
        else []
    )
    return pytest.param(sc, id=sc.param_id, marks=marks)


@pytest.mark.e2e
@pytest.mark.parametrize("sc", [_param(s) for s in SCENARIOS])
async def test_scenario(sc, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    with network_guard() as guard:
        await run_scenario(sc, tmp_path / "projects")
    assert guard.attempts == [], f"{sc.id} tried to reach the network: {guard.attempts}"


@pytest.mark.integration
@pytest.mark.parametrize("sc", [_param(s) for s in SCENARIOS if s.integration])
async def test_scenario_on_postgres(  # type: ignore[no-untyped-def]
    sc, tmp_path: Path, postgres_store_factory: StoreFactory
) -> None:
    await run_scenario(sc, tmp_path / "projects", postgres_store_factory)


def test_scenarios_match_the_spec_catalog() -> None:
    rows = parse_situation_table()
    assert len(rows) == 40, f"the spec table has {len(rows)} rows, expected 40"
    assert len(FILE_SCENARIOS) == len(rows), (
        f"{len(FILE_SCENARIOS)} scenario files for {len(rows)} catalog rows"
    )
    for row, group in zip(rows, FILE_SCENARIOS, strict=True):
        sc = group[0]
        assert sc.id.startswith(row.sit_id + "-"), (
            f"row {row.number} should be {row.sit_id}-<slug>, the file is {sc.id}"
        )
        assert sc.situation == row.situation, (
            f"{sc.id}: situation text differs from spec row {row.number}\n"
            f"  scenario: {sc.situation!r}\n  spec:     {row.situation!r}"
        )
