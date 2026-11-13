# Security: threat model and controls

What the Context Engine protects, from whom, how, and what is knowingly left open. Every control below names the code that holds it and the test that proves it. Where this page and the code disagree, the code wins and this page is the bug.

Related: [overview](overview.md), [flow and gates](flow-and-gates.md), [LLM gateway](llm-gateway.md), [data model](data-model.md), [API](../specs/events-and-api.md), [live budget runbook](../runbooks/live-llm-budget.md), [ADR 0005](../adr/0005-budget-guard-fails-closed.md), [ADR 0009](../adr/0009-security-review-and-routing.md), decisions D-055 to D-058 in [DECISIONS.md](../implementation/DECISIONS.md).

## Assets and adversaries

| Asset | Why it matters |
| --- | --- |
| The approval chain (Brief and Plan, bound to a hash) | `spec_ready` is the signal that a frontend engine may start building an app on someone's authority |
| Live LLM spend | a hard ceiling of USD 8.00 for the whole job (the cap in force is `LLM_BUDGET_USD`, default 6.00); a bug here is real money |
| Secrets: the OpenAI key, the database password, keys a user pastes into the chat | leaking any of them is the worst outcome of a log line or a stored event |
| Tenant data: ideas, uploads, specs, approvals | one tenant must never see or change another's |
| The host: files, git, the shell | the hand-off writes a git repo; engineers' agents run shell commands |

| Adversary | Examples |
| --- | --- |
| A user, careless or hostile | pastes a key; claims an approver role; replays or races events; sends huge or odd input |
| Hostile content | a web page, search result or uploaded document that talks to the model ("ignore your instructions...") |
| Another tenant | guesses session ids; reads or writes across tenants |
| The model itself | returns malformed, oversized, or instruction-following output |
| A buggy or hostile caller of the gateway | asks for a larger budget, a new scope kind, or a privileged purpose |
| An AI coding agent working on this repo | accidentally reads `.env`, switches on live mode, or spends past the cap |

