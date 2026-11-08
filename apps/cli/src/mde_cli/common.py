"""What ``chat`` and ``sim`` share: who you are, which token, and which API (network or in-process)."""

from __future__ import annotations

import getpass
import os
import re
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from mde_cli.client import ApiClient, Identity, make_http
from mde_cli.errors import EXIT_BAD_INPUT, CliError
from mde_cli.runtime import LoopRunner

DEFAULT_TENANT: Final = "local"
# The same shapes the API accepts (``mde_context_api.auth``): a bad value would only come back as
# an unexplained 401, so it is checked here with a message.
_TENANT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_USER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@+-]{0,127}$")
_ROLE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")


def default_user() -> str:
    """The OS user name made safe for the API (spaces and odd characters become ``-``)."""
    try:
        name = getpass.getuser()
    except Exception:  # no user database on some containers
        name = ""
    cleaned = re.sub(r"[^A-Za-z0-9_.:@+-]+", "-", name).strip("-.:@+_")
    return cleaned[:128] if cleaned else "cli-user"


def build_identity(tenant: str, user: str | None, roles: str = "") -> Identity:
    who = user or default_user()
    role_list = tuple(dict.fromkeys(r.strip() for r in roles.split(",") if r.strip()))
    if not _TENANT.match(tenant):
        raise CliError(
            "--tenant may use letters, digits, '_' and '-' (at most 64) and must start with a "
            "letter or digit.",
            exit_code=EXIT_BAD_INPUT,
        )
    if not _USER.match(who):
        raise CliError(
            "--user may use letters, digits and _ . : @ + - (at most 128).",
            exit_code=EXIT_BAD_INPUT,
        )
    for role in role_list:
        if not _ROLE.match(role):
            raise CliError(
                f"{role!r} is not a valid role name (letters, digits, '_', '.', '-').",
                exit_code=EXIT_BAD_INPUT,
            )
    return Identity(tenant, who, role_list)


def resolve_token(token: str | None) -> str:
    """``--token``, else ``API_DEV_TOKEN`` (and the dev default), exactly as the API reads it."""
    if token:
        return token
    from mde_core.settings import load_settings

    return load_settings().api_dev_token.get_secret_value()


def read_script(source: str) -> list[str]:
    """The lines of a ``--script`` file (``-`` is standard input), UTF-8, with or without BOM."""
    try:
        if source == "-":
            return sys.stdin.read().splitlines()
        return Path(source).read_text(encoding="utf-8-sig").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        raise CliError(
            f"cannot read the script {source!r} ({type(exc).__name__}).", exit_code=EXIT_BAD_INPUT
        ) from None


@dataclass(frozen=True)
class Target:
    """Which API to talk to."""

    url: str
    token: str | None
    local: bool = False
    memory: bool = False
    projects_dir: Path | None = None
    verbose: bool = False


@contextmanager
def open_api(target: Target, identity: Identity) -> Iterator[tuple[LoopRunner, ApiClient]]:
    """A loop runner and a client for ``target``; the in-process API (``--local``) is started
    before and stopped after, whatever happens in between."""
    runner = LoopRunner()
    local = None
    client: ApiClient | None = None
    try:
        if target.local:
            from mde_cli.local import LocalApi
            from mde_core.logging import configure_logging

            configure_logging("INFO" if target.verbose else "ERROR", json=False)
            local = LocalApi(memory=target.memory, projects_dir=target.projects_dir)
            client = runner.run(local.start(identity))
        else:
            client = ApiClient(
                make_http(target.url),
                resolve_token(target.token),
                identity,
                label=f"the API at {target.url}",
            )
        yield runner, client
    finally:
        try:
            if local is not None:
                runner.run(local.stop())
            elif client is not None:
                runner.run(client.aclose())
        finally:
            runner.close()


def debug_enabled() -> bool:
    return bool(os.environ.get("MDE_DEBUG"))
