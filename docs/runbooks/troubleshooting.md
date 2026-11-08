# Runbook: troubleshooting

Failure modes that actually happened on this project, with the cause and the fix. Commands are PowerShell, from the repository root, and none of them needs an API key or Docker. For running the product see [local-dev.md](local-dev.md); for live spending see [live-llm-budget.md](live-llm-budget.md).

**First five minutes** (all read-only):

```powershell
$env:Path += ";$env:USERPROFILE\.local\bin"      # puts uv and just on the path (once per shell)
uv run python scripts/dev_db.py status           # is the private Postgres up? (127.0.0.1:54329)
uv run mde doctor                                # which settings are set (names only), does the database answer
just budget                                      # live spend by scope; zero or tiny is normal
git status --short                               # what is changed here
```

## 1. Machine and database

| Symptom | Cause | Fix |
| --- | --- | --- |
| `PostgreSQL is not reachable at ... Integration tests never skip: start it with: just db-up` | the private cluster is down | `just db-up`, then `just migrate`. Check with `uv run python scripts/dev_db.py status` |
| `dev_db.py up` prints `docker unavailable or paused (left untouched); using local binaries` | **normal.** Docker Desktop is paused on purpose and holds other projects' containers ([ADR 0008](../adr/0008-private-postgres-and-docker-paused.md)) | nothing to fix. Never unpause, start or restart Docker, and never run `wsl --shutdown` |
| `No PostgreSQL available: install Docker, PostgreSQL 16+, or uv pip install pgserver` | the script found no `pg_ctl`. It looks at `MDE_PG_BIN`, then `pg_ctl` on `PATH`, then `C:\Program Files\PostgreSQL\*\bin` | install PostgreSQL 16 or newer, or point `MDE_PG_BIN` at its `bin` folder |
| something talks to port **5432** | that server belongs to the user's other projects | never use it. The default URL is `postgresql+psycopg://mde:mde@127.0.0.1:54329/mde`; only the private cluster listens on 54329 (`MDE_PG_PORT` changes the port the script uses, rarely needed) |
| `error: The database has no tables yet.` | migrations not applied | `just migrate` |
| `InterfaceError: Psycopg cannot use the 'ProactorEventLoop' to run in async mode` | Windows' default event loop cannot run psycopg's async mode (D-042). The code path started a loop without the selector policy, typically `uvicorn mde_context_api.main:app` run directly | start the API with `just dev` (`python -m mde_context_api`). In a new script call `mde_persistence.db.ensure_selector_loop_policy()` **before** any loop exists (`create_engine` and the pytest plugin already do) |
| `just db-reset` seems tempting | it deletes `data/pg` of the **main checkout**, so every worktree's `mde` database goes too | use it only for a corrupt cluster and only when no one else is mid-test; otherwise `just db-down` then `just db-up` |
| shells or background jobs die, the machine crawls | about 3 GB of RAM is free | one heavy thing at a time; no whole-repo pyright (scope to your paths); no servers left running; at most four or five agents. `apps/cli` tests take about 3.5 minutes: that is not a hang |
| `just` or `uv` not found | `uv tool install rust-just` put them in `%USERPROFILE%\.local\bin` | add that folder to `PATH` for the shell (first line above) |
| a heredoc in the Bash tool lost its backslashes; `bash` inside PowerShell behaves oddly | heredocs eat backslashes; a bare `bash` in PowerShell is the **WSL launcher** | write files with Windows paths with an editor tool, and use Git Bash only for POSIX scripts |

## 2. Tests