Out of scope by design: real SSO and SCIM (roles and approvals are modelled; the API's identity headers are the seam that SSO replaces), fetching URLs a user supplies (not built, so no server-side request forgery), parsing PDFs (text, Markdown and images only), and an attacker with write access to the developer's file system.

## Controls

### 1. Untrusted content is data, never instructions

Web results, search text, uploads and any user text that a *different* call re-reads are untrusted (hard rule 5).

* **Structure, not wording.** A call spec lists its `untrusted_inputs`; the gateway wraps each in `<untrusted_content source="..." sha256="...">` (hidden characters stripped, a nested tag defused, size capped, `mde_llm_gateway.untrusted`), appends a notice to the system prompt, and **refuses to run a spec that has both untrusted inputs and tools** (`UntrustedWithTools`, a hard error). A call that reads web text cannot call anything.
* **Research layers** (`mde_research.safety`): clean (hidden characters, scripts, tags and every non-source URL removed), an instruction look-alike tripwire that drops a result before any model reads it, C7 without tools, every returned fact validated in code (no URLs, markup or instructions; at most 240 characters; the source URL must be one the search returned), C8 reading the facts as untrusted too, links never followed. The tripwire is explicitly *not* the defence; the structural layers hold when a payload gets past it.
* **Templates and parsers.** Jinja runs sandboxed with `StrictUndefined`; every YAML read is `safe_load`; `ask_if` is a hand-written bounded evaluator; there is no `eval`, `exec` or `pickle` anywhere (hard rule 9, scanned in `tests/security/test_hardening_static.py`).
* **Engines cannot act.** An engine may return messages, cards, notices and progress. A gate, `plan_ready`, `spec_ready` or a state change from an engine is dropped by the flow, so injected text cannot approve anything.
* **Proof:** `tests/security/test_injection_suite.py` (nine payloads, hidden in a search result, an uploaded document and page-like content, through the real code paths end to end: none may change an output, reach the spec, leak a system prompt or the fake key, approve a gate or trigger `spec_ready`), the research suite (eleven payloads through the screen, five through a model that obeys), `tests/security/test_hardening_static.py`. Whether a real model *obeys* a payload is measured only by live batch L4, which has not run.

### 2. Gates, hash binding and the hand-off

* The state machine is one table; `machine.apply` is the only code that writes `state`, `plan_phase`, `origin` or `paused_from` (an AST test fails on any other writer).
* An approval is recorded on exactly `(version, hash)`. A new version starts with none. `assert_handoff_allowed` refuses a spec whose text does not hash to its recorded hash, and refuses unless every required role approved the current hash. There is **one** `SpecReady(...)` constructor in the codebase (`FlowService._handoff`), reached only after that check and a successful commit.
* **One hash rule.** `mde_spec_loader.hashing.compute_spec_hash` is the only definition; the builder, the loader and the flow all call it ([ADR 0006](../adr/0006-one-spec-hash-rule.md)).
* **Who may decide.** Only the session owner is the `requester` (a `requester` in a caller's claimed roles is ignored). A caller who holds no role in the session gets `NotFound`, word for word the answer for an id that does not exist. A gate with several roles needs a different person behind each; the owner acts only as the requester. After hand-off, a change skips Gate 2 only when `policy.gates.plan.approvers == ["requester"]` and the change was asked for (not derived from an upload or an inference) and is within the policy's limits.
* **Silence never approves**: no timer approves; the `idle` action at a gate is a no-op.
* **Immutability.** `SpecRepo.add` refuses a second write of a version; a database trigger (`spec_version_guard`) refuses edits to content and illegal status moves, even from raw SQL.
* **Proof:** `test_security_review.py` in the flow package (each attack replayed and mutation-checked), the hypothesis stateful suite with attacker rules (an attacker who claims the requester role presses approve on every open gate; one person holding two roles), and the persistence contract suite. The stateful suite is not sensitive to dropping a single defence layer (D-050): it fails when the approval filter, or the completeness check plus `assert_handoff_allowed` plus the hash filter, are dropped together. A single-layer mutation test remains a hardening idea.
* **The hand-off adapter** (`LocalGitProjectRepo`) validates `tenant_id` and `app_id` with `fullmatch` (no separators, `..`, drive letters, reserved Windows names, case collisions), refuses a symlink or Windows junction inside the path, scrubs every `GIT_*` variable, pins identity and config through `-c` flags, disables hooks and signing, commits with `--no-verify`, and is idempotent per `(version, hash)` through `spec-v<N>` tags.

### 3. The budget guard and the ledger

Total live spend never exceeds USD 8.00 (ceiling; default cap in force 6.00). Details: [llm-gateway](llm-gateway.md), [runbook](../runbooks/live-llm-budget.md), [ADR 0005](../adr/0005-budget-guard-fails-closed.md).

* **Live gating:** `LLM_MODE=live` and `MDE_FORBID_LIVE` unset (the pytest autouse fixture sets it, so a test cannot go live even if `LLM_MODE=live` is in the outer environment). `mde_llm_gateway.gating.require_live` checks both.
* **Reserve, then call.** Every provider attempt reserves its worst case in the ledger inside one critical section (file lock) with the cap check, so parallel processes cannot overshoot. `HARD_CAP_USD = 8.00` clamps `LLM_BUDGET_USD`; the effective cap is at most USD 7.60; unknown models, tools and scope kinds are refused; scoped caps apply; tripwires at 50%, 80% (only `must-run` purposes) and 95% (everything refused).
* **Fail closed.** A commit that fails, a negative or unpriceable usage, a timeout, a cancellation or a bug after the request left books the **worst case**; a reservation never closed counts as **spent** after ten minutes, never as released. `must-run` is a privilege of in-process code: the HTTP API rewrites it to `api:must-run...`.
* **Ledger integrity.** The ledger path is anchored to the main repository root (another working directory cannot see a fresh USD 6) and guarded by `<ledger>.guard` (size, lines, spend, first-line hash). A deleted, truncated or replaced ledger makes live calls refuse with `LedgerIntegrityError` (HTTP 503) until an operator resets deliberately.
* **External backstop.** The in-repo guard cannot see spend by other tools. Set a USD 6 limit on the OpenAI project in the dashboard before the first live batch.
* **Proof:** the gateway suite (95% source coverage), `test_hardening_static.py` for the hook, the `llm-gateway-api` contract test on both clients.

### 4. Secrets

* **Never read `.env`.** `.claude/settings.json` denies reading `.env*` and the common shell spellings that print it or the environment, for both the Bash and PowerShell tools. Settings hold the key as `SecretStr`; `repr` and `model_dump` of `Settings` hide `database_url` (it carries a password); `mde doctor` and `scripts/doctor.py` report presence and names only.
* **Redaction** (`mde_core.redact`): API-key families, bearer tokens, connection strings, private-key blocks, password-style assignments. Every pattern is linear (100,000 characters in about 10 ms; long input is scanned in overlapping windows; an unterminated PEM header redacts to the end). Text is folded first (NFKC, hidden characters, dashes), so a key split with zero-width characters, a soft hyphen or typed in full-width letters is still found; `redact_text` therefore returns the *normalised* text.
* **Before storage.** `mde_flow.screening.screen_event` redacts every inbound free-text field (message, "something else" text, an upload's text and file name, a gate comment) before the event is recorded, shown to another approver or given to an engine. Inbound fields also have maximum lengths.
* **Logs.** `configure_logging` scrubs structlog events and stdlib `logging` records (a filter on the root and existing handlers plus a wrapped `LogRecord` factory for handlers attached later, such as uvicorn's); a record that cannot be scrubbed is withheld. Prompts are not logged unless `LOG_PROMPTS=1`, and then redacted.
* **Spec and cassettes.** Validator 9 of the spec builder rejects a secret-looking assignment in a spec (normalised first; prose like "password: incorrect" is a warning). Cassettes carry no headers or keys and every text field passes `redact_text`.
* **Repository.** `tests/security/test_repo_hygiene.py` scans every tracked or unignored file, `docs/` included, plus runtime data (ledger, projects), for key-like text and for the actual values in `.env` (compared in memory, never printed). A deliberate fixture carries `secret-scan: allow` on its line.
* **Proof:** `test_secrets.py`, `test_log_redaction.py`, `test_spec_secrets.py`, `test_repo_hygiene.py`, `packages/core/tests/test_redact_hardening.py` (a time bound at 100,000 characters for every pattern family).

### 5. Tenant isolation

Every unit of work is bound to the caller's tenant by the `Principal` the API builds from the request; every query filters by `tenant_id`; composite foreign keys `(session_id, tenant_id)` make a mismatched child row impossible even in raw SQL; another tenant's id behaves exactly like an id that does not exist (reads empty, writes `NotFound`). System scope (`begin(None)`) can only claim and recover jobs. The hand-off path uses the validated tenant id. Proof: `tests/security/test_tenant_isolation.py` on both stores, the persistence contract suite (`check_tenant_isolation_everywhere`). See [data-model](data-model.md).

### 6. The API surface

* Bearer token compared in constant time plus `X-Tenant-Id`, `X-User-Id`, `X-Roles`; anything wrong is the same `401`. With no gateway token configured, the gateway API refuses every `/v1` request.
* Request bodies over `API_MAX_BODY_BYTES` are refused before parsing; uploads are text, Markdown and images only (image bytes never enter an event, only a `data_ref`); a user-supplied URL is never fetched.
* Every failure is `{code, message}`; a `500` carries a request id and never a stack trace.
* `/readyz` is unauthenticated and says `ok` or one word per check (`database`, `ledger`): never a reason, a path, a number or the budget's state. `/docs`, `/redoc` and `/openapi.json` are served only with `API_DOCS=1`. The numbers are `GET /v1/budget`, behind the token.
* No connection is held across an SSE stream or a sleep, so idle streams cannot exhaust the pool.

### 7. Hooks and permissions (the agent tooling)

* `.claude/settings.json`: an allow list (`uv`, `just`, `git`, `pytest`, `ruff`, `pyright`, `alembic`, `docker compose`, `python scripts/...`) and a deny list for `.env` and environment dumps, mirrored for the `PowerShell` tool.
* `format_py.py` (PostToolUse) formats `.py` files after edits and ignores everything else.
* CI runs with `permissions: contents: read`.

## The first independent security review (D-055 to D-058)

A read-only reviewer examined `main`; every finding was reproduced in mock mode. No critical findings and four high ones (D-055 counts four and itemises H1 to H3). Routing and fixes:

| Id | Severity | Finding | Fix (decision) |
| --- | --- | --- | --- |
| H1 | high | `requester` claimable from `principal.roles`: a same-tenant user could approve both gates and cause `spec_ready` | only the owner is the requester; a stranger gets `NotFound` (D-058, `gates.held_roles`) |
| H2 | high | post-hand-off "light" changes bypassed a multi-approver Gate 2; screen operations had no heavy category | light only when `plan approvers == ["requester"]`; screen ops count against `max_new_screens_light`; removal is also `scope`; `HeavyChange` gained `screens` (D-058, D-059) |
| H3 | high | `connection_string` redaction regex was quadratic on uncapped input: one request froze the loop (40k characters, 6.9 s) | every pattern linear; windowed scanning; `max_length` on inbound event fields (D-056) |
| M1 | medium | raw secrets stored by `record_inbound`, gate comments and upload file names | `screening.py` redacts every inbound free-text field before storage (D-058) |
| M2 | medium | redaction defeated by zero-width, soft-hyphen, full-width or space-split keys; `DB_PASSWORD=` was invisible | `normalize_for_scan` inside `redact_text`, `find_secrets`, `redact_value`; validator 9 normalises (D-056, D-057) |
| M3 | medium | a shell hook (since removed, D-140) matched only `Bash` and plain spellings | mirrored deny rules (D-056); the hook itself is gone |
| M4 | medium | ledger path relative to the working directory: another cwd meant a fresh USD 6 | anchored to the main repo root, guard file, `LedgerIntegrityError` (D-056) |
| M5 | medium | `LLM_BUDGET_USD=60`, unknown scope kinds and caller-set `must-run` weakened the guard | hard clamp, unknown kinds refused, HTTP API rewrites `must-run` (D-056) |
| M6 | medium | accounting failed open (a commit failure or stale reservation lost spend; timeouts booked input cost only) | worst case booked everywhere; stale reservations count as spent (D-056) |
| M7 | medium | `commit_spec` before the database commit with version-number reuse could wedge `spec-vN` | the draft row is committed first; later runs build `max(...)+1` (D-058) |
| M8 | medium | an upload-derived change after hand-off was handed off automatically | derived changes are always heavy (D-058) |
| M9 | medium | no separation of duties: one owner holding two approver roles reached `spec_ready` alone | one person cannot be two approvers; the owner acts only as the requester (D-058) |
| L | low | hand-off name validation (`$` accepted a trailing newline, case collisions); settings repr leaked the database password; CI had no `permissions`; `git` found in the current directory on Windows; `docs/` was exempt from the secret scan; no static ban list | `fullmatch`, case collisions and links refused; `database_url` hidden; read-only CI; `git` from `PATH` only (D-064); `docs/` scanned; `test_hardening_static.py` with the `# hardening: allow` marker (D-056, D-058) |

Follow-ups from the review that were also closed: `/readyz` runs the ledger integrity check and never shows the tripwire; `/docs` and `/openapi.json` are off by default (D-066); `projects_dir` is anchored like the ledger and `scripts/docs_check.py` finds `git` on `PATH` only (D-064).

**Confirmed safe by the reviewer** (evidence in its report): a wrong hash or version is rejected; exactly one `SpecReady(` constructor sits behind `assert_handoff_allowed`; engines cannot emit gates, `spec_ready` or state changes; `approver_id` must equal the principal; the hand-off adapter has no shell and no hooks and scrubs `GIT_*`; budget reserve and append are atomic under the file lock, retries and repair each reserve, the SDK runs with `max_retries=0`, an unknown model or tool is refused, live gating is checked, API auth fails closed with `compare_digest`; mock-handler strings are never caller-controlled; the untrusted wrapper is defused, Jinja is sandboxed, all YAML is `safe_load`, there is no `eval`, `exec` or `pickle`, the `ask_if` evaluator is bounded, nothing fetches URLs server-side, and SQL uses Core with bind parameters and per-tenant filters.

**Not reviewed in that pass:** `apps/context-api`, `evals/`, `apps/cli`, the Postgres code (read, not run), `proposal_ops.py` and `baseline.py` in depth, the discovery LLM fallbacks. These are the scope of the second review.

## Review 2

Second independent, read-only review of the final tree (commit 25be56f; code identical to 46457b9). Probes were run in mock mode on the in-memory flow kit; no live calls. Decisions and routing: D-071.

**Part A: did the first review's fixes hold?** H1, H2, H3 (redaction), M2, M4, M5, M6, M7, M8 and the lows hold. M1 (inbound text screened, but id-like fields stored raw), M3 (a since-removed shell hook stripped backslashes before matching a runner path) and M9 (distinct people enforced, identities caller-asserted) were partial.

| Id | Severity | Finding | Status |
| --- | --- | --- | --- |
| N1 | high | Quadratic injection-lexicon pattern on every typed message and upload: 40k spaces took 21 s on the shared event loop | fixed, `feat/regex` (0.011 s) |
| N2 | high | Change-request `ADD_SCREEN` rule polynomial (about n^4) | fixed, `feat/regex` |
| N3 | high | Dev auth lets one token holder mint identities and roles | fixed (`API_TOKENS` server-side identity binding; the entry point refuses a non-loopback bind without it) |
| N4 | med-high | Caller picks mode and policy | fixed (`TENANT_POLICIES` and per-token binding; outside the binding is a 403) |
| N5 | med | More quadratic regexes (name pattern, research markdown/script handling) | fixed, `feat/regex`, plus others found beyond the list (spec loader parser, references, mock extractors) |
| N6 | med | No caps on SSE streams per principal or on session creation | fixed (5 streams per caller with leases and idle back-off; session creation 10 per minute and 100 per hour per caller and per tenant) |
| N7 | med | `/v1/budget` listed every tenant's session spend | fixed (five totals only, no per-scope list, no tripwire) |
| N8, N9, N10, N11 | low | huge `Last-Event-ID`; unscreened id-like fields; shell-hook backslash path; cassette PII and key check | fixed (id patterns and screening; `Last-Event-ID` digits only; the shell hook was later removed, D-140; cassette email and phone redaction and a key check on replay) |
| N12 | low | Summary lines like `hash: "..."` not escaped | accepted: the loader reads only the front matter |
| N13 | low | Builder prompts labelled research "trusted" | fixed, relabelled as screened data (specs v2) |
| N14 | low | Dev tooling allow-list (`uv`, `git`, `pytest`) allows arbitrary code, so `.env` deny rules are advisory; CI actions pinned by tag; org profile overwritable within a tenant | accepted, documented |

Also fixed by the lead: the gateway API `/readyz` shows readiness only, and the gateway cache key includes the tenant. Every regex over user, upload, search or model text is now linear (bounded whitespace gaps, anchored run starts, no lazy `.*?` over long spans, whitespace folded first, rule handlers read at most 20k characters, change-request rules 8k), guarded by `tests/security/test_regex_linearity.py` (about 30 entry points and all module-level patterns on 49 hostile families at 25k and 100k characters). Not checked by review 2: Postgres integration runs, SSE exhaustion beyond reading the code, `openai_responses.py`, `proposal_ops.py` and `baseline.py` in depth, the hand-off git adapter beyond name validation, mutation checks of the part A fixes, dependency CVEs, replay of real cassettes.

## Known accepted risks and open items

| Risk | Status |
| --- | --- |
| **Dev authentication.** Without `API_TOKENS` the API trusts identity headers behind one shared token: anyone holding it can claim any tenant, user and role. The entry point therefore refuses a non-loopback bind unless `API_TOKENS` (server-side identity and policy binding) is set. Rate limits are per process and keyed by the declared identity, there is no CORS or HTTPS, and tokens are read at start-up (no rotation) | by design for a dev seam; a gateway or SSO in front is expected before any shared deployment (spec 2.4) |
| **The injection tripwire is not a guarantee.** Structural layers hold, but whether a real model obeys a payload is only measured live (L4) | not measured yet: L4 has not run |
| **The gateway's in-memory cache is not keyed by tenant.** A cache hit for an identical input costs nothing and is visible as `cache_hit`; the answer to an identical input is identical, but a hit reveals that someone sent the same document | no decision recorded; D-055 left "accept or key by tenant" to Phase 5, so the lead should decide |
| **Research summaries are labelled "trusted"** in the C9a, C9b, C9c and C10 templates. They are derived from web content, but capped, screened and validated upstream | not relabelled yet; a template wording change (needs a `version` bump) |
| **The ledger guard cannot catch a coordinated rewrite** of both the ledger and its guard file by someone with file access | accepted; the dashboard limit on the OpenAI project is the backstop |
| **A stale reservation burns its worst case** (ten minutes after a crash), so the ledger runs ahead of the invoice | deliberate; reconcile against the dashboard |
| **Cassettes store redacted prompts.** Secrets, emails and phone numbers are redacted and replay verifies the file's own key, but other ordinary personal text a live user typed would be kept | review a cassette before committing one recorded from real user text |
| **A duplicate event delivered to a second process** while the first is in flight runs the engine again (wasted cost, no second effect) | accepted; closing it needs a durable in-flight marker |
| **An aborted hand-off pass may already have written the git commit.** The version reservation and `spec-v<N>` idempotence handle replays | accepted (D-051, D-058) |
| **The policy guard** (banned libraries, allowed regions) applies in the interview only, not at gates; a negation such as "no code push" still triggers its card | known limit (D-065) |
| **The budget gate is a heuristic** (headroom for one plan); it can only stop early. The gateway's guard stays the authority | by design |
| **`GET /v1/budget` shows spend and caps** to anyone holding the token | development route, behind auth |
| **The gateway API's `/readyz` is unauthenticated** and reports mode, provider, billable flag and the budget tripwire (the context API's `/readyz` was reduced to `ok` or one word, D-066; this one was not) | open: a lead item; keep the service on a private network until aligned |

To add a control: put it in code, add a test that fails when the control is removed (a mutation check for anything on the approval path), update this page and log the decision in `DECISIONS.md`.
