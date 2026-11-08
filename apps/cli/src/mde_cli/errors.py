"""CLI errors and the friendly wrapper every command runs under.

Exit codes (the same for every command, documented in ``docs/packages/cli.md``):

* ``0`` success;
* ``1`` the command ran and found a problem (an expectation failed, a contract was violated, a
  required service is down, the API refused a request);
* ``2`` the input cannot be used (a bad option value, a file that cannot be read or parsed);
* ``3`` refused on purpose (``mde sim --llm`` without live mode and budget).

A failure is one plain line on stderr, never a stack trace. ``MDE_DEBUG=1`` re-raises, so a developer
can still see the trace.
"""

from __future__ import annotations

import functools
import os
import sys
from collections.abc import Callable
from typing import Final, ParamSpec, TypeVar

import typer

from mde_cli.client import ApiError, ApiUnreachable

EXIT_OK: Final = 0
EXIT_PROBLEM: Final = 1
EXIT_BAD_INPUT: Final = 2
EXIT_REFUSED: Final = 3

P = ParamSpec("P")
R = TypeVar("R")


class CliError(Exception):
    """A failure with a message fit for the user and the exit code to leave with."""

    def __init__(self, message: str, *, exit_code: int = EXIT_PROBLEM, hint: str | None = None):
        super().__init__(message)
        self.message = message
        self.exit_code = exit_code
        self.hint = hint


def api_error_text(exc: ApiError) -> str:
    """What to tell the user about an API failure (never a token, never a stack trace)."""
    if exc.status == 401:
        return (
            "The API did not accept the credentials. Check --token (or API_DEV_TOKEN), --tenant "
            "and --user."
        )
    text = exc.message or f"the API answered {exc.status}"
    if exc.request_id:
        text += f" (request id {exc.request_id})"
    return text


def fail(message: str, *, exit_code: int = EXIT_PROBLEM, hint: str | None = None) -> None:
    """Print ``error: ...`` (and a hint) on stderr and leave with ``exit_code``."""
    sys.stderr.write(f"error: {message}\n")
    if hint:
        sys.stderr.write(f"hint: {hint}\n")
    raise typer.Exit(exit_code)


def friendly(func: Callable[P, R]) -> Callable[P, R]:
    """Run a command so that every expected failure becomes one line and an exit code."""

    @functools.wraps(func)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        try:
            return func(*args, **kwargs)
        except (typer.Exit, typer.Abort, SystemExit):
            raise
        except CliError as exc:
            fail(exc.message, exit_code=exc.exit_code, hint=exc.hint)
        except ApiUnreachable as exc:
            fail(str(exc), hint=exc.hint)
        except ApiError as exc:
            fail(api_error_text(exc))
        except KeyboardInterrupt:
            sys.stderr.write("\ninterrupted\n")
            raise typer.Exit(130) from None
        except Exception as exc:
            if os.environ.get("MDE_DEBUG"):
                raise
            fail(
                f"unexpected problem ({type(exc).__name__}). Run again with MDE_DEBUG=1 to see "
                "the details."
            )
        raise AssertionError("unreachable")  # pragma: no cover - fail() always raises

    return wrapper
