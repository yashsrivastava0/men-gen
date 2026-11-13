# Build prompt 02: Compass, AI-planned interview (fix and upgrade), v1, 2026-11-09

> Verbatim copy of the owner's prompt, kept for reference (Global rules B: an agent keeps a copy of its build prompts). Prompt 01 (`prompts/01-compass-build-prompt.md`) is not preserved in this repository; see D-091 for how its rules are covered. Decisions taken while executing this prompt: D-090, D-091, ADR 0015, doc 11 section 17.

Paste into Claude Code in the repo. Prompt 01 (`prompts/01-compass-build-prompt.md`) is still in force for everything this prompt does not change: repo rules, the one-folder rule, no edits to `mde_discovery`, the model (`gpt-6-luna` only), baseline safety, the live-gating rules and the **USD 2.00 total live-test cap for Compass** (count what is already spent; stay within what is left). Also read `docs/architecture-v3/11-discovery-agent-design.md` (it now has a section 17 describing this change) and `00-index-and-decisions.md` (AD-29 to AD-32).

## GLOBAL RULES (apply first; they override anything below that conflicts)
The canonical text is `prompts/00-global-rules.md`. Create `docs/implementation/GLOBAL_RULES.md` from the text below if it is missing, commit it, and add a one-line pointer to it in the repo `CLAUDE.md`.

**A. Commit policy (nothing missed, everything revertible).**
1. Start: `git status`; commit any pre-existing uncommitted changes separately as `chore(wip): checkpoint existing changes before <task>` (never discard, never mix); note branch and hash; create tag `pre/<task>-<yyyymmdd-hhmm>`.
2. Work on a branch `feat/<agent>-<task>` (one branch per parallel agent, per the repo worktree rule). Merge to `main` only when green, with `git merge --no-ff` (no squash, no rebase of shared history) so the whole task reverts with `git revert -m 1 <merge-hash>`.
3. Small atomic commits, one reason each; commit at least every 15 to 20 minutes, before any risky step (migration, large move, deletion, dependency change), and before any time or usage limit ends. No uncommitted work at the end of a session; `git status` clean.
4. Every commit leaves its area green. Moves use `git mv` in a commit containing only moves plus the config edits needed to stay green (verify with `git diff -M --stat`). Do not mix formatting, refactor, behavior and docs. Tests go with their code. Migrations, generated files, and decision/ADR/PROGRESS updates get their own commits.
5. Message: `type(scope): imperative summary` (72 characters or fewer), blank line, body (what and why, decisions, tests run with counts, risks, how to revert if non-trivial), footer `Task: <prompt id>`, `Phase: <n>`, `Refs: D-nnn, ADR-00nn`. Types: feat fix refactor test docs build chore perf revert wip. Scope: the agent or package. Follow the repo's trailer convention if any.
6. Tags: after each green phase an annotated tag `<agent>/<task>-p<n>` (for example `compass/ai-interview-p3`) with a one-paragraph description; `pre/<what>-<yyyymmdd-hhmm>` before risky steps. Never move or delete tags, branches or other agents' files without the owner's yes.
7. Never: force-push; `git reset --hard`, `git checkout .`, `git clean -fd` on uncommitted or unbacked-up work; amend or rewrite pushed commits; commit secrets, caches, `.venv`, `node_modules`, data directories or large binaries (cassettes are fine after the secret scan); commit conflict markers (`git grep '^<<<<<<<'` before every merge commit).
8. Remote: run `git remote -v`. If a GitHub remote exists, push the task branch and tags after every phase and at the end with plain `git push` (never `--force`); do not push to `main` directly unless the owner said so; no pull requests unless asked. If there is no remote, commit locally, say so in the report, and never create or change remotes.
9. Each commit must be revertible alone (`git revert <hash>`); migrations get working downgrades.
10. Report: branch and tag names; `git log --oneline` for the task; push status; working-tree status; and the exact revert commands for the last commit, the whole task (`git revert -m 1 <merge-hash>`), and returning to a tag without losing history (`git switch -c recover/<name> <tag>`).

