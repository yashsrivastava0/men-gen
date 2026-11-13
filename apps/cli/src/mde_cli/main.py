"""The ``mde`` command (Typer). Run: ``uv run mde chat`` or ``uv run python -m mde_cli chat``.

Commands stay thin: they parse options and hand over to a module (``chat``, ``sim``, ``budget``,
``doctor``, ``specs``). Heavy imports happen inside the command so ``mde --help`` is instant.
No ``from __future__ import annotations`` here: Typer reads the annotations at run time.
"""

from pathlib import Path
from typing import Annotated

import typer

from mde_cli import __version__
from mde_cli.errors import friendly

DEFAULT_URL = "http://127.0.0.1:8100"

app = typer.Typer(
    name="mde",
    help="Talk to the Context Engine, play personas against it, and check spend, setup and specs.",
    no_args_is_help=True,
    add_completion=False,
    pretty_exceptions_enable=False,
    rich_markup_mode=None,
)
spec_app = typer.Typer(
    help="Read and check an APP_SPEC.md file.",
    no_args_is_help=True,
    add_completion=False,
    pretty_exceptions_enable=False,
    rich_markup_mode=None,
)
app.add_typer(spec_app, name="spec")

UrlOpt = Annotated[
    str, typer.Option("--url", envvar="MDE_API_URL", help="Base URL of the Context API.")
]
TokenOpt = Annotated[
    str | None,
    typer.Option(
        "--token",
        envvar="API_DEV_TOKEN",
        show_default=False,
        help="Dev token (default: API_DEV_TOKEN, else the dev default).",
    ),
]
TenantOpt = Annotated[
    str, typer.Option("--tenant", envvar="MDE_TENANT", help="Tenant the session belongs to.")
]
UserOpt = Annotated[
    str | None,
    typer.Option(
        "--user",
        envvar="MDE_USER",
        show_default=False,
        help="Your user id (default: your OS user).",
    ),
]
RolesOpt = Annotated[
    str,
    typer.Option(
        "--roles",
        envvar="MDE_ROLES",
        help="Comma separated approver roles you act under (Enterprise).",
    ),
]
LocalOpt = Annotated[
    bool,
    typer.Option(
        "--local",
        help="Run the API in this process on the local PostgreSQL, real stack, mock mode "
        "(no key, no network).",
    ),
]
MemoryOpt = Annotated[
    bool,
    typer.Option(
        "--memory",
        help="With --local: keep state in memory instead of PostgreSQL (nothing is saved).",
    ),
]
ProjectsOpt = Annotated[
    Path | None,
    typer.Option(
        "--projects-dir", help="With --local: where hand-off git repositories are written."
    ),
]
VerboseOpt = Annotated[
    bool, typer.Option("--verbose", help="With --local: show the engines' log lines.")
]


def _version(value: bool) -> None:
    if value:
        typer.echo(f"mde {__version__}")
        raise typer.Exit()


@app.callback()
def main_callback(
    version: Annotated[
        bool,
        typer.Option("--version", callback=_version, is_eager=True, help="Show the version."),
    ] = False,
) -> None:
    """mde: the Context Engine command line."""


