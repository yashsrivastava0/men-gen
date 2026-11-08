# Context Engine: final build spec

Nov 8, 2026 · Revision 3 (six steps, two approval gates, one output per engine; research now runs after Gate 1) · **Supersedes** "Context Engine and Planner - design for the enterprise mobile builder" (v1) and "Context Engine v2 - Blueprint and frontend handoff". Still valid from earlier docs: the fixed stack (v1 section 2), the enterprise architecture proposal, and the event-log and git-versioning model from the research docs. All numbers marked "starting value" or "estimate" are proposals to tune in a spike, not measurements.

**What changed in revision 3:** the greeting is no longer a step (it is the first turn of Discover); research moved out of the interview and runs after Gate 1, one step at a time, before the Spec Builder; research results are reviewed at Gate 2 as "What research added".

## 1. What we are building

A Context Engine that talks to the user from the first "hi" to an approved app, and ends by writing **one file per app**, `APP_SPEC.md`. That file is the only input the frontend engine needs, and the backend and QA engines read it too.

**Non-negotiables**
1. **One session is one app, and produces one spec file.** A user or company can have many apps; each has its own session and file.
2. **Mostly multiple-choice.** Each card shows three options (one marked recommended), a box to type something else, and a "you decide" link. Free text is used only where choices make no sense (names, links, the first idea).
3. **No agents.** There are many LLM calls, but each is a single call that returns structured output. Plain code decides every next step. (Slot-filling systems that give the LLM the language work and keep control flow in code are an established pattern: the LLM owns language, code owns what to ask next and when to stop.)
4. **Ask only what is necessary; assume the rest.** Necessary questions are always asked. Everything else is assumed from defaults, research or memory, and listed for the user to review.
5. **Two engines, run in sequence.** A Conversation Engine (foreground, interactive) talks to the user. After the user approves the Brief, a Plan phase runs the Research Engine first and then the Spec Builder, which writes the file. Research never runs alongside the interview.
6. **Enterprise is policy, not a different product.** The same engines run with a different policy profile: more questions, more sources, approvals.
7. **Nothing hardcoded to one company or app type.** Everything that varies lives in data: Domain Packs, question bank, policy profiles.
8. **One output per engine, one approval gate per output.** Nothing downstream starts before the user approves the output it depends on. Silence never approves.

**Market baseline.** Lovable's chat mode asks clarifying questions about audience, features and design before any code, Replit has a Plan mode that explores requirements without changing code, and Bolt's Plan mode can also search the web. So a plan step before building is expected. Our differences are the MCQ-first interview, visual choices rendered from the real component kit, and the single spec file as a precise handoff.

## 2. Flow, gates and architecture

### 2.1 The flow: six steps, two gates

| # | Step | Owner | Single output | Gate |
| --- | --- | --- | --- | --- |
| 1 | Discover | Conversation Engine (turn loop, section 3.1); its first turn is the canned greeting | **The Brief** | None |
| 2 | Review the Brief | Chat UI | The approved Brief (version and hash) | **Gate 1** |
| 3 | Research | Research Engine (section 4), one step at a time, after Gate 1 | **The Research Note** | None |
| 4 | Build the spec | Spec Builder (section 5) | **`APP_SPEC.md`** (draft version) | None |
| 5 | Review the Plan | Chat UI | The approved Plan (version and hash) | **Gate 2** |
| 6 | Hand off | Flow controller and Kernel | `spec_ready`: the file is committed to git and the frontend engine starts | None |

```
1 DISCOVER (turn loop) → 2 [GATE 1: Brief] → 3 RESEARCH → 4 BUILD THE SPEC
        → 5 [GATE 2: Plan = APP_SPEC.md] → 6 HAND OFF → frontend engine

   Steps 3 and 4 are the Plan phase (one run, one Stop button).
   Change something at a gate: patch only what is affected, make a new version,
   the same gate re-opens with a diff.
```

**Session states (code, persisted).** Every session is in exactly one state. A new session starts in DISCOVERING and its first reply is the canned greeting.

```yaml
states:
  DISCOVERING:  {on: {brief_complete: BRIEF_REVIEW}}
  BRIEF_REVIEW: {on: {approve: PLANNING, change: DISCOVERING}}   # Gate 1. A change re-enters the loop for the affected slot only, then returns here at once
  PLANNING:     {phases: [research, build], origin: remembered,
                 on: {plan_ready: PLAN_REVIEW, needs_answer: DISCOVERING, stop: origin}}
  PLAN_REVIEW:  {on: {approve: HANDED_OFF, change: PLANNING}}    # Gate 2. A change is a patch; only the affected writers re-run
  HANDED_OFF:   {on: {change: PLANNING}}                         # Light change: plan_ready skips Gate 2 and returns to HANDED_OFF. Heavy change: stops at PLAN_REVIEW
side_states: [PAUSED, STOPPED, ARCHIVED]
```

PLANNING runs the phases `research` then `build`. Research is skipped (and marked unavailable) when policy disables it, the budget is exhausted, or it fails. A change that does not touch research inputs re-enters PLANNING at `build` and reuses the Research Note (dependency map in 3.6). PLANNING remembers where its run started (`origin`: BRIEF_REVIEW, PLAN_REVIEW or HANDED_OFF); Stop returns there. After step 6 the same Conversation Engine stays in the chat for changes (section 3.6). Later engines follow the same rule: the frontend engine's one output is the working app slice, approved at its S5 stop (section 7.1), before the backend engine starts.

### 2.2 Gate rules

1. A gate shows exactly one output as a plain-language page with two buttons: **Looks right** and **Change something**. (Enterprise: **Send for approval**.)
2. An approval is bound to the version's hash. If the version changes, the approval is void and the gate re-opens with a diff and one button.
3. Typed "yes", "go ahead" or "build it" counts only when a gate is open and C1 is confident; otherwise the engine shows the buttons. The approval records whether it came from a tap or typed text.
4. A change the user asks for is its own approval of that change. Only things the engine added, assumed or repaired need the tap.
5. No step runs before its gate: research and the Spec Builder do not start before Gate 1, and the frontend engine does not start before Gate 2.
6. Silence never approves. An open gate is saved and waits; Enterprise sends reminders to the named approvers.
7. Enterprise: the required approver roles come from policy. The build starts when every required approver has approved the same hash. A rejection with comments turns each comment into a change request (C14).
8. A version that is already approved is never changed silently (for example by a repair). The engine makes a new version and asks again.

### 2.3 One output per engine

