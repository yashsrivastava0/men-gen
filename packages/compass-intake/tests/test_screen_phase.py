"""Screens carry Compass's ``core`` / ``extended`` phase, read from ``structure.slices`` (the Brief's records lack it)."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest

from mde_compass_intake import read_export

NINE = Path(__file__).resolve().parents[3] / "docs" / "compass" / "research-inputs"


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("quick-yoga-studio-bengaluru", {"core": 10, "extended": 3}),
        ("standard-produce-marketplace-nashik", {"core": 23, "extended": 5}),
        ("thorough-freight-fleet-india", {"core": 34, "extended": 98}),
    ],
)
def test_phase_is_carried(name: str, expected: dict[str, int]) -> None:
    path = NINE / f"{name}.json"
    if not path.exists():
        pytest.skip("export not present")
    spec_input = read_export(json.loads(path.read_text(encoding="utf-8")))
    assert dict(Counter(s.phase for s in spec_input.structure.screens)) == expected


def test_no_slices_means_no_phase() -> None:
    path = NINE / "pune-clinic-appointments.json"
    if not path.exists():
        pytest.skip("export not present")
    spec_input = read_export(json.loads(path.read_text(encoding="utf-8")))
    assert all(s.phase == "" for s in spec_input.structure.screens)
