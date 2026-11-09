"""Named edge-case scenarios (``SB-<ID>``): YAML scripts run against the real Service in mock mode.

A scenario marked ``known_issue: BUG-n`` is a strict xfail: the product behaviour is wrong today, the script says
what should happen, and it fails the build when the bug is fixed (remove the mark then).
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from mde_compass.scenarios import Scenario, load_scenario, run_scenario

DATA = Path(__file__).parent / "data"


def _params() -> list[object]:
    out: list[object] = []
    for path in sorted(DATA.glob("SB-*.yaml")):
        scn = load_scenario(path)
        marks = []
        if scn.known_issue:
            marks.append(
                pytest.mark.xfail(
                    strict=True, reason=f"{scn.known_issue}: {scn.known_issue_note}".strip()
                )
            )
        out.append(pytest.param(scn, id=scn.id, marks=marks))
    return out


@pytest.mark.parametrize("scn", _params())
async def test_scenario(scn: Scenario, service) -> None:
    result = await run_scenario(scn, service=service)
    result.raise_if_failed()


def test_every_named_scenario_is_implemented_or_deferred() -> None:
    wanted = {
        *(f"IN-{n:02d}" for n in (6, 11, 12, 13)),
        *(f"IV-{n:02d}" for n in range(1, 12)),
        *(f"DC-{n:02d}" for n in range(1, 15)),
    }
    have = {p.stem.removeprefix("SB-") for p in DATA.glob("SB-*.yaml")}
    deferred = {d["id"] for d in yaml.safe_load((DATA / "DEFERRED.yaml").read_text())["deferred"]}
    assert wanted <= have, f"missing scenario files: {sorted(wanted - have)}"
    assert {"IN-01", "IN-02", "IN-04", "IN-05", "IN-07", "IN-08", "IN-10", "IN-14", "IN-15"} <= deferred
    assert not (deferred & have), "a deferred id also has a scenario file"
