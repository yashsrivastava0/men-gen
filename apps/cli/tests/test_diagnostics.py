"""``mde budget`` and ``mde doctor``: the numbers are the gateway's own, and no value is ever printed."""

from __future__ import annotations

import contextlib
import io
import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

from mde_cli import diagnostics
from mde_cli.main import app
from mde_contracts.gateway import Usage
from mde_llm_gateway import budget_cli
from mde_llm_gateway.ledger import FileLedger, LedgerEvent


def fake_key() -> str:
    """Key-shaped, built at run time: the tests compare output against it."""
    return "sk-" + "test-" + "a1b2c3d4e5f6a7b8c9d0e1f2a3b4"


def spend(path: Path, micros: int, scope: str = "eval:run-1") -> None:
    ledger = FileLedger(path)
    now = datetime.now(UTC)
    ledger.reserve(
        LedgerEvent(
            event="reserve",
            ts=now,
            reservation_id="r1",
            scopes=[scope],
            worst_case_micros=micros * 2,
            mode="live",
        ),
        lambda _snapshot: None,  # no cap check: the test writes the ledger directly
    )
    ledger.append(
        LedgerEvent(
            event="commit", ts=now, reservation_id="r1", scopes=[scope], mode="live",
            cost_micros=micros, usage=Usage(input_tokens=10, output_tokens=5, cost_micros=micros),
        )
    )  # fmt: skip


# --------------------------------------------------------------------------- budget


