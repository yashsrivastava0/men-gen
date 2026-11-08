"""N11: cassettes are committed fixtures, so they must not carry contact details, and a replay must
not serve a file that is not the one recorded for the request.

* emails and phone numbers are redacted on write (conservatively: dates, IPs, versions and plain
  numbers stay), with the secret redaction still applied first;
* ``read_cassette`` (which replay uses) refuses a file whose own ``key`` differs from the key it is
  stored under, or whose spec and version differ from its directory.
"""

from __future__ import annotations

import dataclasses
import json
import time
from pathlib import Path

import pytest

from mde_contracts.gateway import Citation, Usage
from mde_core.errors import CassetteMissing, ConfigError
from mde_llm_gateway.providers import ProviderRequest, ReplayProvider
from mde_llm_gateway.providers.base import ProviderResponse
from mde_llm_gateway.providers.cassette import (
    EMAIL_MARK,
    PHONE_MARK,
    build_cassette,
    cassette_key,
    cassette_path,
    read_cassette,
    redact_pii,
    write_cassette,
)
from mde_llm_gateway.testing_specs import ECHO_SPEC, EchoIn

SECRET = "sk-" + "proj-" + "Zx9Yw8Vu7Ts6Rq5Po4Nm3Lk2Jh1"  # built at runtime, never a literal key


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


# --------------------------------------------------------------------------- emails


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("mail alice@example.com now", f"mail {EMAIL_MARK} now"),
        ("a.b+tag_c%d@sub.example.co.in.", f"{EMAIL_MARK}."),
        ("(bob@example.org)", f"({EMAIL_MARK})"),
        ('{"email": "carol@corp.example"}', f'{{"email": "{EMAIL_MARK}"}}'),
        ("two: a@b.co and c@d.io", f"two: {EMAIL_MARK} and {EMAIL_MARK}"),
        ("x@y.zz\nz@w.zz", f"{EMAIL_MARK}\n{EMAIL_MARK}"),
        ("UPPER@EXAMPLE.COM", EMAIL_MARK),
        ("mailto:me@example.com?subject=hi", f"mailto:{EMAIL_MARK}?subject=hi"),
    ],
)
def test_emails_are_redacted(text: str, expected: str) -> None:
    assert redact_pii(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "no at sign here",
        "@screen_name has no local part",
        "ping @everyone",
        "user@localhost has no dot",
        "price 3 @ 5 each",
        "a @ b.com with spaces",
        "email@",
        "@",
        "@@@@@@",
        "name@.com",
        "name@-.com",
        "user@10.0.0.1",
        "user@host.x",
    ],
)
def test_text_that_is_not_an_email_is_left_alone(text: str) -> None:
    assert redact_pii(text) == text


# --------------------------------------------------------------------------- phone numbers


@pytest.mark.parametrize(
    "number",
    [
        "+91 98765 43210",
        "+91-98765-43210",
        "+919876543210",
        "+1 (555) 123-4567",
        "+44 20 7946 0958",
        "(555) 123-4567",
        "555-123-4567",
        "555.123.4567",
        "98765 43210",
        "9876543210",
        "919876543210",
        "09876543210",
        "0091 98765 43210",
    ],
)
def test_phone_numbers_are_redacted(number: str) -> None:
    assert redact_pii(f"call {number} today") == f"call {PHONE_MARK} today"
    assert redact_pii(f"call {number}.") == f"call {PHONE_MARK}."
    assert redact_pii(f"({number})") == f"({PHONE_MARK})"
    assert redact_pii(f"tel:{number}") == f"tel:{PHONE_MARK}"


@pytest.mark.parametrize(
    "text",
    [
        "2026-11-08",
        "2026-11-08T12:00:00Z",
        "08/11/2026",
        "192.168.100.200",
        "10.0.19045.5247",
        "1.2.3.4.5.6.7.8.9.10",
        "123456789",  # nine digits
        "12345678901234567890",  # twenty digits
        "1731000000",  # an epoch timestamp (does not start 6-9)
        "1,234,567.89",
        "SCR-001 SCR-002",
        "Q3 2026 revenue 4500000",
        "version 1.20.300",
        "0123456789abcdef0123456789abcdef",
        "ses_01M6WXF6VW2HD0MHDGXZPYY1NP",
        "100 200 300",
        "5000 users, 20000 orders",
    ],
)
def test_dates_ips_versions_and_plain_numbers_are_left_alone(text: str) -> None:
    assert redact_pii(text) == text


def test_numbers_glued_to_words_are_left_alone() -> None:
    for text in (
        "abc9876543210",
        "9876543210xyz",
        "id-9876543210-x",
        "x.9876543210",
        "k_9876543210",
    ):
        assert PHONE_MARK not in redact_pii(text), text