@app.command()
@friendly
def chat(
    url: UrlOpt = DEFAULT_URL,
    token: TokenOpt = None,
    tenant: TenantOpt = "local",
    user: UserOpt = None,
    roles: RolesOpt = "",
    mode: Annotated[str, typer.Option("--mode", help="guided or enterprise.")] = "guided",
    policy: Annotated[
        str | None, typer.Option("--policy", help="Policy id (default: the mode's).")
    ] = None,
    lang: Annotated[
        str, typer.Option("--lang", help="Language of the CLI's own hints: auto, en, hi, hi-Latn.")
    ] = "auto",
    local: LocalOpt = False,
    memory: MemoryOpt = False,
    projects_dir: ProjectsOpt = None,
    script: Annotated[
        str | None,
        typer.Option(
            "--script",
            help="Play this file, one input per line, then stop ('-' reads standard input).",
        ),
    ] = None,
    session: Annotated[
        str | None,
        typer.Option("--session", help="Resume this session id instead of starting one."),
    ] = None,
    wait: Annotated[
        float, typer.Option("--wait", help="Seconds to wait for the plan before giving up.")
    ] = 600.0,
    verbose: VerboseOpt = False,
) -> None:
    """Talk to the Context Engine: questions as numbered choices, then the Brief and the Plan."""
    from mde_cli.chat import ChatOptions, ChatSession, run_loop
    from mde_cli.common import Target, build_identity, open_api, read_script
    from mde_cli.console import Out
    from mde_cli.errors import EXIT_BAD_INPUT, CliError
    from mde_cli.strings import LANGUAGES

    if mode not in ("guided", "enterprise"):
        raise CliError("--mode must be guided or enterprise.", exit_code=EXIT_BAD_INPUT)
    if lang != "auto" and lang not in LANGUAGES:
        raise CliError(
            f"--lang must be auto or one of: {', '.join(LANGUAGES)}.", exit_code=EXIT_BAD_INPUT
        )
    identity = build_identity(tenant, user, roles)
    lines = read_script(script) if script is not None else None
    out = Out()
    target = Target(url, token, local, memory, projects_dir, verbose)
    options = ChatOptions(
        mode=mode, policy=policy, lang=lang, wait_s=wait, script=lines is not None, session=session
    )
    with open_api(target, identity) as (runner, client):
        if local:
            store = "in memory (nothing is saved)" if memory else "PostgreSQL"
            out.line(f"In-process API, mock mode (no key, no network). State: {store}.", "dim")
        code = run_loop(ChatSession(client, out, options), runner, lines)
    raise typer.Exit(code)


@app.command()
@friendly
def sim(
    url: UrlOpt = DEFAULT_URL,
    token: TokenOpt = None,
    tenant: TenantOpt = "sim",
    user: UserOpt = None,
    local: LocalOpt = False,
    memory: MemoryOpt = False,
    projects_dir: ProjectsOpt = None,
    personas: Annotated[
        Path | None,
        typer.Option(
            "--personas", help="Folder of persona files (default: evals/datasets/personas)."
        ),
    ] = None,
    persona: Annotated[
        str, typer.Option("--persona", help="A persona id (or its start, like P04), or all.")
    ] = "all",
    llm: Annotated[
        bool,
        typer.Option(
            "--llm",
            help="Let a model play the persona. Spends real money: refused unless live calls "
            "are switched on in the environment and the budget allows (see docs/packages/cli.md).",
        ),
    ] = False,
    max_turns: Annotated[
        int, typer.Option("--max-turns", help="With --llm: turns per persona.")
    ] = 14,
    wait: Annotated[
        float, typer.Option("--wait", help="Seconds to wait for each plan before giving up.")
    ] = 600.0,
    transcript: Annotated[
        bool, typer.Option("--transcript", help="Print every conversation as mde chat shows it.")
    ] = False,
    verbose: VerboseOpt = False,
) -> None:
    """Play personas (scripted users) against the API and print one result line each."""
    from mde_cli.common import Target, build_identity, open_api
    from mde_cli.console import Out
    from mde_cli.errors import EXIT_BAD_INPUT, EXIT_PROBLEM, CliError
    from mde_cli.persona import find_personas_dir, load_personas, select_personas
    from mde_cli.sim import SimOptions, render_runs, run_llm, run_scripted

    out = Out()
    gateway = None
    settings = None
    if llm:  # the money gate comes first: nothing else runs when it says no
        from mde_cli.llm_user import check_live_allowed
        from mde_core.settings import load_settings

        settings = load_settings()
        check_live_allowed(settings)
    chosen = select_personas(load_personas(find_personas_dir(personas)), persona)
    if not chosen:
        raise CliError(f"no persona matches {persona!r}.", exit_code=EXIT_BAD_INPUT)
    if llm:
        from mde_cli.llm_user import SIM_USER_SPEC
        from mde_llm_gateway import StaticRegistry, create_gateway

        assert settings is not None
        gateway = create_gateway(settings, StaticRegistry([SIM_USER_SPEC]))
    options = SimOptions(personas, persona, llm, max_turns, wait, transcript)
    identity = build_identity(tenant, user)
    target = Target(url, token, local, memory, projects_dir, verbose)
    with open_api(target, identity) as (runner, client):
        if llm:
            runs = runner.run(run_llm(client, chosen, options, out, gateway))
        else:
            runs = runner.run(run_scripted(client, chosen, options, out))
    out.lines(render_runs(runs, scripted=not llm))
    raise typer.Exit(0 if all(r.ok for r in runs) else EXIT_PROBLEM)


