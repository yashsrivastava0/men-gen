# Flow controller and gates

The flow controller (`mde_flow`) is the safety core of the Context Engine. It owns the session state machine, the two approval gates, the one plan run per session, queued messages, change handling and the hand-off. Engines (Discover, Research, Spec Builder) are called through ports and never change state themselves.

Normative sources: spec sections 2, 3.6, 9, 10, 11 and brief 8.1. This page says how the code enforces them. Package page: [../packages/flow.md](../packages/flow.md).

## The guarantees

| Guarantee | Where it holds | Proved by |
| --- | --- | --- |
| No state change outside the machine | `machine.apply` is the only code that writes `state`, `plan_phase`, `origin`, `paused_from` | `test_no_state_writes_outside_machine` (AST scan of the package), table tests |
| No `spec_ready` without Gate 2 approval of the **current hash** by **every required role** | `FlowService._handoff` is the only creator of `SpecReady`; it runs only after `gates.assert_handoff_allowed` and `ProjectRepoPort.commit_spec` | stateful property test, unit tests per rule, mutation checks |
| Approvals never survive a version change | approvals are matched on exact `(version, hash)`; a new version starts with none | property invariant, `test_gates.py` rule 2 |
| Approved versions are immutable | `SpecRepo.add` refuses a second write of a version; `set_status` moves status forward only (a handed-off version is history: it stays `handed_off`, the session's `handed_off_version` names the current one); new content is always a new version | property invariant, store tests, contract suite |
| One active run per session | `JobRepo.enqueue_plan` (`Conflict`) plus an in-process guard and the row lock | property invariant |
| Idempotent inbound events, ordered outbound events | `EventRepo.record_inbound` in the same unit of work as the event's effects (a crash leaves nothing recorded); per-session `seq`; an in-flight duplicate is answered at once | property invariants, `test_transactions.py` |
| No connection is held across an engine (LLM) call | turns are transactions replayed around their engine calls (see "Events and transactions") | `test_transactions.py`: a pool of 2 connections, 6 sessions inside a slow engine |
| Silence never approves | nothing creates an approval except a decision from an authorised caller; the `idle` action at a gate does nothing | rule 6 tests, `test_integration_todos.py` |
| Only the owner is the requester | `gates.held_roles` adds `requester` for the session owner and drops it from everyone else's claimed roles; a non-owner who holds no approver role of the session gets `NotFound`, exactly like an unknown id | `test_requester_role_not_claimable`, the stateful suite (an attacker who claims the role presses approve on every open gate) |
| One person is never two approvers; the requester never approves for the others | `gates.record_decision` and `gates.is_complete` (a gate with several roles needs a different person behind each role) | `test_distinct_approvers`, the stateful suite |
| After hand-off, a change skips Gate 2 only when the requester alone is Gate 2 | `dependency_map.classify_post_handoff` (`plan_roles == ["requester"]`); `gates.assert_handoff_allowed` has no light bypass | `test_post_handoff_multi_approver_always_gate2` |
| No secret is stored, shown to other approvers or fed to an engine | `screening.screen_event` runs on every inbound event before anything is recorded | `test_security_review.py` (message, comment, upload text and name, zero-width and full-width keys) |
| A spec version number that reached the project repo is never reused | the post-handoff run reserves its draft row in its own transaction before the repo write; `_run_job` builds `max(...) + 1` | `test_handoff_replay_after_rollback` |

## State machine (`mde_flow.machine`)

Pure functions over `MachineState(state, plan_phase, origin, paused_from)` and one explicit table (`TABLE`). Every `(state, transition)` pair is tested against the table; BFS from a fresh session checks that every reachable machine state satisfies the structural invariants (`check`).

```
DISCOVERING --brief_complete--> BRIEF_REVIEW --approve_brief--> PLANNING --plan_ready--> PLAN_REVIEW --approve_plan--> HANDED_OFF
     ^                              |  ^                         |  |  |                    |  ^                          |
     +-------change_brief-----------+  |                         |  |  +--stop--> origin    |  +-----change_plan----------+  (PLANNING)
     +-------needs_answer--------------+------------------------+  |                       +---- change_after_handoff ---> PLANNING
     (answer_given resumes)                                          +--plan_ready (light, origin HANDED_OFF)--> HANDED_OFF
pause/resume: DISCOVERING, BRIEF_REVIEW, PLAN_REVIEW, HANDED_OFF <-> PAUSED      archive: any state -> ARCHIVED (terminal)
```

| Transition | From | To | Notes |
| --- | --- | --- | --- |
| `brief_complete` | DISCOVERING | BRIEF_REVIEW | compile the Brief, open Gate 1 |
| `approve_brief` | BRIEF_REVIEW | PLANNING | phase `research`, `origin = BRIEF_REVIEW` |
| `change_brief` | BRIEF_REVIEW | DISCOVERING | re-enter the loop for the affected slot, then return at once |
| `plan_ready` | PLANNING | PLAN_REVIEW, or HANDED_OFF | HANDED_OFF only for a **light** change whose origin is HANDED_OFF (no Gate 2) |
| `needs_answer` | PLANNING | DISCOVERING | run suspended: `plan_phase` and `origin` are kept as the marker |
| `answer_given` | DISCOVERING (suspended) | PLANNING | resumes the run; flow-only transition |
| `rediscover` | PLANNING, or suspended DISCOVERING | DISCOVERING | app type / `scope.apps` / region changed: drop the run; flow-only transition |
| `stop` | PLANNING | `origin` | `STOPPED` only if the origin is unknown (legacy data) |
| `approve_plan` | PLAN_REVIEW | HANDED_OFF | hand-off |
| `change_plan` | PLAN_REVIEW | PLANNING | `origin = PLAN_REVIEW`, start phase `research` or `build` |
| `change_after_handoff` | HANDED_OFF | PLANNING | `origin = HANDED_OFF` |
| `pause` / `resume` | DISCOVERING, BRIEF_REVIEW, PLAN_REVIEW, HANDED_OFF / PAUSED | PAUSED / `paused_from` | a running PLANNING is stopped, never paused. The budget pause (below) uses exactly these two, after a `stop` for a run in flight: no new state |
| `archive` | any but ARCHIVED | ARCHIVED | start over archives, never deletes |

`origin` is where the current or last run started (what Stop returns to). `plan_phase` is set only while a run is active: in PLANNING, or in DISCOVERING while a `needs_answer` question waits, or while PAUSED from there.

## Gates (`mde_flow.gates`)

A gate is **derived**, never stored: it is open when the session is in BRIEF_REVIEW (Gate 1, the session's latest Brief version) or PLAN_REVIEW (Gate 2, the latest spec version), and its status comes from the approval records on exactly that `(version, hash)`.

| Rule (spec 2.2) | Enforcement |
| --- | --- |
| 1. One page, two buttons | `open_gate` builds one `GateEvent` page; `gate_buttons`: "Looks right" / "Change something", Enterprise "Send for approval" |
| 2. Bound to the hash; void on change | `check_binding` raises `HashMismatch`; `derive_status` ignores other hashes; a re-opened gate carries `diff_lines` and `single_button=True` (one button) |
| 3. Typed approval only with a gate open and a confident engine | `typed_approval_counts(gate_open, result)`; recorded with `via="typed"` (taps are `"tap"`); text that also carries a change is a change |
| 4. A requested change is its own approval of that change | applied at once with no confirm step; post-handoff light changes need no gate; anything the engine added or repaired escalates (below) |
| 5. No step before its gate | research and build start only via `machine.apply("approve_brief")`; the frontend is notified only after `assert_handoff_allowed` and a successful commit |
| 6. Silence never approves | no timer approves; Enterprise reminders (`ReminderPort`) only nudge |
| 7. Enterprise approvers | required **roles** come from `policy.gates.<gate>.approvers`; `is_complete` needs every role's *latest* decision on the same hash to be `approve`; a rejection's comments (one per line) each go to the discovery engine as a change request |
| 8. Approved versions never change silently | the service only ever adds new versions; `assert_handoff_allowed` also refuses a spec whose text does not hash to its recorded hash |

`record_decision` is pure and returns a `DecisionOutcome`:

| kind | Meaning |
| --- | --- |
| `approved` | recorded; every required role has approved this hash (gate complete) |
| `pending` | recorded; roles are still missing (the gate is re-shown with `approved_roles`) |
| `duplicate` | this role already approved this hash; nothing stored, completeness re-evaluated |
| `submitted` | Enterprise requester pressed "Send for approval" but is not a required approver: nothing recorded, approvers are reminded |
| `change_requested` / `rejected` | recorded (audit); comments become change requests; a role's later decision overrides its earlier one on the same hash |

Roles: **only the session owner is the `requester`** (a `requester` in someone's claimed roles is ignored); other callers act under their other `Principal.roles`. A caller can only act as a role they hold (`GateError` otherwise), `approver_id` must be the authenticated user, and a non-owner may only send `gate_decision` for a gate whose required roles they hold (`Forbidden`). A caller who is neither the owner nor holds any role of the session's gates cannot see the session at all: reads and events raise `NotFound` with the same words as for an id that does not exist (no probing).

**Separation of duties.** When a gate requires more than one role: each role is approved by a *different* person (one person cannot approve for two roles, and `is_complete` also refuses two roles behind one user), and the owner can only act as the `requester` (a plan gate that lists `requester` and `delivery_lead` needs the owner and somebody else; one that lists only other roles is submitted by the owner and decided by others). A gate with a single role is unaffected.

Stale and impossible decisions are answered with a friendly `Notice` and the current gate (with a diff against the version the caller saw), never an error: `stale_gate` (hash or version is not current) and `gate_not_open`.

### Light-change approval record

A light post-handoff change has no gate, but `spec_ready` for its new version must still be backed by an approval so the invariant "no `spec_ready` without an approval of the current hash" has no exception. The flow records one honest `ApprovalRecord` for the requester (`comment = gates.LIGHT_CHANGE_COMMENT`, `via` = how the user asked). That record completes the gate **only because the requester is the whole gate** (`policy.gates.plan.approvers == ["requester"]`): `classify_post_handoff` sends every change back to Gate 2 when anyone else must approve the plan (one other role or several), and `assert_handoff_allowed` has no light bypass any more, so the record can never stand in for other approvers. It never forges approvals for the other required roles.

## Runs (`mde_flow.runs`)

* **One active run** per session: `RunManager.start_run` checks `JobRepo.active_for_session`, then `enqueue_plan` (which raises `Conflict`), behind an in-process guard. `JobRepo.cancel` cancels a run no worker has started outright (state `cancelled`, the slot is free at once); one that is still winding down makes the caller show "the previous run is still stopping".
* **Cooperative cancellation.** `cancel_run` flags the job in the caller's unit of work (so it commits or rolls back with the state change); the in-process `CancellationToken` is flipped **after** commit. Workers also poll the database flag at every checkpoint and every progress event, which is how a Stop reaches a worker in another process. Engines receive the token in `RunContext.cancel`. A worker that loses its job (`heartbeat` raises `Conflict`: the job was recovered or finished elsewhere) treats that as a cancellation and stops quietly; every job write first proves, under the session row lock, that the job is still *running under this worker* and not cancelled.
* **Ending a job.** `JobRepo.complete(error=...)` alone would requeue the job while attempts remain, but the flow has already put the session back (a stop, a failure with its notice), so a retry would find nothing to do and block the next run. `RunManager.finish` therefore flags the run cancelled first, which makes `complete` end it `cancelled` with the reason in `error` (`failed`, `cancelled`, ...). A flow-handled failure thus ends `cancelled` with `error="failed"`; the store's `failed` state only arises from exhausted attempts after recovery. `finish` leaves a job this worker no longer owns alone.
* **Queue during PLANNING.** A typed message is classified by the discovery engine: a change of app type or `scope.apps` cancels the run and goes back to Discover at that card; anything else is stored (secrets redacted, at most 25) and acknowledged ("I'll apply this as soon as the plan is ready"). When the plan is ready, or the run is stopped, the queue is replayed through the engine as changes at that gate.
* **Approve, then immediately change.** If the run was enqueued but nothing has started, a change arrives *at the gate it came from*: the run is cancelled (a queued job is cancelled outright by the store), the state returns to the origin, the version changes, the approval is void and the gate re-opens with a diff.
* **Stop** cancels the run, returns to `origin`, shows the origin gate again and applies queued messages. Nothing the stopped run produced is persisted: every write re-checks under the session lock that the job is still the session's current run.
* **`needs_answer`.** The builder's one plain question becomes a card; the state goes to DISCOVERING with the run suspended. The answer (`answer_given`) starts a new job at the suspended phase with the answer slots (`PlanJobPayload.answer_slot_ids`, the question's own slot included); research is not repeated. A reply that changes the app type drops the suspended run instead. See *Answering the builder's question* below for what counts as an answer.
* **Content the research ignored.** A research engine that has `run_with_report` (the default one does; the flow checks for the method, it never imports the engine) tells the flow when it dropped a result because it looked like instructions aimed at the AI. The flow then appends one `Notice` (`content_flagged`, level `info`, "I ignored some instructions I found in that content ...") to the session's event log with the research result, whatever the number of pages. The text names no payload and no number (spec 9: "noted in the event log"). The engine's own log line (`research.injection_suspected`) stays.
* **Failures never show raw errors.** Research failure, `unavailable`, policy off or `BudgetExceeded` produce an `unavailable` Research Note and a `Notice`; the build continues from defaults. A build failure, an invalid result (its hash must equal `compute_spec_hash(spec_text)`, the one spec hash rule of `mde_spec_loader.hashing`), or a failed light commit returns to the origin with a `Notice` and ends the job (`error="failed"`). An unexpected exception also leaves the session usable and then re-raises to the worker.

### Answering the builder's question (BUG-6)

While the run is suspended, a reply is **an answer** when the question was settled, whether or not a slot changed: a tap, "you decide", a skip, or a typed reply that Discover read as an answer (`intent` answer, volunteered, change, idea or approve) or that wrote a slot. The run then resumes (an answer that changes the app type drops it instead), recording the question's slot in `answer_slot_ids` even when the value stayed what it was.

Anything else (chat, a question, off-topic text) is **not an answer**, even though the real Discover drops a card whose slot already has a value: the flow puts the question back, shows the card again and sends a `Notice` (`answer_needed`). The third such reply in a row (an edit of another slot is progress, not a wasted reply) instead offers "use the default": an assistant message with the chip "Use the default", and from then on a typed "Use the default" is handed to Discover as a tap on "you decide" for that question (the recommended option, as an assumption). A card that cannot be skipped never gets the offer (a person must answer it). The counters live in `DiscoveryState.extra` (`flow.answer_tries`, `flow.answer_offer`) and are cleared when the question ends. A session stranded by the old behaviour (suspended run, no card) resumes on its next message.

## The budget pause (`FlowService.budget_gate`, GAP-5)

Spec 9, "Enterprise: budget cap reached": pause before the Plan phase, explain, ask an approver. `FlowService` takes an optional `BudgetGate` (`mde_contracts.ports`; the composition root builds `mde_context_api.budget.GatewayBudgetGate` over the LLM gateway's budget status, so the flow never imports the gateway). `check(session_id, tenant_id)` returns a `BudgetVerdict` (`ok`, `reason`, a plain `message`). The flow asks, inside a turn through the same journal as an engine call (no unit of work is open while a gate that may be remote answers):

| Where | When it is asked | If `ok` is false |
| --- | --- | --- |
| Gate 1 approved (`_approve_brief`) | before the job is enqueued | the brief stays `draft`, no job; BRIEF_REVIEW -> PAUSED (`paused_from=BRIEF_REVIEW`); the approvals stay recorded; hold `gate1` |
| a change at Gate 2 or after hand-off (`_start_change_run`) | before the job is enqueued | the change is **not** applied (nothing was built); PLAN_REVIEW or HANDED_OFF -> PAUSED; hold `dropped`, and the notice says to ask again |
| the answer to a builder question (`_resume_planning`) | before the job is enqueued | the answer is kept; DISCOVERING (suspended) -> PAUSED; hold `answer` |
| a plan job, before research (when the policy enables it) and before the build | outside any unit of work, between engine calls | the run stops cleanly: `stop` (back to `origin`), then `pause`; the job ends `cancelled` with `error="budget paused"`; what research found is kept; hold `gate1` (origin Gate 1) or `dropped` |

The pause is the existing PAUSED side state (`machine.apply` only: `stop` then `pause` for a run in flight, so no transition or state was added). One `Notice` (`budget_cap_reached`, level `warn`) says why, in plain words and never a number: Guided briefly ("I paused here because the budget for this app is used up. Tap Resume ..."), Enterprise with the verdict's own message plus the approver roles ("Someone who can approve it needs to raise the limit. Waiting for: client_product_owner, delivery_lead."). `SessionSnapshot.paused_for == "budget"` tells a client why the session waits. The hold (`flow.budget_hold` in `DiscoveryState.extra`) says what to start again.

**Resume.** The existing `resume` action. The owner may send it, and so may an approver (anyone holding a required role of either gate) **while the session is held for the budget**; for a pause the user asked for, only the owner can. `resume` asks the gate again: still no, the session stays PAUSED with the notice again; yes, the state returns to `paused_from` and what was held starts: `gate1` re-completes Gate 1 on the unchanged Brief (the stored approvals still count) and enqueues the run, **reusing the research already done** (an empty `rerun_jobs` hands the engine its previous note back, so research is not paid for twice); `answer` starts the run with the held answer; `dropped` just shows the gate again. A gate that cannot answer (an exception) counts as "ok": the gateway's own guard still refuses every call that would break a cap, so the gate can only stop early, never open anything the guard keeps shut.

## Two stakeholders disagree (`mde_flow.stakeholders`, GAP-3)

Spec 9: *record both; resolve by the role precedence in policy or flag to the decision owner; never overwrite silently.* Enterprise only (Guided has one speaker). After every Discover result the flow compares the slots before and after (`stakeholders.settle_writes`, pure) and asks who spoke: the session owner under `requester` (plus any other roles they hold), or an approver under the role of their decision (`outcome.role`) for the comments of a "change" or "reject". Discover itself never sees roles; the flow keeps its own bookkeeping in `DiscoveryState.extra` (`flow.slot_actors`: who last answered each slot; `flow.slot_conflicts`: every position taken).

A **stakeholder answer** is a value a person tapped or typed (`source` tap or text, state volunteered or confirmed). Defaults, research, uploads and the organisation profile are not answers; the same person changing their mind is not a disagreement. A different person giving a different value for a slot someone else answered is:

| Precedence (`Policy.role_precedence`) | What happens | Notice |
| --- | --- | --- |
| the new speaker's best role is listed above the earlier one's (a role the list does not mention ranks below every listed one) | the new value is used; the earlier answer stays on record | `slot_conflict_resolved`, both values and roles named |
| the earlier speaker's role is above | the new value is **not** applied (the slot is put back, the engine's "Done, I updated ..." chat is dropped); the new answer stays on record | `slot_conflict_resolved` |
| it cannot decide (empty list, neither role listed, the same place, e.g. two people in one role) | the earlier value stays, the slot becomes `disputed`, the **decision owner** (the requester) is asked | `slot_disputed` (level `warn`): "The person who owns this app needs to choose ..." |

A disputed slot is settled only by the decision owner writing it again (any value; naming the value it already holds counts, the slot becomes `confirmed`): `slot_dispute_settled`. Anyone else's write leaves it disputed and is added to the record (`slot_disputed`, "still waiting"). **While any slot is disputed no gate completes**: every approver may say yes, but the flow shows `dispute_blocks_approval` and the gate again until the owner chooses (settling changes the Brief, so the earlier approvals are void anyway). The record of positions keeps the last 12 per slot.

## Change handling (`mde_flow.dependency_map`)

The matrix of spec 3.6 is data (`DEPENDENCY_RULES`, first match wins; every slot id resolves to a rule). `plan_change(ops, note, research_stale)` returns the sections to rebuild, the research jobs to re-run, the heavy-change categories, whether the run must go back to Discover, and the start phase (`build` reuses the Research Note; `research` when a job re-runs, there is no note, or the note is older than 14 days).

| State when the change arrives | What the flow does |
| --- | --- |
| DISCOVERING | the engine updates the draft |
| BRIEF_REVIEW | `change_brief`, then (when the loop is complete) `brief_complete`: a new Brief version, Gate 1 re-opens with the changed rows and one button |
| PLANNING | app type / `scope.apps`: cancel and `rediscover`; anything else is queued |
| PLAN_REVIEW | `change_plan`: a new job rebuilds only the affected sections; Gate 2 re-opens with a diff |
| HANDED_OFF | `change_after_handoff`: a new version; **light** applies directly, **heavy** re-opens Gate 2 |

**Light or heavy** (`classify_post_handoff`) is decided when the new version exists. **Always heavy** unless the requester alone is Gate 2 (`plan_roles == ["requester"]`). Otherwise heavy when any of: the run must go back to Discover; an op could not be classified; a changed category is in `policy.gates.post_handoff.heavy_if` (screen-level ops carry the category `screens`, and removing a screen is also `scope`); more screens were added, removed, patched or re-worded than `max_new_screens_light` (`0` makes every screen change heavy); research had to re-run; the builder had to repair something (`repair_rounds > 0`); or the change was **derived** instead of asked for (from an uploaded document, or an engine inference such as a preference mined from chat: only an explicit instruction, `intent == "change"`, or a tap is its own approval). Everything is policy data: nothing names a company or app type. (`screens` is part of the contract's `HeavyChange` literal since D-059, so a policy may list it; the screen limit and the `scope` category cover screen operations either way.)

## Events and transactions

**Rule: no unit of work is open while an engine runs.** A unit of work pins one pooled connection (Postgres: pool of 5, no overflow) and an engine (LLM) call takes tens of seconds, so a Discover turn must not hold a transaction across `engine.handle(...)`. Engine calls made from turn logic go through `FlowService._journalled`, and a turn runs as a transaction **replayed around its engine calls** (`_run_turn`, the same idea as a deterministic workflow replay):

```
pass 1   begin; lock session (FOR UPDATE); record inbound event; run the turn logic
         -> the logic needs the engine: _NeedEngine, rollback           (connection released)
engine   discovery.handle(...)                                           (no connection held)
         result appended to the turn's journal
pass 2   begin; lock; record inbound; run the turn logic again, served the journalled result
         -> no more engines: save the session (compare-and-set), append outbound events, commit
after    post-commit effects: notify the frontend, reminders, token cancellation
```

A turn with N engine calls is N + 1 short units of work (a typed answer that completes the Brief makes two calls: `handle`, then `compile_brief`; draining a queue of 25 notes makes 25). Only the last pass writes anything durable; the earlier passes roll back, so replaying never double-applies anything. Each journal entry stores a fingerprint of its request (minus freshly minted event ids): if the logic, on replay, asks for a *different* request, the state moved while the engine ran, the journalled result is not used and the turn starts over with an empty journal (`_Retry`).

* **Retry on conflict.** `_flush` saves the session with a compare-and-set; losing it (`Conflict`) raises `_Retry`: re-read the session, run the turn again, at most `MAX_TURN_RETRIES` (5) times, then the caller gets a `Conflict` and may resend the event. Passes also lock the row (`get(for_update=True)`), so in practice a lost CAS needs a writer that did not lock first; the test forces one.
* **Idempotency.** The inbound event is recorded *in the same unit of work as its effects* (not in an earlier committed one): a crash, an engine failure or a `Conflict` leaves no half-recorded event, and a retried id is processed from scratch. A repeat of a finished event returns the stored replies. A repeat of an event **still in flight in this process** is answered at once from what is stored, or with an in-progress `Notice` (`message_in_progress`) when nothing is stored yet: never a second effect, and no waiting behind a slow engine. A duplicate that reaches *another process* runs the engine too (wasted work, nothing else) and loses at the idempotent insert, returning the stored replies. Events of one session run one at a time per process (`RunManager.session_lock`; the row lock covers other processes).
* **Secrets are screened first.** `handle_event` runs `screening.screen_event` before anything else: every free-text field (message, "something else" text, an upload's text and file name, a gate comment) has hidden characters dropped and secret-looking spans replaced by `[REDACTED]` (`mde_core.redact`, after an NFKC fold so full-width or zero-width-split keys are found). The recorded inbound event, the approval comment other approvers see and what the engines receive are the screened versions; the user gets a `secret_redacted` notice.
* **Silence (`idle`).** The client reports a pending card left unanswered. The flow does not answer it itself: in DISCOVERING (the interview, or a builder question that suspended a run) it hands the `idle` event to Discover as an ordinary turn, and Discover owns the behaviour (the first silence on a card nudges once beside the same card; the next one takes the recommended option as `assumed`, with its reason, except where a person must confirm; an answer resets it). The result is adopted like any Discover turn, so an assumed builder question resumes the suspended run. In every other state (any gate, planning, paused, archived, handed off) `idle` is a silent no-op that never reaches the engine: silence never approves.
* **Engine replies are filtered**: engines may send messages, cards, notices and progress; a gate, `plan_ready`, `spec_ready` or state change from an engine is dropped. Every outbound event gets a fresh id and the per-session `seq`.
* `create_session` calls the engine's `start` before the session exists, then creates it and appends the greeting in one short unit of work. For an Enterprise session the caller also passes the tenant's Org Profile (the API reads or stores it first): the flow puts it on the `DiscoveryRequest` of this one `start` call and never again, so the engine can turn it into `assumed` slots (source `org_profile`); a Guided session never carries it, and the profile is not stored on the session record.
* **The plan job** works in short units of work between engine calls: one to start, one to persist the Research Note, one at the end (new draft, gate, queue drain). The research and build engines run with no connection held; the job's last transaction is a replayed turn too, because applying the queued notes calls the discovery engine. Every job transaction (`_job_tx`) first proves under the row lock that the job is still the session's current run.
* **Not LLM, but I/O:** `ProjectRepoPort.commit_spec` (a local git commit) runs inside the hand-off unit of work; it is fast, idempotent for `(version, hash)`, and replays of an aborted pass repeat it harmlessly.
* **Version numbers are reserved before the repo can see them.** The Gate 2 approval needs nothing (the draft row exists). A run that started *after* hand-off may commit its spec inside its final transaction (the light path), so it first commits the draft row alone (one short transaction) and the final transaction only moves that draft along. If that transaction rolls back or the process dies, the repo keeps `spec-v<N>` and the database also knows N, so the next attempt builds `max(enqueued number, highest row + 1)` instead of a different spec under the same N (which `commit_spec` would refuse for ever). A run that ends without using its reserved number marks that draft `superseded`; after a crash the next run's draft supersedes it. `get_spec` and `get_plan` serve the session's *current* version (not merely the highest row), and incremental builds start from it.

Side effects that must follow a commit run after it: notifying the frontend (failure becomes a `Notice`; the committed `spec_ready` stands), reminders, token cancellation.

## The in-memory store is validated against the production contract

Every flow unit and property test runs on `mde_flow.testing.InMemoryStore`, so the fake must behave like `PostgresStore`. The persistence package ships a reusable suite (`mde_persistence.testing_contract.CHECKS`, 22 checks: sessions and CAS, idempotent events, `seq`, versions, immutability, approvals, the job queue, tenancy, system scope, commit and rollback). Postgres passes it in `packages/persistence/tests`; the same checks run against the fake in `tests/integration/test_inmemory_store_contract.py` (offline). The first run failed 9 of 22: the fake accepted duplicate approvals, research notes and versions, let system scope read everything, only flagged a queued job on cancel, completed a job with an error as `failed`, ignored lost heartbeats, never failed an exhausted job on recovery, did not stamp `approved_at` or `at`, and allowed `handed_off -> superseded`. All were fake bugs; the flow adapted (see Runs and Hand-off).

Beyond the contract, `FlowService` itself runs on both stores: the scenario modules (`test_scenarios.py`, `test_scenarios_more.py`, `test_service_happy.py`) are parametrized over `flow_store` (`conftest.py`): the in-memory fake and `PostgresStore` (`@pytest.mark.integration`; start the local server with `uv run python scripts/dev_db.py up`; an ephemeral database, truncated per test). Assertions read through `kit.peek` (`mde_flow.testing.peek`), which uses only the repository ports, so they hold on either store. The remaining known difference: the fake makes an open unit of work's writes visible to others immediately (Postgres is READ COMMITTED); the flow writes only under the session row lock, so it cannot observe it.

## Hand-off (`mde_flow.handoff`)

`LocalGitProjectRepo` writes `APP_SPEC.md` (byte for byte, so `compute_spec_hash(file) == hash`: the hash of the file with its own `hash:` line blanked, [ADR 0006](../adr/0006-one-spec-hash-rule.md)) and `spec.lock.json` (`{"version", "hash"}`) to `PROJECTS_DIR/<tenant>/<app_id>/`, `git init`s on first use and commits. Identity, config files, hooks, signing and line endings are fixed through the environment and `-c` flags (`--no-verify`), so nothing depends on the user's git setup. Each commit is tagged `spec-v<version>`: replaying the same `(version, hash)` returns that commit; the same version with another hash is a `Conflict`. `tenant_id` and `app_id` are validated as plain names (no separators, `..`, drive letters or reserved Windows names). `NoopFrontendEngine` records notifications.

Superseding: a new handed-off version leaves earlier handed-off versions as they are (the store refuses `handed_off -> superseded`, and the Postgres trigger guards it); only drafts and approved versions are superseded.

## Tests

* `test_machine.py`: every table row and every illegal pair, BFS over reachable states, the static state-write guard.
* `test_gates.py`: each of the eight rules, Enterprise role logic. `test_dependency_map.py`, `test_diffing.py`.
* `test_scenarios.py`, `test_scenarios_more.py`: the flow rows of spec section 9 end to end with scripted engines: typed yes with no gate, unclear approval, approve then change, hash mismatch, gate left open, typing during PLANNING, change of app type, Stop (from every origin), research failure, `needs_answer`, Enterprise split approvers, light vs heavy post-handoff, hand-off and notify failures, pause, restart, tenant isolation, idempotency.
* `test_store.py` (what is specific to the fake: row locks, commit, rollback, JSON round trip, open-unit counters), `test_runs.py`, `test_handoff_git.py` (real temp git repos).
* `test_transactions.py`: a Postgres pool of **two** connections serving six sessions stuck inside a slow Discover engine (and six plan jobs stuck in research, then build): `pool.checkedout() == 0` while all six are inside; the same on the fake via `open_units`. A mutation that calls the engine inside the unit of work fails both. Also: an in-flight duplicate, the same event at two processes, a failing engine leaving nothing behind, a state that moved during an engine call, a lost compare-and-set, a session that never stops changing, and the flow as a `JobHandler` of the persistence worker.
* `tests/integration/test_inmemory_store_contract.py` (repo root): the persistence contract suite against the fake.
* `test_properties.py`: a hypothesis stateful machine drives a Guided and an Enterprise session with random messages, taps, gate decisions (current, stale, wrong hash, wrong role), UI actions, engine failures, hand-off failures, interrupted runs, racing events, duplicate events and clock jumps, and checks after every step: `spec_ready` only with full approval of its hash and after the commit (the project repo asserts the approval exists at the moment `commit_spec` runs), approved versions frozen and statuses forward-only, no carried-over approvals, at most one active run, consecutive `seq`, valid machine state. The run asserts it reached HANDED_OFF in both modes, split approvals, light revisions, cancelled and failed runs, pause and archive, so the invariants are not vacuous. Targeted rules (`approver_pushes_back`, `failing_build`) make sure approver rejections and failed builds are reached in every run, not by luck of the draw. The hand-off is defended in layers (completeness check, `assert_handoff_allowed`, hash-exact matching in the gate logic and in the store), so one defence removed alone changes nothing observable: **mutation checks** therefore remove a whole layer set. Dropping the approval filter (version and hash) in the gate logic and in the store fails it (`check_gate_status`); dropping the completeness check, `assert_handoff_allowed` and the hash filter together fails it with "spec_ready without approval of its hash".
* `test_integration_todos.py` (the D-048 items, over memory and Postgres): silence handed to Discover (the scripted engine keeps the real engine's contract), Research's references and brand site, the builder's change requests and first draft, gate comments. `test_inputs.py`: the pure input derivation (a brand link only from a user's own words; no credentials, IP literals, private hosts).
* `test_answer_resume.py` (BUG-6), `test_budget_pause.py` (GAP-5: Gate 1, a run in flight between phases, changes, answers, who may resume, a gate that fails), `test_stakeholders.py` (pure) and `test_stakeholders_flow.py` (GAP-3 through the flow), `test_flags_and_notes.py` (`content_flagged`, `screen_notes`, the `flow.*` bookkeeping), all over memory and Postgres. The stateful suite also flips the budget gate at random (`budget` rule). Scenarios SIT-022, SIT-034, SIT-035 and SIT-038 play the same behaviours through the real engines.
* `test_security_review.py` (memory and Postgres): the attacks of the independent review replayed, each one mutation-checked (the test fails when its fix is removed): a claimed `requester`, a post-handoff change that skips several approvers, screen ops as a loophole, secrets in messages, comments, upload names and text (plain, zero-width-split, full-width), a spec number reused after a crash, a change derived from an upload, one person as two approvers. The stateful suite also lets an attacker who claims `requester`, an owner who holds every role and one person holding two approver roles press approve on every open gate, and checks that the requester role only ever belongs to the owner and that no `spec_ready` has one person behind two roles. `test_handoff_git.py` adds the newline, case-collision and directory-link (junction on Windows) checks.

## Decisions and assumptions

* Two flow-only transitions, `answer_given` and `rediscover` (see the table), were added to `TransitionName` (D-040).
* `STOPPED` is reachable only for a PLANNING record with no origin; `stop` normally returns to `origin`.
* Pause is refused while PLANNING (the UI offers Stop).
* Policy ids `guided-default` and `enterprise-default` are the defaults when none is given; a policy whose `mode` differs from the session mode is rejected.
* The change ops, the answer slots and how the user asked (`tap`/`typed`) travel in `PlanJobPayload.change_ops_json` as a flow-private JSON object (a bare list of `ChangeOp` is still read).
* `BuildRequest.slots` is the current slot table (it includes changes made after Gate 1); `brief` is the approved Brief.
* Typed approval binds to the current version of the open gate; only the owner can type.
* `restart` archives the session; creating the fresh session is the API's job (it calls `create_session`).
* `run_plan_job(job, worker_id, cancel)` takes the claimed `JobRecord` (system scope cannot read a job by id).
* The flow's own bookkeeping (answer counters, the budget hold, who answered which slot, the disagreements) lives in `DiscoveryState.extra` under `flow.*` keys: the contract's additive escape hatch, copied through by Discover, so no contract changed. Discover's `screen_notes` is read the same way.
* `BuildRequest.screen_notes`: Discover keeps the details about screens as one text (notes joined with `"; "`), without a screen id, so each note becomes one entry keyed `note-1`, `note-2` ... in the order given; a JSON object stored there (screen id or route to note) is passed through unchanged.
* The budget gate's answer is journalled like an engine call; a gate that raises counts as "ok" (the gateway's guard stays the authority). `GatewayBudgetGate` pauses when the tripwire is at 80% or 95%, or when the whole budget or the session's own cap has less headroom than one plan needs (`BUDGET_PLAN_RESERVE_USD`, default 0.05); a gateway without status has no gate.
* A budget pause of a run in flight returns to `origin` first (`stop`) and then pauses, and resuming a Gate 1 run starts a new run from the approved Brief with the research reused; the flow does not try to continue the old job (its units of work are gone).
* The `answer_needed` reminder and the "use the default" offer count only typed replies Discover did not read as an answer; edits of other slots do not count.
* The inbound event is recorded together with its effects, not in an earlier committed unit of work: a crash or an engine failure must not leave an event that was recorded but never applied (there is no way to un-record one through the ports, and its retry would be refused as a duplicate).

## Known gaps

* Flow-written strings (`messages`) are English only, including the secret notice.
* Reminders and the frontend are ports with no-op implementations; the API layer wires the real ones.
* `ReminderPort.schedule` is called every time an Enterprise gate is shown (including re-shows); an implementation must treat `(session, gate, hash)` as the key. `cancel` is called when the gate completes, is voided by a change, or the session is archived.
* `JobRepo.complete` completes only a RUNNING job (a queued or closed one raises `Conflict`); the port docstring says so since D-051. The flow never relies on completing a queued job: a never-started run is cancelled outright with `JobRepo.cancel`.
* A terminal "failed" with no retry has no port method: the flow ends such jobs `cancelled` with `error` set. A `JobRepo.fail(job_id, error)` would let ops tell them apart.
* A change request refused by the budget pause is dropped, not queued: the user is told to ask again after the pause is lifted.
* A disputed slot is flagged to the decision owner by a notice and the blocked gate; there is no dedicated "choose between these two" card (a card would need Discover's content to know both values).
* Which typed replies count as an answer depends on Discover's intent labels (`_ANSWER_INTENTS` in `service.py`); a new label there needs a decision here.
* A duplicate event delivered to a second process while the first is in flight calls the engine a second time (nothing else happens). Closing that would need a durable "in flight" marker with expiry.
* A pass of an aborted hand-off may already have written the git commit for `(version, hash)`; if the unit of work later fails, the version number is reused by the next run with a different hash and `commit_spec` raises `Conflict`. Pre-existing (the commit was always inside the unit of work).