| Engine | Its single output | Where the user approves it |
| --- | --- | --- |
| Conversation Engine | The Brief (a plain-language one-pager built from the slot table) | Gate 1 |
| Research Engine | The Research Note (facts, suggestions, flags, each with a source) | Inside Gate 2: shown in the Plan view as "What research added", so there is no separate gate |
| Spec Builder | `APP_SPEC.md` | Gate 2, through the Plan view |

**What Gate 1 shows (the Brief).** Each row is tap-to-edit and opens that slot's card. It contains only what the user told us plus what we assumed.

| Row | Content |
| --- | --- |
| What you are building | One sentence (C11) |
| Who uses it | Roles |
| Main features | Checklist with toggles |
| Look | Swatch and style name |
| Login | Method |
| Name and logo | Name, logo or placeholder |
| We assumed | Every assumed value, each with a toggle |

**What Gate 2 shows (the Plan).** Built from `APP_SPEC.md` by code, plus the C11 summary.

| Part | Content |
| --- | --- |
| Screen map | Screens grouped by flow, with counts |
| Look preview | Theme rendered from the tokens |
| Data in plain words | Entities as everyday nouns |
| What research added | Each addition from research with its source link and an include or exclude toggle; compliance pointers (not legal advice). If research was skipped, it says so |
| What is real and what is mocked | Integrations tagged mock, adapter or real |
| Roles | Who sees what |
| What changed since Gate 1 | Diff |
| Phase 2 backlog | Only if present |
| Enterprise extras | Approver status, comments, optional full-file view |

### 2.4 Components

| Component | Job | Lives in |
| --- | --- | --- |
| Chat UI | Renders messages, question cards, visual cards, the two gate pages, progress | Web and mobile client |
| Chat Gateway | Auth, streaming, rate limits | Edge service |
| Flow controller | Runs the session state machine, opens and records gates, enforces the gate rules, owns the one active run per session | Context service |
| Conversation Orchestrator | The turn loop (section 3.1); stateless workers | Context service |
| LLM Gateway | Routes each call type to a model tier, validates structured output, retries, meters cost, applies per-tenant model policy | Platform service |
| Session store | State, slot table, rolling summary, pending card or gate | Postgres (hot state cached) |
| Event log | Append-only record of every turn and decision (from the earlier design) | Postgres |
| Question bank and visual bank | Pre-prepared cards and pre-rendered visual options, versioned | Content store plus CDN |
| Domain Packs and Stack Profile | Screen templates, entities, flows, recipes, themes, capability matrix | Git, versioned |
| Research Service | Search through the LLM Gateway, extractors, cache | Worker pool |
| Memory store | User and organization profiles (opt-in) | Postgres |
| Spec Draft store | Structured, versioned JSON of the app | Postgres JSONB |
| Spec Renderer | Deterministic render of the draft to `APP_SPEC.md`; the commit to the project repo happens at Gate 2 approval | Code, no LLM |
| Kernel | Starts the frontend engine when `spec_ready` fires | Existing design |

The **Spec Draft** is the working truth during the conversation. `APP_SPEC.md` is the compiled, versioned output and is read-only. This avoids chat state and file drifting apart. Git only ever holds approved versions; drafts live in the `spec_version` table.

### 2.5 Factors and where each is handled

| Factor | How it is handled |
| --- | --- |
| Latency | A tap costs 0 LLM calls and a typed message costs 1 (section 3.1). Research and building run off the hot path with progress events |
| Cost | A per-session budget metered by the LLM Gateway; the research depth dial; research and the Spec Builder run only after Gate 1, so nothing is spent on an idea the user abandons |
| Interrupts | One active run per session. Messages sent during research or building are queued and applied at Gate 2; a Stop button returns to where the run started |
| Changes | Patches only; only the affected parts re-run (section 3.6) |
| Resume | State, pending card or open gate are persisted; on return the UI shows where the user left off |
| Duplicate events | Every event has an ID; a repeated tap is ignored |
| Failure | One repair retry per call, then bank cards and templates; research never blocks (section 4.5) |
| Trust | Approvals are bound to a hash; silence never approves; web and uploaded content is data, never instructions |
| Enterprise | Approver roles, audit trail, budget cap and policy checks use the same gates |
| Scale | Tiered context slicing and the scope guard (sections 5 and 7.2) |
| Language | Replies follow the language the user writes in |
| Handoff | Git commit only at Gate 2; `spec_ready` only after approval |

## 3. Conversation Engine

### 3.1 The turn loop (inside step 1, Discover)

One loop with five steps, repeated for every event until the Brief is complete. Code decides; the LLM helps with language.

1. **Receive** an event with an ID: typed message, option tap, upload, or button. A repeated ID is ignored.
2. **Understand.** A tap needs no LLM. Typed text goes to call **C1**, which returns the intent, any slot updates with the evidence for them, references (company, "like X", regions), the language, and safety flags. The first idea (or a long upload) also goes to **C2**, which classifies the app type and flags sensitive domains. No web research runs during Discover.
3. **Apply** (code): update the slot table with provenance; run consistency checks; log to the event log; refresh the rolling summary when it grows past its budget.
4. **Reply.** Code replies to taps and simple answers with a few words of acknowledgment. **C3** is called only for questions, chit-chat, brainstorming and help mode, and its reply is streamed (table 3.2).
5. **Next** (code): pick the next card from the queue (3.4); if the bank has no card for a slot, call **C5** to write one. If no required slot is left, leave the loop and compile the Brief for Gate 1.

**Exit rules (any one):**
- No required slot is empty.
- The user taps "Finish with smart defaults".
- The card cap or turn cap is reached (starting values: 10 cards, 30 turns). The engine then offers to finish with defaults instead of asking more.

**Cost per turn:** a tap is 0 LLM calls, a typed message is 1 (C1), and a question or chat is 2 (C1 then C3, streamed while code already picks the next card).