**B. Repository layout.** `agents/<name>/` = one self-contained folder per agent (LLM prompt templates, structured call specs, schemas, examples, mocks, catalogs and data, engine logic, store, API router and `create_app()`, tests, evals, README, and a copy of its build prompts in `docs/build-prompts/`). `apps/web/` = the **global front end** for all agents (today a static HTML/CSS/vanilla-JS test console, later React), **never inside an agent folder**. `apps/<other>/` = other apps. `packages/*` = shared infrastructure and legacy packages. Agents import shared packages only, never each other or any app; the front end never imports agent code and talks to agents only through their HTTP API and shared JSON Schemas; each agent exposes `create_app()` and a `router`; package names stay `mde_<agent>`; the uv workspace members include `agents/*` and `apps/*`; update import-linter and CI paths. Front end: `apps/web/console/` (shell with an agent switcher, `shared/` tokens and components, `agents/<name>/` screens), `apps/web/app/` reserved for React, `apps/web/tests/` for UI smoke tests; static files are served by an agent's dev server from `MDE_WEB_DIR` (default `apps/web/console`). Legacy agents in `packages/` stay untouched until replaced and retired in a separate task.

**C. Always.** Log decisions and ADRs; update `PROGRESS.md`; respect the repo's rules on secrets, live spend (the owner's caps are hard limits), memory (one suite at a time) and the operating system.

---

## 0. Working rules (short)
Decide, log in `DECISIONS.md`, do not ask the owner unless blocked. Commit and tag exactly as the Global rules above say. Update `PROGRESS.md` at each phase. Run one test suite at a time. Do not print `.env`. Keep the Brief page, the change flow with undo, Gate 1 and the static UI working (regression tests for them stay green).

---

## 1. The problem (what the owner found when testing)

The questions Compass asks are **hard-coded, generic or vague**. They are not about *this* project. Examples of what is wrong: the same questions appear for a food app, a clinic app and a B2B tool; questions ignore what the user already said; questions are not tied to what the Brief and the downstream agents (Research, Blueprint, Design) actually need; there are no follow-ups when an answer is thin; and the question-and-answer record is not a first-class memory that the final Brief is built from.

**Required outcome:** whatever the user's idea, documents or first message, the questions are **generated by the LLM, in batches, specific to that project**, then **follow-up questions** are generated from the answers, and **every question and answer is stored** so the AI has full knowledge when it writes the Brief. Nothing the user says is lost, nothing is asked twice, and nothing important is skipped.

### 1.1 Diagnose first (report this before changing code)
Reproduce in the test UI in **live mode** (the cap allows it) and in mock mode, and state with file paths which of these is true:
1. The UI was showing **mock handlers** or **deterministic fallback cards** instead of model output (mock/fallback text looks generic by design).
2. The engine chooses *which* questions to ask from a **fixed list** (slot catalog → frames → queue), so only the wording is generated. This is a design flaw, not just a mock problem.
3. Cards are written **one at a time** with no view of the whole interview.
4. **No follow-up logic** exists.
5. Q&A is not stored as a retrievable memory; the Brief is built from slots only.
6. Anything else.
Also show, for five different ideas (a tiffin delivery service in Pune, a clinic appointment app, a B2B inventory tool for pharmacies, a language-learning app for children, a ride-sharing app for senior citizens), the current first questions side by side. Keep this as the "before" evidence.

---

## 2. The new design: AI-planned interview, code-governed

**Principle.** The model decides *what to ask* (specific to the project) and *how to word it*. Code decides *whether a question is allowed* (validators), *when to stop* (coverage and budget), *what is stored*, and *what is shown*. The model never controls the flow.

**The catalog changes role.** `slots.yaml` is no longer a list of questions. It becomes a **Needs Registry**: what the downstream agents need to know. Each need keeps its consumers, impact, risk and level, and gains an **`answer_spec`**: a short statement of what a *sufficient* answer contains for those consumers. Examples:
- `context.geo`: country; city or region (or "nationwide"); languages spoken there; one city or several.
- `audience.segments`: at least one named segment with who they are, their main goal, where and how they use the app; separate the paying customer from the end user if different.
- `idea.business_model`: who pays whom for what; the revenue source; free versus paid parts.
- `roles.list`: each distinct kind of person who signs in; their top goal; what they can approve or change.
- `scope.features`: what each feature is *for*, and what must be in the first release.
Write an `answer_spec` for every need. Frames in `catalog/frames/` stop being scripts: they become **style guides and few-shot examples** for the planner, and the source of a **last-resort fallback card** (section 2.9).

