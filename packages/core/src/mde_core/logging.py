"""structlog setup: JSON logs, request/session context, secret scrubbing (brief 7.11)."""

from __future__ import annotations

import logging
import sys
from collections.abc import MutableMapping
from typing import Any, cast

import structlog

from mde_core.redact import redact_text, redact_value

_known_secrets: list[str] = []


def register_secrets(values: list[str]) -> None:
    """Register exact secret values (API keys) to be scrubbed from every log event."""
    for v in values:
        if v and len(v) >= 8 and v not in _known_secrets:
            _known_secrets.append(v)


def _scrub_text(text: str) -> tuple[str, bool]:
    """``(clean_text, changed)``: registered exact secrets first, then key-like text.

    ``changed`` is true only when something was actually redacted (``redact_text`` also returns
    NFKC-normalised text, which is not a reason to rewrite a log record).
    """
    out = text
    for secret in _known_secrets:
        out = redact_value(out, secret)
    changed = out != text
    out, found = redact_text(out)
    return out, changed or bool(found)


def _scrub(value: Any) -> Any:
    if isinstance(value, str):
        return _scrub_text(value)[0]
    if isinstance(value, dict):
        mapping = cast("dict[Any, Any]", value)
        return {k: _scrub(v) for k, v in mapping.items()}
    if isinstance(value, list):
        return [_scrub(v) for v in cast("list[Any]", value)]
    if isinstance(value, tuple):
        return tuple(_scrub(v) for v in cast("tuple[Any, ...]", value))
    return value


