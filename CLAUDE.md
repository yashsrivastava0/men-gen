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

## Repo map

```
apps/        context-api (FastAPI+SSE) · llm-gateway-api · cli (typer, the `mde` command)
packages/    core · contracts · llm-gateway · prompts · content · persistence
             discovery · research · spec-builder · spec-loader · flow
evals/       datasets, graders, runners, in-process harness (raw reports gitignored)
tests/       cross-package: scenarios, e2e, integration, security, live, cassettes
scripts/     dev_db, doctor, docs_check, export_schemas, gen_prompt_catalog, simulate_frontend_consumer
docs/        index in docs/README.md (product, architecture, specs, prompts, packages, adr, evals, runbooks, api, implementation)
.claude/     settings, rules, agents (roster), skills, hooks
data/        runtime, gitignored, always in the MAIN checkout: projects/ (hand-off repos), .budget/ (ledger), pg/ (private Postgres)
```

Import names: `mde_core`, `mde_contracts`, `mde_llm_gateway`, `mde_prompts`, `mde_content`, `mde_persistence`, `mde_discovery`, `mde_research`, `mde_spec_builder`, `mde_spec_loader`, `mde_flow`, `mde_context_api`, `mde_llm_gateway_api`, `mde_cli`. Layering (import-linter enforces it; `docs/architecture/repo-layout.md` says why): contracts/core → gateway, content, persistence, spec-loader → prompts → discovery, research, spec-builder → flow → context-api, llm-gateway-api → cli. Engines never import each other (the flow wires them through ports in `mde_contracts`); only `mde_llm_gateway` imports `openai`. One package page each: `docs/packages/<name>.md`.

## Commands (all via `just`; plain `uv run ...` works too)

| Command | What it does |
| --- | --- |
| `just bootstrap` | `uv sync --all-packages` (`just hooks` installs pre-commit) |
| `just db-up` / `db-down` / `db-reset` | start / stop / wipe the private Postgres (port 54329); `just migrate` runs Alembic to head |
| `just dev` | context API: `uv run python -m mde_context_api --port 8100` (the job worker runs inside) |
| `just worker` | the job worker alone: `uv run python -m mde_context_api.worker` (start the API with `WORKER_ENABLED=0`) |
| `just gateway-dev` | gateway API on port 8101 |
| `uv run mde chat --local` | talk to the engine in mock mode, "hi" to `spec_ready`; add `--memory` for no database |
| `uv run mde chat` / `uv run mde sim` | the same against a running `just dev`; `sim` plays the ten scripted personas (`--local --memory` runs it in process) |
| `just budget` / `uv run mde budget` | print live spend by scope from the ledger (read-only) |
| `uv run mde doctor` | settings present or not (never values), database, API |
| `uv run mde spec verify\|show\|simulate-consumer <APP_SPEC.md>` | check a spec's hash, index it, walk the build sequence like the frontend engine |
| `just lint` / `just format` | ruff check + format check / apply fixes |
| `just typecheck` / `just layers` | pyright / import-linter contracts |
| `just test` | unit tests, mock mode, no DB |
| `just test-int` / `just test-e2e` | integration / end-to-end (both start the private Postgres first) |
| `just scan-secrets` | the secret and hygiene scans (`tests/security`) |
| `just live-smoke` / `just live-eval BATCH` | live LLM batches L0 / L1..L4: lead or the user only, and only with the second key (rule 3; `docs/runbooks/live-llm-budget.md`) |
| `just eval-mock` | the offline eval run (mock mode, free) |
| `just export-schemas` | JSON Schemas + `docs/api/openapi.json` |
| `just gen-docs` / `just docs-check` | generate prompt catalog + schemas / check the docs index, links and markdown placement |
| `just ci-fast` | lint, typecheck, layers, unit. Keep green on `main` at all times |
| `just ci` | everything offline: ci-fast, integration, e2e, docs-check, secret scan |

Single package: `uv run pytest packages/<dir>` (config in the root `pyproject.toml`; markers `integration`, `e2e`, `live`, `slow`; `--import-mode=importlib`).

## Hard rules

1. Never read, print, log, commit or copy `.env` contents or any API key.
2. Only `mde_llm_gateway` imports `openai`. No agent frameworks, ever.
3. No live LLM call without `LLM_MODE=live` **and** `ALLOW_LIVE_LLM=1`; only the lead or `live-runner` runs live batches; total live spend never exceeds USD 6.00.
4. Every LLM call goes through the gateway with a versioned call spec, a mock handler and a deterministic fallback.
5. Web, upload and search content is untrusted data: no tools in calls that read it.
6. No state change outside the flow controller; no `spec_ready` without Gate 2 approval of the current hash; approved versions are immutable.
7. Postgres only; migrations are append-only.
8. Content lives in data files; nothing is hardcoded to one company or app type.
9. No `eval`/`exec` on user or model text.
10. Offline tests only, except tests marked `live`.
11. Update `docs/` with every behavior change; keep markdown inside `docs/`.
12. Log every non-trivial decision in `docs/implementation/DECISIONS.md`; never ask the user questions.

