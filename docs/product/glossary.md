# Glossary

Terms as used in the [spec](context-engine-spec.md) and the [brief](../implementation/BRIEF.md); the second half of the page lists the terms the build introduced.

| Term | Meaning |
| --- | --- |
| **Context Engine** | The two-engine system built here: the Conversation Engine (Discover) and the Plan phase (Research Engine, then Spec Builder). Ends at hand-off. |
| **Session** | One conversation about one app; produces one spec file. One user or company can have many sessions. |
| **Discover** | Step 1: the turn loop that interviews the user (mostly multiple-choice cards) and fills the slot table. Its first turn is the canned greeting. |
| **Slot** | A named piece of app information (e.g. `idea.archetype`, `auth.method`). Has a level (`must`, `should`, `may`), a state (`empty`, `assumed`, `volunteered`, `confirmed`, `disputed`) and a source (`tap`, `text`, `upload`, `research`, `org_profile`, `memory`, `default`). |
| **Card** | A question shown to the user: `choice3`, `multi`, `visual3`, `text`, `upload`, `confirm`. Pre-written in the question bank; an LLM writes one (C5) only when none exists. |
| **Question bank** | The pre-prepared set of cards per slot and archetype, versioned data. |
| **Brief** | The plain-language one-pager built from the slot table (what you are building, who uses it, features, look, login, name, what we assumed). The output of Discover; approved at Gate 1. |
| **Gate 1 / Gate 2** | The two approval points. Gate 1 approves the Brief; Gate 2 approves the Plan (the spec). Each shows one output with two buttons (**Looks right**, **Change something**). Approval is bound to the version's hash; silence never approves. |
| **Hash binding** | An approval is valid only for the exact version hash it was given for; any change voids it and re-opens the gate with a diff. |
| **Plan phase** | PLANNING state: research then build, one run, one Stop button. |
| **Research Engine / Research Note** | Step 3: bounded sequential pipeline (plan, web search, extract, synthesize, cross-check). Its single output is the Research Note (facts, suggestions, flags, each with a source). Reviewed inside Gate 2 as "What research added". |
| **Untrusted content** | Web results, uploads and any user text re-read by a different call. Treated as data, never instructions; wrapped in untrusted blocks; calls that read it have no tools. |
| **Spec Builder** | Step 4: builds `APP_SPEC.md` from a deterministic baseline plus validated LLM proposals, nine validators, a critic and repair rounds. |
| **Baseline** | The complete, schema-valid spec expanded deterministically from a Domain Pack (or base recipes). Valid by construction; the model only refines it. |
| **Proposal / patch** | A structured suggested edit from an LLM call (add a screen, tune copy, ...). Code validates each; valid ones are applied, invalid ones dropped and logged. |
| **`APP_SPEC.md`** | The single output file per app: Markdown for people, fenced YAML blocks (` ```yaml id=SCR-014 kind=screen `) for machines. The only input the frontend engine needs. |
| **Plan view** | The typed, plain-language page shown at Gate 2, built from the spec (screen map, look, data, what research added, real vs mocked, roles, diff, backlog). |
| **Domain Pack** | Data for one archetype: default features, roles, entities, flows, recipe mapping, navigation, API skeletons, integrations, compliance triggers. |
| **Archetype** | The app type (e.g. `commerce`, `food_delivery`, `booking_services`, `internal_workforce`, fallback `custom`); a plain string resolved from content. |
| **Recipe** | A reusable screen pattern (e.g. `detail-with-sticky-cta`) that supplies loading, empty and error states so screen briefs stay small. |
| **Stack Profile** | Curated capability matrix of the target stack (Expo/React Native): what is supported, what needs a development build, what is unsupported. |
| **Theme / design direction** | A preset of tokens (color light and dark, type, radius, spacing, elevation, motion) rendered for visual cards. |
| **Policy** | A YAML profile (slot levels, research policy, gates, limits, model policy) that makes Guided, Enterprise or regulated behavior; never special code. Inheritance up to three levels; copied in so the file is self-contained. |
| **Guided / Enterprise** | The two modes on the same engines: anyone with about seven cards, or company workspaces with approvers, audit and deeper research. |
| **Call spec** | A versioned definition of one LLM call (id like `C1.understand`, IO models, templates, route, effort, token cap, tools, untrusted inputs, mock handler, fallback). |
| **LLM Gateway** | The only code that calls an LLM: renders specs, budget-guards, retries, repairs, meters, caches, logs. Library plus HTTP API with the same interface. |
| **Budget guard / ledger** | Reserves worst-case cost before each call and commits actual cost to an append-only ledger; hard cap USD 6.00 total for live work (effective 5.70). |
| **Mock / replay / record** | Provider modes: deterministic offline handlers; served from cassettes (missing cassette fails); recording live responses into cassettes. |
| **Cassette** | A sanitized recorded provider response keyed by a hash of spec, version, model, messages, tools, effort and schema. |
| **Flow controller** | The component that owns the session state machine, gates, approvals, runs and hand-off. No state change happens outside it. |
| **Hand-off** | Step 6: commit the approved `APP_SPEC.md` and `spec.lock.json` to the project git repo, then emit `spec_ready` (only after Gate 2 approval of the current hash). |
| **Spec Loader** | Read-only library the frontend engine uses: `index`, `get`, `slice`, `neighbors`, `refs`, `diff`, `verify`, with tiered context. |
| **Build sequence** | Ordered steps S0..S10 in the spec that the frontend engine follows (S5 is the vertical slice with a stop for feedback). |
| **`raise_decision`** | How the frontend engine reports a real gap back to the Conversation Engine instead of editing the spec. |
| **Situation catalog** | The table of about 40 situations (spec section 9); each row is a scenario test. |

## Terms the build introduced

Words that came out of building and operating the system. Each row points at the page that explains it.

### Money and live mode

| Term | Meaning |
| --- | --- |
| **Live mode** | `LLM_MODE=live` in the environment of the process that makes live calls, with no `MDE_FORBID_LIVE`. [Runbook](../runbooks/live-llm-budget.md) |
| **Hard cap / effective cap** | USD 6.00 for the whole job, clamped in code whatever the environment says; the guard plans against 95% of it, USD 5.70. [ADR 0005](../adr/0005-budget-guard-fails-closed.md) |
| **Tripwire** | A level of spent plus reserved: 50% warns, 80% allows only `must-run` calls, 95% refuses everything. |
| **`must-run`** | A call purpose prefix that may still run past the 80% tripwire. In-process code only; the HTTP API rewrites it. |
| **Reservation / stale reservation** | The worst-case cost held in the ledger before a provider call and settled afterwards. One never closed counts as **spent** after ten minutes, never as released. |
| **Fail closed** | Every uncertainty about money (a failed commit, a timeout, unpriceable usage) books the worst case instead of nothing. |
| **Ledger guard** | `<ledger>.guard`: the ledger's size, line count, spend and first-line hash. A deleted or truncated ledger raises `LedgerIntegrityError` until an operator resets on purpose. [Troubleshooting](../runbooks/troubleshooting.md) |
| **Budget gate** | The flow's `BudgetGate` port. Asked before enqueueing a plan run and before each plan phase; a "no" parks the session in PAUSED with a `budget_cap_reached` notice. [Flow](../architecture/flow-and-gates.md) |
### Gates, roles and versions

| Term | Meaning |
| --- | --- |
| **Spec hash rule** | The one definition of an `APP_SPEC.md` hash: sha256 of the file with its own front-matter `hash:` value blanked (`mde_spec_loader.hashing`). [ADR 0006](../adr/0006-one-spec-hash-rule.md) |
| **Requester** | The role of the session owner, and only the owner. A `requester` claimed by anyone else is ignored. |
| **Separation of duties** | A gate with several roles needs a different person behind each; one person cannot approve for two roles. |
| **Light / heavy change** | After hand-off, a light change (requester alone is Gate 2, asked for explicitly, within the policy's limits) produces a new version without Gate 2; anything else is heavy and re-opens Gate 2. |
| **Disputed slot** | A slot two stakeholders answered differently that `Policy.role_precedence` cannot settle: the earlier value stays, the slot is flagged, and no gate completes until the decision owner chooses. |
| **Role precedence** | The policy list that says whose answer wins when stakeholders disagree. |
| **`needs_answer`** | The builder's one plain question: the run is suspended, a card is shown, and the answer resumes the run. |
| **`idle`** | The UI action reporting silence on a pending card: one nudge, then the recommended option is assumed. At a gate it does nothing; silence never approves. |
| **Plan job** | The queued run of research then build; one active run per session; claimed by a worker. |

### Storage and concurrency

| Term | Meaning |
| --- | --- |
| **Unit of work / compare-and-set** | One transaction bound to a tenant, and the session save that fails if the session changed meanwhile (`expected_version`). |
| **Replayed turn (journal)** | How a turn avoids holding a transaction across an LLM call: roll back, run the engine, replay the logic with the engine's result served from a journal. [ADR 0004](../adr/0004-transactions-never-span-engine-calls.md) |
| **Principal** | The authenticated caller: tenant, user and roles. Every unit of work is bound to its tenant. |
| **Private cluster** | The PostgreSQL server `scripts/dev_db.py` runs on 127.0.0.1:54329 with data in the main checkout; Docker is left paused. [ADR 0008](../adr/0008-private-postgres-and-docker-paused.md) |
| **Selector event loop** | The asyncio loop psycopg's async mode needs on Windows; start the API with `python -m mde_context_api`. |
| **Main checkout / anchored path** | Shared runtime data (ledger, cassettes, hand-off repos, the database directory) is resolved against the main repository, not the working directory, so every worktree agrees. |

### Contracts, testing and evals

| Term | Meaning |
| --- | --- |
| **Additive change** | A contract change that cannot break a consumer (a new optional field, model or `Literal` member): made by the lead, logged without a version bump. [ADR 0011](../adr/0011-contract-evolution-additive-only.md) |
| **Contract suite** | The 22 reusable checks (`mde_persistence.testing_contract.CHECKS`) every `Store` must pass: PostgreSQL and the in-memory fake. [ADR 0007](../adr/0007-fake-store-validated-by-contract-suite.md) |
| **Fake store** | `mde_flow.testing.InMemoryStore`, the offline stand-in for PostgreSQL, held honest by the contract suite. |
| **Deterministic fallback** | The named non-LLM answer (a bank card, a canned reply, a template) an engine uses when a call fails or the budget refuses it. |
| **Overlay provider** | The eval harness's provider (`ScriptedProvider` over `MockProvider`): scripted items for chosen call ids, mock handlers for everything else, or a live provider behind the script. |
| **Scripted provider** | A test provider that serves queued outputs, errors or delays for chosen calls. |
| **Golden** | A recorded reference output: a golden spec (`APP_SPEC.md.golden` per archetype under `evals/datasets/briefs/`, regenerated with `make_goldens`, a mock-mode snapshot). Not the same as a cassette, which records a provider response. |
| **Sufficiency probe** | An eval call that sees only one screen slice and answers concrete questions about it; every wrong answer names a field the slice lacks. [Eval plan](../evals/eval-plan.md) |
| **Persona / harness / scenario** | A scripted user (ten of them, including Hindi and Hinglish); the in-process stack the tests and evals share; one situation-catalog row played end to end. |
| **Strict `xfail` pin** | A test that pins a known product bug (`BUG-n` or `GAP-n`) and fails the suite as soon as the bug is fixed, so the fixer removes the pin. |
| **Mutation check** | Removing a fix (or a defence layer) on purpose to prove its test fails. |
| **Untrusted block / injection tripwire** | The `<untrusted_content>` wrapper around text a call re-reads; and the research screen that drops results that look like instructions. A tripwire, not the defence: the structural layers (no tools, validated output) hold without it. [Security](../architecture/security.md) |