Every typed message is also mined for slot values, so casual chat and brainstorming still fill the spec. The same loop handles changes at Gate 1 (re-enter with the affected slot's card), at Gate 2 and after handoff (section 3.6).

### 3.2 Intents and handling

| Intent | Behavior |
| --- | --- |
| Greeting | Canned reply, no LLM: "Hi! What would you like to build?" plus pre-prepared starter chips and an input box |
| Idea or answer | Apply to slots, acknowledge in a few words, ask the next card |
| Volunteered info | Apply, and skip any card it already answers |
| Question about the process or product | **C3** answers from the knowledge base (what we build, preview, code download, privacy); never invents price or time; then returns to the pending card |
| Chit-chat or brainstorming | **C3** replies briefly and warmly, offers 3 idea chips if the user seems stuck; steers back softly after two off-topic turns |
| Confused or "I don't know" | Help mode: show 3 inspiration cards, or "tell me about your business" input, or "you decide" |
| Change | Section 3.6 |
| Upload | Document, screenshot or brand file: extract slots, confirm what was understood |
| Approve | Counts only when a gate is open and C1 is confident (gate rule 3); otherwise the gate buttons are shown again |
| Stop, pause, start over | Stop during planning cancels the run and returns to where it started; pause saves state and offers resume; "start over" archives the draft and never deletes it |
| Unsafe or disallowed | Decline briefly, explain, offer a safe alternative or end |

### 3.3 Slots

Each slot has a **level**: `must` (always asked), `should` (asked in Enterprise, assumed in Guided), `may` (never asked, always defaulted). Each filled slot has a **state** (`empty`, `assumed`, `volunteered`, `confirmed`, `disputed`) and a **source** (`tap`, `text`, `upload`, `research`, `org_profile`, `memory`, `default`).

Rules: a correction overwrites, never appends; an explicit negation clears the slot; an `assumed` value never replaces a `confirmed` one; a slot the user did not touch is never reset.

| Group | Slot | Level (Guided) | Default if not answered |
| --- | --- | --- | --- |
| Idea | `idea.summary` | must | none (blocks) |
| | `idea.archetype` | must (confirm detected type) | "custom" using base recipes |
| | `scope.apps` | must if multi-sided | One app (the main one); others become separate projects |
| | `scope.features` | must (checklist, pre-ticked from the Domain Pack) | Pack defaults |
| Users | `users.roles` | must if more than one role | Single "user" role |
| | `auth.method` | must | Email or phone code |
| Look | `design.direction` | must (visual card) | The recommended direction |
| | `brand.name`, `brand.assets` | should (one combined card, skippable) | Placeholder name, no logo |
| | `design.accent`, `design.mode`, `design.nav_style` | may | From the chosen direction; follow system light/dark |
| Money | `pay.model` | must for commerce-like apps | Mocked checkout |
| | `pay.provider`, `data.source` | may | Mock provider; new generated backend |
| Behavior | `behavior.offline`, `.notifications`, `.analytics`, `locale`, `a11y` | may | Basic cache, notifications where relevant, no analytics, English, standard accessibility |
| Compliance | `compliance.flags` | must-confirm when triggered (health, finance, children, identity documents, precise location) | Conservative defaults, flagged in the spec |
| Enterprise | identity (SSO), integrations list, data classification, regions, release channel, approvers | should in Guided: not asked; **must or should in Enterprise** | From Org Profile |

A typical Guided session asks about **seven cards**: idea, confirm app type, who uses it (if needed), features, login, look, and name or logo. Hard cap: ten. Anything else is assumed.

### 3.4 Question system

**Card types**
- `choice3`: three options (one recommended), "Something else" input, "You decide" link.
- `multi`: a checklist with defaults ticked, plus "Add my own".
- `visual3`: three pre-rendered options.
- `text`: a single input (names, links).
- `upload`: logo, brand file, document, screenshot, or a link.
- `confirm`: "Here is what I understood. Right?"

**Question card (schema excerpt)**

```json
{
  "id": "Q-look-01",
  "slot": "design.direction",
  "type": "visual3",
  "text": "Which look feels closest to your app?",
  "options": [
    {"id": "A", "label": "Clean and bright", "preview": "cdn://themes/commerce/clean-bright.png",
     "recommended": true, "sets": {"design.direction": "clean-bright"}},
    {"id": "B", "label": "Bold and dark",    "preview": "cdn://themes/commerce/bold-dark.png",
     "sets": {"design.direction": "bold-dark"}},
    {"id": "C", "label": "Warm and friendly", "preview": "cdn://themes/commerce/warm.png",
     "sets": {"design.direction": "warm-friendly"}}
  ],
  "other": {"enabled": true, "placeholder": "Describe the look you want"},
  "skip": {"enabled": true, "assumes": "A"},
  "ask_if": "idea.archetype in [commerce, marketplace, booking, custom]"
}
```

**The question bank is pre-prepared.** Every card above is written and tested ahead of time per slot and per archetype, with each option declaring what it sets in the spec. This is faster, cheaper, consistent and testable. An LLM writes a card (C5) only when no bank card exists.

**Visual cards are pre-rendered.** Each style direction is a theme preset (colors, type, radius, motion) rendered offline with a sample card, list and button from the real component kit, so "what you pick is what you get". Other visual cards: navigation style, home layout, density. In Guided, show at most two visual cards (look first); default the rest.

**Order (code).** Pick the first `must` slot that is empty and whose precondition holds, ordered by dependency (type, then apps, features, login, look, payments) and impact. When none remain: Guided goes to the Brief (Gate 1), Enterprise continues with `should` slots grouped by owner.

**Assume policy.**
- "You decide", skip, or silence after one nudge: use the recommended option, mark `assumed`, give a one-line reason, continue.
- Three skips in a row: offer "Finish with smart defaults" (fast path to the Brief).
- "Build with defaults" from the first message is a valid path: only the idea and the look are asked.
- Must-confirm slots (compliance flags, and in Enterprise payments and identity) cannot pass Gate 1 as assumptions in Enterprise. In Guided they get conservative defaults plus a visible flag. Sensitive-domain flags are decided in Discover by C2 and the Domain Pack rules, not by research.

### 3.5 Memory ("it can remember")

- **Session:** the slot table plus a rolling summary (starting value: 1 to 2k tokens) plus the last few turns. The full transcript stays in the event log.
- **User or organization (opt-in, editable):** brand assets, past style picks, the Org Profile. The engine proposes remembered values ("Use the colors from your last app?") and never applies them silently the first time.
- **Resume:** on return, show "We were designing your <app>; next: <question>", or the open gate if the session was waiting on one.
- **Staleness:** a Research Note older than a set age (starting value: 14 days) is refreshed when planning next runs.

### 3.6 Changes

Changes are handled by the same engine at every step, before and after handoff.

1. **C14** turns the request into patch operations on slots or spec-item IDs.
2. Code applies them and uses the dependency map to find affected sections and research jobs:

| Changed slot | Sections rebuilt | Research re-run |
| --- | --- | --- |
| `design.*`, `brand.*` | Design tokens only | R3 only if the brand site changed |
| `scope.features` | Screens, flows, navigation, data, API, build sequence | None (the Note is reused) |
| `scope.apps`, `idea.archetype`, region | Everything | R1, R2, R4; the run goes back to Discover first |
| `auth.method`, `users.roles` | Roles, navigation gates, auth screens | None |
| `pay.*`, integrations | Integrations, affected screens, API | R5 for the changed provider |
| `behavior.*`, `locale` | Behavior section, affected screens | None |

3. Only the affected writers re-run. The user sees a plain-language diff and a new version.
4. What happens next depends on the state the change arrives in:

| State when the change arrives | What happens |
| --- | --- |
| DISCOVERING | The draft updates immediately |
| BRIEF_REVIEW (Gate 1) | The slot updates, the Brief re-renders with the changed row marked, same button |
| PLANNING (research or build) | A change to app type or `scope.apps` cancels the run and goes back to Discover at that card. Anything else is queued, acknowledged ("I'll apply this as soon as the plan is ready") and applied at Gate 2 |
| PLAN_REVIEW (Gate 2) | Patch, only the affected writers re-run, the Plan view re-renders with a diff, same button. Toggling a research addition is a change like any other |
| HANDED_OFF | A new spec version with a `revision` block listing the changed IDs. A **light** change (design tokens, copy, layout of existing screens, or a few new screens) applies directly because the user's request is the approval. A **heavy** change (scope, auth, payments, integrations, more than a set number of new screens, or anything the policy lists) re-opens Gate 2 with a diff. The Kernel then runs the frontend engine incrementally on the changed IDs |

Starting value for "a few new screens": five.

5. "I don't like this" at a visual card: show three new pre-rendered options (excluding seen ones). After two rounds, offer upload of an inspiration image or a typed description, mapped to the nearest preset with token tweaks. Bounded, never an endless regenerate loop.

Changes requested from the preview screen (for example "make the buttons rounder", "move the price") enter through the same path with the selected screen ID attached.

## 4. Research Engine (step 3, after Gate 1)

### 4.1 What it researches

Starts when Gate 1 is approved, with the full Brief as input, and runs before the Spec Builder, one step at a time. Nothing runs alongside the interview.

| Job | When | Output |
| --- | --- | --- |
| R1 Market scan | Always (archetype and region known) | Common features and UX patterns in similar apps, as feature suggestions |
| R2 Reference apps | "Like X" or a named competitor | Public feature lists (patterns only, never brand assets) |
| R3 Brand | The user gave their own site, or confirmed the one we found | Colors, fonts, tone words, logo availability, mapped to a style direction |
| R4 Compliance pointers | Sensitive-domain flag from Discover | Checklist pointers with links; not legal advice |
| R5 Integration notes | A provider is chosen | Official-docs facts: SDK availability, Expo compatibility, caveats |
| R6 Internal sources (Enterprise) | Connectors authorized | Extracted facts from PRDs, API specs, brand portals; no web |

Stack feasibility is **not** researched on the open web. It is checked against our curated Stack Profile, which is more reliable.

### 4.2 Pipeline (fixed, sequential, not an agent loop)

Plan the research (**C6**, one call: which jobs, a few queries each: two at Light, more at Standard and Deep so the depth cap below can be reached (D-078), trimmed to the depth cap and the remaining budget) → for each query in order: search (the model's hosted web search, called through the LLM Gateway) → extract facts from the result, treated as untrusted data (**C7**, no tools) → after all queries: synthesize one Research Note (**C8**) → cross-check (code: every fact has a source URL and retrieval date; high-impact facts need two independent sources or are marked `unverified`) → store.

Deep depth adds one gap-fill round: C8 lists gaps, a few more queries run, then it re-synthesizes.

**Why after Gate 1 and one step at a time:**
- The planner sees the whole Brief, so queries are specific and fewer.
- There are no joins, no late findings and no interruptions during the interview.
- Nothing is spent on research before the user approves the Brief.
- Cost is easier to cap. Concurrency is allowed inside this step but defaults to 1 (starting value, configurable).
- The price is a longer wait after Gate 1, so the UI shows progress events ("Looking at similar apps", "Checking integrations").

Why not a free-roaming research agent: Anthropic reports that agents use about 4× the tokens of chat and multi-agent research about 15×, and that such systems pay off on broad, open-ended questions. Our questions are narrow (features, brand, flags, integrations), so a bounded pipeline gets most of the benefit at a fraction of the cost.

**Depth dial (starting values, to tune)**

| Level | Used by | Search calls | Notes |
| --- | --- | --- | --- |
| Light | Guided | up to about 6 | One call after another; roughly a minute or two (estimate) |
| Standard | Larger Guided or small teams | up to about 12 | |
| Deep | Enterprise | up to about 20, plus a gap-fill round | Plus internal connectors (R6); may run several minutes |

### 4.3 How findings reach the user

Research never edits the spec silently. The Spec Builder uses the Research Note as input, and every research-driven addition is tagged `source: research` in the file. Gate 2's Plan view lists them under "What research added", each with its source link and an include or exclude toggle; toggling is a normal change (3.6). Compliance pointers appear in the same list.

In Guided, show a simple "seen in most similar apps" note and an info link. In Enterprise, show full citations. If research was skipped, the Plan view says so and the spec uses Domain Pack defaults.

### 4.4 Safety rules for web content

OWASP lists prompt injection, including indirect injection from websites and files, as the top LLM risk, and notes foolproof prevention does not exist. So assume a page will sometimes fool a model and contain the damage:
- Search results and pages are **data, never instructions.** Extraction calls (C7) have no tools, see the content inside a clearly marked untrusted block, and must return only schema-valid fields.
- Strip scripts and hidden text; cap content size; never follow links the content asks us to follow.
- Domain allow and block lists per policy.
- Outputs are validated; high-impact facts need two independent sources or are marked `unverified`.
- **Query privacy:** queries are built from generic terms; in Enterprise, a policy flag decides whether the company name may appear in a search query at all. Internal documents never go to a search provider.
- Copyright: store facts, a short paraphrase and the URL, with the retrieval date; no long quotes.
- Brand extraction only for a site the user owns or confirms.

### 4.5 Failure handling

Timeout, no results, budget cap, or policy turned research off: skip it, mark `research: unavailable`, and build from Domain Pack defaults. Never block the Plan phase and never show raw errors.

## 5. Spec Builder (step 4, the sequence inside the Context Engine)

Starts when research has finished or been skipped, and only after Gate 1. The user sees progress messages while it runs and a Stop button.

| Step | What | LLM? |
| --- | --- | --- |
| B1 | Freeze the inputs: the Brief approved at Gate 1 (version and hash) and the Research Note | No |
| B2 | Expand the Domain Pack: screen list, navigation tree, entities, API operations, flows, recipes. With no pack, compose from the base recipe library (auth, profile, settings, list, detail, form, search, notifications) plus the chosen features | No |
| B3 | Section writers in parallel: **C9a** screens and navigation, **C9b** data, API and roles, **C9c** design tokens from the chosen direction and brand. They use the Research Note for additions, tagged `source: research` | Yes |
| B4 | Screen briefs in batches of several screens per call (**C10**), run in parallel; per-batch retry | Yes |
| B5 | Assemble: assign IDs, build the reference graph, render YAML blocks | No |
| B6 | Validators (section 6.4) | No |
| B7 | Critic (**C12**): does the spec match what the user asked? contradictions? missing flows? | Yes |
| B8 | Repair (**C13**), at most two rounds, driven by validator and critic findings | Yes |
| B9 | Generate the build sequence (code, from flows and the dependency graph); render `APP_SPEC.md` as a draft version and hash it; build the Plan view (code plus the **C11** summary); emit `plan_ready(version, hash)` | No |

The git commit and `spec_ready` do **not** happen here. They happen at Gate 2 approval (step 6).

**Scope guard.** If the screen count exceeds the policy cap (starting value: 50 in Guided, configurable in Enterprise), propose phase one (top flows) and put the rest in a "Phase 2 backlog" section of the file.

**If it cannot finish.** When validation still fails after the repair rounds, the engine derives one plain question from the failing check, asks it through the `needs_answer` transition (back to Discover for that slot only), then re-runs only the failing writer.

## 6. The single file: `APP_SPEC.md`

### 6.1 Format

Markdown for people, fenced YAML blocks for machines. Each block's info string carries an ID and kind, for example ` ```yaml id=SCR-014 kind=screen `. A small loader reads the file into an index of `ID → location → size`. The file is validated against a JSON Schema per block kind.

### 6.2 Sections (fixed order)

| # | Section | Contents |
| --- | --- | --- |
| 0 | Header and index | Front matter: `spec_version`, `schema`, `app_id`, `mode`, `stack_profile`, `inherits`, `language`, `hash`; a table of sections with IDs and approximate token sizes |
| 1 | Summary | The plain-language PRD view: purpose, users, features by flow, the look, what is mocked, what was assumed |
| 2 | App | Name, one-line purpose, audience, platforms, tone |
| 3 | Design | Tokens (color light and dark, type scale, spacing, radius, elevation, motion), style direction, imagery rules, component variants |
| 4 | Navigation | Route tree to screen IDs, including auth gates |
| 5 | Data | Entities, fields, enums, state machines, seed-data guidance |
| 6 | API | Compact operation list: ID, method, path, request and response entity refs, auth. Mock fixtures guidance |
| 7 | Roles | Roles and permissions per screen and operation |
| 8 | Integrations | Each: purpose, mode (mock, adapter, real), provider, notes with research source |
| 9 | Behavior | Offline, locale, deep links, notifications, analytics events, permission prompts, error conventions, accessibility |
| 10 | Screens | One block per screen (`SCR-###`) |
| 11 | Flows | Journeys (`FLW-###`): ordered screens, used for the vertical slice and QA |
| 12 | Build sequence | Ordered steps the frontend engine follows (section 7) |
| 13 | Acceptance | Global checks (per-screen checks live in the screen blocks) |
| 14 | Assumptions and sources | Every `assumed` item with one-line impact; research additions with their toggles, sources and retrieval dates; `research: unavailable` if skipped |
| 15 | Revision | Version, previous version, changed IDs since the last build |

**Size (estimate):** about 400 to 600 tokens per screen block, so 50 screens is roughly 20 to 30k tokens; with the other sections the whole file is likely 30 to 40k tokens. The frontend engine never loads it whole.

### 6.3 Screen block (excerpt)

```yaml id=SCR-014 kind=screen
route: /product/[id]
surface: customer
recipe: detail-with-sticky-cta
purpose: Show one product and let the shopper add it to the cart
auth: public
data:
  - bind: API-getProduct(id)
    show: [name, images, price, rating, description, variants]
sections: [gallery, title_price, variant_picker, description, reviews_preview]
actions:
  - add_to_cart: {calls: API-addCartItem, on_success: "toast + cart badge"}
  - see_all:     {go: SCR-015}
states:
  out_of_stock:  {disable: add_to_cart, show: notify_me}
copy: {cta: "Add to cart"}
checks:
  - Add to cart increments the cart badge
  - An out-of-stock variant disables the button
```

Loading, empty and error states are not repeated because the recipe supplies them; only deviations appear. This keeps briefs small.

### 6.4 Validators (code, run before the file is accepted)

1. Schema valid for every block.
2. Every navigation target exists and every screen is reachable.
3. Every data binding names a real entity, field or API operation.
4. Every action resolves to a screen, operation or sheet.
5. Every role gate refers to a defined role.
6. Every flow starts and ends on a real screen.
7. Screen count within the cap; file size within budget.
8. Everything required by the Stack Profile capability matrix is supported (anything needing a development build is flagged).
9. No secrets or credentials in the file.

## 7. Build sequence and how the frontend engine consumes the file

The frontend engine starts only on `spec_ready`, which the Flow controller emits at Gate 2 approval after committing the approved version to git. It reads the file; it does not talk to the user.

### 7.1 Build sequence (generated by code, stored in section 12)

| Step | Name | Loads | Gate |
| --- | --- | --- | --- |
| S0 | Scaffold from the stack template | App, Design | App boots on web preview |
| S1 | Theme and tokens | Design | Token file validates |
| S2 | Component kit variants | Design | Kit renders in all variants |
| S3 | Navigation shell and auth gate | Navigation, Roles | All routes resolve |
| S4 | Data layer: types, API client, mock server | Data, API | Typecheck passes |
| S5 | Vertical slice: the primary flow end to end | The primary `FLW`, its screens | **Stop for user feedback** through the Conversation Engine (the frontend engine's own approval point, outside the context flow) |
| S6 | Screen groups in parallel (one task per screen, file ownership per group) | Each screen block plus references | Each screen renders in all states; its checks pass |
| S7 | Global behavior wiring: offline, locale, notifications, analytics, deep links | Behavior | Flow tests pass |
| S8 | Integrations: mocks and adapters | Integrations | Adapters typecheck |
| S9 | Polish and accessibility | Design, Behavior | Lint and accessibility checks pass |
| S10 | Hand off to QA; backend engine starts from approval | Acceptance | Release gate |

```yaml id=BUILD kind=build_sequence
steps:
  - {id: S0, name: Scaffold, load: [APP, DESIGN], gate: boots_on_web_preview}
  - {id: S5, name: Vertical slice, flow: FLW-001, screens: [SCR-001, SCR-004, SCR-014, SCR-020], stop_for_feedback: true}
  - id: S6
    name: Screen groups
    parallel:
      - {group: browse,   screens: [SCR-002, SCR-003, SCR-015], owns: ["app/(tabs)/browse/**"]}
      - {group: checkout, screens: [SCR-021, SCR-022, SCR-023], owns: ["app/checkout/**"]}
```

### 7.2 The Spec Loader (read-only library the frontend engine calls)

| Call | Returns |
| --- | --- |
| `index()` | Sections and IDs with sizes |
| `get(ids)` | Exact blocks |
| `slice(step_or_screen)` | The ready-made context bundle for one task |
| `neighbors(id)` | Screens linked by navigation or actions |
| `refs(id)` | Entities, operations and integrations a block references |
| `diff(vA, vB)` | Changed IDs between two versions |
| `verify(hash)` | Confirms the file is untampered |

`slice()` builds each task's context in tiers:

| Tier | Content | Loaded | Estimate |
| --- | --- | --- | --- |
| 0 | Header, summary line, tone, naming, stack rules | Always | 1 to 2k tokens |
| 1 | Design and navigation | Every run | 2 to 4k tokens |
| 2 | The task's block, its referenced entities, operations and integrations, signatures of the kit components its recipe uses | Per task | 1 to 2k tokens |
| 3 | Neighbors, other code, fixtures | On demand | As needed |

A screen task therefore needs well under 10k tokens of spec context whether the app has 12 screens or 50 (estimate; measure it).

**Rules for the frontend engine:** it never edits the spec and never interviews the user. A real gap (a button with no destination) goes back through `raise_decision(question, options, default)` to the Conversation Engine, which asks the user once or applies the default and logs it. Generated files carry a header such as `@spec SCR-014@v7`, so a new version marks exactly the stale screens. A hand-edited screen is marked locked and skipped on regeneration.

## 8. LLM call catalog

Every call returns JSON validated against a schema. Invalid output gets one repair retry, then falls back to a deterministic path. Model tiers are starting points.

| ID | Call | Trigger | Output | Tier |
| --- | --- | --- | --- | --- |
| C1 | Understand | Every typed message | Intent, slot updates with evidence, references, language, safety flags | Small |
| C2 | Interpret brief | First idea or large upload (Discover) | Archetype candidates with confidence, regions, named references, multi-app flag, sensitive-domain flags, size estimate | Medium |
| C3 | Respond | Questions, chit-chat, brainstorming, confusion | Short reply, optional suggested cards | Medium |
| C5 | Write card | No bank card for a slot | A card in the schema above | Medium |
| C6 | Plan research | Step 3 start | Research plan: jobs and a few queries each (two at Light, more at Standard and Deep) | Medium |
| C7 | Extract | Per search result, step 3 (no tools, untrusted input) | Facts in a fixed schema | Small |
| C8 | Synthesize research | All extractions done, step 3 | Research Note with source, date, confidence | Large |
| C9a/b/c | Draft app model | Spec Builder B3 | Screens and navigation; data, API and roles; design tokens | Large |
| C10 | Screen briefs | B4, batched in parallel | Screen blocks | Medium |
| C11 | Write summary | Brief compile (one sentence) and Plan view (plain-language PRD) | Plain-language text | Medium |
| C12 | Critic | B7 | Findings: mismatches, contradictions, gaps | Large |
| C13 | Repair | Validation or critic findings | Patched blocks | Medium |
| C14 | Interpret change | Change intent, or a rejection comment | Patch operations | Medium |

Hot path (every turn of Discover) is C1 and sometimes C3. Everything else is occasional. The web search call itself is made by the Research Engine through the same gateway and is not a structured call.

## 9. Situation catalog

Each row is also a test case (section 12).

| Situation | Detection | Behavior |
| --- | --- | --- |
| Just "hi" | Greeting intent | Canned greeting, starter chips, input box |
| Vague idea ("something like an app") | C1 low confidence | Help mode: three inspiration cards, or "tell me about your business" |
| User is confused or says "I don't know" | Confused intent | Offer examples; "you decide"; explain the choice in one line |
| Casual chat or brainstorming | Chit-chat intent | Reply briefly, offer idea chips, mine the chat for slots, steer back softly after two turns |
| Asks about cost, time, tech, code ownership | Question intent | Answer from the knowledge base only; if unknown, say how they will see it (for example "you will see the plan before anything is built") |
| Names a known app ("like X") | C2 reference | Ask which parts they want; R2 runs at step 3; build an original design, never copy brand assets |
| Pastes a long document | Upload or long text | Extract in chunks, show "Here is what I understood", ask only missing must slots |
| Uploads screenshots or Figma | Upload | Extract style and layout notes into slots; confirm |
| Several apps requested (customer plus business plus admin) | C2 multi-app flag | Ask which to build first (one app recommended); each app is its own session and file |
| Scope too large | Size estimate over cap | Propose phase one plus a Phase 2 backlog |
| Unsupported type (game, AR, desktop, hardware-heavy, background audio) | Stack Profile check | Say plainly what is not supported; offer the closest option; flag anything needing a development build and its preview limit |
| Sensitive domain (health, finance, children, identity documents, precise location) | C2 flags, pack rules | Must-confirm compliance cards in Discover, conservative defaults, visible flag; R4 adds source pointers at step 3 (not legal advice) |
| Contradiction ("no login" then "user accounts") | Consistency check | One-tap resolution card with three options |
| User ignores a card | Idle or off-topic | One nudge, then assume the recommended option and mark it |
| "You decide" | Phrase or button | Pick recommended, one-line reason, mark `assumed` |
| Changes their mind | Change intent | Section 3.6; show diff |
| Dislikes all visual options | "None of these" | Three new options; after two rounds, upload or describe |
| Specific detail about one screen | Text referencing a screen | Store as that screen's notes; mark `custom` |
| Writes in another language | C1 language | Reply in that language; app copy defaults to it; spec keywords stay English |
| Abusive, illegal or harmful app | C1 unsafe flag | Decline briefly, explain, offer a safe alternative or end; log |
| Secrets pasted (keys, passwords) | Pattern detection plus C1 flag | Redact before storing; explain how to connect services securely |
| Injection attempt in a document or search result | Untrusted-block rules, flags | Treated as data; never executed; noted in the event log |
| Abandons the session | Inactivity | Save state (including an open gate); resume later with a summary; refresh a stale Research Note |
| Wants to start over | Intent | Confirm; archive the draft; fresh session (memory options offered) |
| Types "yes" or "ok" with no gate open | Approve intent, state not a review state | Not an approval; treat as an answer to the pending card or ask what they mean |
| Types an unclear approval at a gate ("ok maybe", "looks fine but...") | C1 low confidence | Not approved; show the gate buttons again; apply any change inside the text |
| Approves, then immediately changes | Approve then change before the next step has run | Version changes, approval void, same gate re-opens with a diff |
| Types while research or building is running | State PLANNING | Queue and acknowledge; apply at Gate 2; Stop button available; a change of app type cancels the run (section 3.6) |
| Version changes while a gate is open | Hash mismatch | Approval void; re-open with a diff; never approve a stale version |
| Gate left open for a long time | Inactivity in a review state | State persists; no auto-approval; Enterprise reminds approvers |
| LLM timeout or invalid output | Gateway | One repair retry; fall back to bank cards and templates; friendly "taking longer than usual" message |
| Research slow, failed, off by policy or out of budget | Timeout, error, policy or metering | Skip it, build from Domain Pack defaults, say so in the Plan view |
| User dislikes a research-added item | Toggle at Gate 2 | Exclude it; only the affected writers re-run |
| Validation fails at build | B6, B7 | Repair up to twice; if still failing, ask the user one plain question derived from the failing check |
| Enterprise: two stakeholders disagree | Conflicting answers on one slot | Record both; resolve by the role precedence in policy or flag to the decision owner; never overwrite silently |
| Enterprise: approvers split (one approves, one rejects) | Gate approvals on one hash | The build does not start; the rejection comments become change requests; the owner decides per policy |
| Enterprise: request violates org policy (banned library, data region) | Policy check | Explain the constraint; show allowed alternatives as a card |
| Enterprise: budget cap reached | Metering | Pause before the Plan phase; explain; ask an approver |
| Post-handoff light change | Change in HANDED_OFF, light | Patch, new version, incremental frontend run; no gate |
| Post-handoff heavy change | Change in HANDED_OFF, heavy | Patch, Gate 2 re-opens with a diff; the incremental run starts after approval |

## 10. Guided and Enterprise, and company profiles

Same engines, same schema, same file, same two gates. Policy decides the rest.

| | Guided | Enterprise |
| --- | --- | --- |
| Who | Anyone, including non-technical users | Company workspaces |
| Slot levels | Table 3.3 | Stricter: `should` becomes asked, compliance must-confirm |
| Questions | About 7 cards, hard cap 10 | As many as needed, batched, routed to named owners |
| Research | Light, after Gate 1 | Standard or Deep after Gate 1, plus internal connectors, query-privacy policy |
| Starting context | Memory (opt-in) | Org Profile preloaded (brand, identity, compliance, API catalog, approvers) |
| Gates | Two, one tap each | Same two gates, plus required approver roles, comments, diff, optional full-file view |
| Audit | Event log | Event log plus approvals (who, which version and hash, tap or typed), exportable |

Enterprise competitors already gate SSO, SCIM and audit logs behind higher plans (Lovable on Business, Bolt on Teams, Replit and v0 on Enterprise), so these are expected, not differentiating.

**Policy profiles (examples of configuration, not code paths).** A company never needs special code; it needs a policy file.

| Profile | Typical differences |
| --- | --- |
| Product company building its own apps (retail, food, fintech) | Org Profile with brand and API catalog; imports existing APIs; deep research on competitors and regulation; approvers from product and security |
| IT services or consulting firm delivering for clients | Workspace per client with strict isolation; firm profile (standards, reusable packs) inherited by a client profile (brand, constraints); the client reviews through an invited reviewer link; export of code and spec for handover; IP and retention settings per client |
| Internal IT (employee or field apps) | SSO, device management and offline are must; fewer visual questions; internal release channel |
| Regulated organization (banking, health) | Compliance and data classification must-confirm; restricted research sources; regional data handling; model routing limited to approved providers; full-file review at Gate 2 |

```yaml
# policy.yaml (example values only)
org: example-services-firm
inherits: platform-default@1
slot_policy:
  auth.sso: must
  data.classification: must
  design.direction: should        # client brand usually overrides
research_policy:
  depth: deep
  web: {allow_company_name_in_queries: false, blocked_domains: []}
  connectors: [docs, wiki, tickets]
gates:
  brief: {approvers: [requester]}
  plan:  {approvers: [client_product_owner, delivery_lead], full_file_view: true}
  post_handoff:
    heavy_if: [scope, auth, pay, integrations]
    max_new_screens_light: 5
limits: {max_screens: 60}
model_policy: {providers: [approved-a, approved-b], retention: none}
```

Inheritance is at most three levels: platform defaults, organization (and client) profile, app. The generated file is **self-contained**: shared pieces are copied in at build time and tagged with `inherits`, so the frontend engine still gets exactly one file.

## 11. Data model and events

| Table | Purpose |
| --- | --- |
| `session` | One app conversation; mode; policy; owner; current state and plan phase (section 2.1) |
| `turn` (event log) | Every message, tap, call result and decision, each with an event ID |
| `slot` | Slot ID, value, state, source, evidence, updated at |
| `card_instance` | Which card was shown, answers, skips |
| `brief_version` | Versioned Brief (rendered from the slots) with its hash and status |
| `research_job`, `research_fact` | Jobs and cited facts with URL, retrieval date, confidence |
| `research_note` | Versioned Research Note with the hash of the Brief it was built from |
| `spec_draft` | Versioned JSONB of the app |
| `spec_version` | Version, hash, status (`draft`, `approved`, `handed_off`), git SHA once committed |
| `memory_item` | Opt-in user or org memory |
| `org_profile`, `policy` | Standing answers and rules |
| `approval` | Gate (`brief` or `plan`), version, hash, approver, role, decision, via (`tap` or `typed`), comments, time |

Events between the UI and the engine: client to engine `user_message`, `option_selected`, `upload_added`, `gate_decision` (approve or change, with version and hash), `ui_action` (stop, undo, restart); engine to client `assistant_message`, `card`, `progress` (with phase: research or build), `gate` (which gate, version, hash, the page to show), `plan_ready`, `spec_ready`.

## 12. Evals and definition of done

- **Scripted conversations:** every row of the situation catalog is a test with an expected state change and reply type.
- **State machine and gate tests:** every transition in section 2.1, and every gate rule in section 2.2 (hash binding, typed approval only with a gate open, silence never approves, queued messages during planning, approved versions never changed silently, split approvers).
- **Golden briefs:** 15 to 20 across app types and company profiles, each with a golden `APP_SPEC.md`; compare slots, screens, data and flows.
- **Context sufficiency probe:** give a small model only the file and ask concrete questions ("where does Add to cart go? who can see this screen?"); every wrong answer marks a missing field.
- **Ablation:** drop a field type from the schema, rebuild a sample app, compare; if quality does not drop, remove the field.
- **Metrics:** cards asked per Guided session (target 7, cap 10), share of sessions reaching `spec_ready`, first-pass approval rate at each gate, changes requested per gate, time spent in each step (including research), assumption rate, research additions accepted at Gate 2, edits after Gate 2, validator first-pass rate, screens passing gates first try, `raise_decision` calls per build, injection tests blocked, cost and latency per session (targets to set after the spike).
- **Done when:** a non-technical user can go from "hi" through both gates in about seven cards; an enterprise profile can do the same with imports and approvers; the frontend engine builds a 50-screen sample app from the file alone with no human clarification beyond logged `raise_decision` items.

## 13. Build order

| Milestone | Delivers | Exit check |
| --- | --- | --- |
| M1 | Slot table, spec schema, renderer, validators, loader (no LLM) | A hand-filled draft renders a valid `APP_SPEC.md`; loader slices it |
| M2 | Flow controller (six steps, session states, both gates, approvals bound to hashes), question bank, visual bank for one archetype, orchestrator on taps only, Plan built from Domain Pack defaults | End-to-end "hi" to `spec_ready` through both gates with no LLM for one archetype |
| M3 | C1, C2, C3: free text, chat, questions, help mode | Situation tests for the opening and understanding rows pass |
| M4 | Spec Builder writers (C9, C10), critic, repair, build sequence | Frontend engine builds a 50-screen app from the file |
| M5 | Research Engine at step 3, Light depth, "What research added" in the Plan view | Research additions appear at Gate 2 with sources and toggles; injection tests pass |
| M6 | Change handler, dependency map, versions, change handling in every state, post-handoff patches | Change tests pass; only stale screens regenerate |
| M7 | Enterprise: Org Profile, policy files, approver roles on gates, Deep research, connectors | Two different policy profiles run on the same engines |
| M8 | Evals, hardening, cost and latency tuning | Metrics in section 12 meet targets |

Build deterministic parts first; the LLM calls then plug into a working skeleton.

## 14. Risks and open decisions

**Risks**
- Weak bank cards or options make the interview feel generic; the bank needs real editorial care per archetype.
- Over-assuming can produce generic apps; mitigations are the style pick, the feature checklist and a visible assumptions list with toggles.
- Two gates can feel like friction for non-technical users; keep each to one tap and watch the drop-off metric. If Gate 2 shows little value in Guided, it can later become an automatic pass behind a policy flag.
- Research after Gate 1 means a longer wait after the user approves the Brief, and research-driven additions are seen only at Gate 2. Mitigations: progress events, the Light depth default, and one-tap toggles at Gate 2.
- Domain Packs are a content burden; the base recipe library is the fallback for any app type.
- Research cost and noise; the depth dial, caps and caching contain cost, and unverified facts are labeled.
- Prompt injection can never be fully eliminated; the design limits damage by giving extraction calls no tools and validating outputs.
- Very large apps (100+ screens) outgrow one file comfortably; the scope guard and phase backlog handle this for now.
- Enterprise programs with many apps duplicate shared parts across files; `inherits` plus build-time copy keeps them consistent.

**Decisions needed** (the recommended value applies until you say otherwise)
1. Starting caps: seven cards typical and ten maximum for Guided.
2. Light research on by default in Guided, run after Gate 1.
3. Show citations to Guided users, or only a simple note: simple note, with full citations in Enterprise.
4. How long to wait before assuming on silence: one nudge.
5. Both gates required for Guided users: yes, one tap each.
6. Typed approval ("yes, build it") allowed at a gate: yes, only when a gate is open and the engine is confident.
7. Research runs after Gate 1, not during the interview: yes.

## Sources

- Lovable: [chat mode and follow-up questions](https://lovable.dev/blog/chat-mode-and-questions), [Plan mode explained (third party)](https://www.rapidevelopers.com/md/lovable-issues/lovable-plan-mode-explained)
- Replit: [Plan vs Build mode](https://docs.replit.com/learn/plan-vs-build-mode); Bolt: [Plan Mode](https://support.bolt.new/best-practices/plan-mode)
- Anthropic: [How we built our multi-agent research system](https://www.anthropic.com/engineering/multi-agent-research-system); [summary with parallelism figures](https://signals.aktagon.com/articles/2026/03/how-we-built-our-multi-agent-research-system/); [discussion of single-agent simplicity](https://patmcguinness.substack.com/p/the-ai-agent-architecture-debate)
- OWASP: [LLM01 Prompt Injection](https://genai.owasp.org/llm01/); [layered defenses summary](https://rubinlake.com/en/technology-radar/ai-security-and-governance/prompt-injection-defenses)
- Slot filling with LLMs: [Slot Filling DAG framework (Google Cloud)](https://cdn.jsdelivr.net/gh/GoogleCloudPlatform/cxas-scrapi@main/examples/bella_notte/PATTERN.md), [dialogue state tracking with structured output](https://learn.traeai.com/t/ai-engineering/phases/05-nlp-foundations-to-advanced/29-dialogue-state-tracking.html)
- Enterprise features at builders: [SSO, RBAC and audit logs across Lovable, Replit, Bolt, v0 (ibl.ai)](https://ibl.ai/blog/enterprise-alternative-lovable-replit-internal-tools.md)
- Earlier project docs: Enterprise architecture proposal; v1 and v2 of the Context Engine; Dyad and OpenHands deep dive; App-Builder research.

Caveats: competitor feature statements come from vendor pages and third-party summaries and change quickly. The Anthropic token multiples are their reported figures for their own system, not a prediction for ours. All numeric caps, depths and token sizes are starting values.
