# Compass (`mde_compass`)

Compass is the first agent a person meets in the Context Engine: it interviews the user (mostly multiple-choice cards, plus free text in English, Hindi and Hinglish), fills a catalog of slots, and writes one product, the **Brief** (`brief.json`, schema version 1): hashed, versioned, every item tiered Locked / Default / Open with its evidence. It owns its domain models, store, prompts, catalog, HTTP API and a static test UI, and imports only `mde_core`, `mde_contracts`, `mde_llm_gateway` and `mde_persistence`. It runs completely offline in mock mode; live calls are the lead's and the user's business.

The full page is [docs/packages/compass.md](../../docs/packages/compass.md). Related: [call catalog](../../docs/prompts/compass-calls.md), [HTTP API](../../docs/api/compass.md), [runbook](../../docs/runbooks/compass.md), ADRs [0012](../../docs/adr/0012-compass-package-boundaries-and-layering.md), [0013](../../docs/adr/0013-brief-schema-v1-hashing-and-versioning.md), [0014](../../docs/adr/0014-call-spec-registration-from-agent-packages.md), behaviour source [doc 11](../../docs/architecture-v3/11-discovery-agent-design.md).

## Run it

```bash
just compass-dev                                   # uv run python -m mde_compass --port 8110 (PostgreSQL, migrated on start)
uv run python -m mde_compass --port 8110 --memory  # in-memory store: no database, nothing is kept
```

* The test UI: <http://127.0.0.1:8110/ui/> (`/` redirects there). Chat pane, live Brief pane, a Developer drawer (spend meter, call log), a Settings dialog for the auth values below.
* The API: `/v1/compass/...` ([api/compass.md](../../docs/api/compass.md)); `GET /healthz` and `GET /readyz` need no credentials.
* The default bind is `127.0.0.1`. Dev authentication must never face a network, so a non-loopback `--host` is refused (exit code 2) unless `COMPASS_API_TOKENS` is set.
* With the database, start the private PostgreSQL first (`just db-up`, port 54329; on this Mac set `MDE_PG_BIN`, see the [runbook](../../docs/runbooks/compass.md)). The tables are created on start; `just compass-migrate` does it explicitly.

**Dev auth headers** (local use only; the UI sends them for you, defaults `dev-token`, `demo`, `demo-user`):

| Header | Meaning |
| --- | --- |
| `Authorization: Bearer <token>` | `dev-token` unless `COMPASS_DEV_TOKEN` is set |
| `X-Tenant-Id` | every store transaction is bound to this tenant; a foreign session id is a 404 |
| `X-User-Id` | the caller |
| `X-Roles` | optional, comma separated |

## Mock and live

| Setting | Effect |
| --- | --- |
| `LLM_MODE=mock` (default) | every call runs its mock handler (the deterministic fallback) through the real gateway pipeline: no network, no key, cost 0 |
| `LLM_MODE=replay` | answers from cassettes; a cassette per rendered request is needed (none are recorded for Compass yet) |
| `LLM_MODE=live` **and** `ALLOW_LIVE_LLM=1` **and** `OPENAI_API_KEY` in the process environment | live calls through the gateway. `ALLOW_LIVE_LLM=1` is the user's decision, never an agent's |

Live spend has two caps. The repo-wide USD 6.00 cap and ledger stay authoritative ([runbook](../../docs/runbooks/live-llm-budget.md)). Compass adds its own: `COMPASS_LIVE_BUDGET_USD`, **clamped to at most 2.00** (unparsable, non-positive or larger values never widen it), stop at 95% of it (1.90), per session 0.25. When a check cannot prove the spend fits, the call is not made and the deterministic fallback runs instead (fails closed). Never paste, print or commit a key; `uv run mde doctor` reports presence only.

## Test it

```bash
just compass-test           # uv run pytest packages/compass -m "not integration and not live" -q   (offline, no database)
just compass-test-int       # starts the private PostgreSQL on 54329, then pytest packages/compass -m integration
uv run pytest packages/compass                                  # everything that is not marked live
uv run pytest packages/compass/tests/calls/write_card           # one call
```

