"""``--local`` on the real PostgreSQL (an ephemeral database, never ``mde``): start the private
server first with ``uv run python scripts/dev_db.py up``."""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

import psycopg
import pytest
from sqlalchemy.engine import URL
from typer.testing import CliRunner

from mde_cli.main import app

pytestmark = pytest.mark.integration

Script = Callable[..., str]


@pytest.fixture
def database(
    pg_database: URL, monkeypatch: pytest.MonkeyPatch
) -> str:  # the SQLAlchemy URL of the test database, also set as DATABASE_URL
    from mde_persistence.testing_plugin import truncate_all

    truncate_all(pg_database)
    url = pg_database.render_as_string(hide_password=False)
    monkeypatch.setenv("DATABASE_URL", url)
    return url


def rows(url: str, query: str) -> list[tuple[object, ...]]:
    dsn = url.replace("postgresql+psycopg://", "postgresql://")
    with psycopg.connect(dsn) as conn:
        return conn.execute(query).fetchall()  # type: ignore[arg-type]


def test_a_journey_on_postgres_leaves_a_handed_off_spec_in_the_database(
    runner: CliRunner, tmp_path: Path, write_script: Script, journey: list[str], database: str
) -> None:
    result = runner.invoke(
        app,
        [
            "chat",
            "--local",
            "--projects-dir",
            str(tmp_path / "p"),
            "--script",
            write_script(journey),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "State: PostgreSQL." in result.stdout and "Spec ready." in result.stdout
    shown = dict(re.findall(r"^  (hash|commit): +(\w+)$", result.stdout, re.M))
    assert rows(database, "select status, hash from spec_version") == [
        ("handed_off", shown["hash"])
    ]
    assert rows(
        database, "select count(*) from session_event where payload::text like '%spec_ready%'"
    ) == [(1,)]
    assert rows(database, "select state from session") == [("HANDED_OFF",)]
    assert (
        int(str(rows(database, "select count(*) from llm_call")[0][0])) > 0
    )  # the gateway's call log too


def test_a_session_on_postgres_can_be_resumed_by_a_second_run(
    runner: CliRunner, tmp_path: Path, write_script: Script, database: str
) -> None:
    common = ["--local", "--projects-dir", str(tmp_path / "p"), "--tenant", "acme", "--user", "ann"]
    first = runner.invoke(
        app,
        [
            "chat",
            *common,
            "--script",
            write_script(["An online shop for handmade candles"], "a.txt"),
        ],
    )
    sid = re.search(r"(ses_\w{26})", first.stdout)
    assert first.exit_code == 0 and sid is not None
    second = runner.invoke(
        app,
        [
            "chat",
            *common,
            "--session",
            sid.group(1),
            "--script",
            write_script(["r*", "1", "1"], "b.txt"),
        ],
    )
    assert second.exit_code == 0, second.output
    assert f"Resumed session {sid.group(1)} (guided, DISCOVERING)." in second.stdout
    assert "Spec ready." in second.stdout and "HANDED_OFF (spec_ready: 1" in second.stdout
    other_user = runner.invoke(
        app,
        [
            "chat",
            *common[:-1],
            "bob",
            "--session",
            sid.group(1),
            "--script",
            write_script(["hi"], "c.txt"),
        ],
    )
    assert (
        other_user.exit_code == 1 and "not found" in other_user.stderr
    )  # tenant-bound, owner-bound


def test_a_persona_plays_to_the_end_on_postgres(
    runner: CliRunner, tmp_path: Path, database: str
) -> None:
    result = runner.invoke(
        app, ["sim", "--local", "--projects-dir", str(tmp_path / "p"), "--persona", "P02"]
    )
    assert result.exit_code == 0, result.output
    assert "P02-vague-founder-hindi" in result.stdout and "HANDED_OFF" in result.stdout
    assert rows(database, "select count(*) from approval where decision = 'approve'") == [(2,)]


def test_an_empty_database_is_a_clear_message_about_migrating(
    runner: CliRunner,
    write_script: Script,
    empty_database: URL,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABASE_URL", empty_database.render_as_string(hide_password=False))
    result = runner.invoke(app, ["chat", "--local", "--script", write_script(["hi"])])
    assert result.exit_code == 1
    assert "error: The database has no tables yet." in result.stderr
    assert "just migrate" in result.stderr and "Traceback" not in result.output


def test_doctor_sees_the_database(runner: CliRunner, database: str) -> None:
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0, result.output
    assert "database: reachable" in result.output
    assert "mde:mde" not in result.output  # no credentials in the report
