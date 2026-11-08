"""GAP-6: records written through the standard ``logging`` module are scrubbed too.

``configure_logging`` used to scrub structlog events only; uvicorn, httpx, sqlalchemy and every other
third-party logger reached the handlers verbatim. Now a filter sits on the handlers and a record
factory scrubs each record as it is created (which also covers handlers attached later).
"""

from __future__ import annotations

import io
import logging
from collections.abc import Iterator

import pytest
import structlog

from mde_core import logging as mde_logging
from mde_core.logging import (
    ScrubFilter,
    configure_logging,
    protect_handler,
    register_secrets,
    scrub_record,
)

GITHUB = "ghp_" + "abcdefghijklmnopqrstuvwxyz0123456789"  # synthetic
OPENAI = "sk-" + "proj-" + "A1b2C3d4E5f6G7h8I9j0K1l2"  # synthetic
PLAIN = "correct-horse-battery-staple-9"  # not key-shaped: only the exact-value registry finds it


class Capture(logging.Handler):
    """Keeps the records exactly as the handler chain delivered them."""

    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


@pytest.fixture
def out() -> Iterator[io.StringIO]:
    factory = logging.getLogRecordFactory()
    buffer = io.StringIO()
    configure_logging("DEBUG", json=True, stream=buffer)
    register_secrets([PLAIN])
    try:
        yield buffer
    finally:
        structlog.reset_defaults()
        logging.basicConfig(level=logging.WARNING, force=True)
        logging.setLogRecordFactory(factory)


def test_a_key_in_a_percent_style_argument_never_reaches_the_handler(out: io.StringIO) -> None:
    logging.getLogger("third.party").warning("pushed %s to %s (%d files)", GITHUB, "origin", 3)
    text = out.getvalue()
    assert "pushed [REDACTED] to origin (3 files)" in text
    assert GITHUB not in text


def test_a_secret_split_between_the_template_and_its_argument_is_found(out: io.StringIO) -> None:
    logging.getLogger("t").warning("auth header: Bearer %s", "A" * 12 + "b" * 12 + "9" * 8)
    logging.getLogger("t").warning("api_key=%s", OPENAI)
    text = out.getvalue()
    assert "A" * 12 not in text and OPENAI not in text and "A1b2C3d4" not in text
    assert "api_key=[REDACTED]" in text


def test_a_template_never_loses_a_placeholder_to_the_redaction(
    out: io.StringIO, capsys: pytest.CaptureFixture[str]
) -> None:
    logging.getLogger("t").warning("password=%s user=%s", "Tr0ub4dor3xyzQ9", "alice")
    assert "password=[REDACTED] user=alice" in out.getvalue()
    assert "Logging error" not in capsys.readouterr().err  # no "not all arguments converted"


def test_a_registered_exact_value_is_scrubbed_even_when_it_is_not_key_shaped(
    out: io.StringIO,
) -> None:
    logging.getLogger("t").error("login failed for %s", PLAIN)
    logging.getLogger("t").error("%s", {"token": PLAIN})
    assert PLAIN not in out.getvalue()
    assert "login failed for [REDACTED]" in out.getvalue()


def test_a_traceback_that_carries_a_key_is_scrubbed(out: io.StringIO) -> None:
    try:
        try:
            raise ValueError(f"inner: {OPENAI}")
        except ValueError as inner:
            raise RuntimeError(f"upstream rejected {GITHUB}") from inner
    except RuntimeError:
        logging.getLogger("t").exception("call failed")
    text = out.getvalue()
    assert "call failed" in text and "Traceback" in text
    assert "RuntimeError: upstream rejected [REDACTED]" in text
    assert GITHUB not in text and OPENAI not in text


def test_an_exception_passed_as_an_argument_is_scrubbed(out: io.StringIO) -> None:
    logging.getLogger("t").warning("request failed: %s", RuntimeError(f"bad key {GITHUB}"))
    logging.getLogger("t").warning("request failed: %r", RuntimeError(f"bad key {GITHUB}"))
    assert GITHUB not in out.getvalue()
    assert out.getvalue().count("bad key [REDACTED]") == 2