### 2.1 Components
1. **Needs status (code):** each round, compute for every need: `satisfied | partial | missing | assumed`, its impact, what is missing against the `answer_spec`, and the current assumed value if any.
2. **Interview Memory:** a Q&A ledger, a fact ledger and a rolling summary (section 3).
3. **Question Planner (`cmp.plan_questions`)** produces a *batch* of project-specific questions (modes: `initial`, `followup`, `replan`, `deepen`, `simplify`).
4. **Question Auditor (`cmp.audit_questions`)** reviews each generated question for specificity, leading wording, jargon, duplication and fit to a need. Code validators run too.
5. **Answer Interpreter (`cmp.understand_turn`, extended)** turns each answer into **facts**, need updates, completeness and flags.
6. **Brief Compiler (`cmp.compile_brief`, new; `cmp.compile_text` becomes its narrative helper or is merged)** builds the Brief from the whole fact ledger.

### 2.2 The interview loop
- **Round 0 (idea or documents arrive):** intake and read as before → facts → needs status → `cmp.plan_questions` (mode `initial`) which also returns the **Domain Hypothesis** (so one call replaces separate `situate` + first-card calls; keep `situate` only if it is still useful). While the planner and auditor run, the user reads the **playback card** ("Here is what I understood"); the first batch is usually ready by the time they confirm. If not, show "Preparing your questions…".
- **Batch presentation:** the batch arrives as **2 to 3 themed groups** with model-written titles in the user's language (for example "How your tiffin service works", "Who orders", "Look and feel"), 3 to 4 questions each. Two UI modes: **one at a time** (default for non-technical users) and **answer all** (a form with every question; default for experts). The Situation Profile picks the default; the user can switch.
- **After each answer:** `understand_turn` extracts facts and checks **completeness against the answer_spec**. If a high-impact need is `partial` or `vague` and no follow-up has been used for it, generate **one targeted follow-up** (mode `followup`, given the exact Q&A pair and the missing parts). Maximum follow-up depth 2 per need; total follow-ups at most 40% of base questions. A new topic that matters is added to a **backlog** for the next batch; a contradiction triggers an immediate small either/or.
- **After each batch:** **gap audit**: recompute needs status; run `cmp.plan_questions` in mode `replan` with everything answered so far; the planner may add new questions, drop obsolete ones, and must not repeat anything in the ledger. If every `must` need is satisfied or assumed and the marginal value of more questions is low, stop asking.
- **Stop rules:** coverage reached and marginal value low; or budget reached; or the user says "that's enough" (remaining needs become visible assumptions). Budgets are adaptive, not fixed scripts: **Quick** up to 5 questions, **Standard** up to 12 including follow-ups, **Thorough** up to 25 in batches of 6 to 8. The user chooses at the start and can press **Ask me more** at any time (mode `deepen`: deeper domain questions on the lowest-confidence areas). Compass is the agent that asks the most questions, so allow depth when the user wants it; stop early when the user shows fatigue (many skips or "you decide").
- **Then** propose-then-correct checklists, research seeding, handoff probes, compile and Gate 1, exactly as before, but built from the fact ledger.

### 2.3 What the first batch must contain (planner instructions, enforced by the auditor)
For the first batch (5 to 7 questions, or 4 to 5 in Quick): (a) if domain confidence is low, 1 or 2 broad grounding questions first (for example "describe a typical day of one customer using this"), not detailed ones; (b) 2 or 3 **domain-specific mechanics** questions that only make sense for this idea (how money and responsibility flow, what is unique); (c) the consumer-driven basics (where, who, which apps, roles) **phrased in the project's own terms**; (d) at least one **expert blind-spot** question: something a seasoned product consultant in this domain would ask that the user has not covered. When documents are rich, ask **less**: confirm and fill gaps only, never ask what a document already answers.

### 2.4 Question object (planner output)
Each question: `id`, `group`, `intent` (what we want to learn, plain words), `serves` (needs it fills; or `domain:<topic>` for project-specific details), `unlocks` (which downstream decision: Research query, Blueprint roles, Design tone...), `answer_spec` (what a good answer contains), `depends_on`, `priority`, `type` (choose_one | pick_many | priority | confirm | text | quick_form | upload_or_link), `text`, `why_we_ask`, `options[]` (label, short description, `basis`: your_document | your_words | typical | suggestion, `reason`), `recommended`, `allow_custom` (always true), `skip_assumes`, `followup_hint` (what to ask if vague), `examples` (placeholder hints for text questions). Batch also returns `domain_hypothesis`, `covered_needs`, `assumed_instead` (needs it chose not to ask, with the proposed assumption), `expert_gaps`, `backlog`.
**Rule:** use `choose_one` only when you can name three genuinely distinct, plausible alternatives *for this project*. Otherwise use `text` or `quick_form` with example hints. Never invent options to fill the shape.

