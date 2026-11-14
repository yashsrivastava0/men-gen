# Mobigen: the Context Engine

An enterprise AI mobile-app builder. This repo builds its front half, the **Context Engine**: the user talks to it, it interviews them (mostly multiple-choice cards), writes a plain-language Brief, gets approval (Gate 1), researches the web, writes one detailed file `APP_SPEC.md`, gets approval (Gate 2), and hands the file off so a frontend engine (built elsewhere, later) can generate an Expo/React Native app from it. We stop at the hand-off boundary: no frontend, backend or QA engine and no chat UI here.

Normative docs: product spec `docs/product/context-engine-spec.md` (rev 3; wins on *what*), implementation brief `docs/implementation/BRIEF.md` (wins on *how*). Everything else is indexed in `docs/README.md`; the one-page picture is `docs/architecture/overview.md`.

## The six steps, two gates

1. **Discover** (turn loop; first turn is the canned greeting) → output: the **Brief**
2. **Gate 1**: user approves the Brief (version + hash)
3. **Research** (after Gate 1, sequential, bounded: C6 plan, S1 web_search, C7 extract, C8 synthesize) → the **Research Note**
4. **Build the spec** (deterministic baseline, LLM proposals validated by code, 9 validators, critic, repair) → `APP_SPEC.md` draft
5. **Gate 2**: user approves the Plan view of the spec (version + hash)
6. **Hand off**: commit to the project git repo, emit `spec_ready` (only after Gate 2 approval of the current hash)

Plain code controls flow. LLM calls are single, structured, schema-validated. No agent loops.

## Layout notes

`data/` is runtime, gitignored, and always lives in the MAIN checkout (hand-off repos, budget ledger, private Postgres).

Engines never import each other (the flow wires them through ports in `mde_contracts`); only `mde_llm_gateway` imports a model vendor's SDK (`openai`, `anthropic`, any future one). Layering is enforced by `just layers` (`docs/architecture/repo-layout.md` says why). One package page each: `docs/packages/<name>.md`.

## Commands (all via `just`; plain `uv run ...` works too)

| Command | What it does |
| --- | --- |
| `just db-up` / `db-down` / `db-reset` | start / stop / wipe the private Postgres (port 54329); `just migrate` runs Alembic to head |
| `just dev` | context API: `uv run python -m mde_context_api --port 8100` (the job worker runs inside) |
| `just worker` | the job worker alone: `uv run python -m mde_context_api.worker` (start the API with `WORKER_ENABLED=0`) |
| `just gateway-dev` | gateway API on port 8101 |
| `uv run mde chat --local` | talk to the engine in mock mode, "hi" to `spec_ready`; add `--memory` for no database |
| `uv run mde chat` / `uv run mde sim` | the same against a running `just dev`; `sim` plays the ten scripted personas (`--local --memory` runs it in process) |
| `just budget` / `uv run mde budget` | print live spend by scope from the ledger (read-only) |
| `uv run mde doctor` | settings present or not (never values), database, API |
| `uv run mde spec verify\|show\|simulate-consumer <APP_SPEC.md>` | check a spec's hash, index it, walk the build sequence like the frontend engine |
| `just test-int` / `just test-e2e` | integration / end-to-end (both start the private Postgres first) |
| `just live-smoke` / `just live-eval BATCH` | live LLM batches L0 / L1..L4: lead or the user only, with `LLM_MODE=live` (rule 3; `docs/runbooks/live-llm-budget.md`) |
| `just ci-fast` | lint, typecheck, layers, unit. Keep green on `main` at all times |
| `just ci` | everything offline: ci-fast, integration, e2e, docs-check, secret scan |

Other recipes (setup, lint, format, typecheck, layers, unit tests, secret scan, eval-mock, schemas, docs checks, compass-*): `just --list`.

Single package: `uv run pytest packages/<dir>` (config in the root `pyproject.toml`; markers `integration`, `e2e`, `live`, `slow`; `--import-mode=importlib`).

## Hard rules