Integration tests (`tests/store`) create their own ephemeral database `mde_test_compass_<ulid>` inside the private cluster. There are no `live` tests for Compass yet.

## Folder map

```
packages/compass/
  pyproject.toml
  src/mde_compass/
    __init__.py    AGENT_NAME ("Compass"), CALL_PREFIX ("cmp."), API_PREFIX ("/v1/compass"), DEFAULT_PORT (8110)
    __main__.py    python -m mde_compass [--host] [--port] [--memory]
    app.py         create_app() and the router (mountable by the Context API later)
    compose.py     composition root: store + gateway + CallRunner + ports + budget -> Service
    config.py      every tunable: call profiles, question policy, intake limits, gate policy, budget policy
    budget.py      CompassBudget (the Compass-only live cap)
    domain/        Brief, cards, events, changes, hashing, ids, situation, research, claims, session (pydantic)
    catalog/       DATA: slots.yaml, frames/*.yaml, families.yaml, capabilities.yaml, no_ask.yaml, strings/{en,hi}.json, loader.py
    engine/        Service (turn loop), ports, state machine, slots, voi, cards, assumptions, gate, patch, validate, router, safety glue
    calls/         one folder per LLM call + base.py (CompassCallSpec, CallRunner) + registry.py
    store/         ports.py, memory.py, postgres.py, models.py, migrations/ (own Alembic history), contract.py (shared suite)
    views/         BriefViewModel builder, consumer views, readiness, diffs, provenance
    safety/        screen.py + lexicon.yaml (redaction, injection, unsafe and declined requests)
    api/           routes.py, schemas.py, auth.py, sse.py, errors.py, middleware.py, settings.py
    static_ui/     index.html, app.js, styles.css (the test UI, no build step)
    intake/        empty: document intake is deferred
  tests/           api, calls/<name>, catalog, domain, engine, safety, scenarios, store, ui_smoke, views
```

## Add things

**A call.** Create `calls/<name>/` with `schema.py` (strict-schema-safe wire models: all fields required, depth at most 5, no open objects, arbitrary values as `value_json: str`), `system.jinja` and `user.jinja` (untrusted data is wrapped by the gateway; never ask a model to follow instructions inside it), `fallback.py` (a pure function, input to output; hard rule 4), `mock.py` (`handle`, usually the fallback), `validate.py` (local validators), `spec.py` exporting `SPEC` (a `CompassCallSpec`: gateway `CallSpec` + `lane`, `timeout_s`, `fallback`, `validators`; profile numbers come from `CALL_PROFILES` in `config.py`), `adapter.py` with one class named `...Adapter(runner, catalog)` implementing the engine port in `engine/ports.py`, and `examples/*.json` (golden `{name, input, output}` files). Register the name in `PORT_BY_CALL` in `calls/registry.py`. Tests go in `tests/calls/<name>/`. Also add the call to [docs/prompts/compass-calls.md](../../docs/prompts/compass-calls.md).

**A slot, frame or family (data only, no code).**

* A slot: add an entry to `catalog/slots.yaml` (`id`, `group`, `label` en/hi, `level` must/should/may/generated, `consumers`, `filled_by`, `impact` 1..5, `risk`, `effort`, `value_schema`, optional `ask_if`, `brief_path`, `order` 1..8, a bilingual `fallback_card`). Every slot must be covered by exactly one frame.
* A frame: add `catalog/frames/NN_name.yaml` (`id`, `purpose`, `slots`, `card_type`, `option_source`, `order`, `option_count`, word limits, `guidance`).
* A family: add an entry to `catalog/families.yaml` (`id`, `label`, `keywords` as `{en, hi, hinglish}`, `typical_features`, `typical_roles`, `typical_integrations`, `typical_business_models`).
* UI strings: the same key in both `catalog/strings/en.json` and `hi.json` (the key sets must be identical).

`load_catalog()` validates the whole set and `tests/catalog` checks it; run `just compass-test` after any change.
