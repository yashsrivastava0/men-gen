"""Log redaction (brief 7.11, 9.1): a key value or key-like text never reaches a log line.

Three layers: the structlog processor on its own, the stdlib logging bridge, and the whole stack
(a provider error that echoes the key, scripted through the real gateway, must not surface in a
log, a call record or an event).
"""

from __future__ import annotations

import io
import logging
from pathlib import Path

import structlog

from evals.harness.driver import FlowDriver
from evals.harness.observe import capture_logs, llm_record_text
from evals.harness.stack import NetworkGuard, StackOptions, build_stack, script_item
from mde_core.logging import configure_logging, get_logger, register_secrets

FAKE_KEY = "sk-test-LOGLOGLOGLOGLOGLOGLOGLOGLOG0123456789"  # secret-scan: allow
GITHUB = "ghp_abcdefghijklmnopqrstuvwxyz0123456789"  # secret-scan: allow


def test_the_processor_scrubs_registered_values_and_key_like_text() -> None:
    buffer = io.StringIO()
    configure_logging("DEBUG", json=True, stream=buffer)
    try:
        register_secrets([FAKE_KEY])
        log = get_logger("security-test")
        log.info(
            "event_with_secrets",
            token=FAKE_KEY,
            note=f"hello {FAKE_KEY} and {GITHUB}",
            nested={"a": [FAKE_KEY, {"b": GITHUB}]},
            password="password = Tr0ub4dor3xyzQ9",  # secret-scan: allow
        )
        try:
            raise RuntimeError(f"upstream said: invalid key {FAKE_KEY}")
        except RuntimeError:
            log.exception("failed_with_secret_in_the_traceback")
    finally:
        structlog.reset_defaults()
        logging.basicConfig(level=logging.WARNING, force=True)
    text = buffer.getvalue()
    assert "event_with_secrets" in text and "failed_with_secret_in_the_traceback" in text
    assert FAKE_KEY not in text and GITHUB not in text and "Tr0ub4dor3xyzQ9" not in text
    assert "[REDACTED]" in text


def test_stdlib_log_lines_are_scrubbed_too() -> None:
    """GAP-6 (fixed): records written through the stdlib ``logging`` module (the Spec Builder logs
    this way, for one) are scrubbed by the filter ``configure_logging`` installs."""
    buffer = io.StringIO()
    configure_logging("DEBUG", json=True, stream=buffer)
    try:
        logging.getLogger("stdlib-test").warning("stdlib line with %s", GITHUB)
    finally:
        structlog.reset_defaults()
        logging.basicConfig(level=logging.WARNING, force=True)
    assert "stdlib line" in buffer.getvalue()
    assert GITHUB not in buffer.getvalue()


async def test_a_provider_error_that_echoes_the_key_never_reaches_a_log_a_record_or_an_event(
    tmp_path: Path, guard: NetworkGuard
) -> None:
    script = {
        "C1.understand": script_item(
            "C1.understand",
            {"error": "provider_400", "message": f"401 invalid_api_key: {FAKE_KEY} is not valid"},
        ),
        "C2.interpret_brief": script_item(
            "C2.interpret_brief",
            {"error": "provider_500", "message": f"upstream 500 for key {FAKE_KEY}", "times": 4},
        ),
    }
    with capture_logs() as logs:
        stack = build_stack(
            tmp_path / "projects", StackOptions(script=script, api_key=FAKE_KEY, max_attempts=2)
        )
        d = FlowDriver.for_user(stack)
        await d.start()
        await d.say("An online store for handmade candles")  # C1 fails
        await d.say("I would like to sell candles in an online store")  # C1 ok; C2 retried, fails
        events = "\n".join(e.model_dump_json() for e in await d.events())
    text = logs.getvalue()
    assert "llm_" in text or "retry" in text, "the gateway logged nothing: the check is vacuous"
    assert FAKE_KEY not in text
    assert FAKE_KEY not in events
    assert FAKE_KEY not in llm_record_text(stack)
    assert stack.calls.failed(), "the scripted provider errors never fired"
