"""Cassette round trip, live gating and the factory (brief 7.4, 7.9, 7.13)."""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest

from mde_contracts.gateway import Citation, Usage
from mde_core.clock import FakeClock
from mde_core.errors import CassetteMissing, ConfigError, LiveCallForbidden
from mde_core.paths import main_repo_root
from mde_core.settings import Settings
from mde_llm_gateway.factory import create_gateway
from mde_llm_gateway.ledger import FileLedger, MemoryLedger
from mde_llm_gateway.providers import (
    MockProvider,
    ProviderRequest,
    ReplayProvider,
    ScriptedProvider,
    ScriptedResponse,
)
from mde_llm_gateway.providers.cassette import (
    build_cassette,
    cassette_key,
    cassette_path,
    read_cassette,
    write_cassette,
)
from mde_llm_gateway.providers.recording import RecordingProvider
from mde_llm_gateway.testing_specs import (
    ECHO_SPEC,
    SEARCH_SPEC,
    EchoIn,
    EchoOut,
    SearchIn,
    build_test_gateway,
    make_ctx,
    make_test_registry,
)

FAKE_KEY = "sk-" + "test-" + "a1b2c3d4e5f6a7b8c9d0e1f2a3b4"  # built at runtime, never a literal key
GOOD = EchoOut(answer="hello there", confidence=0.5)


def _json_files(root: Path) -> list[Path]:
    return sorted(root.rglob("*.json"))


def live_settings(**kw: object) -> Settings:
    base: dict[str, object] = {
        "llm_mode": "live",
        "forbid_live": False,
        "openai_api_key": FAKE_KEY,
    }
    return Settings(_env_file=None, **{**base, **kw})  # type: ignore[call-arg,arg-type]


# ---------------------------------------------------------------------------- live gating matrix
@pytest.mark.parametrize(
    ("mode", "forbid", "allowed"),
    [
        ("live", False, True),
        ("live", True, False),  # MDE_FORBID_LIVE wins over LLM_MODE=live
        ("mock", False, False),
        ("replay", False, False),
    ],
)
def test_live_provider_construction_gating(mode: str, forbid: bool, allowed: bool) -> None:
    from mde_llm_gateway.providers.openai_responses import OpenAIResponsesProvider

    settings = live_settings(llm_mode=mode, forbid_live=forbid)
    if allowed:
        OpenAIResponsesProvider(object(), settings=settings)  # type: ignore[arg-type]
    else:
        with pytest.raises(LiveCallForbidden):
            OpenAIResponsesProvider(object(), settings=settings)  # type: ignore[arg-type]


def test_forbid_live_message_names_the_flag() -> None:
    with pytest.raises(LiveCallForbidden, match="MDE_FORBID_LIVE"):
        create_gateway(live_settings(forbid_live=True), make_test_registry())


def test_the_autouse_fixture_makes_default_settings_forbid_live() -> None:
    """The root conftest sets MDE_FORBID_LIVE=1: even LLM_MODE=live cannot go live."""
    import os

    os.environ["LLM_MODE"] = "live"  # monkeypatch restores LLM_MODE
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.forbid_live and not settings.live_enabled
    with pytest.raises(LiveCallForbidden):
        create_gateway(settings, make_test_registry())


def test_recording_provider_needs_live_too() -> None:
    with pytest.raises(LiveCallForbidden):
        RecordingProvider(ScriptedProvider(), settings=live_settings(forbid_live=True))
    with pytest.raises(LiveCallForbidden):
        RecordingProvider(ScriptedProvider(), settings=live_settings(llm_mode="mock"))


def test_live_without_a_key_is_a_config_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="OPENAI_API_KEY"):
        create_gateway(live_settings(openai_api_key=None), make_test_registry())


# ---------------------------------------------------------------------------- factory
def test_factory_picks_the_provider_and_ledger_from_the_mode(tmp_path: Path) -> None:
    ledger_path = tmp_path / ".budget" / "ledger.jsonl"
    mock = create_gateway(
        Settings(_env_file=None, llm_budget_ledger=ledger_path),  # type: ignore[call-arg]
        make_test_registry(),
    )
    assert isinstance(mock.provider, MockProvider)
    assert isinstance(mock.guard.ledger, MemoryLedger) and not mock.guard.bills
    replay = create_gateway(
        Settings(_env_file=None, llm_mode="replay", llm_budget_ledger=ledger_path),  # type: ignore[call-arg]
        make_test_registry(),
        cassette_dir=tmp_path / "c",
    )
    assert isinstance(replay.provider, ReplayProvider) and replay.provider.mode == "replay"
    assert not ledger_path.parent.exists()  # offline modes never touch the shared ledger


