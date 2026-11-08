# Repository layout and layering

A uv workspace monorepo. Each package and app is a workspace member with its own `pyproject.toml`, a `src/<import_name>/` layout, a `tests/` folder and a nested `CLAUDE.md` (its job, public interface, test command, conventions). Distribution names are `mde-<name>`; import names are `mde_<name>`. This page is the map; [overview.md](overview.md) is how the pieces work together.

## The tree

```
.
├── CLAUDE.md  AGENTS.md  README.md   entry points for people and agents
├── pyproject.toml                    workspace root: shared ruff, pyright, pytest, coverage config
├── justfile                          every task entry point (just --list)
├── .importlinter                     the layering contract (enforced by `just layers`)
├── conftest.py                       root pytest guard: mock mode, MDE_FORBID_LIVE=1, live tests skip
├── docker-compose.yml  .env.example  .pre-commit-config.yaml  .editorconfig  .gitattributes
├── .github/workflows/ci.yml          `just ci` on Linux with a Postgres service
├── .claude/                          settings.json, rules/, agents/, skills/, hooks/
├── apps/
│   ├── context-api/                  FastAPI service: sessions, events, gates, SSE      (mde_context_api)
│   ├── llm-gateway-api/              the gateway over HTTP                              (mde_llm_gateway_api)
│   └── cli/                          typer CLI: chat, sim, budget, doctor, spec         (mde_cli)
├── packages/
│   ├── core/                         settings, logging and redaction, ids, clock, errors, hashing,
│   │                                 file lock, cancellation, paths                      (mde_core)
│   ├── contracts/                    Pydantic models and Protocols only                  (mde_contracts)
│   ├── llm-gateway/                  providers, pipeline, budget guard, ledger, cassettes (mde_llm_gateway)
│   ├── prompts/                      call specs C1..C14 and S1, templates, mock handlers  (mde_prompts)
│   ├── content/                      data and loaders: questions, packs, recipes, themes,
│   │                                 stack profile, policies                             (mde_content)
│   ├── persistence/                  SQLAlchemy models, repositories, Alembic, job queue (mde_persistence)
│   ├── discovery/                    Discover engine                                     (mde_discovery)
│   ├── research/                     Research engine                                     (mde_research)
│   ├── spec-builder/                 baseline, proposals, validators, critic, renderer   (mde_spec_builder)
│   ├── spec-loader/                  read-only loader for the frontend engine            (mde_spec_loader)
│   └── flow/                         state machine, gates, approvals, runs, hand-off     (mde_flow)
├── evals/                            datasets, graders, runners, the in-process harness (raw reports gitignored)
├── tests/                            cross-package: scenarios, e2e, integration, security, live, cassettes
├── scripts/                          dev_db, doctor, docs_check, export_schemas, gen_prompt_catalog,
│                                     simulate_frontend_consumer
├── infra/                            reserved for Dockerfiles and compose overrides (empty so far)
├── docs/                             the index is docs/README.md
└── data/                             runtime, gitignored: projects/ (hand-off repos), .budget/ (ledger), pg/ (private Postgres)
```

`data/` is anchored to the **main checkout** (not the working directory): `mde_core.paths.anchor_to_main_repo` resolves the ledger, the cassettes and the hand-off repos there, so every git worktree shares one ledger and one set of cassettes.

## Package responsibilities

| Package | Job | Page |
| --- | --- | --- |
| `core` | Leaf utilities, no business logic | [core](../packages/core.md) |
| `contracts` | The frozen v1 vocabulary: slots, cards, events, Brief, Research Note, spec blocks, Plan view, policy, call IO, repository and engine protocols. No I/O | [contracts](../packages/contracts.md) |
| `llm-gateway` | The only code that calls an LLM and the only importer of `openai` | [llm-gateway](../packages/llm-gateway.md) |
| `prompts` | Versioned call specs, `.jinja` templates, deterministic mock handlers, fixtures. Never touches the network | [prompts](../packages/prompts.md) |
| `content` | Everything that varies by archetype, company or policy, as validated YAML plus loaders | [content](../packages/content.md) |
| `persistence` | PostgreSQL implementation of the `Store` protocol, migrations, job queue | [persistence](../packages/persistence.md) |
| `spec-loader` | Parse, slice, diff and verify `APP_SPEC.md`; also the one spec hash rule | [spec-loader](../packages/spec-loader.md) |
| `discovery`, `research`, `spec-builder` | The three engines (steps 1, 3, 4) | [discovery](../packages/discovery.md), [research](../packages/research.md), [spec-builder](../packages/spec-builder.md) |
| `flow` | Orchestrates the engines through ports; owns every state change | [flow](../packages/flow.md) |
| `context-api`, `llm-gateway-api` | HTTP services; composition root | [context-api](../packages/context-api.md), [llm-gateway-api](../packages/llm-gateway-api.md) |
| `cli` | The `mde` terminal client | [cli](../packages/cli.md) |

## The layering contract

