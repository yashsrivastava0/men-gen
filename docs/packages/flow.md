# mde_flow

The flow controller: session state machine, gates and approvals, run manager, change handling, hand-off. Orchestrates the three engines through the ports in `mde_contracts`; imports `mde_core`, `mde_contracts` and, for the one spec hash rule only, `mde_spec_loader.hashing` (never an engine package). Design and guarantees: [../architecture/flow-and-gates.md](../architecture/flow-and-gates.md).

## Public interface

```python
from mde_flow.service import FlowService
from mde_flow.handoff import LocalGitProjectRepo, NoopFrontendEngine

flow = FlowService(
    store=store,                    # mde_contracts.ports.Store (Postgres in production)
    discovery=discovery,            # DiscoveryEngine
    research=research,              # ResearchEngine
    spec_builder=builder,           # SpecBuilderEngine
    project_repo=LocalGitProjectRepo(PROJECTS_DIR),
    frontend=NoopFrontendEngine(),  # FrontendEnginePort
    policies=resolve_policy,        # (policy_id) -> Policy
    clock=SystemClock(),
    ids=new_id,                     # (prefix) -> prefixed ULID
    budget_gate=gate,               # optional BudgetGate: pause planning when the cap is reached
)
```

| Method | Purpose |
| --- | --- |
| `create_session(principal, mode, policy_id=None, org_profile=None)` | new session in DISCOVERING; returns the record and the greeting events. `org_profile` (Enterprise only) goes to Discover's `start` once |
| `handle_event(principal, session_id, event)` | one inbound event (message, tap, upload, gate decision, UI action); returns the outbound events; a repeated `event_id` returns the original replies |
| `run_plan_job(job, worker_id, cancel)` / `run_next_job(worker_id)` | run one claimed plan job (research, build, Gate 2 or light hand-off). `job` is the `JobRecord` that `JobRepo.claim` returned (system scope cannot read a job by id), so `run_plan_job` has the shape of `mde_persistence.worker.JobHandler`; the flow ends the job itself, the worker's own completion afterwards is a logged no-op |
| `get_snapshot`, `get_brief`, `get_plan`, `get_spec`, `list_versions`, `list_events` | read side: where the user left off, the Brief, the Plan view, the spec record (text, hash, status, git sha), versions, the event log for SSE replay. `get_spec` / `get_plan` without a version return the session's **current** version |

**Pauses and disagreements.** With a `budget_gate` the flow asks before it starts a plan run and before each plan phase; when the answer is no it parks the session in PAUSED (`SessionSnapshot.paused_for == "budget"`), sends one `budget_cap_reached` notice and lets the owner or, for a budget pause, an approver lift it with `resume` ([the gates page](../architecture/flow-and-gates.md), "The budget pause"). In Enterprise, two stakeholders' conflicting answers to one slot are resolved by `Policy.role_precedence` or flagged to the requester (`slot_conflict_resolved`, `slot_disputed`; a disputed slot blocks gate completion), see "Two stakeholders disagree". A reply to a builder question that is not an answer leaves the question pending (`answer_needed`, then the offer "Use the default"). Research that ignored instructions it found adds one `content_flagged` notice; the builder is given Discover's screen details (`BuildRequest.screen_notes`, keys `note-1` ...).

Inbound `UiAction("idle")` is the client reporting silence on a pending card. In DISCOVERING the flow hands it to Discover like any other turn (Discover nudges once, then assumes the recommended option); in every other state it is a silent no-op (details in [the gates page](../architecture/flow-and-gates.md), "Events and transactions"). The requests the flow builds for the engines carry `ResearchRequest.references` and `brand_url`, `BuildRequest.change_ops` (the change requests of the run) and `BuildRequest.gate1_spec` (the first draft, for the diff since Gate 1), and `DiscoveryRequest.source="gate_comment"` for the comments of a "Change something" or rejection decision.

Errors: `NotFound` (no such session, **or you hold no role in it**: a stranger cannot tell the two apart), `Forbidden` (an approver of the session who may not do that), `GateError` (not allowed to decide: wrong role, one person for two roles, the requester approving for another role), `HandoffFailed` (commit failed; nothing was recorded), `Conflict`. Stale or impossible gate decisions are friendly `Notice` events, not errors. Only the session owner is the `requester`; where a gate needs several roles each is approved by a different person (see [the gates page](../architecture/flow-and-gates.md)).

