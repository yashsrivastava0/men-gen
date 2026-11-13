# Spec agent HTTP API (`mde_spec_agent`, the Spec Bench backend)

Standalone app, one process, one address: `http://127.0.0.1:8130/` (static front end `apps/spec-bench/` plus `/api/*`).
Loopback only, same-origin only (no CORS), strict CSP, no cookies. It is **not** part of the main flow and imports
nothing from `mde_flow`, `context-api`, `web` or any other agent. Design: `docs/architecture-v3/19-spec-big-apps-bench-and-result-first.md`.

**Adapter contract (designed here, not built):** input = a Compass export plus an optional Research output
(`research.v5`); output = a Spec Pack folder (`MANIFEST.json`, `index.json`, files), its manifest, and the
`spec.event.v1` event stream. A later adapter into the main flow is a thin layer over exactly this.

## Conventions
- JSON everywhere. Errors: `{ "error": { "code", "message", "fix?" } }`; codes: `bad_request`, `not_found`, `conflict`,
  `run_active`, `not_ready`, `budget_blocked`, `refused_estimate`, `gate_required`, `bad_origin`.
- Money is integer micro-USD (`*_micros`). Timestamps are UTC ISO-8601 with offset. Ids: `run_<ulid>`.
- A pack holds user and model text: clients render it as text only (`textContent`), never as HTML.
- Single-flight: one active run per program; the same input hash joins the running run (`200` with `joined: true`).

## Health, budget, profiles
| Method and path | Purpose |
| --- | --- |
| `GET /api/health` | `{ready, mode: "live"\|"offline_only", reasons[], fake: bool, version}`; `fake` is true only for the test fake app |
| `GET /api/budget` | `{period_id, cap_micros, tripwire_micros, spent_micros, reserved_micros, remaining_micros, state: ok\|tripwire\|exhausted}` |
| `POST /api/stop` | graceful stop of every active run: checkpoint and end `partial` (also SIGTERM) |
| `GET /api/profiles` | consumer profiles (id, capability class, effective window, `whole_pack_tokens`, `k`), with unset fields named |
| `GET /api/config` | the [J] numbers in force (read only) |

## Inputs (step 1) and auto-select
| Method and path | Purpose |
| --- | --- |
| `GET /api/inputs` | every Compass export found (`docs/compass/research-inputs`, `agents/spec/.runtime/inputs`, `SPEC_INPUT_DIRS`) classified: `{id, path, format, tier, size_band, profile, readiness{ready, missing[]}, drift_flags[], research_matches[]}` and every Research run found `{id, path, status, input_hash}` |
| `GET /api/inputs/auto?size=compact\|standard\|deep\|program` | the proposed pairs: `{compass_id, research_id\|null, reason, gaps[]}`; one-click override by passing other ids to `POST /api/runs` |
| `POST /api/inputs/upload` | a Compass export (and optionally a research.json); size-limited; stored under `.runtime/inputs/` |

## Runs (steps 2 to 6)
| Method and path | Purpose |
| --- | --- |
| `POST /api/runs` | `{compass_id, research_id?, consumer_profile?, offline: bool}` -> `202 {run_id}`. Refused with `refused_estimate` when the estimate's upper bound exceeds the remaining budget (plain message) |
| `GET /api/runs` | run history |
| `GET /api/runs/{id}` | snapshot: `{status: queued\|running\|paused_g15\|awaiting_g2\|approved\|partial\|failed, step, profile, gates{g15, g2}, spend_micros, estimate{low_micros, high_micros}, banners[], checked_by[], critic_vendor}` |
| `GET /api/runs/{id}/preview` | zero-cost preview (no model call): derived profile, inventory count, phases, estimated cost range, calls by step |
| `GET /api/runs/{id}/events?after=<seq>` | resumable SSE of `spec.event.v1` (`id: <seq>`, `event: <type>`); cursor also via `Last-Event-ID`; keepalive comments |
| `POST /api/runs/{id}/stop` | graceful stop (checkpoint, `partial`) |
| `POST /api/runs/{id}/resume` | continue from the last checkpoint; ends with the same bytes as an uninterrupted run |
| `POST /api/runs/{id}/g15` | `{decision: approve\|change, moves[]?, everything_in_one_release?: bool}` records the consent (the I-4 override record) |
| `POST /api/runs/{id}/g2` | `{decision: approve, content_hash}`; binds the content hash; refused when the pack has a missing builder need |

