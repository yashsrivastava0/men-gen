# mde_flow

**Job:** the flow controller. Owns the session state machine, gates, approvals, runs, queued messages and the hand-off. **No state change happens outside it.** Orchestrates the engines through ports (protocols in `mde_contracts`); never imports an engine package.

**Public interface:** `mde_flow.service.FlowService` (`create_session`, `handle_event`, `run_plan_job` / `run_next_job`, `get_snapshot`, `get_brief`, `get_plan`, `get_spec`, `list_versions`, `list_events`); `machine` (pure table + `apply`), `gates` (rules of spec 2.2), `dependency_map` (spec 3.6 matrix, light/heavy), `runs.RunManager`, `screening` (secrets out of every inbound text before storage), `inputs` (references and brand site for Research), `handoff.LocalGitProjectRepo` / `NoopFrontendEngine`, `testing` (in-memory store, `TrackingStore`/`Peek`, scripted engines, `make_flow`).

**Security rules the code keeps (each has a mutation-checked test in `test_security_review.py`):** only the session owner is the `requester` (`gates.held_roles`); a stranger gets `NotFound`; a gate with several roles needs a different person behind each role and the owner only acts as `requester`; after hand-off a change skips Gate 2 only when `plan approvers == ["requester"]` and the change was asked for (not derived from an upload or an inference) and is within the policy's limits; every inbound free-text field is screened before it is recorded; a spec version number that may have reached the project repo is reserved first and never reused; hand-off names are validated with `fullmatch`, case-collisions and directory links are refused.

**Test:** `uv run pytest packages/flow` (coverage >= 85%; `test_properties.py` is the hypothesis stateful suite; `test_handoff_git.py` uses real temp git repos; everything offline and deterministic).

**Conventions**
- `machine.apply` / `apply_to_record` is the only code that writes `state`, `plan_phase`, `origin`, `paused_from` (an AST test enforces it). Use literal dicts in `model_copy(update=...)`.
- `spec_ready` is created in exactly one place (`FlowService._handoff`), only after `gates.assert_handoff_allowed` and a successful `commit_spec`. Approved versions are never mutated; new content is a new version.
- Every event is one committed unit of work (session row lock, idempotent inbound id, compare-and-set save), but **never open across an engine call**: turn logic calls engines only through `_d_handle` / `_d_apply` / `_d_reopen` / `_d_compile` (`_journalled`); `_run_turn` replays the transaction around them. Turn logic must therefore be repeatable: no side effect outside the unit of work except idempotent ones (`commit_spec`). Side effects that must follow a commit (frontend notify, reminders, token cancel) go in `turn.post`.
- Job writes go through `_job_tx` (proves the job is still running under this worker); end a job with `RunManager.finish` (never a bare `complete(error=)`, which requeues). Never `complete` a queued job.
- `InMemoryStore` must keep passing `mde_persistence.testing_contract.CHECKS`; scenario tests run on both stores (`flow_store`).
- Engines may reply with messages, cards, notices and progress only; the flow drops anything else.
- The flow's own bookkeeping rides on `DiscoveryState.extra` under `flow.*` keys (`mde_flow.bookkeeping`): answer counters, the budget hold, who answered which slot (`mde_flow.stakeholders`). Discover copies `extra` through; never add a contract field for these.
- Discover results are adopted with `_adopt_result` (it settles stakeholder disagreements first: Enterprise, `Policy.role_precedence`), never with a bare `_adopt_state`, wherever a person's answer can change a slot.
- A budget pause is `stop` (a run in flight) then `pause` through the machine, with a `flow.budget_hold`; the gate (`BudgetGate`) is asked through `_d_budget` (journalled, inside a turn) or `_budget_now` (the job, outside any unit of work). A gate that raises counts as ok. No new state, no new transition.
- A reply to a builder question is an answer when the question was settled, not when a slot changed (`_after_question_reply`); chat keeps the question (`answer_needed`) and the third such reply offers "Use the default".
- The only spec-loader import is `mde_spec_loader.hashing` (the one spec hash rule, ADR 0006); ids are diffed locally in `diffing`; user-facing strings live in `messages`; policy and content vary by data, never by code.
- pyright strict; pure functions for transitions and gate rules.
- Docs: `docs/architecture/flow-and-gates.md`, `docs/packages/flow.md`. Owner: `flow-engineer` (also owns `apps/context-api`).
