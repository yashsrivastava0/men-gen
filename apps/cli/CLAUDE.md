# mde_cli

**Job:** the `mde` terminal interface (typer) over the context API. There is no chat UI in this project; the CLI and the OpenAPI-documented API are the interface. Guide: [docs/packages/cli.md](../../docs/packages/cli.md); how to run it: [docs/runbooks/local-dev.md](../../docs/runbooks/local-dev.md).

**Commands:** `mde chat` (interactive or `--script`; cards as numbered choices; the two gates as pages; `--local` runs the API in process in mock mode, `--memory` without a database), `mde sim` (persona simulator: scripted by default, `--llm` only when live is allowed and budgeted), `mde budget` (live spend by scope, same as `just budget`), `mde doctor` (settings present or not, database, API, model in live mode; **never prints values**), `mde spec simulate-consumer|verify|show <file>`. Run: `uv run mde chat --local`.

**Test:** `uv run pytest apps/cli` (about 3.5 minutes; `-m "not integration"` skips the PostgreSQL tests).

**Conventions**
- Works fully offline in mock mode; the CLI itself never calls a model. The only path that could is `mde sim --llm`, fenced by `llm_user.check_live_allowed` (both switches in the environment, the gateway's live check, the budget below the 80% tripwire). Never run it.
- Never echo `.env` values, API keys or the dev token; a pasted secret is masked in the script echo.
- A failure is one `error:` line and an exit code (0 ok, 1 problem, 2 bad input, 3 refused), never a stack trace; `MDE_DEBUG=1` re-raises.
- The client has its own wire models and never imports the API package; `local.py` is the one module that loads `mde_context_api` (by name, because `.importlinter` makes the two siblings).
- The chat is a plain loop on the main thread over one persistent `SelectorEventLoop` (`runtime.py`); do not wrap it in `asyncio.run`.
- The persona player (`persona.py`) has the vocabulary of `evals/harness/persona.py` but never imports `evals`.
- Test modules cannot import each other (D-017): shared fixtures live in `tests/conftest.py`.