## Modules

| Module | Job |
| --- | --- |
| `machine` | pure state machine and transition table; the only writer of `state`, `plan_phase`, `origin`, `paused_from` |
| `gates` | `open_gate`, `record_decision`, hash binding, status, typed-approval rule, hand-off guard, `ReminderPort`/`NoopReminders` |
| `diffing` | `brief_diff`, `diff_ids(old, new)` (local, no spec-loader), `plan_diff` |
| `dependency_map` | the change matrix of spec 3.6 as data; light/heavy classification |
| `runs` | `RunManager`: one active run, cancellation tokens, queue during PLANNING |
| `service` | `FlowService` |
| `screening` | `screen_event`: secrets and hidden characters are removed from every free-text field of an inbound event before anything is stored |
| `bookkeeping` | the flow's own keys in `DiscoveryState.extra` (`flow.*`): answer counters, the budget hold, who answered which slot; `screen_notes` for the builder |
| `stakeholders` | pure: who wins when two stakeholders answer one slot differently (`settle_writes`, `winner`), the record of positions |
| `inputs` | `references`, `brand_url`: what Research is told, derived from stored state (a brand site only if the user typed or tapped it) |
| `handoff` | `LocalGitProjectRepo` (names validated with `fullmatch`, names that differ only by case refused, directory links refused), `NoopFrontendEngine` |
| `models`, `messages`, `errors` | read models and the job intent, user-facing strings, flow errors |
| `testing` | importable test kit (below) |

## Test kit (`mde_flow.testing`)

Shipped in the package so the API tests can use it too. Deterministic and offline (`FakeClock`, `SequentialIds`).

* `InMemoryStore`: implements `Store`, `UnitOfWork` and every repository protocol, and passes the persistence contract suite (22 checks, `tests/integration/test_inmemory_store_contract.py`): transactional units of work with row locks (commit ends the transaction), optimistic CAS on `SessionRepo.save`, idempotent events with `seq` and `replies_to`, tenant isolation with a limited system scope (claim and recovery only), insert-only versions, immutable approved versions (status, git sha and approved_at only, forward only; handed-off stays handed-off), the Postgres job queue (outright cancel of a queued job, retry accounting, recovery, lost heartbeats). Records are stored as JSON so aliasing cannot hide. `open_units` / `peak_open_units` count open units of work.
* `TrackingStore` / `Peek` (`mde_flow.testing.peek`): wrap any `Store`, remember sessions and jobs created through it and read them back through the repository ports, so one assertion works on the fake and on Postgres. `use_store_factory(factory)` / `make_flow_kit(store_factory=...)` choose the store; the flow test `conftest.py` parametrizes the scenario modules over `InMemoryStore` and `PostgresStore`.
* `FakeBudgetGate` (scripted answers, `allow(ok)`, can fail), `FakeReportingResearch` (reports ignored content): the kit's `budget` is the gate the service was built with, answering "ok" until told otherwise.
* `FakeDiscoveryEngine`, `FakeResearchEngine`, `FakeSpecBuilder`, `FakeProjectRepo`: scripted engines that drive the whole flow with no LLM. Typed commands: `yes`, `ok maybe`, `set design.direction=bold-dark`, `feature+ chat`, `stop`, `pause`, `start over`. Research can `raise` or report `unavailable`; the builder can `raise`, `repair`, ask one question (`ask_slot`) or return a wrong hash; both can be held inside a call (`pause_inside()`) to test cancellation deterministically.
* `make_flow_kit()` returns the wired `FlowService` with every collaborator; `make_flow()` returns just the service. `Driver` plays one user's conversation (`say`, `tap`, `decide`, `ui`, `run_job`, `to_gate1`, `to_gate2`); `decide` and `ui` take `who=` to play an approver.

## Commands

```
uv run pytest packages/flow -q
uv run pytest tests/integration/test_inmemory_store_contract.py -q   # the fake vs the contract suite
uv run pytest packages/flow -m integration -q                      # the same scenarios on PostgreSQL (dev_db.py up)
uv run pytest packages/flow --cov=packages/flow/src --cov-report=term-missing -q
uv run ruff check packages/flow ; uv run ruff format packages/flow
uv run pyright packages/flow        # strict on src
uv run lint-imports
```