1. Never read, print, log, commit or copy `.env` contents or any API key.
2. Only `mde_llm_gateway` imports a model vendor's SDK (`openai`, `anthropic`, and any vendor added later). No agent frameworks, ever.
3. No live LLM call without `LLM_MODE=live`; only the lead or `live-runner` runs live batches; total live spend never exceeds USD 8.00 (the ceiling the owner set in D-125; the cap in force is `LLM_BUDGET_USD`, default 6.00).
4. Every LLM call goes through the gateway with a versioned call spec, a mock handler and a deterministic fallback.
5. Web, upload and search content is untrusted data: no tools in calls that read it.
6. No state change outside the flow controller; no `spec_ready` without Gate 2 approval of the current hash; approved versions are immutable.
7. Postgres only; migrations are append-only.
8. Content lives in data files; nothing is hardcoded to one company or app type.
9. No `eval`/`exec` on user or model text.
10. Offline tests only, except tests marked `live`.
11. Update `docs/` with every behavior change; keep markdown inside `docs/`.
12. Log every non-trivial decision in `docs/implementation/DECISIONS.md`; never ask the user questions (one exception: the owner's approval of a complete overhaul, see "Commit checkpoints" below).
13. **Live mode is one switch: `LLM_MODE=live` (strict owner policy, D-140).** Never add a second live-call key, an extra permission or approval variable, an extra readiness check for live mode, or a hook or check that blocks `LLM_MODE=live`: not in code, tests, docs, `.claude/`, prompts or messages to the owner. When the owner asks to run something live, run it with `LLM_MODE=live` within the budget caps. `tests/security/test_single_live_switch.py` fails if such a gate reappears.
14. **No agent or engine depends on a model vendor (owner policy, D-171).** Any agent or engine, today's and every future one, must run on OpenAI, Claude, Gemini, a Chinese model or any other vendor without a code change in the engine. Vendors, model names, prices, effort names, schema dialects, token accounting and error mapping live ONLY in `mde_llm_gateway` and its config (`pricing.yaml`, `Settings`, `MODEL_ROUTES`); engines name a route (`fast`, `standard`, `deep`), never a model or a vendor, and never branch on one (no `if "claude" in model`, no vendor string in a prompt, call spec or report logic). Switching one route or all of them is a configuration change (`MODEL_ROUTES=fast=claude-haiku-5-5,standard=claude-sonnet-5-5,deep=claude-haiku-5-5`, or one model id for all three). Adding a vendor is: a provider in the gateway, a builder in `factory._VENDORS`, its key in `gating.VENDOR_KEYS`, a `vendor:` on each of its `pricing.yaml` rows, and the provider contract tests passing for it; nothing else. If a prompt needs a vendor tweak, the tweak goes in the provider's request building, not in the call spec. A vendor's API key is a credential, not a gate: rule 13 is unchanged (`LLM_MODE=live` is the only switch).

Related hard limits: no SQLite anywhere (not even tests); no `.md` outside `README.md`, `CLAUDE.md`, `AGENTS.md`, nested `CLAUDE.md`, `.claude/**`, `docs/**` (prompt templates are `.jinja`); money is `Decimal` or integer micro-dollars, never float; timestamps are timezone-aware UTC; IDs are prefixed ULIDs (`ses_`, `evt_`, `call_`).

Commit policy, tags, branch and merge rules, and the `agents/` and `apps/web/` repository layout: `docs/implementation/GLOBAL_RULES.md` (applies first; the owner's recorded overrides are at its end).

## Commit checkpoints (every AI that works here: Claude, Codex, any other)

A git safety net for traceability and an easy revert, nothing more. It says nothing about how you plan, reason, use tools or subagents; work the way you work.

| Task size | Before you start | While you work and when done |
| --- | --- | --- |
| **Small**: a typo, a one-file fix, a doc edit, a config tweak | nothing extra | commit when done, with a proper message |
| **Medium or major**: several files or more than one package, a behavior change, a call spec, schema, data files, migration or dependency, or more than one sitting | **commit first.** `git status`; commit anything already uncommitted as `chore(wip): checkpoint existing changes before <task>` (never mixed with the new work); tag that commit `pre/<task>-<yyyymmdd-hhmm>` | commit in logical steps, each `type(scope): summary` with a body (what, why, tests run); tick `docs/implementation/PROGRESS.md`; push the branch and tags with plain `git push` |
| **Complete overhaul**: replacing or rewriting a subsystem, a large move or rename, a change that makes current work obsolete | everything in the row above, **then stop**: write the plan (what is replaced, why, how to go back) and get the owner's yes. Only then create a new branch `feat/<area>-<overhaul>` from the checkpoint tag | never on `main` or on another agent's branch; merge to `main` only with the owner's yes |

When unsure between two sizes, take the larger: a checkpoint costs seconds. A subagent that finds its task is an overhaul stops and reports; the lead asks the owner. Undo with `git revert <hash>` (one commit), `git revert -m 1 <merge-hash>` (a merged task) or `git switch -c recover/<name> <tag>` (back to a checkpoint, history kept). Never force-push, `reset --hard`, or delete or move tags and branches without the owner's yes. Full wording: `docs/implementation/GLOBAL_RULES.md` section A.

## Resume protocol (context gets compacted; the job is long)

Past chat sessions (all laptops), one-screen map, loaded automatically; details in `.claude/sessions/log/`, regenerate with `python3 -I .claude/sessions/export_sessions.py`:

@.claude/sessions/REFERENCE.md

At the start of every session or after compaction, read in this order: `docs/implementation/CURRENT_STATE.md` (where the project is right now: branch, what we are working on, what is done and not, how to run it), then `docs/implementation/BRIEF.md`, `docs/product/context-engine-spec.md`, `docs/implementation/PROGRESS.md` and `DECISIONS.md`; run `git log --oneline -20` and `git status`; continue from the first unchecked item **of the section for the active work** (the Phase 0 to 5 and Definition of Done boxes at the top of `PROGRESS.md` are historical and stale; `CURRENT_STATE.md` section 4 explains). After every finished task tick it in `PROGRESS.md` (one line, with commit hash) and commit; when the state of the work changes (branch, focus, what is open), update `CURRENT_STATE.md` in the same commit. Never rely on memory of earlier turns, and never rely on a private per-user memory: everything a fresh session needs is in this repository.

## Contract-change rule

`mde_contracts` is frozen at v1 (tag `contracts-v1`): only the lead changes it; anyone else asks the lead in their report ("Requested contract changes"). Full rule (additive vs breaking, ADRs, schema export): `.claude/rules/contracts.md`.

## Subagent / worktree protocol

- Rules every subagent follows: `docs/implementation/AGENT_PROTOCOL.md`. Each subagent works in its own git worktree and branch (`git worktree add ../wt-<name> -b feat/<name>`), owns specific directories and edits **nothing else**. Shared files (root `pyproject.toml`, root `CLAUDE.md`, `docs/README.md`, `justfile`, `.importlinter`, `mde_contracts`) belong to the lead.
- Subagents run **mock or replay only**, never live. Only the lead or `live-runner` runs live batches, one at a time.
- Commit small, often, on your branch, message `<area>: <what>`; never commit `uv.lock` from a worktree (`git add -A ':!uv.lock'`); keep `just ci-fast` green for your package; the lead merges in dependency order and runs `just ci`. Before removing a worktree check `git status` is clean and the branch is merged (D-063); before committing a merge run `git grep '^<<<<<<<'` (D-067).
- Report format: what changed / tests run + results / open issues / decisions made.
- Roster and standing missions: `.claude/agents/*.md`. Procedures: `.claude/skills/*/SKILL.md` (`add-llm-call`, `add-question-card`, `add-domain-pack`, `run-live-eval`, `write-adr`). Path rules: `.claude/rules/*.md`.

## Windows notes (and the Mac)

- The project was built on Windows; the current machine is a Mac (zsh, no system PostgreSQL: `MDE_PG_BIN` points `scripts/dev_db.py` at the bundled binaries, `docs/runbooks/compass.md` section 1; the facts that differ are in `docs/implementation/CURRENT_STATE.md`). Everything below that is Windows-specific can be ignored on a Mac.
- Primary shell on Windows is PowerShell; the Bash tool is Git Bash (use it for POSIX scripts). Plain `bash` inside PowerShell is the WSL launcher, avoid it. `just` runs recipes with PowerShell, so recipes are plain `uv run ...` commands. Heredocs through the Bash tool eat backslashes: write files that contain Windows paths with the Write tool.
- Postgres runs as a **private cluster** started by `just db-up` (`scripts/dev_db.py`) on **port 54329**, data in `data/pg` of the main checkout. **Never use port 5432**: it belongs to the user's other projects. Default URL: `postgresql+psycopg://mde:mde@127.0.0.1:54329/mde`. Tests create an ephemeral database per session. psycopg async needs a selector event loop: start the API with `just dev` (`python -m mde_context_api`), never bare `uvicorn`.
- **Never touch Docker Desktop state** (it is paused on purpose; other projects' containers live there). Never run `wsl --shutdown`.
- The machine has little free RAM (about 3.7 GB): no background processes, run pyright only on your own package paths, at most 4 to 5 agents at once.
- `.env` holds `OPENAI_API_KEY` (listed twice, harmless). To see which names exist without values: `grep -oE '^[A-Za-z_][A-Za-z0-9_]*=' .env | tr -d '='`. `uv run python scripts/doctor.py` and `uv run mde doctor` report presence only.