def test_budget_prints_what_just_budget_prints(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ledger = tmp_path / "ledger.jsonl"
    spend(ledger, 123_400)
    monkeypatch.setenv("OPENAI_API_KEY", fake_key())
    result = runner.invoke(app, ["budget", "--ledger", str(ledger)])
    assert result.exit_code == 0, result.output
    assert "live spend      : USD 0.1234" in result.stdout
    assert "eval:run-1" in result.stdout and "tripwire        : ok" in result.stdout
    # the very same text as the gateway's own command (which `just budget` runs)
    reference = io.StringIO()
    with contextlib.redirect_stdout(reference):
        assert budget_cli.main(["--ledger", str(ledger)]) == 0
    assert result.stdout == reference.getvalue()
    assert fake_key() not in result.output


def test_budget_json_and_scope_filter(runner: CliRunner, tmp_path: Path) -> None:
    ledger = tmp_path / "ledger.jsonl"
    spend(ledger, 50_000, "eval:alpha")
    spend_two = FileLedger(ledger)
    now = datetime.now(UTC)
    spend_two.reserve(
        LedgerEvent(event="reserve", ts=now, reservation_id="r2", scopes=["test:beta"], worst_case_micros=20_000, mode="live"),
        lambda _snapshot: None,
    )  # fmt: skip
    spend_two.append(
        LedgerEvent(event="commit", ts=now, reservation_id="r2", scopes=["test:beta"], mode="live", cost_micros=10_000, usage=Usage(cost_micros=10_000))
    )  # fmt: skip
    everything = json.loads(
        runner.invoke(app, ["budget", "--json", "--ledger", str(ledger)]).stdout
    )
    assert everything["spent_micros"] == 60_000 and set(everything["by_scope"]) == {
        "eval:alpha",
        "test:beta",
    }
    only = json.loads(
        runner.invoke(app, ["budget", "--json", "--scope", "eval", "--ledger", str(ledger)]).stdout
    )
    assert set(only["by_scope"]) == {"eval:alpha"} and only["scope_filter"] == "eval"
    text = runner.invoke(app, ["budget", "--scope", "test", "--ledger", str(ledger)]).stdout
    assert "(filter: test)" in text and "eval:alpha" not in text


def test_budget_with_no_ledger_is_zero_and_creates_nothing(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ledger = tmp_path / "data" / "ledger.jsonl"
    monkeypatch.setenv("LLM_BUDGET_LEDGER", str(ledger))
    result = runner.invoke(app, ["budget"])
    assert result.exit_code == 0 and "live spend      : USD 0.0000" in result.stdout
    assert not ledger.exists() and not ledger.parent.exists()  # read-only


# --------------------------------------------------------------------------- doctor


def test_doctor_reports_presence_never_values(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    key = fake_key()
    monkeypatch.setenv("OPENAI_API_KEY", key)
    monkeypatch.setenv("GATEWAY_SERVICE_TOKEN", "gw-" + "token-" + "z9y8x7w6v5u4t3s2r1")
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://mde:hunter2-pw@127.0.0.1:1/mde")
    result = runner.invoke(app, ["doctor"])
    out = result.output
    assert result.exit_code == 1  # nothing listens on port 1
    assert "OPENAI_API_KEY: present" in out and "GATEWAY_SERVICE_TOKEN: present" in out
    assert "LLM_MODE: mock" in out and "DATABASE_URL: set" in out
    assert "database: NOT reachable" in out and "dev_db.py up" in out
    for secret in (key, "gw-token-z9y8x7w6v5u4t3s2r1", "hunter2-pw", "127.0.0.1:1"):
        assert secret not in out
    assert "model: not checked (add --live" in out


def test_doctor_without_a_key_says_absent(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    out = runner.invoke(app, ["doctor"]).output
    assert "OPENAI_API_KEY: absent" in out
    assert "dotenv file: not read (MDE_ENV_FILE is empty)" in out  # the test fixture disables it


def test_doctor_lists_dotenv_names_only_and_notes_duplicates(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = tmp_path / "sample.env"
    secret_value = "value-that-must-not-appear-anywhere"
    env.write_text(
        f"FIRST_NAME={secret_value}\nSECOND=two\nFIRST_NAME=again\n# a comment\nnot a variable\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("MDE_ENV_FILE", str(env))
    out = runner.invoke(app, ["doctor"]).output
    assert "dotenv file sample.env: present=True; variable names: ['FIRST_NAME', 'SECOND']" in out
    assert "note: FIRST_NAME is defined 2 times" in out
    assert secret_value not in out and "again" not in out
    assert diagnostics.env_file_names(tmp_path / "missing.env") == {}


def test_doctor_checks_the_database_when_it_is_up(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(diagnostics, "_check_database", lambda url: True)
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0 and "database: reachable" in result.output


def test_the_model_is_only_probed_in_live_mode(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(diagnostics, "_check_database", lambda url: True)
    out = runner.invoke(app, ["doctor", "--live"]).output
    assert "model: skipped (needs LLM_MODE=live)" in out
    assert "available" not in out  # nothing was called


def test_a_failing_model_probe_prints_the_error_type_only(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    from types import SimpleNamespace

    class Settings(SimpleNamespace):
        pass

    monkeypatch.setattr(diagnostics, "_check_database", lambda url: True)
    import mde_core.settings as core_settings

    real = core_settings.load_settings
    monkeypatch.setattr(
        core_settings,
        "load_settings",
        lambda **kw: real(**kw).model_copy(update={"llm_mode": "live", "forbid_live": False}),
    )
    import mde_llm_gateway.doctor as gateway_doctor

    def boom(settings: object) -> str:
        raise RuntimeError("the key sk-leaked-" + "x" * 20 + " was rejected")

    monkeypatch.setattr(gateway_doctor, "probe_model", boom)
    result = runner.invoke(app, ["doctor", "--live"])
    assert result.exit_code == 1 and "model: probe failed (RuntimeError)" in result.output
    assert "sk-leaked" not in result.output
    monkeypatch.setattr(gateway_doctor, "probe_model", lambda settings: "gpt-test: available")
    good = runner.invoke(app, ["doctor", "--live"])
    assert good.exit_code == 0 and "model: gpt-test: available" in good.output


REAL_CLIENT = httpx.Client  # the genuine class, kept before any test patches it


def mock_httpx_client(monkeypatch: pytest.MonkeyPatch, status: int | Exception) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if isinstance(status, Exception):
            raise status
        return httpx.Response(status, json={"status": "ready"})

    def factory(**kwargs: object) -> httpx.Client:
        return REAL_CLIENT(transport=httpx.MockTransport(handler), **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(httpx, "Client", factory)


def test_doctor_can_check_the_api(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(diagnostics, "_check_database", lambda url: True)
    mock_httpx_client(monkeypatch, 200)
    ready = runner.invoke(app, ["doctor", "--url", "http://api.test"])
    assert ready.exit_code == 0 and "api: ready at http://api.test" in ready.output
    mock_httpx_client(monkeypatch, 503)
    not_ready = runner.invoke(app, ["doctor", "--url", "http://api.test"])
    assert not_ready.exit_code == 1 and "not ready (HTTP 503)" in not_ready.output
    request = httpx.Request("GET", "http://api.test/readyz")
    mock_httpx_client(monkeypatch, httpx.ConnectError("refused", request=request))
    down = runner.invoke(app, ["doctor", "--url", "http://api.test"])
    assert (
        down.exit_code == 1
        and "api: NOT reachable at http://api.test (ConnectError)" in down.output
    )
