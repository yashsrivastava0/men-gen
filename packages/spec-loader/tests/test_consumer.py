from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from mde_contracts.spec import AppSpec, ScreenAction
from mde_spec_builder.render import render_spec
from mde_spec_builder.testing import make_sample_spec
from mde_spec_loader import SpecIndex, simulate_consumer

REPO = Path(__file__).resolve().parents[3]
SCRIPT = REPO / "scripts" / "simulate_frontend_consumer.py"


def test_a_valid_spec_passes(index12: SpecIndex) -> None:
    report = simulate_consumer(index12)
    assert report.ok and report.problems == [] and report.hash_ok
    assert report.unresolved == [] and report.unscheduled == []
    ids = [s.id for s in report.slices]
    assert ids[:11] == [f"S{i}" for i in range(11)]  # every step first, then every screen
    assert ids[11:] == [s.id for s in index12.spec.screens]
    assert report.max_tokens == max(s.tokens for s in report.slices)
    assert [s.kind for s in report.slices[:11]] == ["step"] * 11


def test_the_50_screen_sample_fits_the_budget(index50: SpecIndex) -> None:
    report = simulate_consumer(index50, budget_tokens=10_000)
    assert report.ok, report.problems
    assert len(report.slices) == 11 + 50
    assert report.max_tokens < 10_000


def test_a_tiny_budget_fails_with_the_oversized_slices_named(index12: SpecIndex) -> None:
    report = simulate_consumer(index12, budget_tokens=500)
    assert not report.ok
    assert all(s.over_budget for s in report.slices)
    assert any("slice SCR-001 is" in p and "over the 500 budget" in p for p in report.problems)


def test_unresolved_references_fail(spec12: AppSpec) -> None:
    broken = spec12.model_copy(
        update={
            "screens": [
                s.model_copy(update={"actions": [ScreenAction(name="x", go="SCR-099")]})
                if s.id == "SCR-006"
                else s
                for s in spec12.screens
            ]
        }
    )
    report = simulate_consumer(SpecIndex.from_text(render_spec(broken)))
    assert not report.ok
    assert report.unresolved == ["SCR-006 -go-> SCR-099 (x)"]
    assert any(p.startswith("unresolved reference:") for p in report.problems)


def test_a_tampered_file_fails_the_hash_check(text12: str) -> None:
    report = simulate_consumer(
        SpecIndex.from_text(text12.replace("Show one product", "Show a product"))
    )
    assert (
        not report.hash_ok
        and "the embedded hash does not match the file content" in report.problems
    )


def test_a_screen_no_step_builds_is_reported(spec12: AppSpec) -> None:
    steps = [
        s.model_copy(
            update={
                "parallel": [g.model_copy(update={"screens": g.screens[:-1]}) for g in s.parallel]
            }
        )
        if s.id == "S6"
        else s
        for s in spec12.build.steps
    ]
    broken = spec12.model_copy(update={"build": spec12.build.model_copy(update={"steps": steps})})
    report = simulate_consumer(SpecIndex.from_text(render_spec(broken)))
    assert report.unscheduled and any("is built by no step" in p for p in report.problems)


def test_a_screen_built_twice_is_reported(spec12: AppSpec) -> None:
    steps = [
        s.model_copy(update={"screens": [*s.screens, "SCR-001"]}) if s.id == "S5" else s
        for s in spec12.build.steps
    ]
    broken = spec12.model_copy(update={"build": spec12.build.model_copy(update={"steps": steps})})
    report = simulate_consumer(SpecIndex.from_text(render_spec(broken)))
    assert any("SCR-001 is built by 2 steps" in p for p in report.problems)


def test_a_step_loading_an_unknown_block_is_reported(spec12: AppSpec) -> None:
    steps = [
        s.model_copy(update={"load": ["APP", "NOPE"]}) if s.id == "S0" else s
        for s in spec12.build.steps
    ]
    broken = spec12.model_copy(update={"build": spec12.build.model_copy(update={"steps": steps})})
    report = simulate_consumer(SpecIndex.from_text(render_spec(broken)))
    assert any(p.startswith("cannot slice S0") for p in report.problems)


# --------------------------------------------------------------------------- the script


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=120,
    )


def test_script_passes_on_a_generated_spec(tmp_path: Path) -> None:
    path = tmp_path / "APP_SPEC.md"
    path.write_text(render_spec(make_sample_spec(14, seed=3)), encoding="utf-8", newline="\n")
    done = _run(str(path))
    assert done.returncode == 0, done.stderr
    assert "SCR-014" in done.stdout and "S10" in done.stdout and "hash ok" in done.stdout
    assert "largest slice:" in done.stdout and "ok: every slice" in done.stdout


def test_script_fails_when_a_slice_exceeds_the_budget(tmp_path: Path) -> None:
    path = tmp_path / "APP_SPEC.md"
    path.write_text(render_spec(make_sample_spec(6)), encoding="utf-8", newline="\n")
    done = _run(str(path), "--budget", "300")
    assert done.returncode == 1
    assert "OVER BUDGET" in done.stdout and "over the 300 budget" in done.stderr


def test_script_fails_on_a_broken_reference(tmp_path: Path) -> None:
    spec = make_sample_spec(6)
    broken = spec.model_copy(
        update={
            "screens": [
                s.model_copy(update={"actions": [ScreenAction(name="x", calls="API-missing")]})
                if s.id == "SCR-003"
                else s
                for s in spec.screens
            ]
        }
    )
    path = tmp_path / "APP_SPEC.md"
    path.write_text(render_spec(broken), encoding="utf-8", newline="\n")
    done = _run(str(path))
    assert (
        done.returncode == 1 and "unresolved reference: SCR-003 -calls-> API-missing" in done.stderr
    )


def test_script_exits_2_on_unreadable_input(tmp_path: Path) -> None:
    assert _run(str(tmp_path / "missing.md")).returncode == 2
    garbage = tmp_path / "garbage.md"
    garbage.write_text("# not a spec\n", encoding="utf-8")
    done = _run(str(garbage))
    assert done.returncode == 2 and "cannot read" in done.stderr
