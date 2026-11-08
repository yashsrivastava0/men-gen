# Subagent protocol (read this first)

Every subagent on the Context Engine job follows this protocol. The lead (the main session) gives you a mission, owned paths and a definition of done in the prompt; this file holds the rules that are the same for everyone.

## 1. Where you work

- Main checkout (read-only for you): `C:\Users\<user>\Desktop\01_Projects\Mobigen` (branch `main`, tag `contracts-v1`).
- **Your worktree** is `C:\Users\<user>\Desktop\01_Projects\wt-<name>` on branch `feat/<name>` (created for you). Work **only** there. Never edit the main checkout or another worktree. Every path you write must be one you own (listed in your prompt).
- Start every session with:
  ```powershell
  Set-Location "C:\Users\<user>\Desktop\01_Projects\wt-<name>"
  $env:Path += ";$env:USERPROFILE\.local\bin"
  uv sync --all-packages
  ```
  Each worktree has its own `.venv`. `just` is installed (`just --list`), but plain `uv run ...` commands are fine and cheaper.

## 2. Read first, in this order

1. `CLAUDE.md` (root hard rules), your agent definition `.claude/agents/<role>.md`, your package's nested `CLAUDE.md`.
2. The sections of `docs/implementation/BRIEF.md` and `docs/product/context-engine-spec.md` named in your prompt (the brief wins on *how*, the spec on *what*).
3. `docs/implementation/DECISIONS.md` (decisions D-001..D-022 already taken).
4. The contract modules you need in `packages/contracts/src/mde_contracts/` (frozen; see `.claude/rules/contracts.md`).

## 3. Hard rules

1. Never read, print, log or copy `.env` or any API key. There is no `.env` in your worktree; keep it that way.
2. **No live LLM calls, ever.** You work in mock or replay mode only. The root `conftest.py` forces `LLM_MODE=mock` and `MDE_FORBID_LIVE=1`. Only the lead runs live batches.
3. Only `mde_llm_gateway` imports `openai`. No agent frameworks (LangChain, LangGraph, LlamaIndex, CrewAI, AutoGen, Semantic Kernel, DSPy, instructor, OpenAI Agents SDK). No SQLite anywhere (Postgres only). No `eval`/`exec` on user or model text. Content that varies by archetype/company/policy is data, never hardcoded in Python.
4. Respect the layering enforced by `uv run lint-imports` (contracts/core at the bottom; engines never import each other; `flow` wires them through contracts ports).
5. Money is integer micro-dollars, timestamps are timezone-aware UTC, ids are prefixed ULIDs (`mde_core.ids.new_id`). Frozen Pydantic models where practical. No `Any` in public signatures.
6. Do **not** ask the user or the lead questions. When something is ambiguous, pick the most reasonable option and list it under "decisions made" in your report.
7. Markdown files only under `docs/**` (and `README.md`, `CLAUDE.md`, `AGENTS.md`, `.claude/**`). Prompt templates are `.jinja`.

## 4. Contracts: you do not edit them

`mde_contracts` is frozen at v1. If you need a change (a new optional field, a new model, a protocol method), **do not edit the package**: write the exact change in your report ("Requested contract changes") and work around it locally in the meantime (a small adapter in your own package). The lead applies additive changes and tells you.

## 5. Shared files you must not edit

Root `pyproject.toml`, `justfile`, `.importlinter`, `conftest.py`, `CLAUDE.md`, `docs/README.md`, `docs/implementation/{DECISIONS,PROGRESS}.md`, anything in `packages/contracts`, `packages/core`, other people's packages. You **may** edit your own package's `pyproject.toml` (third-party dependencies only from the allowed stack: pydantic, pydantic-settings, structlog, httpx, openai (gateway only), sqlalchemy, alembic, psycopg, fastapi, uvicorn, typer, rich, pyyaml, jinja2, hypothesis, pytest*, ruff, pyright, import-linter, anyio, gitpython-free: use `git` via subprocess). **Do not commit `uv.lock`** (`git add -A ':!uv.lock'`; if uv rewrote it, `git checkout -- uv.lock`); the lead regenerates it after merging.