def test_a_key_hidden_behind_invisible_characters_is_scrubbed(out: io.StringIO) -> None:
    hidden = GITHUB[:8] + "​" + GITHUB[8:]
    logging.getLogger("t").warning("token %s", hidden)
    assert "abcdefghijklmnopqrstuvwxyz0123456789" not in out.getvalue()


def test_extra_fields_and_stack_info_are_scrubbed(out: io.StringIO) -> None:
    capture = Capture()
    protect_handler(capture)  # extras are added after the record factory ran: the filter sees them
    logging.getLogger().addHandler(capture)
    logging.getLogger("t").info(
        "with extras", extra={"token": GITHUB, "nested": {"k": [OPENAI]}, "count": 3}
    )
    (record,) = capture.records
    assert record.token == "[REDACTED]"  # type: ignore[attr-defined]
    assert OPENAI not in repr(record.nested)  # type: ignore[attr-defined]
    assert record.count == 3  # type: ignore[attr-defined]  # non-secret extras are untouched
    record2 = logging.makeLogRecord({"msg": "m", "stack_info": f"File x\n  call({GITHUB})"})
    ScrubFilter().filter(record2)
    assert record2.stack_info is not None and GITHUB not in record2.stack_info


def test_a_clean_record_is_left_exactly_as_it_was(out: io.StringIO) -> None:
    capture = Capture()
    logging.getLogger().addHandler(capture)
    logging.getLogger("t").info("%s has %d items (%.1f%%)", "cart", 3, 12.5)
    (record,) = capture.records
    assert record.msg == "%s has %d items (%.1f%%)" and record.args == ("cart", 3, 12.5)
    assert record.getMessage() == "cart has 3 items (12.5%)"
    assert "cart has 3 items (12.5%)" in out.getvalue()


def test_a_handler_attached_later_that_does_not_propagate_is_covered_too(
    out: io.StringIO,
) -> None:
    """uvicorn installs its own handlers (``propagate=False``) after our setup has run."""
    other = io.StringIO()
    logger = logging.getLogger("uvicorn.access.test")
    handler = logging.StreamHandler(other)
    logger.propagate = False
    logger.addHandler(handler)
    try:
        logger.warning('%s - "GET /v1/x?token=%s HTTP/1.1" 200', "127.0.0.1", GITHUB)
    finally:
        logger.removeHandler(handler)
    assert "GET /v1/x?token=[REDACTED] HTTP/1.1" in other.getvalue()
    assert GITHUB not in other.getvalue()


def test_a_record_built_elsewhere_is_scrubbed_by_the_filter(out: io.StringIO) -> None:
    """``makeLogRecord`` (sockets, queues) swaps the content in after the factory has run."""
    record = logging.makeLogRecord({"msg": "token %s", "args": (GITHUB,), "levelno": 30})
    assert ScrubFilter().filter(record) is True
    assert GITHUB not in record.getMessage()


def test_configure_logging_is_idempotent(out: io.StringIO) -> None:
    for _ in range(3):
        configure_logging("DEBUG", json=True, stream=out)
    for handler in logging.getLogger().handlers:
        assert [type(f) for f in handler.filters].count(ScrubFilter) == 1
    factory = logging.getLogRecordFactory()
    assert getattr(factory, "_mde_scrubbing_factory", False)
    assert not getattr(factory.__wrapped__, "_mde_scrubbing_factory", False)  # type: ignore[attr-defined]
    logging.getLogger("t").warning("once %s", GITHUB)
    assert out.getvalue().count("once [REDACTED]") == 1


def test_a_malformed_call_does_not_crash_the_scrubber(out: io.StringIO) -> None:
    record = logging.LogRecord("t", logging.WARNING, __file__, 1, "bad %d", (" " + GITHUB,), None)
    scrub_record(record)  # must not raise
    assert GITHUB not in str(record.args) and GITHUB not in str(record.msg)


def test_a_record_that_cannot_be_scrubbed_is_withheld_not_passed_on(
    out: io.StringIO, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(_record: logging.LogRecord) -> None:
        raise RuntimeError("scrubber bug")

    monkeypatch.setattr(mde_logging, "_scrub_record", boom)
    logging.getLogger("t").warning("secret %s", GITHUB)
    text = out.getvalue()
    assert GITHUB not in text and "withheld" in text