### 2.5 Rules for generated questions (put these in the prompt and in validators)
Specific to the user's project and words; one idea per question; plain language (no jargon for non-technical users); neutral and non-leading; answerable by this user; short (question at most about 20 words, options at most 8); in the user's language; never ask what the facts already answer (turn it into a confirmation instead); never ask a technology or stack question (fixed stack); never offer a tier C capability or a declined category as an option; options that state facts carry a basis and no invented numbers; features beyond the seed families are flagged `custom`; hypotheses are phrased as questions ("Will riders be your own staff or independent?"), never as statements. Bad versus good: BAD "What features do you want?" GOOD "For ordering a tiffin: should people pick a meal each day, subscribe to a weekly plan, or both?"

### 2.6 Specificity, hallucination and duplication controls
- **Code validators:** every non-universal question must contain at least one project term (from the idea, facts or glossary); no semantic duplicate of any ledger question (compare `intent`, `serves`, normalized text); every question maps to a need or a `domain:` topic; options pass the existing card validation; language matches.
- **Auditor call** (low to medium effort): per question `specific` 1 to 5, `leading`, `jargon`, `duplicate_of`, `answerable`, `maps_to_need`, `issues`, `fix`. Reject below 3 or any flag; one repair call to the planner for the rejected items; otherwise drop them. Never silently substitute a canned question.
- **Cross-idea diversity test (offline scripted and live):** run the planner on the five ideas in 1.1. Non-universal questions must differ materially between ideas (token overlap below a threshold you calibrate and document) and at least 90% must contain idea-specific terms.
- **Under-asking control:** the planner returns `expert_gaps`; code checks that every `must` need and every high-impact need is either covered by a question, satisfied by facts, or listed in `assumed_instead`.
- **Over-asking control:** facts with confidence above a threshold close a need; the question becomes a confirmation inside playback or a checklist.

### 2.7 Presentation (beautiful and structured)
The card shape from prompt 01 stays (question, why-we-ask, up to three options with a recommended one and a basis label, a type-your-own box that also takes voice, You decide, Skip, Back). Add: themed group headers with a progress indicator; a **"Why am I being asked this?"** popover showing `why_we_ask` and `unlocks`; an **Answer all at once** form mode; a visible highlight of the Brief row that each answer just filled; **Ask me more**; an editable **Questions and answers** list in the History tab (editing an answer re-derives its facts and triggers a replan). A **banner in mock mode** reading "MOCK MODE: questions are canned" so a mock session is never mistaken for the product. All UI work lives in the global front end (`apps/web/console/agents/compass/` and `shared/`), never inside the agent folder; the agent exposes only its HTTP API and JSON Schemas.

### 2.8 Mock mode must not pretend
Mock handlers exist for offline tests and demos only. Make the mock planner produce clearly labelled `[mock]` questions built from the idea's extracted terms and the Needs Registry (not from a fixed frame list). Tests that need exact behavior use the **scripted provider** with hand-written JSON outputs per scenario. For manual testing, default to live when `LLM_MODE=live` is set; otherwise mock with the banner.

### 2.9 Fallback is a failure, not a feature
If the planner fails validation after one repair, use a minimal deterministic fallback from the Needs Registry, **flag it** (`fallback=true`) in the call log and the developer drawer, and count it. In live batches the fallback rate must be zero; any fallback is a bug to report.

---

## 3. Interview Memory (how the AI keeps full knowledge)

