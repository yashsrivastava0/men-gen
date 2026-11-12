"""``scripts/research_app.py credits show|set``: writes the owner's baselines to the credit ledger in the app's data
folder (never anywhere else) and prints them in plain words. The older subcommands are untouched."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

from mde_research_agent.spend.credits import FILE_NAME, CreditLedger

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "research_app.py"


@pytest.fixture
def app_script(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    monkeypatch.setenv("RESEARCH_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("MDE_ENV_FILE", "")  # the command never needs the dotenv file
    spec = importlib.util.spec_from_file_location("research_app_under_test", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
        yield module
    finally:
        sys.modules.pop(spec.name, None)


def test_show_on_a_fresh_install_says_there_is_no_count(
    app_script: ModuleType, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    assert app_script.main(["credits", "show"]) == 0
    out = capsys.readouterr().out
    assert str(tmp_path / "data" / FILE_NAME) in out
    assert "Tavily" in out and "Serper" in out and out.count("no count yet") == 2
    assert not (tmp_path / "data" / FILE_NAME).exists()  # reading never creates the file


def test_the_owners_two_numbers_are_saved_and_shown(
    app_script: ModuleType, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    assert app_script.main(["credits", "set", "tavily", "--used", "93", "--limit", "1000"]) == 0
    assert app_script.main(["credits", "set", "serper", "--remaining", "2389"]) == 0
    capsys.readouterr()
    assert app_script.main(["credits", "show"]) == 0
    out = capsys.readouterr().out
    tavily = next(line for line in out.splitlines() if line.startswith("Tavily"))
    serper = next(line for line in out.splitlines() if line.startswith("Serper"))
    assert "93 of 1,000" in tavily and "907" in tavily and "counted by this app" in tavily
    assert serper.split()[1] == "-" and "2,389" in serper
    saved = json.loads((tmp_path / "data" / FILE_NAME).read_text(encoding="utf-8"))
    base = saved["providers"]["tavily"]["baseline"]
    assert (base["kind"], base["amount"], base["limit"]) == ("used", 93, 1000)
    assert saved["providers"]["serper"]["baseline"]["kind"] == "remaining"
    stamp = base["as_of"]
    assert stamp.endswith("+00:00")  # as of now, UTC
    ledger = CreditLedger(tmp_path / "data" / FILE_NAME)
    assert ledger.snapshot()["providers"]["serper"]["view"]["remaining"] == 2389


@pytest.mark.parametrize(
    "args",
    [
        ["credits", "set", "tavily"],
        ["credits", "set", "tavily", "--used", "5", "--remaining", "5"],
        ["credits", "set", "tavily", "--used", "-1"],
        ["credits", "set", "tavily", "--used", "50", "--limit", "10"],
    ],
)
def test_a_wrong_baseline_is_refused_and_nothing_is_written(
    app_script: ModuleType, capsys: pytest.CaptureFixture[str], tmp_path: Path, args: list[str]
) -> None:
    assert app_script.main(args) == 2
    assert capsys.readouterr().err.strip()
    assert not (tmp_path / "data" / FILE_NAME).exists()


def test_the_command_line_rejects_what_it_does_not_know(app_script: ModuleType) -> None:
    for args in (
        ["credits"],
        ["credits", "set", "bing", "--used", "1"],
        ["credits", "set", "tavily", "--used", "x"],
    ):
        with pytest.raises(SystemExit) as info:
            app_script.main(args)
        assert info.value.code == 2


def test_a_low_balance_is_explained_in_plain_words(
    app_script: ModuleType, capsys: pytest.CaptureFixture[str]
) -> None:
    app_script.main(["credits", "set", "tavily", "--used", "990", "--limit", "1000"])
    assert "Only 10 left" in capsys.readouterr().out


def test_the_older_subcommands_are_still_there(app_script: ModuleType) -> None:
    with pytest.raises(SystemExit) as info:
        app_script.main(["frobnicate"])
    assert info.value.code == 2
    parser_help = app_script.__doc__ or ""
    for word in ("start", "status", "logs", "stop", "restart"):
        assert word in parser_help