def test_pii_redaction_is_linear_on_hostile_input() -> None:
    started = time.perf_counter()
    for payload in (
        "a@" * 100_000,
        "a" * 200_000,
        "a." * 100_000 + "@x",
        "@" + "a" * 100_000,
        "x@" + "a" * 100_000,
        "x@" + "a." * 50_000,
        "1" * 200_000,
        "1 " * 100_000,
        "1." * 100_000,
        "+" * 100_000 + "1",
        "(1)" * 70_000,
        "9876543210 " * 20_000,
    ):
        redact_pii(payload)
    assert time.perf_counter() - started < 10  # a quadratic scan of 200,000 characters would not


# --------------------------------------------------------------------------- the written file


def test_a_written_cassette_has_no_secret_email_or_phone_anywhere(tmp_path: Path) -> None:
    req = _req(
        system="You are helping alice@example.com (+91 98765 43210).",
        user=f"my key is {SECRET}; reach me at bob@corp.example or 555-123-4567",
    )
    resp = ProviderResponse(
        text=json.dumps({"echo": f"{SECRET} carol@corp.example (555) 123-4567"}),
        usage=Usage(input_tokens=5),
        citations=[Citation(url="https://a.example", title="contact dave@corp.example")],
        refusal="cannot send to erin@corp.example or +44 20 7946 0958",
    )
    path = write_cassette(tmp_path, build_cassette(req, resp, cassette_key(req)))
    raw = path.read_text(encoding="utf-8")
    for needle in (
        SECRET,
        "alice@example.com",
        "bob@corp.example",
        "carol@corp.example",
        "dave@corp.example",
        "erin@corp.example",
        "98765 43210",
        "555-123-4567",
        "123-4567",
        "7946 0958",
    ):
        assert needle not in raw, needle
    assert "[REDACTED]" in raw and EMAIL_MARK in raw and PHONE_MARK in raw
    cassette = read_cassette(path)  # still a valid, readable cassette
    assert cassette.response.usage == Usage(input_tokens=5)
    assert cassette.response.text.startswith('{"echo": "[REDACTED]')


def test_the_key_and_the_structure_survive_redaction(tmp_path: Path) -> None:
    req = _req(user="write to alice@example.com")
    key = cassette_key(req)  # computed from the real text, before any redaction
    path = write_cassette(
        tmp_path, build_cassette(req, ProviderResponse(text="ok", usage=None), key)
    )
    assert path == cassette_path(tmp_path, "T1.echo", 1, key)
    assert read_cassette(path).key == key


# --------------------------------------------------------------------------- replay verifies the key


def recorded(tmp_path: Path, **kw: object) -> tuple[ProviderRequest, Path]:
    req = _req(**kw)
    key = cassette_key(req)
    path = write_cassette(
        tmp_path, build_cassette(req, ProviderResponse(text="recorded", usage=None), key)
    )
    return req, path


async def test_an_untouched_cassette_replays(tmp_path: Path) -> None:
    req, _ = recorded(tmp_path)
    response = await ReplayProvider(tmp_path).complete(req)
    assert response.text == "recorded" and response.cassette_key == cassette_key(req)


async def test_a_cassette_whose_own_key_was_edited_is_refused(tmp_path: Path) -> None:
    req, path = recorded(tmp_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["key"] = "0" * 64
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ConfigError, match=r"does not match the key it is stored under"):
        await ReplayProvider(tmp_path).complete(req)


async def test_a_cassette_copied_into_another_requests_slot_is_refused(tmp_path: Path) -> None:
    """The attack: take the cassette recorded for one request and file it under another request's
    key, so the replay answers B with A's recorded response."""
    request_a, path_a = recorded(tmp_path, user="question A")
    request_b = _req(user="question B")
    slot_b = cassette_path(tmp_path, "T1.echo", 1, cassette_key(request_b))
    slot_b.write_bytes(path_a.read_bytes())
    with pytest.raises(ConfigError) as refused:
        await ReplayProvider(tmp_path).complete(request_b)
    assert "refused" in str(refused.value) and cassette_key(request_a)[:16] in str(refused.value)
    # A itself still replays
    assert (await ReplayProvider(tmp_path).complete(request_a)).text == "recorded"


async def test_a_cassette_moved_to_another_spec_directory_is_refused(tmp_path: Path) -> None:
    req, path = recorded(tmp_path)
    other_version = tmp_path / "T1.echo__v2"
    other_version.mkdir()
    moved = other_version / path.name
    moved.write_bytes(path.read_bytes())
    path.unlink()
    with pytest.raises(ConfigError, match=r"records T1.echo__v1 but sits in T1.echo__v2"):
        read_cassette(moved)
    with pytest.raises(CassetteMissing):  # and the original slot is simply empty now
        await ReplayProvider(tmp_path).complete(req)


async def test_a_cassette_that_is_not_json_is_still_reported_as_unreadable(
    tmp_path: Path,
) -> None:
    req, path = recorded(tmp_path)
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ConfigError, match="unreadable"):
        await ReplayProvider(tmp_path).complete(req)