### 3.1 What is stored (PostgreSQL, tables prefixed `compass_`, plus fakes and the contract suite)
- **`qa`**: one row per question and answer: question id, batch and group, mode, full question object (text, options shown with basis labels, `serves`, `unlocks`), the user's raw answer (choice id or free text, input mode), timestamps, status (answered | skipped | you_decide | assumed | superseded), parent question id (for follow-ups), answer version (edits create new versions).
- **`facts`** (the long-term memory): `FCT-###` id, type (requirement | rule | constraint | preference | number | example | goal | role | exclusion | reference | glossary | context), the **atomic normalized statement**, topic tags, entities, `consumers[]` (which downstream agents need it), confidence, status (active | superseded | disputed | not_used), `source` (qa id, document chunk, or event id), the **verbatim quote**, supersedes ids, created and updated timestamps.
- **`interview_summary`**: a structured rolling summary by topic (decisions, open issues, the user's vocabulary, tone notes) refreshed after each batch with `cmp.update_summary` (low-to-medium effort). Used only when facts exceed the working-context budget.
- **`batches`**: each planner run (mode, inputs hash, outputs, accepted and rejected questions, auditor verdicts, latency, cost).
- Existing tables (slots, assumptions, features, brief versions, events) stay. **Slots become a view over facts**: a slot value is derived from the facts that support it, so the Brief's evidence always points to facts and through them to quotes.

### 3.2 Extracting facts from every input
Every user message, answer and document chunk goes through `understand_turn` / `read_chunk` and yields facts, **including casual chat**. Facts are atomic (one idea each), normalized to the user's vocabulary, and carry a verbatim quote. **Quote verification (code):** the quote must be a substring (after whitespace and case normalization) of the original event or chunk; otherwise the fact is stored with `unverified_quote=true` and can never support a Locked item. A new fact that conflicts with an older one either supersedes it (clear correction) or marks both `disputed` and triggers a clarification card. Older facts are never deleted.

### 3.3 Working context for each call (token budgets in `config.py`, measured and adjustable)
Assemble in this order, each part capped: (1) **project card** (idea one-liner, category, geography, audience, apps, mode, language, situation profile; about 1.5k tokens); (2) **fact digest**: all active facts, one line each (`FCT-014 [rule|pricing] statement (from Q-03)`), grouped by topic; if over budget, include every fact for the topics relevant to the task plus the highest-impact and most recent ones, and fall back to the rolling summary for the rest (about 6k); (3) **needs status table** with `answer_spec`, missing parts and current assumptions (about 2k); (4) **asked list**: previous questions as id, intent, status (about 1.5k); (5) **last four Q&A verbatim** (about 1.5k); (6) the **task input** (for example the specific Q&A being interpreted, or the draft Brief). Never send the raw full transcript.
**Full-knowledge guarantee for the Brief:** `cmp.compile_brief` receives the **entire** active fact ledger (it is compact: even 300 facts is roughly 6 to 9k tokens), all assumptions, the full needs table, the asked list and the answers in compact form, with no truncation. If a very large document set pushes the input over a configured ceiling (default 60k tokens), compile **per Brief section** using the facts tagged for that section, then run one consistency pass over the assembled draft.

### 3.4 Nothing wasted, nothing lost
**Fact coverage check (code, at compile and before Gate 1):** every active fact must be referenced by at least one Brief item (a section row, `domain_facts`, glossary, research request or assumption) or be marked `not_used` with a reason (for example small talk). Anything else is a defect that blocks Gate 1 in tests. The Brief gains a **`facts` section** (field name `domain_facts`): the typed, normalized facts with provenance and `consumers[]`, so the domain-specific details that do not fit a fixed field still reach Research, Blueprint and Design. The Brief page gets a section **"Details we captured"** grouped by topic. Consumer views include the facts tagged for that consumer (never the transcript). An internal `interview` block (counts, coverage, fallback count, question ids) is stored for audit and excluded from consumer views.

### 3.5 Editing, undo, pivots, documents mid-way
Editing an earlier answer creates a new answer version, supersedes its facts, and triggers a replan that may drop or add questions. Undo and Back restore the previous question state. If the user **pivots** ("actually it is for clinics"), confirm the pivot, mark the old facts superseded (not deleted), rerun the domain hypothesis and replan. A document uploaded mid-interview adds facts, closes needs, removes answered questions and shows an updated playback.

---

## 4. Structured LLM calls (changes to the `calls/` folders, same folder rules as prompt 01)

| Call | Status | Purpose | Effort | Starting max out | Must not |
|---|---|---|---|---|---|
| `cmp.plan_questions` | NEW | modes initial, followup, replan, deepen, simplify; returns domain hypothesis (initial), grouped questions, coverage, expert gaps, backlog | high | 7000 | repeat ledger questions; invent options; ask about technology; present hypotheses as facts |
| `cmp.audit_questions` | NEW | per-question specificity, leading, jargon, duplicate, answerable, need mapping, fix | medium | 3000 | rewrite questions itself; approve generic ones |
| `cmp.understand_turn` | EXTENDED | for answers and chat: facts with verbatim quotes, need updates with completeness against the `answer_spec`, flags (vague, new topic, contradiction, off topic, pivot, declined category, unsafe), glossary terms, language | medium | 3500 | invent values; misquote; ask questions |
| `cmp.update_summary` | NEW | rolling structured interview summary by topic | medium | 2500 | drop facts silently |
| `cmp.compile_brief` | NEW | builds every Brief section from the full fact ledger with fact-id evidence; assigns consumers to facts | high | 8000 | use anything not in the ledger; mark Locked without verified evidence |
| `cmp.rephrase_question` | DEMOTED from `write_card` | rewrite or simplify one question when the user says "I do not understand" | medium | 1500 | change what the question is trying to learn |
| `cmp.read_chunk`, `cmp.merge_claims`, `cmp.situate`, `cmp.answer`, `cmp.scan_ambiguity`, `cmp.seed_research`, `cmp.probe_handoff`, `cmp.compile_text`, `cmp.interpret_change`, `cmp.make_variants` | KEEP | as in prompt 01; `interpret_change` and `answer` now receive the fact ledger and Q&A history slice | | | |

Each call keeps its folder (`spec.py`, `schema.py`, `prompt.jinja`, `examples/`, `mock.py`, `tests/`). The planner's `examples/` must hold **three diverse, high-quality example batches from different domains** (for instance a clinic booking app, a B2B inventory tool, a children's learning app) with a note in the prompt: "show the level of specificity expected; never reuse their wording". Use strict schemas; outputs validated; one repair; then fallback (flagged). Register the new specs the same way as the old ones. Update the call log to record which calls produced shown questions.

