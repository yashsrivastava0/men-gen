# Architecture overview

The Context Engine is the front half of an enterprise AI mobile-app builder. A user talks to it; it interviews them (mostly multiple-choice cards), writes a plain-language **Brief**, researches the web, builds one detailed file, `APP_SPEC.md`, and hands that file to a frontend engine that is built elsewhere. It stops at the hand-off boundary: no frontend, backend or QA engine and no chat UI live here.

Three ideas hold the design together:

* **Plain code controls the flow.** A state machine decides what happens next; every LLM call is a single, structured, schema-validated call made through one gateway. There are no agent loops and no agent frameworks.
* **The model refines, code decides.** The spec starts as a valid baseline expanded from data (a Domain Pack); LLM proposals are validated by code and applied or dropped. Questions come from a pre-written bank; the model writes only language.
* **Nothing moves without a person's approval.** Two gates (Brief, Plan) are bound to the version hash; the hand-off event exists in exactly one place and only after Gate 2 approved the current hash.

Normative sources: the [product spec](../product/context-engine-spec.md) (what) and the [implementation brief](../implementation/BRIEF.md) (how). Security view: [security.md](security.md). Tree and layering: [repo-layout.md](repo-layout.md).

## The six steps and two gates

| # | Step | What happens | Where | Output | LLM calls |
| --- | --- | --- | --- | --- | --- |
| 1 | **Discover** | A turn loop fills the slot table. The first turn is a canned greeting. A tap costs no LLM call, typed text one | `mde_discovery` | the **Brief** | C1, C2, C3, C5, C11, C14 |
| 2 | **Gate 1** | The user approves the Brief (version and hash) | `mde_flow.gates` | an approval record | none |
| 3 | **Research** | After Gate 1 only; sequential and bounded: plan, web search, extract, synthesize, cross-check | `mde_research` | the **Research Note** | C6, S1, C7, C8 |
| 4 | **Build the spec** | Deterministic baseline, LLM proposals validated by code, nine validators, a critic, at most two repair rounds | `mde_spec_builder` | `APP_SPEC.md` draft and the **Plan view** | C9a, C9b, C9c, C10, C11, C12, C13 |
| 5 | **Gate 2** | The user approves the Plan view of the spec (version and hash) | `mde_flow.gates` | an approval record | none |
| 6 | **Hand off** | Commit to the project git repo, then emit `spec_ready` | `mde_flow.handoff` | `APP_SPEC.md` and `spec.lock.json` in `PROJECTS_DIR/<tenant>/<app_id>/` | none |

(Steps 3 and 4 together are the PLANNING state: one run with one Stop button.) The call specs are listed in [the prompt catalog](../prompts/prompt-catalog.md). Enterprise mode runs the same engines under a different policy: several approvers per gate, audit, deeper research ([policy format](../specs/policy-format.md)).

**Compass, the new first agent.** Next to Discover there is a second implementation of step 1: [Compass](../packages/compass.md) (`mde_compass`, call ids `cmp.*`, port 8110), a self-contained package with its own Brief schema (hashed, versioned, evidence-tiered), data-driven catalog, store, HTTP API and test UI, designed in [doc 11](../architecture-v3/11-discovery-agent-design.md). It reads uploaded documents and links as well as taps and text (cited, SSRF-safe), imports only core, contracts, the gateway and persistence, is not wired into `mde_flow` yet, and ends at Gate 1 with a hand-off stub; `mde_discovery` stays untouched until a later task retires it. See [ADR 0012](../adr/0012-compass-package-boundaries-and-layering.md), [the API](../api/compass.md) and [the runbook](../runbooks/compass.md).

## One page, as text

```
 client: CLI (mde), a future chat UI, the frontend engine's loader
   |  HTTP + SSE   Authorization: Bearer ...   X-Tenant-Id / X-User-Id / X-Roles
   v
 apps/context-api (FastAPI) ------------------------ composition root, job worker, /healthz /readyz
   |  every route: authenticate -> FlowService.<method>(principal, ...)
   v
 mde_flow  --  state machine, gates, approvals, one run per session, change handling, hand-off
   |            (the only code that changes state; calls engines only through ports)
   |  ports in mde_contracts: DiscoveryEngine, ResearchEngine, SpecBuilderEngine,
   |                          Store/UnitOfWork, BudgetGate, ProjectRepoPort, FrontendEnginePort
   +--> mde_discovery ---+
   +--> mde_research  ---+--> GatewayClient (mde_contracts) --> mde_llm_gateway
   +--> mde_spec_builder-+                                       budget guard + ledger, retries, repair,
   |         |                                                   cache, cassettes, redaction
   |         +--> mde_prompts (call specs, templates, mock handlers)          |
   |         +--> mde_content (question bank, packs, recipes, themes, policies) v
   |                                                              mock | replay | live (OpenAI Responses)
   +--> Store --> mde_persistence --> PostgreSQL (private cluster on port 54329 in development)
   +--> ProjectRepoPort --> local git repo --> APP_SPEC.md --> mde_spec_loader --> frontend engine (elsewhere)
```

`mde_core` (settings, ids, clock, errors, hashing, redaction, file lock) and `mde_contracts` (Pydantic models and protocols, no I/O) sit under everything.

## The pieces