@app.command()
@friendly
def budget(
    scope: Annotated[
        str | None, typer.Option("--scope", help="Only scopes equal to or starting with this.")
    ] = None,
    as_json: Annotated[bool, typer.Option("--json", help="Machine-readable output.")] = False,
    ledger: Annotated[
        Path | None, typer.Option("--ledger", help="Ledger file (default: LLM_BUDGET_LEDGER).")
    ] = None,
) -> None:
    """Live LLM spend by scope, from the ledger (the same numbers as `just budget`)."""
    from mde_cli.diagnostics import run_budget

    raise typer.Exit(run_budget(scope, as_json, ledger))


@app.command()
@friendly
def doctor(
    live: Annotated[
        bool,
        typer.Option("--live", help="Also check the model (only in live mode)."),
    ] = False,
    url: Annotated[
        str | None, typer.Option("--url", help="Also check the Context API at this URL.")
    ] = None,
) -> None:
    """Which settings are present, whether the database answers (never prints a value)."""
    from mde_cli.console import Out
    from mde_cli.diagnostics import run_doctor

    raise typer.Exit(run_doctor(live=live, url=url, out=Out()))


SpecFile = Annotated[Path, typer.Argument(help="Path to an APP_SPEC.md.", show_default=False)]


@spec_app.command("simulate-consumer")
@friendly
def spec_simulate_consumer(
    file: SpecFile,
    budget: Annotated[
        int, typer.Option("--budget", help="Largest allowed slice, in estimated tokens.")
    ] = 10_000,
) -> None:
    """Walk the build sequence like the frontend engine: slice sizes and broken references."""
    from mde_cli.console import Out
    from mde_cli.specs import simulate_consumer_command

    raise typer.Exit(simulate_consumer_command(file, budget, Out()))


@spec_app.command("verify")
@friendly
def spec_verify(
    file: SpecFile,
    hash: Annotated[
        str | None,
        typer.Option("--hash", help="Also require this hash (the one an approval is bound to)."),
    ] = None,
) -> None:
    """Check that the embedded hash matches the file."""
    from mde_cli.console import Out
    from mde_cli.specs import verify_command

    raise typer.Exit(verify_command(file, hash, Out()))


@spec_app.command("show")
@friendly
def spec_show(
    file: SpecFile,
    screen: Annotated[
        str | None,
        typer.Option(
            "--screen", help="Show the slice of this screen (SCR-001), step (S3) or flow."
        ),
    ] = None,
    get: Annotated[
        list[str] | None, typer.Option("--get", help="Show the exact text of this id (repeatable).")
    ] = None,
) -> None:
    """Show the index of the file, or the slice of one screen."""
    from mde_cli.console import Out
    from mde_cli.specs import show_command

    raise typer.Exit(show_command(file, screen, list(get or []), Out()))


def run() -> None:
    """Console entry point (``mde``)."""
    from mde_cli.console import harden_stdio

    harden_stdio()
    app()


if __name__ == "__main__":  # pragma: no cover
    run()