Prompt principles for the planner (put in `prompt.jinja`): think in this order, internally: (1) what is this project really, and how sure am I; (2) for each downstream agent, what does it still lack, per the needs table and answer specs; (3) what would a seasoned product consultant in this domain ask that is not yet covered; (4) group, order, word, and offer options only where genuinely distinct. Keep reasoning out of the output. Respect the question budget. Output only the schema.

---

## 5. Edge cases (each becomes a named scenario `SB-QP-xx`, scripted provider or live)
1. One-line idea ("an app"): broad grounding questions first, not detailed ones.
2. Rich PRD: few questions, mostly confirmations and gaps; no question that a document answers.
3. Novel domain the model is unsure about: low domain confidence → ask for a typical user day and reference apps before mechanics.
4. User answers with a long monologue: many facts extracted, several questions closed, replan drops them.
5. Vague answer to a high-impact need: exactly one follow-up; a second vague answer → assume with a visible chip, do not nag.
6. "I don't know": offer the simplified version (mode `simplify`) once, then "You decide" with a reason.
7. Contradiction across batches: clarification card, newest explicit statement wins, history kept.
8. Idea pivot mid-interview.
9. Off-topic or counter-question: answer, then re-ask or drop by relevance.
10. Declined category or tier C request mid-way: decline with alternative immediately.
11. Language: Hindi, Hinglish, English; questions and options in the user's language; keys normalized.
12. Voice transcript errors: echo names, numbers, places.
13. Planner returns duplicates, generic, jargon-heavy or too many questions: validators and auditor reject; one repair; truncate by priority; extras go to backlog.
14. Planner fails twice: flagged fallback; counted; never silent.
15. User skips everything: stop early, assume visibly.
16. User edits an earlier answer: new answer version, supersede facts, replan.
17. Documents uploaded after questions began.
18. Latency: first batch not ready: spinner with the playback card still usable.
19. Question budget exhausted with unmet must-needs: assume visibly and flag high-impact ones for review at Gate 1.
20. A fact appears in turn 2 and must reach the final Brief after 25 turns (the "needle" test).

---

## 6. Tests and evaluation

