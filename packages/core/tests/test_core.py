from __future__ import annotations

import io
import json
import multiprocessing as mp
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import BaseModel

from mde_core import (
    CancellationToken,
    FakeClock,
    SequentialIds,
    canonical_json,
    contains_secret,
    ensure_utc,
    hash_json,
    hash_model,
    id_prefix,
    is_id,
    load_settings,
    new_id,
    new_ulid,
    redact_text,
    strip_hidden_chars,
    token_cost_micros,
    usd_to_micros,
)
from mde_core.errors import CallCancelled
from mde_core.logging import configure_logging, get_logger, register_secrets
from mde_core.settings import describe_presence
from mde_core.testing import lock_counter_worker

# ---------------------------------------------------------------- ids


def test_ulid_sorted_and_unique() -> None:
    ids = [new_ulid() for _ in range(2000)]
    assert len(set(ids)) == 2000
    assert ids == sorted(ids)
    assert all(len(i) == 26 for i in ids)


def test_prefixed_ids() -> None:
    sid = new_id("ses")
    assert sid.startswith("ses_") and is_id(sid, "ses") and not is_id(sid, "evt")
    assert id_prefix(sid) == "ses"
    with pytest.raises(ValueError):
        new_id("nope")
    with pytest.raises(ValueError):
        id_prefix("ses_short")


def test_sequential_ids_are_valid_ids() -> None:
    gen = SequentialIds()
    a, b = gen("ses"), gen("evt")
    assert a != b and is_id(a, "ses") and is_id(b, "evt")


# ---------------------------------------------------------------- hashing


class _M(BaseModel):
    b: int
    a: str
    hash: str = ""


def test_canonical_json_is_order_independent() -> None:
    assert canonical_json({"b": 1, "a": 2}) == canonical_json({"a": 2, "b": 1})
    assert hash_json({"x": [1, 2]}) == hash_json({"x": [1, 2]})
    assert hash_json({"x": [1, 2]}) != hash_json({"x": [2, 1]})


def test_hash_model_excludes_fields() -> None:
    m1, m2 = _M(b=1, a="x", hash="aaa"), _M(b=1, a="x", hash="bbb")
    assert hash_model(m1, exclude=["hash"]) == hash_model(m2, exclude=["hash"])
    assert hash_model(m1) != hash_model(m2)


# ---------------------------------------------------------------- redaction


@pytest.mark.parametrize(
    "text",
    [
        "my key is sk-proj-abcdefghijklmnopqrstuvwx1234",
        "AKIAABCDEFGHIJKLMNOP is the id",
        "password = hunter2hunter2",
        "postgres://admin:s3cretpass@db.example.com/prod",
        "Authorization: Bearer abcdefghijklmnopqrstuvwxyz012345",
        "-----BEGIN RSA PRIVATE KEY-----\nabc\n-----END RSA PRIVATE KEY-----",
    ],
)
def test_secrets_detected_and_redacted(text: str) -> None:
    assert contains_secret(text)
    clean, kinds = redact_text(text)
    assert kinds and "[REDACTED]" in clean
    assert not contains_secret(clean)


def test_plain_text_untouched() -> None:
    text = "I want an app for my bakery in Pune; मुझे ऐप चाहिए"
    assert redact_text(text) == (text, [])


def test_strip_hidden_chars() -> None:
    assert strip_hidden_chars("he​llo‮ wor\x00ld\n") == "hello world\n"


# ---------------------------------------------------------------- money


def test_money_math() -> None:
    assert usd_to_micros("5.70") == 5_700_000
    # 1M tokens at $0.10/1M = 100_000 micros; rounding goes UP
    assert token_cost_micros(1_000_000, "0.10") == 100_000
    assert token_cost_micros(1, "0.10") == 1
    assert token_cost_micros(10, Decimal("0.01")) == 1


# ---------------------------------------------------------------- clock / cancel


def test_fake_clock_and_utc() -> None:
    clock = FakeClock()
    t0 = clock.now()
    clock.advance(5)
    assert (clock.now() - t0).total_seconds() == 5
    with pytest.raises(ValueError):
        ensure_utc(datetime(2026, 1, 1))  # noqa: DTZ001 - the point of the test
    assert ensure_utc(datetime(2026, 1, 1, tzinfo=UTC)).tzinfo is UTC


async def test_cancellation_token() -> None:
    tok = CancellationToken()
    tok.check()
    tok.cancel("stop")
    assert tok.cancelled and tok.reason == "stop"
    with pytest.raises(CallCancelled):
        tok.check()


# ---------------------------------------------------------------- settings


def test_settings_defaults_and_live_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("LLM_MODE", "ALLOW_LIVE_LLM", "OPENAI_API_KEY", "MDE_FORBID_LIVE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("MDE_ENV_FILE", "")
    s = load_settings()
    assert s.llm_mode == "mock" and s.openai_model == "gpt-6-luna" and not s.live_enabled
    assert s.model_for_route("deep") == "gpt-6-luna"
    assert s.llm_budget_usd == Decimal("6.00")

    monkeypatch.setenv("LLM_MODE", "live")
    assert not load_settings().live_enabled  # needs the second key
    monkeypatch.setenv("ALLOW_LIVE_LLM", "1")
    assert load_settings().live_enabled
    monkeypatch.setenv("MDE_FORBID_LIVE", "1")
    assert not load_settings().live_enabled


def test_describe_presence_never_leaks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MDE_ENV_FILE", "")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-0123456789abcdefghij")
    s = load_settings()
    report = describe_presence(s)
    assert report["OPENAI_API_KEY"] == "present"
    assert "0123456789" not in json.dumps(report) and "0123456789" not in repr(s)


# ---------------------------------------------------------------- logging


def test_logs_are_scrubbed() -> None:
    secret = "sk-proj-SUPERSECRETVALUE1234567890"
    buf = io.StringIO()
    configure_logging("INFO", json=True, stream=buf)
    register_secrets([secret])
    get_logger("t").info("call", key=secret, nested={"k": [f"x {secret} y"]}, plain="ok")
    out = buf.getvalue()
    assert "SUPERSECRETVALUE" not in out and "[REDACTED]" in out
    assert json.loads(out.strip().splitlines()[-1])["plain"] == "ok"


# ---------------------------------------------------------------- file lock (multi-process)


def test_filelock_serialises_processes(tmp_path: Path) -> None:
    lock = str(tmp_path / "x.lock")
    counter = str(tmp_path / "counter.txt")
    ctx = mp.get_context("spawn")
    procs = [ctx.Process(target=lock_counter_worker, args=(lock, counter, 25)) for _ in range(4)]
    for p in procs:
        p.start()
    for p in procs:
        p.join(timeout=120)
        assert p.exitcode == 0
    assert int(Path(counter).read_text()) == 100  # no lost updates
