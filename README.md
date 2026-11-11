# Mobigen: Context Engine

The front half of an enterprise AI mobile-app builder. A user chats with the engine; it interviews them (mostly multiple-choice cards), writes a plain-language **Brief** (Gate 1), researches the web, builds one detailed file, `APP_SPEC.md`, shows a **Plan** (Gate 2), and hands the approved file off to a frontend engine that generates an Expo/React Native app.

This repo contains the conversation, research, spec building, both approval gates, versioning, the hand-off event, a read-only Spec Loader, an LLM Gateway with a hard spend guard, a context API (REST + SSE) and a CLI. It does not contain the frontend, backend or QA engines or a chat UI.

Plain code controls the flow; every LLM call is a single schema-validated call; everything runs offline in mock mode with no API key.

## Quickstart (Windows, PowerShell)

```powershell
$env:Path += ";$env:USERPROFILE\.local\bin"   # puts uv and just on the path (once per shell)
uv sync --all-packages                        # install the workspace (Python 3.13)
uv run mde chat --local --memory              # talk to the engine, "hi" to spec_ready: no database, no key, no network
```

The full path with the database, the API and the tests:

```powershell
just db-up                    # private Postgres on port 54329 (never 5432; Docker is not needed)
just migrate                  # apply migrations
just test                     # unit tests, offline
just ci                       # the whole offline pipeline
uv run mde chat --local       # the same conversation, saved in the private Postgres
just dev                      # the context API on port 8100 (terminal 1) ...
uv run mde chat               # ... and the CLI against it (terminal 2)
```

A scripted run (the shortest full journey is four lines: an idea, `r*` to take the suggested answer to every question, then `1` and `1` to approve the Brief and the Plan): `uv run mde chat --local --memory --script journey.txt`. Other commands: `uv run mde sim` (ten scripted personas), `uv run mde budget` (live spend), `uv run mde doctor` (settings and database, never values), `uv run mde spec verify|show|simulate-consumer <APP_SPEC.md>`.

`just` is installed with `uv tool install rust-just`. Live LLM calls are off by default (`LLM_MODE=mock`); set `LLM_MODE=live` to enable them, and read `docs/runbooks/live-llm-budget.md` first. When something fails: `docs/runbooks/troubleshooting.md`.

## Where things are

- Agent/contributor guide (commands, hard rules, repo map): [CLAUDE.md](CLAUDE.md)
- Docs index: [docs/README.md](docs/README.md)
- Architecture in one page: [docs/architecture/overview.md](docs/architecture/overview.md); security: [docs/architecture/security.md](docs/architecture/security.md)
- Product spec: [docs/product/context-engine-spec.md](docs/product/context-engine-spec.md)
- Implementation brief: [docs/implementation/BRIEF.md](docs/implementation/BRIEF.md)
- Progress and decisions: [docs/implementation/PROGRESS.md](docs/implementation/PROGRESS.md), [docs/implementation/DECISIONS.md](docs/implementation/DECISIONS.md)
- For a frontend engine that consumes the spec: [docs/specs/frontend-consumption.md](docs/specs/frontend-consumption.md), the API in [docs/specs/events-and-api.md](docs/specs/events-and-api.md) and the exported [OpenAPI](docs/api/openapi.json)
