# Runbook: run it locally (Windows first)

Audience: anyone who wants to talk to the Context Engine on their own machine. Everything below runs in **mock mode**: no OpenAI key, no network, no cost. Live calls are the lead's and the user's business ([live-llm-budget.md](live-llm-budget.md)). When something fails, start with [troubleshooting.md](troubleshooting.md).

## The five commands

Run them from the repository root, in PowerShell (the primary shell here):

```powershell
$env:Path += ";$env:USERPROFILE\.local\bin"   # once per shell: puts uv and just on the path
uv sync --all-packages                         # 1. install every package (a venv per worktree)
uv run python scripts/dev_db.py up             # 2. start the private PostgreSQL on port 54329
just migrate                                   # 3. create the tables (Alembic, append-only migrations)
uv run mde chat --local                        # 4. talk to it: the API runs inside the CLI
just dev                                       # 5. or run the API for real (terminal 1) ...
uv run mde chat                                #    ... and talk to it over HTTP (terminal 2)
```

Steps 2 and 3 are needed for the database (`just db-up` is the same as step 2). For a throwaway demo with **no database at all**: `uv run mde chat --local --memory` (nothing is saved; the hand-off repository is still written). `just dev` serves the API on port 8100 with its job worker inside; `just worker` (`python -m mde_context_api.worker`, with `WORKER_ENABLED=0` for the API) runs the worker as a separate process; `just gateway-dev` serves the gateway API on port 8101 ([llm-gateway-api](../packages/llm-gateway-api.md)).

What to expect: the greeting, then questions as numbered choices (`1`, `2`, `r` for the suggested answer, `d` you decide, `s` skip), the Brief page, `1` to approve, the plan building with one-line progress, the Plan page, `1`, and `Spec ready.` with the path, version, hash and git commit of the hand-off repository (`data/projects/<tenant>/<app_id>/APP_SPEC.md`). The full guide to the commands: [../packages/cli.md](../packages/cli.md).

Useful variations:

```powershell
uv run mde chat --local --memory --script journey.txt     # a scripted conversation, no terminal needed
uv run mde sim --local --memory                           # all ten personas, in process (a few minutes)
uv run mde chat --mode enterprise --policy enterprise-it-services-firm --local
uv run mde chat --session ses_01...                       # resume (needs PostgreSQL, not --memory)
uv run mde doctor --url http://127.0.0.1:8100             # settings, database, is the API ready
uv run mde budget                                         # live spend (zero until the lead runs a live batch)
```

A script is one input per line; `r*` answers every remaining question with the suggested option. The shortest full journey:

```
An online shop for handmade candles
r*
1
1
```

## Windows notes

* **PowerShell versus Git Bash.** `just` runs its recipes with PowerShell, so recipes are plain `uv run ...` lines. The Bash tool of Claude Code is Git Bash: use it for POSIX scripts only. A bare `bash` typed inside PowerShell is the **WSL launcher**: avoid it. Files that contain Windows paths are easier to write with an editor than with a shell heredoc (heredocs eat backslashes).
* **The selector event loop.** psycopg's async mode cannot run on Windows' default `ProactorEventLoop` (D-042). Always start the API through its module entry point: `just dev` is `uv run python -m mde_context_api --port 8100`, which sets the selector policy before any loop exists and hands uvicorn a selector loop factory. `uvicorn mde_context_api.main:app` imports fine but leaves uvicorn on the Proactor loop, so database calls fail. `mde chat --local` and `mde sim --local` create a `SelectorEventLoop` themselves; nothing for you to do.
* **PostgreSQL is a private cluster on port 54329** (data in `data/pg` of the main checkout, user and password `mde`, URL `postgresql+psycopg://mde:mde@127.0.0.1:54329/mde`). **Never use port 5432**: that is the server of the user's other projects. `uv run python scripts/dev_db.py up|down|status|reset`. Tests create their own ephemeral database and never touch `mde`.
* **Docker Desktop is paused on purpose** and holds other projects' containers: nothing here needs it, so never start, stop or restart it, and never run `wsl --shutdown`.
* **Little free RAM** (about 3 GB). Run one thing at a time: no background servers left running, no whole-repo pyright. `Ctrl+C` stops `just dev`.
* **Console encoding.** Hindi and Hinglish text prints correctly in Windows Terminal; in an older console or a pipe the CLI replaces any character the console cannot encode instead of crashing. For clean UTF-8 in a pipe: `$env:PYTHONUTF8 = "1"`.
* **`.env`** is optional and never needed for mock mode. Never open or paste it; `uv run mde doctor` shows which names are set without showing a value.

## When something is wrong

| Symptom | Cause and fix |
| --- | --- |
| `error: Cannot reach the local PostgreSQL` (with `--local`) | the private server is down: `uv run python scripts/dev_db.py up`, then `just migrate`; or add `--memory` |
| `error: The database has no tables yet.` | run `just migrate` |
| `error: Cannot connect to the API at http://127.0.0.1:8100.` | `just dev` is not running (start it in another terminal), the port differs (`--url`), or use `--local` |
| `error: The API did not accept the credentials.` | `--token` (or `API_DEV_TOKEN`) differs from the server's, or `--tenant` / `--user` / `--roles` hold characters the API refuses |
| `error: ... not found` when resuming | the session belongs to another user or tenant, or it lived in a `--memory` run |
| the plan seems stuck | `Ctrl+C` stops watching, `/wait` watches again, `stop` cancels the run. With `just dev` the job worker runs inside the API (`WORKER_ENABLED=1`, the default) |
| `unexpected problem (SomeError)` | a bug: run again with `$env:MDE_DEBUG = "1"` to see the trace, and report it |
| `mde` is not found | use `uv run mde ...` (the venv is not on your path), or `uv run python -m mde_cli ...` |

Related: [troubleshooting.md](troubleshooting.md) (the longer list: database, tests, ledger, worktrees), [../packages/context-api.md](../packages/context-api.md) (the service, its configuration and entry points), [../specs/events-and-api.md](../specs/events-and-api.md) (the wire contract).