def scrub_processor(
    _logger: Any, _method: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    return {k: _scrub(v) for k, v in event_dict.items()}


# --------------------------------------------------------------------------- stdlib logging
# Records written through the standard ``logging`` module (uvicorn, httpx, sqlalchemy, any third
# party) never pass the structlog processor chain, so they get their own scrubbing:
#
# * ``ScrubFilter`` sits on every handler we can reach (the root handlers and the handlers of loggers
#   that already exist), so a record that arrives by any route (a queue, ``makeLogRecord``) is
#   scrubbed before it is formatted;
# * a ``LogRecord`` factory wrapper scrubs each record as it is created, which also covers handlers
#   that are attached later and do not propagate to root (uvicorn installs its own with
#   ``dictConfig`` after our setup has run).
#
# The scrub works on the FORMATTED message: a secret that is split between a ``%s`` template and its
# argument ("key=%s") is still found, and a template can never lose a placeholder to the
# redaction (``password=%s`` -> ``password=[REDACTED]`` with ``args`` cleared). A record with
# nothing to redact is left untouched (template, args and exception info as they were).

_STDLIB_ATTRS = frozenset(logging.makeLogRecord({}).__dict__) | {"message", "asctime"}
_SCRUBBED_MARK = "_mde_scrubbed"  # (msg, args) as they were when the record was scrubbed
_FACTORY_MARK = "_mde_scrubbing_factory"
_WITHHELD = "[log record withheld: it could not be scrubbed]"


def _exc_tuple(exc_info: object) -> tuple[Any, Any, Any] | None:
    if isinstance(exc_info, BaseException):
        return (type(exc_info), exc_info, exc_info.__traceback__)
    if isinstance(exc_info, tuple):
        return cast("tuple[Any, Any, Any]", exc_info)
    return None


def _scrub_exception_text(record: logging.LogRecord) -> None:
    if not record.exc_text:
        exc = _exc_tuple(record.exc_info)
        if exc is None:
            return
        text = logging.Formatter().formatException(exc)  # what a plain Formatter would print
    else:
        text = record.exc_text
    clean, changed = _scrub_text(text)
    if changed:
        record.exc_text = clean  # the stdlib Formatter prints exc_text instead of exc_info


def _scrub_record(record: logging.LogRecord) -> None:
    try:
        message: str | None = record.getMessage()
    except Exception:  # a malformed call: logging reports it itself; scrub its parts instead
        message = None
    if message is not None:
        clean, changed = _scrub_text(message)
        if changed:
            record.msg, record.args = clean, None
    else:
        if isinstance(record.msg, str):
            record.msg = _scrub_text(record.msg)[0]
        if record.args:
            record.args = _scrub(record.args)
    _scrub_exception_text(record)
    if record.stack_info:
        clean_stack, changed = _scrub_text(record.stack_info)
        if changed:
            record.stack_info = clean_stack


def _scrub_extras(record: logging.LogRecord) -> None:
    """``extra={...}`` fields (a JSON formatter emits them). ``Logger.makeRecord`` adds them AFTER
    the record factory has run, so only a handler filter ever sees them: this step is repeated for
    a record whose message was already scrubbed."""
    for key, value in list(record.__dict__.items()):
        if key in _STDLIB_ATTRS or key == _SCRUBBED_MARK:
            continue
        clean_value = _scrub(value)
        if clean_value is not value and clean_value != value:
            record.__dict__[key] = clean_value


def scrub_record(record: logging.LogRecord) -> None:
    """Redact a stdlib ``LogRecord`` in place (message, args, exception text, stack, extras).

    Registered exact secrets (:func:`register_secrets`) and key-like text (``mde_core.redact``) are
    replaced. Idempotent per record. Fails closed: a record that cannot be scrubbed is replaced by
    a fixed placeholder rather than passed on.
    """
    mark = record.__dict__.get(_SCRUBBED_MARK)
    try:
        if mark is not None and mark[0] is record.msg and mark[1] is record.args:
            _scrub_extras(record)  # the content is clean (``makeLogRecord`` would swap it)
            return
        _scrub_record(record)
        _scrub_extras(record)
    except Exception:
        record.msg, record.args = _WITHHELD, None
        record.exc_info = record.exc_text = record.stack_info = None
    record.__dict__[_SCRUBBED_MARK] = (record.msg, record.args)


class ScrubFilter(logging.Filter):
    """A handler filter that scrubs every record it sees (and never drops one)."""

    def filter(self, record: logging.LogRecord) -> bool:
        scrub_record(record)
        return True


def protect_handler(handler: logging.Handler) -> None:
    """Put a :class:`ScrubFilter` on ``handler`` (once)."""
    if not any(isinstance(f, ScrubFilter) for f in handler.filters):
        handler.addFilter(ScrubFilter())


def _install_record_factory() -> None:
    current = logging.getLogRecordFactory()
    if getattr(current, _FACTORY_MARK, False):
        return

    def factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
        record = current(*args, **kwargs)
        scrub_record(record)
        return record

    setattr(factory, _FACTORY_MARK, True)
    setattr(factory, "__wrapped__", current)  # noqa: B010 - the functools.wraps convention
    logging.setLogRecordFactory(factory)


def install_stdlib_scrubbing() -> None:
    """Scrub everything written through the standard ``logging`` module. Idempotent.

    Called by :func:`configure_logging`; call it again after attaching a handler later.
    """
    for handler in logging.getLogger().handlers:
        protect_handler(handler)
    for existing in list(logging.Logger.manager.loggerDict.values()):
        if isinstance(existing, logging.Logger):
            for handler in existing.handlers:
                protect_handler(handler)
    _install_record_factory()


def configure_logging(level: str = "INFO", *, json: bool = True, stream: Any = None) -> None:
    """Configure structlog + stdlib logging, both scrubbed of secrets. Idempotent."""
    renderer: Any = structlog.processors.JSONRenderer() if json else structlog.dev.ConsoleRenderer()
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.format_exc_info,
            scrub_processor,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelNamesMapping().get(level.upper(), logging.INFO)
        ),
        logger_factory=structlog.PrintLoggerFactory(file=stream or sys.stderr),
        cache_logger_on_first_use=False,
    )
    logging.basicConfig(level=level.upper(), stream=stream or sys.stderr, force=True)
    install_stdlib_scrubbing()


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)  # type: ignore[no-any-return]


def bind_context(**kwargs: Any) -> None:
    """Bind request_id / session_id / call_id for the current task."""
    structlog.contextvars.bind_contextvars(**kwargs)


def clear_context() -> None:
    structlog.contextvars.clear_contextvars()