def test_factory_live_uses_the_shared_file_ledger_and_an_injected_client(tmp_path: Path) -> None:
    ledger_path = tmp_path / "ledger.jsonl"
    settings = live_settings(llm_budget_ledger=ledger_path)
    gw = create_gateway(settings, make_test_registry(), openai_client=object())
    assert gw.provider.name == "openai-responses" and gw.guard.bills
    assert isinstance(gw.guard.ledger, FileLedger) and gw.guard.ledger.path == ledger_path
    recording = create_gateway(
        settings,
        make_test_registry(),
        openai_client=object(),
        record_cassettes=True,
        cassette_dir=tmp_path,
    )
    assert recording.provider.name == "recording" and recording.provider.billable


def test_factory_builds_a_real_client_with_retries_disabled(tmp_path: Path) -> None:
    gw = create_gateway(live_settings(llm_budget_ledger=tmp_path / "l.jsonl"), make_test_registry())
    client = gw.provider._client  # type: ignore[attr-defined]
    assert client.max_retries == 0


def test_factory_env_switches(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CASSETTE_DIR", str(tmp_path / "env-cassettes"))
    replay = create_gateway(Settings(_env_file=None, llm_mode="replay"), make_test_registry())  # type: ignore[call-arg]
    assert replay.provider.cassette_dir == tmp_path / "env-cassettes"  # type: ignore[attr-defined]
    monkeypatch.setenv("MDE_RECORD_CASSETTES", "1")
    rec = create_gateway(
        live_settings(llm_budget_ledger=tmp_path / "l.jsonl"),
        make_test_registry(),
        openai_client=object(),
    )
    assert rec.provider.name == "recording"


def test_factory_reads_the_cassette_switches_from_settings(tmp_path: Path) -> None:
    """D-034: ``cassette_dir`` / ``record_cassettes`` are Settings fields; arguments override."""
    settings = Settings(_env_file=None, llm_mode="replay", cassette_dir=tmp_path / "c")  # type: ignore[call-arg]
    gw = create_gateway(settings, make_test_registry())
    assert gw.provider.cassette_dir == tmp_path / "c"  # type: ignore[attr-defined]
    other = create_gateway(settings, make_test_registry(), cassette_dir=tmp_path / "other")
    assert other.provider.cassette_dir == tmp_path / "other"  # type: ignore[attr-defined]

    ledger = tmp_path / "l.jsonl"
    live = live_settings(
        llm_budget_ledger=ledger, record_cassettes=True, cassette_dir=tmp_path / "rec"
    )
    rec = create_gateway(live, make_test_registry(), openai_client=object())
    assert rec.provider.name == "recording"
    assert rec.provider.cassette_dir == tmp_path / "rec"  # type: ignore[attr-defined]
    off = create_gateway(live, make_test_registry(), openai_client=object(), record_cassettes=False)
    assert off.provider.name == "openai-responses"


def test_factory_does_not_read_the_cassette_env_vars_behind_settings_back(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The env vars are read once, by Settings: a later change must not alter a built gateway."""
    settings = live_settings(llm_budget_ledger=tmp_path / "l.jsonl")
    replay = Settings(_env_file=None, llm_mode="replay")  # type: ignore[call-arg]
    monkeypatch.setenv("MDE_RECORD_CASSETTES", "1")
    monkeypatch.setenv("CASSETTE_DIR", str(tmp_path / "late"))
    assert create_gateway(settings, make_test_registry(), openai_client=object()).provider.name == (
        "openai-responses"
    )
    assert create_gateway(replay, make_test_registry()).provider.cassette_dir != tmp_path / "late"  # type: ignore[attr-defined]


def test_the_default_cassette_dir_is_the_main_repository_not_the_cwd(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = main_repo_root()
    assert root is not None
    monkeypatch.chdir(tmp_path)
    replay = create_gateway(Settings(_env_file=None, llm_mode="replay"), make_test_registry())  # type: ignore[call-arg]
    assert replay.provider.cassette_dir == root / "tests" / "cassettes"  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------- cassette keys
def _req(**kw: object) -> ProviderRequest:
    base = ProviderRequest(
        spec=ECHO_SPEC,
        input=EchoIn(question="q"),
        model="gpt-6-luna",
        system="sys",
        user="usr",
        effort="low",
        max_output_tokens=100,
        tools=(),
        schema=None,
        schema_name="n",
        schema_hash="hash1",
        timeout_s=1.0,
    )
    return dataclasses.replace(base, **kw)  # type: ignore[arg-type]


def test_cassette_key_covers_spec_model_messages_tools_effort_and_schema() -> None:
    base = cassette_key(_req())
    assert base == cassette_key(_req(max_output_tokens=999, timeout_s=9.0, attempt=3))  # not in key
    changed = [
        _req(model="other"),
        _req(system="sys2"),
        _req(user="usr2"),
        _req(effort="high"),
        _req(schema_hash="hash2"),
        _req(tools=SEARCH_SPEC.tools),
        _req(spec=dataclasses.replace(ECHO_SPEC, version=2)),
        _req(spec=dataclasses.replace(ECHO_SPEC, id="T9.other")),
    ]
    keys = {cassette_key(r) for r in changed}
    assert base not in keys and len(keys) == len(changed)
    assert len(base) == 64


def test_cassette_file_is_sanitised_and_readable(tmp_path: Path) -> None:
    from mde_llm_gateway.providers.base import ProviderResponse

    secret = "sk-" + "proj-" + "Zx9Yw8Vu7Ts6Rq5Po4Nm3Lk2Jh1"
    req = _req(user=f"my key is {secret} thanks")
    resp = ProviderResponse(
        text=f'{{"echo": "{secret}"}}',
        usage=Usage(input_tokens=5),
        citations=[Citation(url="https://a.example", title=f"title {secret}")],
    )
    key = cassette_key(req)
    path = write_cassette(tmp_path, build_cassette(req, resp, key))
    assert path == cassette_path(tmp_path, "T1.echo", 1, key)
    raw = path.read_text(encoding="utf-8")
    assert secret not in raw and "[REDACTED]" in raw
    assert "authorization" not in raw.lower() and "headers" not in raw.lower()
    data = json.loads(raw)
    assert data["format"] == 1 and data["key"] == key and data["request"]["spec_id"] == "T1.echo"
    assert read_cassette(path).response.usage == Usage(input_tokens=5)
    assert not list(tmp_path.rglob("*.tmp"))  # written atomically


# ---------------------------------------------------------------------------- round trip
async def test_record_then_replay_round_trip_through_the_gateway(tmp_path: Path) -> None:
    clock = FakeClock()
    cassettes = tmp_path / "cassettes"
    live = ScriptedProvider(mode="live", billable=True, clock=clock)
    live.queue(
        "T1.echo",
        ScriptedResponse(output=GOOD, usage=Usage(input_tokens=120, output_tokens=40)),
    )
    live.queue(
        "T4.search",
        ScriptedResponse(
            output="found it",
            citations=[Citation(url="https://example.com/x", title="X", start=0, end=5)],
            search_calls=1,
            usage=Usage(input_tokens=300, output_tokens=60),
        ),
    )
    settings = live_settings()
    recorder = RecordingProvider(live, settings=settings, cassette_dir=cassettes)
    recording_gw = build_test_gateway(
        recorder, clock=clock, ledger=MemoryLedger(clock=clock), settings=settings
    )
    recorded_echo = await recording_gw.run(ECHO_SPEC, EchoIn(question="same input"), make_ctx())
    recorded_search = await recording_gw.run(SEARCH_SPEC, SearchIn(query="cafes"), make_ctx())
    assert recorded_echo.mode == "live" and recorded_echo.cassette_key
    assert len(_json_files(cassettes)) == 2

    replay_gw = build_test_gateway(ReplayProvider(cassettes), clock=clock)
    echo = await replay_gw.run(ECHO_SPEC, EchoIn(question="same input"), make_ctx())
    search = await replay_gw.run(SEARCH_SPEC, SearchIn(query="cafes"), make_ctx())
    assert echo.output == recorded_echo.output and echo.mode == "replay"
    assert echo.cassette_key == recorded_echo.cassette_key
    assert echo.usage.cost_micros == 0  # replay is free
    assert search.text == "found it" and search.search_calls == 1
    assert search.citations == recorded_search.citations
    assert search.cassette_key == recorded_search.cassette_key


async def test_a_missing_cassette_fails_clearly_and_never_goes_live(tmp_path: Path) -> None:
    gw = build_test_gateway(ReplayProvider(tmp_path / "empty"))
    with pytest.raises(CassetteMissing) as exc:
        await gw.run(ECHO_SPEC, EchoIn(question="never recorded"), make_ctx())
    msg = str(exc.value)
    assert "T1.echo" in msg and "never calls the API" in msg
    assert exc.value.details["spec_id"] == "T1.echo" and len(str(exc.value.details["key"])) == 64


async def test_a_corrupt_cassette_is_a_config_error(tmp_path: Path) -> None:
    provider = ReplayProvider(tmp_path)
    req = _req()
    path = cassette_path(tmp_path, "T1.echo", 1, cassette_key(req))
    path.parent.mkdir(parents=True)
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ConfigError, match="unreadable"):
        await provider.complete(req)


async def test_recording_disabled_is_a_transparent_pass_through(tmp_path: Path) -> None:
    live = ScriptedProvider(mode="live", billable=True).queue("T1.echo", GOOD)
    recorder = RecordingProvider(
        live, settings=live_settings(), cassette_dir=tmp_path, enabled=False
    )
    response = await recorder.complete(_req())
    assert response.cassette_key is None and not _json_files(tmp_path)


async def test_a_failed_call_records_nothing(tmp_path: Path) -> None:
    live = ScriptedProvider(mode="live", billable=True).queue("T1.echo", RuntimeError("boom"))
    recorder = RecordingProvider(live, settings=live_settings(), cassette_dir=tmp_path)
    with pytest.raises(RuntimeError):
        await recorder.complete(_req())
    assert not _json_files(tmp_path)