## 6. Machine etiquette (about 3 GB of RAM is free for five agents)

- No background processes, no dev servers left running, no `just ci` or whole-repo pyright. Scope everything to your paths:
  `uv run pytest packages/<x> -q`, `uv run ruff check packages/<x>`, `uv run ruff format packages/<x>`, `uv run pyright packages/<x>`, `uv run lint-imports`.
- PowerShell is the primary shell; Git Bash exists. Use the Write/Edit tools for files with Windows paths (heredocs eat backslashes).
- A formatter hook (ruff format) rewrites `.py` files after you edit them. If an Edit fails with "string not found", Read the file again first.
- PostgreSQL (only persistence/integration work): `uv run python scripts/dev_db.py up` gives a private server at `127.0.0.1:54329` (trust auth). Never use port 5432 (other projects), never touch Docker. Tests create an ephemeral database per session; never write to database `mde` from tests.
- Multi-process tests: put the worker function in a real package module (see `mde_core.testing`), because `--import-mode=importlib` test modules cannot be imported by spawned children (D-017).

## 7. Testing and quality bar

- Write the test first for subtle behavior. Tests are deterministic (fake clock, seeded ids, no network, no wall-clock sleeping). Offline unless marked `live` (you never run those).
- Mark DB tests `@pytest.mark.integration`; end-to-end `@pytest.mark.e2e`.
- Before reporting: ruff check + format clean, pyright clean on your package (strict for contracts/core/llm-gateway/flow/spec-loader `src`), `uv run lint-imports` kept, your tests green, coverage at least 85% on the targets in your prompt (`uv run pytest packages/<x> --cov=packages/<x>/src --cov-report=term-missing -q`).

## 8. Commits

Commit small and often on your branch with a short prefix: `git add -A ':!uv.lock'; git commit -m "<area>: <what>"`. End the message with a blank line and:

`Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`

Do not merge, rebase onto `main`, push, tag or delete branches. The lead merges.

## 9. Final report (your last message, under 450 words)

1. **Branch and final commit hash.**
2. **What changed** (modules, public interfaces, files).
3. **Tests run and results** (exact commands, pass counts, coverage numbers, pyright/ruff/lint-imports status).
4. **Open issues / known gaps.**
5. **Decisions made** (one line each: decision, reason).
6. **Requested contract or shared-file changes** (exact text).
7. **Docs written** (paths).

## 10. Temporary checkouts: create, use, remove

Every extra checkout (an agent's worktree, a throwaway clean-checkout for a CI run) is temporary. The rules, from the project owner's request and the mistakes that led to them:

1. **Use git worktrees, never `git clone`, for temporary checkouts.** An agent gets a named worktree (`git worktree add ../wt-<name> -b feat/<name>`); a throwaway verification gets a detached one (`git worktree add --detach ../tmp-<name> HEAD`; it contains only tracked files, so it is a faithful clean checkout). Never use the harness's automatic worktree option for agents: it builds the worktree from the repository the session started in (the home directory), not from this project.
2. **One known place.** They live beside the main checkout in `01_Projects` with a `wt-` or `tmp-` prefix, so `git worktree list` shows all of them.
3. **Remove them when the task is done, not later.** Right after the work is merged and the merge result is verified on `main` (D-063), and only if the worktree is clean (`git status --porcelain`, ignoring `uv.lock`) and its branch is merged (`git branch --merged main`), remove it with `git worktree remove --force <path>` and then `git worktree prune`. A throwaway verification worktree is removed in the same command that reads its result.
4. **Never delete a checkout by hand** (no manual recursive delete): `git worktree remove` is the supported way, and the tooling protects paths such as `.venv`. If a removal is blocked, do not work around it: report the exact path and the one command for the owner to run.
5. **End-of-job sweep.** Before the final report, `git worktree list` must show only the main checkout. Merged `feat/*` branches may then be deleted with `git branch -d` (it refuses unmerged ones).
6. A worktree that still has unmerged commits or uncommitted work is never removed: merge it, or write down why it stays.