| Symptom | Cause | Fix |
| --- | --- | --- |
| an `integration` test fails at start with the Postgres message above | those tests need the server; they fail rather than skip so a green run means something | `just db-up` first (`just test-int` and `just test-e2e` do it for you) |
| `[XPASS(strict)]` | a **stale strict-xfail pin**: a test pinned a known bug (`xfail(strict=True)`, or a scenario file's `known_issue:`), the bug was fixed, and the pin was not removed | remove the `xfail` mark or the `known_issue:` key in the same commit as the fix. A pin left behind fails the suite; a marker edit that is never committed leaves stale tests on `main` (D-063) |
| `ModuleNotFoundError: No module named 'test_...'` inside a spawned worker | pytest runs with `--import-mode=importlib`, so test modules cannot be imported by child processes (D-017) | put the worker function in a real package module, as `mde_core.testing` does |
| warnings or log assertions vanish only in a full run | an earlier test changed global state (log level, environment, working directory) and did not restore it (D-038) | restore it in a fixture (module scope plus `structlog.reset_defaults()`); tests must leave nothing behind |
| the secret scan fails (`key-like text found`, names only, never values) | a key-like string in a tracked or unignored file, `docs/` and cassettes included | replace it with a placeholder. A deliberate fixture carries `secret-scan: allow` on the **same line**; if the formatter moves the trailing comment, assign the fixture to a variable first |
| an Edit fails with "string not found" after you did not change the file | the formatter hook rewrote the `.py` file | Read the file again, then edit |
| a live test is skipped with `ALLOW_LIVE_LLM=1 must be set in the outer environment` | **expected**: live tests need the second key and are not for subagents | nothing to fix |
| `docs-check: git was not found on PATH` | the scripts resolve `git` from `PATH` only and ignore empty or relative entries (a planted `git.exe` must never run) | put an absolute Git folder on `PATH` |

## 3. Mock, replay and live

| `LLM_MODE` | Needs | Cost | When you see it |
| --- | --- | --- | --- |
| `mock` (default) | nothing | none | all offline work; every call spec ships a deterministic handler |
| `replay` | a cassette per rendered request | none | free reruns of calls recorded live |
| `live` | `ALLOW_LIVE_LLM=1`, a key, no `MDE_FORBID_LIVE` | metered against USD 6.00 | only the lead's or operator's batches |

* **`CassetteMissing: no cassette for <spec> v<n> (key ...)`**: replay never goes live. A cassette goes stale whenever the rendered prompt, model, tools, effort or schema changes, so a prompt edit makes every older cassette a miss. Switch to mock for development; re-record only in a live batch. `CASSETTE_DIR` (default `tests/cassettes`) is anchored to the **main checkout**, so a worktree replays only cassettes already in main. Never edit a cassette by hand.
* **`LiveCallForbidden`**: one of the three switches is wrong (`LLM_MODE`, `ALLOW_LIVE_LLM`, `MDE_FORBID_LIVE`). In tests this is by design.
* **`UnknownModelPrice`**: the model or tool has no price in `pricing.yaml`. Do not guess a price; stay in mock or replay.
* **`mde sim --llm` exits 3**: refused on purpose (it needs both switches and a budget below the 80% tripwire).

## 4. The ledger and the live-call guard

**`LedgerIntegrityError` / `ledger_integrity` / HTTP 503, or `/readyz` says `ledger: failed`.** The ledger (`data/.budget/ledger.jsonl` in the main checkout) contradicts its guard file (`ledger.jsonl.guard`): it was deleted, truncated, edited or replaced, or the guard is unreadable. Live calls refuse rather than hand out a fresh USD 6. Do **not** reset to "get budget back": the real spend is on the OpenAI invoice. A deliberate reset is the lead's or operator's call, in this order:

1. Stop every process that uses the ledger (the API, the worker, any CLI).
2. Copy `ledger.jsonl` and `ledger.jsonl.guard` somewhere safe.
3. Reconcile the spend with the OpenAI dashboard; write the carried-over total in `docs/implementation/PROGRESS.md`.
4. Reset on purpose: `uv run python -m mde_llm_gateway.budget_cli init --force` (or `$env:MDE_BUDGET_LEDGER_RESET = "1"` for the next start only). `init` without `--force` only creates a missing ledger and refuses when the guard says one was deleted.
5. `just budget` to confirm.

**`Blocked by .claude/hooks/guard_live.py`.** The hook stops a shell command that would turn live mode on (assigning the live value to the mode variable, running a live `just` recipe or the live batch runner) unless `ALLOW_LIVE_LLM=1` is **already in the environment**.
* If you meant to run something live: that is not yours to do. Only the lead or operator runs live batches, and adding the second key is their decision (D-069; [the runbook](live-llm-budget.md)). A subagent stays in mock or replay.
* If you only *mentioned* those words (a heredoc, a commit message, an `echo`): the hook matches command **text**, so a command that merely quotes them is blocked too (D-056). Put such text in a file with the editor's Write or Edit tool, which the hook does not inspect, and keep it out of shell commands. Do not try respellings: the hook normalises quoting and obfuscation, and a guard that is easy to dodge is no guard.

**`BudgetExceeded`.** Read `details`: a `scope` means that scope's cap, a `tripwire` means 80% or 95% of the whole budget, `reason: unknown_scope_kind` means a scope kind that is not allowed. Engines fall back to deterministic output; they never retry. In a session the flow parks it in PAUSED with a `budget_cap_reached` notice and the owner or an approver resumes it ([flow-and-gates](../architecture/flow-and-gates.md)). A reservation left by a crashed process counts as **spent** after ten minutes (the ledger may run ahead of the invoice; reconcile against the dashboard).

## 5. Git, worktrees and `uv.lock`

* **`uv.lock`.** `uv sync` may rewrite it in a worktree. Never commit it from a worktree: stage with `git add -A ':!uv.lock'`, and if uv rewrote it run `git checkout -- uv.lock`. The lead regenerates it after merging.
* **Every worktree has its own `.venv`.** Run `uv sync --all-packages` first (`$env:Path += ";$env:USERPROFILE\.local\bin"` before it). The main checkout is read-only for agents; shared data (ledger, cassettes, hand-off repos, the Postgres data directory) lives in the main checkout on purpose.
* **Commit before merging, and check before removing.** Marker edits made in a side worktree but never committed were lost when the worktree was removed with `--force`, and `main` carried four stale tests (D-063). Before `git worktree remove <path>`: `git -C <path> status --short` must be empty and `git log main..<branch>` must be merged. Never use `--force` to make a dirty worktree go away. Validate a merge on `main`, not in the side worktree.
* **Conflict markers.** Run `git grep -n '^<<<<<<<'` before committing a merge. Do not chain `git commit` after a script whose failure matters (D-067: a leftover marker was committed that way).
* **Hand-off fails** (`handoff_failed`, 503, nothing recorded): git is not on `PATH`; `tenant_id` or `app_id` is not a plain name; a symlink or junction sits inside `PROJECTS_DIR/<tenant>/<app_id>`; or the same version was committed earlier with another hash (`Conflict`). Retrying the same `(version, hash)` is safe.

## 6. Running the product

The table in [local-dev.md](local-dev.md#when-something-is-wrong) covers `mde chat` errors (database down, no tables, API not reachable, rejected credentials, a plan that seems stuck). Two more:

* `/readyz` answers `503` and `failed`: the database or the ledger (the reason is only in the server log, never in the response). Run `uv run mde doctor` and `just budget`, then see sections 1 and 4.
* The session says it paused because the budget is used up: that is the budget gate, not a crash. See section 4; the numbers are behind the token at `GET /v1/budget`.