### `spec.event.v1`
`run.queued`, `run.started`, `plan.ready`, `step.started`, `step.finished`, `call.finished` (kind, tokens, cost_micros, repaired), `validators.done`
(gate, blockers, warnings), `repair.rcode`, `repair.reask`, `banner` (e.g. `weak_route`), `gate.opened` (g15, g2), `gate.closed`, `checkpoint`,
`run.partial` (reason: `budget_cap`, `budget_tripwire`, `stop`, `rate_limit`, `no_credit`, `crash`), `run.resumed`, `run.finished`, `run.failed`.
Every event: `{seq, type, at, run_id, step?, data{}}`; `seq` is dense and starts at 1.

## The pack (read only, per run)
| Method and path | Purpose |
| --- | --- |
| `GET /api/runs/{id}/pack/tree` | files with token sizes per file; per task read totals |
| `GET /api/runs/{id}/pack/file?path=` | one file's text (`text/plain`); `path` is validated against the manifest |
| `GET /api/runs/{id}/pack/manifest` | `MANIFEST.json` |
| `GET /api/runs/{id}/pack/tasks` | task cards by wave |
| `GET /api/runs/{id}/pack/budget` | budget report: tokens by file kind and by format, task totals against the budget |
| `GET /api/runs/{id}/pack/checks` | every validator result with `{code, severity, where, why, fix}`; the builder-needs matrix (BN-01..17); the coverage matrix |
| `GET /api/runs/{id}/pack/selfcheck` | `evidence/self-check.json`: R-code changes, re-asks, flags, banners |
| `GET /api/runs/{id}/pack/verify` | `evidence/verify.json` (deep): findings, patches, rounds, canary results per task card |
| `GET /api/runs/{id}/views/g15` | structure view data: app map, roles and access, entities in plain words, inventory, phase split |
| `GET /api/runs/{id}/views/g2` | G2 view data: summary, flows, content maps, data in plain words, differences from the Brief, assumptions, ranked confirm list (top 20), capability tiers, store rules, suggestions, degraded nodes, code fixes and flags, risks |
| `GET /api/runs/{id}/views/screen/{sid}?profile=` | content map data of one screen on a mock profile (regions by importance with no position; MUST, USUALLY, COULD kept apart) |
| `GET /api/runs/{id}/views/journey/{flow_id}?role=&profile=` | stories, emotion line, moments that matter, unhappy paths, steps by declared entries and exits |
| `GET /api/runs/{id}/views/graph?role=` | navigation graph data (nodes, edges, in/later, unreachable, dead ends) |
| `GET /api/runs/{id}/views/data` | entities and lifecycles, operations, fixtures, mock behaviours, integrations with interrupted-flow and pending-timeout rules |
| `GET /api/runs/{id}/views/needs` | needs, blocks, assets, motion intents, stories, opportunities |
| `GET /api/runs/{a}/diff/{b}` | file-level diff of two pack versions by hash |
| `GET /api/runs/{id}/export/preview.html` | one self-contained offline `preview.html` for the pack |

## The mock front end's role (simulator and probe)
| Method and path | Purpose |
| --- | --- |
| `GET /api/runs/{id}/simulate?mode=all\|one\|parallel&consumer=<profile>&screen=<sid>` | `all`: allowed only if the pack fits the profile's `whole_pack_tokens` (else says why not); `one`: the exact files and bytes a worker reads (prefix + card + closure) and the token total against the budget; `parallel`: task cards by wave, worker slots from the profile's `k`, a dry-run timeline. Totals equal the packager's task totals |
| `POST /api/runs/{id}/probe` | `{task_id, offline: bool}`: the weakest profile reads the task card and returns `{outline[], blocking_questions[], assumptions[], acceptance_coverage[]}` (live inside the budget, or an offline stub) |

## Headless (Claude Code)
`uv run python -m mde_spec_agent bench auto --size deep` prints the proposed pair; `bench run --input <path> [--research <path>] [--offline]`
runs end to end and prints the run folder, gate results and the pack hash. Both use the same auto-select function as the UI.

## Files under `agents/spec/.runtime/`
`runs/<run_id>/{run.json, input.json.gz, events.jsonl, graph.json, checkpoints/, pack/, evidence/}`, `spend-record.json` (+ `.lock`), `inputs/`, `logs/`, `app.pid`.
