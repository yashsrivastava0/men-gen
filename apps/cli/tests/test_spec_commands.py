"""``mde spec ...`` on a generated 50-screen spec: simulate-consumer, verify, show."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from typer.testing import CliRunner

from mde_cli.main import app
from mde_spec_builder.render import render_spec
from mde_spec_builder.testing import make_sample_spec
from mde_spec_loader import SpecIndex, compute_spec_hash


@pytest.fixture(scope="module")
def spec_text() -> str:
    return render_spec(make_sample_spec(50))


@pytest.fixture
def spec_file(tmp_path: Path, spec_text: str) -> Path:
    path = tmp_path / "APP_SPEC.md"
    path.write_bytes(spec_text.encode("utf-8"))
    return path


# --------------------------------------------------------------------------- simulate-consumer


def test_the_consumer_simulator_passes_on_a_50_screen_spec(
    runner: CliRunner, spec_file: Path
) -> None:
    result = runner.invoke(app, ["spec", "simulate-consumer", str(spec_file)])
    assert result.exit_code == 0, result.output
    out = result.stdout
    assert re.search(
        r"^APP_SPEC\.md: version \d+, 50 screens, ~\d+ tokens in total, hash ok$", out, re.M
    )
    assert "budget per slice: 10000 tokens" in out
    assert re.search(r"^target\s+kind\s+tokens$", out, re.M)
    assert len(re.findall(r"^SCR-\d{3}\s+screen\s+\d+$", out, re.M)) == 50
    assert len(re.findall(r"^S\d+\s+step\s+\d+$", out, re.M)) == 11
    assert "ok: every slice is within budget and every reference resolves" in out
    assert re.search(r"^largest slice: \d+ tokens$", out, re.M)


def test_a_slice_over_the_budget_is_marked_and_exits_1(runner: CliRunner, spec_file: Path) -> None:
    result = runner.invoke(app, ["spec", "simulate-consumer", str(spec_file), "--budget", "300"])
    assert result.exit_code == 1
    assert "OVER BUDGET" in result.stdout and "budget per slice: 300 tokens" in result.stdout
    assert "problem(s):" in result.stderr and "over the 300 budget" in result.stderr
    assert "ok: every slice" not in result.stdout


def test_a_tampered_file_fails_the_hash_check_in_the_simulator(
    runner: CliRunner, spec_file: Path, spec_text: str
) -> None:
    spec_file.write_text(
        spec_text.replace("SCR-001", "SCR-001", 1) + "\nextra line\n", encoding="utf-8"
    )
    result = runner.invoke(app, ["spec", "simulate-consumer", str(spec_file)])
    assert result.exit_code == 1 and "hash MISMATCH" in result.stdout
    assert "the embedded hash does not match the file content" in result.stderr


def test_an_unreadable_or_malformed_file_exits_2_with_one_line(
    runner: CliRunner, tmp_path: Path
) -> None:
    missing = runner.invoke(app, ["spec", "simulate-consumer", str(tmp_path / "nope.md")])
    assert missing.exit_code == 2 and "error: cannot read" in missing.stderr
    junk = tmp_path / "junk.md"
    junk.write_text("# not a spec\n", encoding="utf-8")
    for command in (["simulate-consumer"], ["verify"], ["show"]):
        result = runner.invoke(app, ["spec", *command, str(junk)])
        assert result.exit_code == 2, (command, result.output)
        assert "Traceback" not in result.output
    binary = tmp_path / "bin.md"
    binary.write_bytes(b"\xff\xfe\x00\x80")
    assert runner.invoke(app, ["spec", "verify", str(binary)]).exit_code == 2


# --------------------------------------------------------------------------- verify


def test_verify_accepts_an_intact_file_and_the_hash_it_is_bound_to(
    runner: CliRunner, spec_file: Path, spec_text: str
) -> None:
    digest = compute_spec_hash(spec_text)
    result = runner.invoke(app, ["spec", "verify", str(spec_file)])
    assert result.exit_code == 0, result.output
    assert f"embedded hash {digest}" in result.stdout and f"recomputed    {digest}" in result.stdout
    assert "ok: the embedded hash matches the file." in result.stdout
    bound = runner.invoke(app, ["spec", "verify", str(spec_file), "--hash", digest])
    assert bound.exit_code == 0 and "and the expected hash." in bound.stdout


def test_verify_refuses_a_changed_file_and_a_hash_it_is_not_bound_to(
    runner: CliRunner, spec_file: Path, spec_text: str
) -> None:
    other = runner.invoke(app, ["spec", "verify", str(spec_file), "--hash", "0" * 64])
    assert other.exit_code == 1 and "intact but it is not the version bound to" in other.stdout
    spec_file.write_text(spec_text.replace("Home", "Hone", 1), encoding="utf-8")
    changed = runner.invoke(app, ["spec", "verify", str(spec_file)])
    assert changed.exit_code == 1 and "changed after its hash was written" in changed.stdout
    blank = spec_text.replace(compute_spec_hash(spec_text), "")
    spec_file.write_text(blank, encoding="utf-8")
    no_hash = runner.invoke(app, ["spec", "verify", str(spec_file)])
    assert (
        no_hash.exit_code == 1
        and "(none)" in no_hash.stdout
        and "no embedded hash" in no_hash.stdout
    )


def test_a_spec_checked_out_with_crlf_still_verifies(
    runner: CliRunner, spec_file: Path, spec_text: str
) -> None:
    spec_file.write_bytes(spec_text.replace("\n", "\r\n").encode("utf-8"))
    assert runner.invoke(app, ["spec", "verify", str(spec_file)]).exit_code == 0


# --------------------------------------------------------------------------- show


def test_show_without_options_prints_the_index(
    runner: CliRunner, spec_file: Path, spec_text: str
) -> None:
    result = runner.invoke(app, ["spec", "show", str(spec_file)])
    assert result.exit_code == 0, result.output
    view = SpecIndex.from_text(spec_text).index()
    assert f"hash {view.hash}" in result.stdout and f"~{view.total_tokens} tokens" in result.stdout
    assert "sections:" in result.stdout and re.search(
        r"^block\s+kind\s+lines\s+tokens$", result.stdout, re.M
    )
    assert len(re.findall(r"^SCR-\d{3}\s+screen\s", result.stdout, re.M)) == 50


def test_show_a_screen_prints_its_slice_with_the_spec_tag(
    runner: CliRunner, spec_file: Path, spec_text: str
) -> None:
    result = runner.invoke(app, ["spec", "show", str(spec_file), "--screen", "SCR-001"])
    assert result.exit_code == 0, result.output
    sliced = SpecIndex.from_text(spec_text).slice("SCR-001")
    assert f"--- slice SCR-001 ({sliced.spec_tag})" in result.stdout
    assert f"about {sliced.token_estimate} tokens" in result.stdout
    assert "SCR-001" in result.stdout.split("\n", 1)[1]
    step = runner.invoke(app, ["spec", "show", str(spec_file), "--screen", "S3"])
    assert step.exit_code == 0 and "--- slice S3" in step.stdout


def test_show_exact_text_of_ids_and_unknown_ids_exit_2(runner: CliRunner, spec_file: Path) -> None:
    result = runner.invoke(
        app, ["spec", "show", str(spec_file), "--get", "SCR-002", "--get", "SUMMARY"]
    )
    assert result.exit_code == 0, result.output
    assert "--- SCR-002" in result.stdout and "--- SUMMARY" in result.stdout
    unknown = runner.invoke(app, ["spec", "show", str(spec_file), "--screen", "SCR-999"])
    assert unknown.exit_code == 2 and "unknown spec id: SCR-999" in unknown.stderr
    unknown_get = runner.invoke(app, ["spec", "show", str(spec_file), "--get", "NOPE-1"])
    assert unknown_get.exit_code == 2 and "unknown spec id: NOPE-1" in unknown_get.stderr


def test_spec_group_help_lists_the_three_commands(runner: CliRunner) -> None:
    out = runner.invoke(app, ["spec", "--help"]).stdout
    for name in ("simulate-consumer", "verify", "show"):
        assert name in out