Related hard limits: no SQLite anywhere (not even tests); no `.md` outside `README.md`, `CLAUDE.md`, `AGENTS.md`, nested `CLAUDE.md`, `.claude/**`, `docs/**` (prompt templates are `.jinja`); money is `Decimal` or integer micro-dollars, never float; timestamps are timezone-aware UTC; IDs are prefixed ULIDs (`ses_`, `evt_`, `call_`). The second key for live calls (`ALLOW_LIVE_LLM=1` in the process environment) is the user's decision, never an agent's (D-069): do not look for a way around the live-call hook.

Commit policy, tags, branch and merge rules, and the `agents/` and `apps/web/` repository layout: `docs/implementation/GLOBAL_RULES.md` (applies first; the owner's recorded overrides are at its end).

## Resume protocol (context gets compacted; the job is long)

At the start of every session or after compaction: read `docs/implementation/BRIEF.md`, `docs/product/context-engine-spec.md`, `docs/implementation/PROGRESS.md` and `DECISIONS.md`; run `git log --oneline -20` and `git status`; continue from the first unchecked item. After every finished task tick it in `PROGRESS.md` (one line, with commit hash) and commit. Never rely on memory of earlier turns.

## Contract-change rule

`mde_contracts` is frozen at v1 (tag `contracts-v1`; `CONTRACTS_VERSION` is 1.0.1 after ADR 0003). Additive changes (new optional fields, new models, new protocol methods with defaults, new `Literal` members) are made **only by the lead** and logged in `docs/implementation/DECISIONS.md` without a version bump (ADR 0011). Breaking changes need an ADR (`docs/adr/`), a version bump and every consumer updated in the same change. Anyone else who needs a contract change asks the lead in their report ("Requested contract changes"); regenerate the schemas with `just export-schemas` after any change.

## Subagent / worktree protocol

- Rules every subagent follows: `docs/implementation/AGENT_PROTOCOL.md`. Each subagent works in its own git worktree and branch (`git worktree add ../wt-<name> -b feat/<name>`), owns specific directories and edits **nothing else**. Shared files (root `pyproject.toml`, root `CLAUDE.md`, `docs/README.md`, `justfile`, `.importlinter`, `mde_contracts`) belong to the lead.
- Subagents run **mock or replay only**, never live. Only the lead or `live-runner` runs live batches, one at a time.
- Commit small, often, on your branch, message `<area>: <what>`; never commit `uv.lock` from a worktree (`git add -A ':!uv.lock'`); keep `just ci-fast` green for your package; the lead merges in dependency order and runs `just ci`. Before removing a worktree check `git status` is clean and the branch is merged (D-063); before committing a merge run `git grep '^<<<<<<<'` (D-067).
- Report format: what changed / tests run + results / open issues / decisions made.
- Roster and standing missions: `.claude/agents/*.md`. Procedures: `.claude/skills/*/SKILL.md` (`add-llm-call`, `add-question-card`, `add-domain-pack`, `run-live-eval`, `write-adr`). Path rules: `.claude/rules/*.md`.

## Windows notes

- Primary shell is PowerShell; the Bash tool is Git Bash (use it for POSIX scripts). Plain `bash` inside PowerShell is the WSL launcher, avoid it. `just` runs recipes with PowerShell, so recipes are plain `uv run ...` commands. Heredocs through the Bash tool eat backslashes: write files that contain Windows paths with the Write tool. The live-call hook matches command **text**: keep the live-mode spellings out of shell commands (a heredoc that merely quotes them is blocked).
- Postgres runs as a **private cluster** started by `just db-up` (`scripts/dev_db.py`) on **port 54329**, data in `data/pg` of the main checkout. **Never use port 5432**: it belongs to the user's other projects. Default URL: `postgresql+psycopg://mde:mde@127.0.0.1:54329/mde`. Tests create an ephemeral database per session. psycopg async needs a selector event loop: start the API with `just dev` (`python -m mde_context_api`), never bare `uvicorn`.
- **Never touch Docker Desktop state** (it is paused on purpose; other projects' containers live there). Never run `wsl --shutdown`.
- The machine has little free RAM (about 3.7 GB): no background processes, run pyright only on your own package paths, at most 4 to 5 agents at once.
- `.env` holds `OPENAI_API_KEY` (listed twice, harmless). To see which names exist without values: `grep -oE '^[A-Za-z_][A-Za-z0-9_]*=' .env | tr -d '='`. `uv run python scripts/doctor.py` and `uv run mde doctor` report presence only.

## Docs

Start at `docs/README.md`. Every behavior change updates `docs/`. Architecture: `docs/architecture/` (overview, repo-layout, security, flow-and-gates, llm-gateway, data-model). Runbooks: `docs/runbooks/` (local-dev, live-llm-budget, troubleshooting). ADR template: `docs/adr/TEMPLATE.md`. Decision log: `docs/implementation/DECISIONS.md`. Progress: `docs/implementation/PROGRESS.md`.