**Offline (scripted provider, deterministic):** planner output validation (accept, reject, repair, truncate, dedupe against the ledger, fallback flagged); needs-status computation; follow-up trigger rules (vague → one follow-up; specific → none; depth caps); memory assembly stays within budgets; **fact coverage check** (every fact used or `not_used`); **quote verification**; supersession and dispute; answer editing and replan; pivot; edge-case scenarios above; property tests (no question repeated; no Locked without a verified quote; every Brief item's evidence resolves to facts; consumer views contain no transcript text); regression for the Brief page, change flow, Gate 1 and UI smoke.
**Live batches (within what is left of the USD 2.00 total; print the plan and expected cost first; report spend after each; stop if an allowance is crossed; keep at least USD 0.30 in reserve for the owner's manual sessions):**
- **E. Planner quality (about 0.40):** first batches for the five ideas in 1.1, plus audit. **Print all five batches in the report**, with the old ones from the "before" evidence. Run the cross-idea diversity check.
- **F. Full interviews (about 0.50):** two simulated interviews of 15 to 25 Q&A with vague and specific answers, a planted "needle" fact in turn 2, a contradiction, a pivot; compile the Brief. Verify follow-ups fired where expected, no repeated questions, fact coverage 100%, the needle appears in the Brief, handoff probes pass.
- **G. Rich input and language (about 0.30):** a PRD-led case (few questions expected) and a Hinglish case.
Record cassettes (secret-scan), then replay offline.

**Targets (report measured values honestly):** at least 90% of non-universal questions contain project-specific terms; cross-idea overlap below your documented threshold; zero repeated or already-answered questions; follow-up fired in at least 90% of scripted vague answers and in none of the specific ones; fact coverage 100%; quote verification 100% (unverified quotes never Locked); needle fact present in the final Brief; fallback rate 0 in live batches; auditor-rejected questions repaired or dropped, never shown; handoff probes pass; first batch ready in about 10 seconds or less after playback (report actual latency); all earlier acceptance numbers from prompt 01 §9.3 still met or explained.

---

## 7. Phases (commit per phase when green; update PROGRESS.md)
0. **Diagnose** (section 1.1) and print the plan.
0b. **Restructure first, in its own commits and tag (Global rules B).** If Compass is still at `packages/compass/`: `git mv` it to `agents/compass/`, add `agents/*` to the uv workspace members, update import-linter, CI and test paths and the README, and keep everything green. Move the test UI out of the agent into the global front end: `apps/web/console/` (a shell with an agent switcher, `shared/` tokens and components, and `agents/compass/` screens), serve it from Compass's dev server through `MDE_WEB_DIR`, and move the UI smoke tests to `apps/web/tests/`. Keep a copy of both build prompts in `agents/compass/docs/build-prompts/` (or `docs/compass/build-prompts/` if the markdown rule forbids it). The agent folder then holds everything about Compass except the front end. The old `mde_discovery` stays untouched.
1. **Memory:** tables, ports, fakes, contract suite, fact and Q&A ledgers, quote verification, supersession, coverage check.
2. **Needs Registry:** add `answer_spec` to every need; needs-status computation; adapt frames into examples and fallback.
3. **Calls:** `plan_questions`, `audit_questions`, extended `understand_turn`, `update_summary`, `compile_brief`, `rephrase_question`; mocks (labelled), scripted fixtures, registration.
4. **Engine:** batch loop, groups, follow-ups, replan, stop rules, adaptive budgets, editing answers, pivots; wire the Brief compile to the fact ledger; add `domain_facts` and the "Details we captured" section; update consumer views.
5. **UI (in the global `apps/web/`):** themed groups, answer-all mode, why-popover, Ask me more, Questions and answers editor, mock banner, fallback badge.
6. **Offline tests** (section 6).
7. **Live batches E, F, G.**
8. **Report.**

## 8. Report back
1. Diagnosis with file paths (which causes were true).
2. What changed, by folder; anything outside `agents/compass/` and `apps/web/`; and the git details required by the Global rules (branch, tags, `git log --oneline`, push status, revert commands).
3. **Before and after:** the five ideas' first question batches, old and new, printed in full; one complete simulated interview transcript with follow-ups; the final Brief's `domain_facts` section for it.
4. Test counts; measured numbers against the targets in section 6; latency; fallback count.
5. Spend per batch and the total used of the USD 2.00 cap.
6. Decisions, ADRs, dependencies.
7. Deviations and open questions for the owner.
8. How to run it in live mode and in mock mode, and the URL.

## 9. Do not
Do not leave uncommitted or untagged work; do not put UI code inside the agent folder; do not ship canned questions as the product; do not let the model control flow; do not drop facts; do not pass the transcript downstream; do not exceed the cap; do not edit `mde_discovery`; do not push; do not print secrets; do not hide fallbacks.