```
mde_cli
mde_context_api | mde_llm_gateway_api
mde_flow
mde_discovery | mde_research | mde_spec_builder
mde_prompts
mde_llm_gateway | mde_content | mde_persistence | mde_spec_loader
mde_contracts
mde_core
```

A package may import only from layers **below** it (and, for the `|` groups, never from its siblings). `.importlinter` encodes six contracts and `uv run lint-imports` (`just layers`, part of `just ci-fast`) must report all of them kept:

1. **Layering**: the stack above.
2. **Engines never import each other**: `mde_discovery`, `mde_research`, `mde_spec_builder` are independent. `mde_flow` talks to them through the protocols in `mde_contracts.engines`; the composition root (`mde_context_api.composition`) hands the real engines to the flow.
3. **Foundation packages do not import each other**: `mde_llm_gateway`, `mde_content`, `mde_persistence`, `mde_spec_loader`.
4. **Only `mde_llm_gateway` imports `openai`** (a ruff banned-api rule backs it up).
5. **No agent frameworks and no SQLite anywhere** (`langchain`, `langgraph`, `llama_index`, `crewai`, `autogen`, `semantic_kernel`, `dspy`, `instructor`, `sqlite3`, `aiosqlite`).
6. **`mde_contracts` and `mde_core` do no I/O**: no `openai`, `httpx`, `requests`, `sqlalchemy`, `psycopg`, `fastapi` or `uvicorn`.

### Why these rules

* **Parallel work without collisions.** Engines that cannot import one another can be built, tested and replaced independently; the flow is the single place where their results meet. Repository protocols live in `mde_contracts.ports`, so the flow was built against an in-memory fake while persistence was written on PostgreSQL ([ADR 0007](../adr/0007-fake-store-validated-by-contract-suite.md)).
* **One door for money and for models.** Because only the gateway imports `openai`, every call is metered, validated, redacted and logged in one place, and the budget guard cannot be bypassed by a stray import ([security](security.md)).
* **A frozen vocabulary.** `contracts` depends only on `core`, so it can be versioned and exported as JSON Schema without dragging in a database or a web framework ([ADR 0011](../adr/0011-contract-evolution-additive-only.md)).
* **The gateway cannot see prompts.** `CallSpec` lives in `contracts` (D-007) so the gateway (below `prompts`) can run any spec without importing `mde_prompts`.
* **Apps are thin.** `mde_cli` is above `mde_context_api` because it can run the API in process (`--local`); it loads the API by name in one module (`local.py`) and otherwise talks HTTP with its own wire models.

Exceptions the layering allows and the code uses: `mde_flow` imports `mde_spec_loader.hashing` for the one spec hash rule ([ADR 0006](../adr/0006-one-spec-hash-rule.md)), and `mde_prompts` depends on `mde_content` because its mock handlers read content (D-047). Neither crosses a forbidden boundary.

## Conventions every package shares

* Python 3.13 (`requires-python >=3.12,<3.14`), hatchling build backend, ruff for lint and format (line length 100), pyright `standard` everywhere and `strict` for the `src` of `contracts`, `core`, `llm-gateway`, `flow` and `spec-loader`.
* Money is integer micro-dollars (or `Decimal`), never float; timestamps are timezone-aware UTC; ids are prefixed ULIDs (`ses_`, `evt_`, `call_`, `job_`, `apr_` from `mde_core.ids.new_id`); Pydantic models are frozen where practical; no `Any` in public signatures.
* Content that varies by archetype, company or policy is data, never Python. Prompt text lives only in `.jinja` files under `mde_prompts` (and test fixtures).
* Tests are offline and deterministic (fake clock, seeded ids, no network). Markers: `integration` (needs `just db-up`), `e2e`, `live`. pytest runs with `--import-mode=importlib`, so test modules cannot import each other: shared helpers live in a real package module (`mde_core.testing`, `mde_flow.testing`, `mde_context_api.testing`, ...), as D-017 requires.
* Markdown lives only in `README.md`, `CLAUDE.md`, `AGENTS.md` (any depth), `.claude/**` and `docs/**` (`scripts/docs_check.py` enforces it).

## Where to start for a task

| Task | Start in |
| --- | --- |
| A new question or card | `packages/content/src/mde_content/data/questions/` (skill `add-question-card`, [question cards](../specs/question-cards.md)) |
| A new app type | `packages/content/src/mde_content/data/packs/` (skill `add-domain-pack`) |
| A new or changed LLM call | `packages/prompts/src/mde_prompts/calls/<call_id>/` (skill `add-llm-call`) |
| A state, gate or approval rule | `packages/flow/src/mde_flow/` and [flow-and-gates](flow-and-gates.md) |
| A new route or event | `packages/contracts` (lead only), then `apps/context-api` and [events-and-api](../specs/events-and-api.md) |
| A schema change | a new Alembic revision in `packages/persistence/src/mde_persistence/migrations/versions/` (append-only) |
| A decision worth recording | `docs/implementation/DECISIONS.md` and, when it shapes the design, a new ADR (skill `write-adr`) |