| Piece | Package | Job | Page |
| --- | --- | --- | --- |
| Flow controller | `mde_flow` | State machine, gates, approvals, runs, queued messages, change handling, hand-off | [flow-and-gates](flow-and-gates.md), [flow](../packages/flow.md) |
| Discover | `mde_discovery` | The five-step turn loop: understand, update slots, pick the next card, render, compile the Brief | [discovery](../packages/discovery.md) |
| Research | `mde_research` | Plan, search, extract, synthesize and cross-check into a Research Note | [research](../packages/research.md) |
| Spec Builder | `mde_spec_builder` | Baseline, proposals, validators, critic, repair, renderer, Plan view | [spec-builder](../packages/spec-builder.md) |
| Spec Loader | `mde_spec_loader` | Read-only access for the frontend engine: index, slice, refs, diff, verify | [spec-loader](../packages/spec-loader.md), [frontend consumption](../specs/frontend-consumption.md) |
| LLM Gateway | `mde_llm_gateway` | The only code that calls a model: budget guard, ledger, retries, cassettes | [llm-gateway](llm-gateway.md), [package](../packages/llm-gateway.md) |
| Prompts | `mde_prompts` | Versioned call specs, templates, mock handlers | [prompts](../packages/prompts.md), [catalog](../prompts/prompt-catalog.md) |
| Content | `mde_content` | Data and loaders: questions, packs, recipes, themes, stack profile, policies | [content](../packages/content.md) |
| Persistence | `mde_persistence` | PostgreSQL repositories, migrations, the job queue | [data-model](data-model.md), [persistence](../packages/persistence.md) |
| Contracts, core | `mde_contracts`, `mde_core` | The frozen vocabulary and the shared utilities | [contracts](../packages/contracts.md), [core](../packages/core.md) |
| Context API | `mde_context_api` | REST and SSE in front of the flow, composition, worker | [events-and-api](../specs/events-and-api.md), [context-api](../packages/context-api.md) |
| Gateway API | `mde_llm_gateway_api` | The gateway over HTTP, same interface | [llm-gateway-api](../packages/llm-gateway-api.md) |
| CLI | `mde_cli` | `mde chat`, `sim`, `budget`, `doctor`, `spec` over the API | [cli](../packages/cli.md), [local dev](../runbooks/local-dev.md) |

## The data flow of one session

1. `POST /v1/sessions` creates a session. The flow asks Discover for its canned greeting (starter chips from content) and stores both in one short transaction.
2. Each inbound event (a typed message, a tapped option, an upload, a gate decision, a UI action) goes to `POST /v1/sessions/{id}/events`. Secrets in any free text are redacted **before** anything is recorded. The event is idempotent by its `event_id`.
3. Discover updates the slot table and returns the next card or, when the loop is complete, the Brief. No transaction is open while the engine (an LLM call) runs: the turn is replayed around its engine calls ([flow-and-gates, "Events and transactions"](flow-and-gates.md)).
4. Gate 1 shows the Brief. Approval is recorded on exactly that `(version, hash)` and moves the session to PLANNING; the plan job is enqueued (`202`).
5. A worker (inside the API process by default, or `python -m mde_context_api.worker`) claims the job with `FOR UPDATE SKIP LOCKED` and runs research, then the build. Progress events stream over SSE (`Last-Event-ID` replays what a client missed). A budget gate can pause the run before each phase.
6. The builder produces the spec and the Plan view; the flow opens Gate 2 with a diff against what the user saw at Gate 1.
7. Gate 2 approval (by every required role, each a different person in Enterprise) lets `FlowService._handoff` commit the spec to the project repo and emit `spec_ready`. The frontend engine reads the committed file with the Spec Loader.
8. After hand-off a change request creates a new version: a **light** change by the requester alone goes straight through; anything else re-opens Gate 2.

Every state change in steps 1 to 8 is made by `mde_flow.machine.apply`; the engines return events and state, never a gate, `plan_ready`, `spec_ready` or a state transition (the flow drops those if an engine tries).

## Modes and the money rule

| `LLM_MODE` | Provider | Cost |
| --- | --- | --- |
| `mock` (default) | deterministic handlers shipped with every call spec | none; no key, no network |
| `replay` | recorded cassettes; a missing cassette fails, never goes live | none |
| `live` | OpenAI Responses API; needs `ALLOW_LIVE_LLM=1` as well, and never under `MDE_FORBID_LIVE=1` | metered against a hard USD 8.00 ceiling (default cap in force 6.00) |

The gateway runs in-process or as its own HTTP service (`GATEWAY_MODE=inprocess|http`); engines see only the `GatewayClient` protocol. Everything in the test suite is offline.

## Guarantees and where they are proved

| Guarantee | Page |
| --- | --- |
| No state change outside the flow controller; `spec_ready` only after Gate 2 approval of the current hash; approved versions immutable | [flow-and-gates](flow-and-gates.md), [ADR 0006](../adr/0006-one-spec-hash-rule.md) |
| One transaction never spans an engine call | [ADR 0004](../adr/0004-transactions-never-span-engine-calls.md) |
| Live spend never exceeds USD 8.00 (default cap in force 6.00); the guard fails closed | [llm-gateway](llm-gateway.md), [ADR 0005](../adr/0005-budget-guard-fails-closed.md) |
| Web, upload and search text is data, never instructions; secrets never stored or logged | [security](security.md) |
| Tenants cannot see each other; a stranger gets the same 404 as an unknown id | [data-model](data-model.md), [security](security.md) |
| The in-memory fake store behaves like PostgreSQL | [ADR 0007](../adr/0007-fake-store-validated-by-contract-suite.md) |

## Not in scope

Chat UI, the frontend, backend and QA engines, real SSO and SCIM (roles and approvals are modelled), real publishing, billing, fetching URLs a user supplies (a server-side request forgery risk, deliberately not built) and PDF parsing (text, Markdown and images only).
