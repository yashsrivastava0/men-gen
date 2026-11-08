# ADR 0001: Stack

- **Status:** accepted
- **Date:** 2026-11-08
- **Deciders:** lead (fixed by the implementation brief, section 2)

## Context

The brief fixes the stack so that parallel agents do not diverge. The product must be controllable (plain code decides flow), testable offline, cheap to run with a small model, and safe around untrusted web content and a hard USD 6.00 live-spend cap.

## Decision

| Area | Decision | Rationale |
| --- | --- | --- |
| Language | Python 3.13 (3.12 acceptable), async throughout, type hints everywhere | Mature async, Pydantic, SQLAlchemy ecosystem; strict typing catches contract drift |
| Workspace | `uv` workspace monorepo; `ruff`, `pyright`, `pytest`, `pytest-asyncio`, `hypothesis`, `import-linter`, `pre-commit`, `just` | One lockfile, fast installs, enforceable layering |
| API | FastAPI, Pydantic v2, Uvicorn; SSE for streaming events | OpenAPI for free, typed models shared with contracts |
| Database | PostgreSQL 16+ only (JSONB, `FOR UPDATE SKIP LOCKED`, advisory locks); SQLAlchemy 2.0 async, Alembic, psycopg 3. **No SQLite anywhere**, not in tests | Queue, one-active-run index and JSONB specs need Postgres semantics; tests must match production |
| Agent frameworks | **Forbidden** (LangChain, LangGraph, LlamaIndex, CrewAI, AutoGen, Semantic Kernel, DSPy, instructor, OpenAI Agents SDK, similar). `openai` SDK only inside the gateway as an HTTP client | Plain code controls flow; one place meters, validates and logs every call |
| LLM calls | Single, structured, schema-validated calls; no agent loops | Predictable cost and behavior with a small model |
| Provider and model | OpenAI `gpt-6-luna` for every call (configurable per route); Responses API only | Cheap, focused model; Responses API carries web search and reasoning controls |
| Live spend | Hard cap USD 6.00 total (effective 95%) | Budget guard and ledger are safety systems |
| Content | Everything varying by archetype, company or policy is YAML/JSON data | Nothing hardcoded to one company |
| Secrets | `.env` never printed, logged, committed or copied | Hard rule 1 |
| Offline-first | Every test passes offline (mock or replay); network only in explicit `live` tests | Reproducible CI, free reruns |
| Markdown | Only `README.md`, `CLAUDE.md`, `AGENTS.md`, nested `CLAUDE.md`, `.claude/**`, `docs/**`; prompt templates are `.jinja` | Keeps the tree clean and prompts out of docs |

## Alternatives considered

- LangGraph or similar for orchestration: rejected; hides control flow and cost, forbidden by the brief.
- SQLite for tests: rejected; behavior differs from Postgres (locking, JSONB, partial indexes).
- Redis/Celery for the queue: rejected; a Postgres `SKIP LOCKED` queue removes a moving part.
- Chat Completions API: rejected; built-in tools and reasoning controls belong to Responses.

## Consequences

- Import-linter contracts enforce layering and the "only the gateway imports `openai`" rule; ruff banned-api backs it up.
- Mock providers and handlers must ship with every call spec so the product runs without a key.
- Environment adaptations to this stack are recorded in [ADR 0002](0002-environment-adaptations.md).

## References

Brief sections 2, 3, 7; spec sections 1, 2.5.
