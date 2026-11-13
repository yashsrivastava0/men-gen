# 15. The Spec agent: design (v1)

- **Status:** active, created 2026-11-13. **Proposed** until its gates in [doc 16](16-spec-proof-plan.md) pass; nothing here is Proven. Decisions **AD-65 to AD-76**, extended by [doc 17](17-spec-content-model.md) (AD-77 to AD-82) ([doc 00](00-index-and-decisions.md)). Amends AD-35, AD-37 and parts of docs 03, 04, 06 and 12; every amendment is listed in §16, none is silent.
- **Amended 2026-11-14 by [doc 18](18-spec-final-architecture.md) (the final architecture, AD-83 to AD-91, V-45): where doc 18 differs, it wins.** Changed here: §4.8 and §6.1 (the one-file form only for consumers that read about 80k or more), §5.4 and §5.7 (a slice's closure carries the bodies of the blocks it uses and the route targets of its navigation effects), §8.1 and §8.2 (steps and calls: fused by size, integrations and glossary calls, hero skeleton), §8.4 and §9 (**no Verify agent for compact and standard**: Spec checks itself at three code gates with every validator; Verify is reserved for deep and not built; the repair ladder of doc 18 §3.2 replaces §9.3 for those sizes), §13 (the canary and independent-critic lines are **deliberately reversed** for compact and standard), SPC-MD-13 and SPC-OP-02 (stale). The complete list is doc 18 §10.
- **Owner directions (2026-11-13, voice, lightly cleaned; I am the sole authority):**
  - "The only role of it is to help the frontend engine. It does not do the heavy thinking."
  - "It should not be binding the frontend engine ... It can tell the design tokens and things like that, which do not affect much ... Other than that, it should not bind him, restrict him, or tie his hands."
  - "If it is a small app, focus on the small app ... if it is a big app or an enterprise app, it should be giving us the most detailed and structured format."
  - "It can divide its output and create one global reference file, so it can access it through those files."
  - "In the future there will be a lot of users who will be overusing it, so we need to handle that."
  - "We cannot hardcode the number of screens ... but logically the minimum number of screens will be required for the big app we have to put in." The front end can generate about 60 screens.
  - "Think like a developer": API routes, each page division, each page logic, global tokens and global logic.
  - "The composite agent output will be changing a little bit. Your job is to work on how you manage the things."
  - Later the same day: the USD 5 and 60-screen values are **starting values, not limits the owner has set**; think through all scenarios, edge cases and optimization before the other Claude Code builds; the engines use **a temporary Haiku-class model now and may use Gemini or OpenAI models later; nothing may depend on a model or a provider.**
- **Evidence labels:** as in doc 00. Numbers marked **[J]** are judgment starting values that the proof (doc 16) tunes; every one of them is a configuration value, never a constant in logic.
- **Read first:** [doc 12](12-four-agent-pipeline.md) §4.3 to §5, [doc 04](04-spec-package-and-handoffs.md), [doc 13](13-research-agent-design.md) §9, and the builder's `docs/research-agent/spec-contract.md` (what Research guarantees Spec).

## 0. In plain words

**What Spec is.** The last agent before the engine that writes the app. It reads what the user told us (Compass) and what the market tells us (Research) and writes one **Spec Pack**: a folder with a map file, a few global files and one small file per screen. The app-writing engine reads exactly the pieces it needs for the task in front of it.

**What it decides.** *What the app is and does*: who uses it, which screens exist, what each screen must show and do, the data, the rules, how screens connect, the words on screen, and the API the screens will call.

**What it never decides.** *How it looks*: layout, components, animation, pictures. The front-end engine keeps free hands. Spec gives only a starting look system (colours, type, spacing, a mood) that the front end may extend.

**Small, medium, big.** One format for all. A small app is a short pack (a tiny one ships as a single file the engine can read whole). A big app is split into many small files with a map, plus a build plan that says which files each worker reads, so no worker needs the whole app in its head. Even a small app is usually cut into a few tasks: the real Quick example comes to about 60k tokens in all (§14.1). When an app has more screens than the front end can build in one go, the pack is cut into **phases**, and you agree the cut before it happens.

**Any model.** The content of a Spec does not depend on which AI model reads or writes it. Moving from a Haiku-class model to Gemini or OpenAI changes normally only how the pack is cut to fit that model's window, which is code, not another model run.

**What you will see.** At the structure check (G1.5): the app map and, if it is too big for one release, the proposed Phase 1 / Phase 2 split (what is built first, what waits) for your yes. At the spec review (G2): a plain summary, a map of what each screen shows (not wireframes), what was assumed, what needs your confirmation (money, legal, identity first), and what differs from your approved Brief.

**What is new compared with the older docs.** Spec's role is written down in one place; Verify only checks; the front end's freedom is enforced by the format itself; budgets exist for how much the front end reads at once; phases replace the fixed 12/60 caps; model independence is a design rule; edge cases and cost at scale are worked out (§12 to §13).

## 1. Role and boundaries (AD-65)

### 1.1 Where Spec sits
```
 Compass ──► Research ──► SPEC ──► VERIFY ──► FRONT-END ENGINE ──► Backend ──► QA
 (the idea)  (the market) (what)   (checks)   (code and look)
              Spec reads Compass + Research and writes the Spec Pack.
              Verify reads the pack and only checks; it never writes into it.
```
Spec is one agent with three lanes that share one graph and one orchestrator (AD-35, kept): **Product** (older docs say "Blueprint"): what the app is; **Design**: the starting look system and direction; **Screens**: the page-by-page cards. A fourth step, **assemble**, is deterministic code that renders the graph into the pack.

### 1.2 What Spec does and never does
| Spec does | Spec never |
|---|---|
| Reads Compass and Research through adapters and grades every input by trust (§3) | Searches the web or reads raw pages (Research did) |
| Fixes the app's meaning: roles and access, apps, modules, screen inventory, navigation map, flows, data model and lifecycles, rules, integrations, global behaviours, API Contract v0, starter copy | Asks the user anything, except at its two review gates (G1.5, G2) |
| Gives a starting look system (tokens and direction), not layouts (§2) | Decides layout, components, motion or imagery |
| Writes one card per screen: what it shows, what each action does, every state, the words, acceptance | Writes code, pseudo-code or library choices (the Stack Kit is fixed data) |
| Packs everything in layers sized for the consumer's window, with a build plan (§5, §6) | Invents a Locked fact, a source, a number or a provider (R21 to R28) |
| Says honestly what is assumed, degraded, deferred or missing | Edits the approved Brief, or silently drops a feature the user asked for |

It is mostly **assembly, derivation and completion**. Its judgment steps (merging and splitting screens, proposing phases, writing a direction, writing cards) are few, narrow and checked by code.

### 1.3 Why it stays one agent (AD-38)
The T1 to T4 boundary test of doc 12 §3 is unchanged: Spec has no tools, trust or gate of its own that Product, Design and Screens lack, and Design must not wait for Product. Verify is separate only for T2 (independence). Nothing in this design adds an agent.

## 2. The freedom contract (AD-66)

**One line decides every field: meaning is Spec's, expression is the front end's.** The front-end engine is an LLM; over-specified input makes it produce the same template for every app, and under-specified input makes it guess. The contract below is enforced by the format, not by good intentions.

### 2.1 Who owns what
| Spec says, and how firmly | Spec stays silent (front end decides) |
|---|---|
| What each screen must communicate, as **information regions** with an importance level (`primary`, `supporting`, `optional`), bound to data. No order, no position | Layout, grid, spacing choices, composition, hierarchy inside the tokens |
| **Actions as intents** with precondition, effect, errors and behaviour flags (`optimistic`, `offline`, `idempotent`). The trigger (button, swipe, long-press) is not stated | Component or widget choice (no kit component names appear in a card), gestures, animation, micro-interaction |
| Every **state** and the message it must carry (loading, empty, error, offline, forbidden, not found, stale, partial, success, plus domain states) | How a state looks (skeleton, spinner, banner, toast) |
| **Starter copy** per language (Default; the front end may refine within the voice) | Imagery, illustration, icon choice (Spec states the *need* and the allowed sources only) |
| **Tokens**: colour roles, type scale and families (with script fallbacks), spacing, radii, elevation, motion durations and easing, breakpoints, touch targets, light and dark. Brand roles are Locked where the user supplied them, otherwise Default. The front end may add **`ext.*` tokens** (gradients, blur, spring, extra semantic colours) freely | Section position (Spec gives importance, never order) |
| **Design direction**: mood, audience, voice, what to avoid, reference *categories* (Open guidance) | Per-screen *form* hints (none; enhancement ideas live only as Open COULD entries in their own file, doc 17 §1) |
| **Navigation**: the primary destinations and their importance in three levels, plus every screen's entry points and exits | The navigation widget (tab bar, drawer, rail, top tabs) |
| **System-wide design invariants** as Default guidance only: one primary action per screen, at most two type families | Component variants and per-screen-type composition (these are the front end's own design system) |
| **Accessibility floors** (contrast, touch-target size, text scaling, announcements), **the allowed stack** and **store rules**: Locked **by policy**, each with a citation in the policy sheet (§7.4) | |

The front end may **merge or split regions, reorder them, choose any widget, add supporting UI** (dialogs, sheets, empty-state art) and extend tokens without asking. It records a deviation only where it changes a Locked or Default *meaning* (a region dropped, an action changed, a state removed). A new route or screen that is not in the inventory is flagged `unplanned` and shown at G3; it is not forbidden.

### 2.2 Enforced by schema first
1. **Closed vocabularies**: `importance`, `screen_type`, `state_kind`, `action_flag`, `bind` targets and token names are enumerations or references; a value outside them fails validation (V-24).
2. **Short typed free text and typed grammars**: `purpose` is at most two sentences; `what` and `message` are single sentences; there is no free-form "layout" or "notes" field in a card. An action's `effect` is a list of at most three typed steps (`call OP-…`, `go SCR-…`, `stay`, `external_flow <name>`), and a `guard` is a list of structured predicates (`signed_in`, `role_has_access`, `{entity, field, equals or in}`), never an expression string. Every action carries an `importance` (`primary` or `secondary`), so "one primary action per screen" can be checked.
3. **Tier ceiling per field** (§2.3): a validator rejects a card that marks a field firmer than its ceiling.
4. **Form-vocabulary lint** on free text (V-25): position words (top, bottom, left, right, above, below, beside, sticky, fixed, floating, sidebar), widget words (button, tab bar, drawer, carousel, modal, bottom sheet, toast, snackbar, banner, chip, badge, skeleton, spinner, accordion, dropdown, toggle, slider), units (px, pt, dp, rem, columns), motion words (fade, slide, bounce, animate), colour names and hex values. The lint also reads **identifiers** (region and action keys, copy keys, route names): a key that ends in a widget or trigger word (`_button`, `_btn`, `_cta`, `_modal`, `_sheet`, `_tab`, `_banner`, `_toast`, `_chip`, `_now`) names a widget, not a meaning (`pay_now`, `pay.cta`), so it is flagged. It is a **warning in v1**, tuned on the corpus with an allow-list for content uses (right-to-left, "top 20", a map as the subject of a map screen); it becomes a blocker only for the words that never name content. The schema, not the lint, is the main defence. The lint also reads `usually` and COULD text.
5. **No form hints in the build slice.** Peer practice appears as the `usually` voice: descriptive notes with an evidence class ("most delivery apps show the courier's live position; evidence: corroborated"), never an instruction. Enhancement ideas appear only as COULD entries (Open, intent and effect, a basis, at most 3 per screen, in their own file; doc 17 §1).
6. A **freedom probe** in the proof (doc 16 PS-4): the same card built twice with different style seeds must not give the same layout, otherwise Spec is over-binding.

### 2.3 Tier ceilings
| Field family | Highest firmness Spec may set |
|---|---|
| A screen's purpose, who may see it, the data it binds | Locked (only with user or document evidence), otherwise Default |
| Information regions (what, importance, freshness) | Default |
| Actions (intent, precondition, effect, errors) | Locked or Default |
| States and their messages | Default |
| Starter copy | Default |
| User-supplied brand roles (colours, fonts, logo) | Locked |
| All other tokens | Default |
| `ext.*` tokens, layout, widgets, motion, imagery | not set by Spec |
| Design direction, voice, imagery and icon policy (sources, rights) | Open |
| Accessibility floors, allowed stack, store rules | Locked by policy |
| Information shapes, interaction intents, the needs inventory and shared blocks (doc 17) | Default (MUST where evidenced) |
| Motion intents (doc 17) | Open (COULD); the non-motion alternative for an essential one is Locked by policy |
| Asset needs: the need, subject, count and avoid-list (doc 17) | Default; mood and style Open |
| COULD entries (doc 17) | Open |

### 2.4 Say this, not that
| Not (binds the front end) | Say (meaning only) |
|---|---|
| "sticky category tabs under a hero photo" | region `categories` (supporting): the menu's categories; region `photos` (primary): up to five photos of the place |
| "loading: skeleton of hero and six rows" | state `loading`: nothing to show yet; the user must still see that content is coming |
| "offline: banner, add button greyed out" | state `offline`: last saved data is shown; the action `add_to_cart` is unavailable; the message says why |
| "bottom sheet to choose delivery slot" | action `choose_slot`: the user picks one of the available slots; precondition: items in cart |

## 3. Inputs: adapters, trust, thin input (AD-67)

### 3.1 What Spec reads from Compass
Compass exports two formats today (format 2 with `consumer_views` and `structure`; format 1 with the four views but no `structure`; a trimmed variant with neither) and will change again. **All Compass-format knowledge lives in one shared reader, `packages/compass-intake`**, extracted from the reader Research already has (`agents/research/.../intake/formats.py`) so a Compass change is fixed once; if the extraction is risky the builder copies it and keeps contract tests on both (AD-33: agents import shared packages only, never each other). The reader:
- detects `format_version`, reads the **consumer views first** (AD-28, AD-44 a) and the Brief for tiers and provenance, and builds one internal `SpecInput`;
- **tolerates** a missing structure, views, quality block, tier tags or request origin; ignores unknown keys; never crashes;
- raises **drift flags** loudly: an unknown version, an expected field missing, a field present but empty, a changed enum, a size jump; flags travel to G2;
- runs a **readiness pre-check** that is fatal only when there is **no idea statement or no feature** (scope cannot be invented, and Research never sets it): Spec stops and hands the orchestrator a plain list of what Compass must still ask (Spec itself asks the user nothing). Everything else proceeds as `thin_input` and is checked at G1: **missing roles are derived from the features and the audience and marked `assumed`**, following the fill ladder of doc 12 §6 (the Brief is immutable and Research has already been paid for, AD-45).

| Compass field | What Spec uses it for | Reliability seen in the real exports (doc `notes/compass-as-built-2026-11-10.md` §7) |
|---|---|---|
| `idea`, `audience`, `geography`, `experience` (tone words, density, locales, devices), `access` (auth methods, account model) | product statement, roles, locales, direction, auth design | tone is a default in all three real runs (confidence 0.4); locales may be a sentence (hospital); geo may be country-only or a joined string |
| `features` (name, intent, scope_status, priority) | the Scope Ledger and the inventory | existence is evidenced by verified quotes; **priority is unreliable** (Thorough: 26 of 30 `nice`); duplicates exist ("Online payment", "Pay for orders online") |
| `roles`, `apps` | roles, access matrix, apps and shells | duplicated or junk entries; a name used as both role and app ("Manager side"); empty goals; `rough_permissions` empty everywhere |
| `integrations` | the integration list | thin; payments `real_later` while the user said "pay inside the app"; none at all in Standard |
| `domain_facts`, `rules_and_numbers`, `sensitive_data`, `existing_systems` | rules, data model, compliance flows | 226 of 228 facts Locked yet 33 have confidence under 0.7; a Locked "Kind of app: payments_wallet" at 0.4 |
| `structure` (modules, screens with `pattern` and `phase`, flows, entities with typed fields, states and per-role `access`, `role_matrix`, `navigation`, `issues`) | the proposal Product starts from | a proposal only: Thorough has 132 screens, 58 unreachable, entities average 3.3 fields, 44 of 72 typed `text`; Standard keeps 3 access-conflict errors |
| `meta.degraded`, `meta.probes`, `readiness`, `fallback_count`, `input_binding` | the **trust profile** (§3.4) | `degraded` can be true while `readiness.design` says pass; `fallback_count` misses timeouts |

### 3.2 What Spec reads from Research
Through the builder's contract (`research.v5` extended; unchanged file name): `lane_decisions[]`, `dossiers[]` (typed provider fields with evidence and an explicit null reason), `platform_constraints[]`, `compliance_register[]`, ranked `confirm[]`, `conflicts[]`, `items[]` and `norms[]` by Brief id, `slices{}`, and `pack(research, need_card)` (pure code; selection by Brief id, one hop, a title index; split, never truncated). **Use rules (R21 to R28) bind Spec**: a finding becomes Default only if `official` or `corroborated`; `single_source` is an Open suggestion or goes to confirm; `unverified` is never used; numbers, fees, limits and legal facts are always on confirm; a thin lane or a decision with a `gap` means "assumed" from the library, never invented; every research-derived value records the item ids (`claims_used`); a null means "not stated" and `figures_dropped` means the figure is unknown. Research never sets scope (AD-39): its `NEW` items are suggestions listed at G2.

Research's six views are not Spec's three lanes. The mapping (resolves a collision between the two documents):
| Research view (`slices`) | Used by Spec lane | For |
|---|---|---|
| `blueprint` | Product | roles, workflows, entity and lifecycle patterns, rules, competitors, trends |
| `integrations` | Product | integration options, dossiers, compliance notes, mock plan |
| `mock` | Product (mock plan), Screens (data realism) | realistic data hints, local formats |
| `design` | Design | conventions, script needs, local formats |
| `screens` | Screens, and Product (inventory check) | screen patterns, states, exceptions |
| `verify` | Verify | confirm list, defaults, compliance notes, gaps |

### 3.3 Spec's own reads are budgeted
Spec must not feed a whole export into one call: the Thorough export is 4 MB (about 1M tokens with history) and its blueprint view alone about 53k tokens; a Big Research store is far above its 40k-token threshold. So **every Spec call receives a view assembled by code** (AD-20): the skeleton call reads a *summary* of the Compass view (names, counts, roles, features, modules, entity names); each per-module Product call reads only that module's Compass slice plus `pack(need_card)` for the module's Brief ids with a `token_budget`; Screens calls read the frozen graph slice for their batch. Each call has an **input budget** from the route's consumer profile (§11) and fails the build rather than overflow. A shared entity has one **owner module** (defined once in `data-model`, referenced elsewhere). `pack(need_card)` counts characters over 4 for its `token_budget`; Spec converts its profile's token budget into that unit with the profile's factor, so Indic text is not undercounted.

### 3.4 Trust is graded per field, not per label
Compass's own tiers are weak (every feature is Locked, "approval does not mean the person reviewed it"), so Spec grades each field it uses.
| Field | Grade |
|---|---|
| Feature existence | Locked if a verified user quote supports it (the real exports do); else Default |
| Feature `priority` | Default unless the user ranked it; never used alone to demote a Locked feature |
| Role or app identity | Locked only after cleaning (merge duplicates, split role from app) with the merge recorded |
| Geography, locales, tone, density, brand | Locked only with a quote and confidence of at least 0.7; otherwise Default; a default tone is `assumed` |
| Compass `structure` | Default (a proposal) |
| Compass `assumptions` | Default |
| Research item | Default if `official` or `corroborated`; Open if `single_source`; unused if `unverified` |
| Store rules, accessibility floors, the fixed stack | Locked by **policy** (provenance `policy:<id>`) |

When Compass says `degraded: true` or the approval is unverified (`input_binding: unverified_*`), every Default stays Default, more items go to confirm, and the G2 summary leads with "weak inputs".

### 3.5 Degradation ladder (Spec never refuses over format; it refuses only an unusable Brief)
| Situation | Spec does |
|---|---|
| Research complete | normal |
| Research partial, skipped or failed | every lane reads as thin: libraries fill the gap, marked `assumed`; G2 says "research incomplete"; never a crash |
| A lane `thin` or `none` (for example screens) | pattern and state libraries fill; no invented source |
| Compass format 1 (no structure) | Product derives the structure itself (more calls, flagged) |
| Compass trimmed (no views) | views derived from the Brief |
| Both inputs thin | a minimal spec that says so; missing items go to the G2 confirm list; `missing` blocks G2 (AD-37) |

### 3.6 IDs
Compass-origin IDs (FTR, ROL, APP, MOD, SCR, FLW, ENT) are **carried** when the node is the same; a merge or split keeps the old ID as an **alias** (I-5). **Spec-minted IDs live in their own namespace** (`SCR-S012`) so a later Compass `SCR-029` cannot collide. Regions and actions are keyed inside a screen (`SCR-021.menu`, `SCR-021.add_to_cart`) and become `testID`s. **Keys are minted once and never reused; a rename adds an alias (I-5 applies to keys too); cross-card references use `SCR-id.key` and resolve through aliases** (V-31, V-32).

### 3.7 All upstream text is data
Brief quotes, Research items and page-derived text are **untrusted data** (prompt-injection hygiene, as in Research): Spec calls that read them carry no tools, place them inside delimiters, validate every output against a schema, and never execute or follow instructions found in them.

## 4. Size, screens and phases (AD-68)

### 4.1 Detail profiles: the user's tier is a floor, content can only raise it
Compass's size band (`structure.band` and `research.size_band`; the tier is the fallback) is the user's own statement of size, so it sets the **detail profile** (this replaces the screen-count sizes of doc 03 §1). Content raises it, never lowers it. All raise thresholds are config [J]: two or more apps, a sensitive-data flag (health, minors, identity documents, payments with payouts), three or more integrations, three or more locales or a right-to-left script, offline-first use, four or more apps or eight or more modules for `deep`.

| | `compact` (Quick) | `standard` (Standard) | `deep` (Thorough) |
|---|---|---|---|
| Typical content | one app, few roles, 1 to 4 modules | 1 to 4 apps, several roles, 4 to 8 modules | several apps including portals, many roles and entities |
| Product lane | one combined call for product, domain and contract | skeleton call, then one call per module | skeleton, per module, per-app shells, a compliance pass |
| Design lane | direction chosen automatically; tokens adapted from a preset to the brand | up to three directions, the user picks at G2 (or the default stands) | as standard plus dark-mode parity and script-fallback checks |
| Screens lane | cards with library states; at least 3 acceptance scenarios | full cards; at least 5 scenarios | full cards; at least 8 scenarios, including permission-denied and conflict |
| Contract v0 | operations, schemas, fixtures | plus pagination, errors, auth matrix, events | plus idempotency, concurrency, webhooks, versioning notes |
| Pack form | by measured tokens: one file if it fits, else the tree | tree plus task cards | tree, task cards, waves, interface freeze |
| Verify | final pass; canaries on a sample of task cards (at least two) | a canary per task card | a canary per task card; critic from another vendor where configured |

### 4.2 The screen count is derived, never fixed
The inventory is the **result** of these rules (R-INV), applied by code with judgment only for merge and split:
1. **Start from Compass's structure** as a proposal (screens with `screen_type` and `phase`, entity use, roles); derive it from the Brief when it is absent (format 1).
2. **Entities**: an entity a role can create, edit or manage gets a list (if many), a detail and **one form screen with a create/edit mode**; a view-only entity gets list or detail only where a flow needs it; an entity that exists only as data (join table, log) gets no screen.
3. **Flows**: every flow step is a screen, or a documented state or intent of a screen. A step that only confirms is a state unless it carries its own data (a receipt), then a `confirmation` screen.
4. **Roles**: every role has a home unless it has a single destination. Screens that serve one purpose for several roles are **one screen** whose regions and actions differ by role; split only when the purposes differ.
5. **Integrations**: each needs its setup, consent or permission steps (payment method, location rationale, identity documents).
6. **System set** (§4.3), added when its condition holds.
7. **Search and filter** are regions and actions of the list screen, not screens, unless the filter is itself a multi-step flow.
8. **Peer check**: for each screen pattern Research found common in the category and absent here, add it as Default or record the reason for leaving it out; the list appears at G2 as "peers usually have".
9. **Merge and split** (one judgment call, `SPEC-PLAN`): merge trivial pairs (create and edit listing become one form); split a screen whose slice exceeds the cap (§5.4).
10. **Dialogs, sheets and toasts are not screens.**
11. **Verify checks (V-29)**: no entity, role or flow without a screen; the system set is present when accounts exist.

### 4.3 The system set (a store-ready app with accounts needs roughly a dozen before its first feature screen)
| Item | Appears when | Notes |
|---|---|---|
| Sign in and sign up (method variants: phone code, email and password, social) | the app has accounts | variants are states of one screen unless the flows differ |
| Verify code | phone-code login | |
| Forgot and reset password | email and password login | |
| Account or role chooser | a user holds several roles or business accounts | |
| Profile; settings; language; notification settings | accounts exist | |
| Notifications inbox | push or in-app notifications exist | |
| Help, support, contact | always (store readiness) | Default |
| Legal: terms, privacy | always | a link out is allowed |
| **Account deletion** | accounts can be created in the app | store requirement (§7.4) |
| Consent and age gate | minors, health or identity data | from the trust signals in Research |
| Onboarding | only when the value needs explaining | Default off for `compact` |
| Permission rationale | per sensitive permission | a dialog of the triggering screen unless it is a multi-step flow |
| Launch, error, maintenance, update-required, offline | always | launch is configuration; the others are **states and global behaviours**, not screens |

### 4.4 Screen types (closed vocabulary; functional, not visual)
`auth`, `onboarding`, `list`, `detail`, `form`, `wizard`, `search`, `dashboard`, `calendar`, `map`, `chat`, `media`, `checkout`, `confirmation`, `status`, `notifications`, `profile`, `settings`, `report`, `permission`, `legal`. Compass's 14 seen in the real exports are a subset; Spec adds onboarding, wizard, search, chat, media, permission and legal. Each type owns a **state set** and a **standard-behaviours entry** in the state library (data), instantiated by code, so every list has loading, empty, error, offline and not-found states without a model writing them. (The older docs call this "pattern"; `pattern:<id>` now appears only in provenance.)

### 4.5 Scope cap and phases (reuses doc 03 §1, doc 05 §5 and doc 06 §3; replaces the fixed 12 / 60 / policy-set caps)
- **One cap per app per phase**, configuration `scope_cap_per_app_phase`, default **60** (the owner's current number; a starting value, not a limit). It is the front end's per-run capacity; it is never written into logic.
- **Phase 1 is the current release and Phase 2 is the backlog, exactly as doc 05 §5 defines them:** Phase 1 is the features with status `in`, Phase 2 the features with status `later`. A deferral for capacity sets the feature `later` with **`reason: capacity`**; a user's own deferral is `reason: user` and is never relabelled; features beyond Compass's approved `core` set (the first-release split the user approved with the structure at G1) are `later` with `reason: first_release`. **A deferral that touches a user-evidenced `must` changes a Locked field, so the G1.5 consent is its recorded override (I-4: who, when, why).** A deferred feature keeps an **outline** in the Scope Ledger (the screens it would need, its entities, its dependencies) and has **no screen nodes** while it is `later` (V-19).
- **Procedure.** (1) Take the features behind Compass's `phase: core` screens as the Phase 1 candidates (Compass's approved first-release split; the rest is `later`, `reason: first_release`). (2) **Pull-in rule:** each role's core journey must run end to end inside Phase 1, so a deferred feature whose screens a Phase 1 flow needs is set `in` (never one the user deferred, `reason: user`; a feature that straddles the line may be split into two ledger entries with aliases, recorded). (3) Add the system set and the minimum operations screens a Phase 1 flow depends on (for example approvals). (4) If an app's `in` screens exceed the cap, defer the lowest journey-criticality features with their dependents (`later`, `reason: capacity`). A **user-evidenced `must` is never deferred without the user's consent.** (5) If an app still exceeds the cap, G1.5 shows the conflict and the options.
- **G1.5 consent is mandatory whenever a capacity deferral is proposed** and whenever a user-evidenced `must` would be deferred, whatever the profile. Options: accept; move named features; **everything in one release** (allowed, recorded, with a plain warning about time and quality).
- **The whole-scope data model and Contract v0 are written once**: entities and operations needed by `later` features are included and flagged `later`, so starting Phase 2 never reshapes Phase 1's data. **Starting Phase 2** is the Scope Change Protocol (doc 05 §6) applied to the backlog as one batch: the features move `later` to `in` with the user's approval, Spec regenerates incrementally (§10.5) with the Brief and Research as they are then, the contract grows additively, Phase 1's approved baseline stays immutable and Phase 2 gets its own.
- **No `in` node may depend on a `later` node** (V-28).
- **The cap is per app**, because the front end builds one app codebase per run; a program with several apps is capped per app, and shared modules are built once. If the owner meant 60 for a whole program, only the deferral rule changes (it is applied across apps).
- **Compass's `core` is a proposal, not the answer.** In the real Thorough export `core` is 34 of 132 screens, yet the Dispatch module (the heart of a freight app) has **0 core and 19 extended** screens; Phase 1 by Compass's `core` would ship a freight app with no dispatch. The pull-in rule exists for exactly this (worked in §14.3).

### 4.6 Ordering inside a phase
Order, not membership: user-evidenced `must` first; then journey criticality (features on the golden journeys), dependency depth, Research peer prevalence (core, common, advanced, emerging), and the user's own ranking if there is one. Never used to demote a Locked `must`.

### 4.7 Planning ranges (my own enumeration of typical screens; sanity checks for the derived inventory, not rules)
| App class | Typical screens |
|---|---|
| Small utility or booking app, one role plus an owner view | 10 to 20 |
| Medium marketplace or service, customer plus provider side | 25 to 45 **in total** across its apps (the real Standard export: about 30 over four small role apps) |
| Big on-demand (Uber Eats class): customer / courier / restaurant / admin | 40 to 60 / 18 to 30 / 22 to 35 / 30 to 50, so a program of about 110 to 175 and a Phase 1 per app inside the cap |
The range is per program for the small and medium classes and per app for the big class, so V-29's warning compares like with like. A derived inventory far outside its class is flagged for review (Verify, V-29), never trimmed automatically.

### 4.8 Pack form by measured tokens

> **Amended (doc 18 §2.1, §2.3):** the rule stands; "all at once" is only available to a consumer that reads about 80k or more, so the design-target consumer always gets the tree.

`whole_pack_tokens` is not an independent number: a whole-pack read is one task, so it equals `task_read_budget` minus the front end's allowance minus the task card (28k − 4k − 1.5k, about **22k** [J]). A pack at or below it **also ships as one readable file**; above it the tree is the only form. Screen count never decides this.

## 5. The Spec Pack (AD-69)

### 5.1 Graph and pack
The **Spec Graph stays canonical** (AD-03): typed nodes, edges, tiers, provenance, versions, tombstones. The **Spec Pack** is its deterministic render and the front end's interface; it refines doc 04 §3 and drops nothing from it. The Spec Server (AD-16) is a thin reader over the same files; a harness without MCP reads the files directly. **The same graph and the same consumer profile always give the same bytes.**

### 5.2 The tree
```
spec-pack/
  AGENTS.md  INDEX.md  MANIFEST.json  index.json  APP_SPEC.md  spec.json  prefix.bundle.md
  global/    product · personas · roles-and-access · navigation (+routes.json) · data-model · rules ·
             behaviors · state-and-sync · errors · integrations · platform · norms · design-direction ·
             motion-intents · DESIGN.md · tokens.dtcg.json · blocks · needs.json · assets.json · suggestions ·
             copy/<locale>.json · scope-ledger.json · glossary
  contract/  openapi.yaml (or resources/<resource>.yaml) · schemas/ · fixtures/ (hero records) · mock/ (profiles, recipes, behaviors) · auth-matrix.json · events.json
  apps/<APP>/app.md           identity, surface, roles, primary destinations, shell behaviour
  modules/<MOD>/module.md  +  screens/<SCR>.md   (a screen file is the card with its generated closure below it: the slice)
                              +  screens/<SCR>.could.md   (COULD, doc 17)
  stories/<MOD>.md   journeys/<FLW>.md   scenarios/*.json
  build/build-plan.json       waves, task cards, ownership, reads, capability classes
  views/  exports/  evidence/   (views, exports and evidence are not for the front end)
```
`INDEX.md` is **the global reference file** the owner asked for: identity, profile, the release and the backlog, a **hierarchical map** (global files, apps and modules; each module file lists its screens, so the index never lists every screen), the glossary, the conventions and the rules of engagement. **It holds no size, hash or count that changes with an edit**; those live in `index.json`, which tools read and the model does not. `AGENTS.md` is the short instruction page for any coding agent (read the index first, never edit the spec, stamp files, raise decisions, you may merge, split, reorder and style freely within the meaning).

### 5.3 Layers and starting budgets [J]
Layers are named in words (never "tiers" or "lanes", which already mean other things).
| Layer | Content | Budget | Loaded |
|---|---|---|---|
| **index** | `AGENTS.md`, `INDEX.md` (hierarchical) | at most 6k tokens, typical 3 to 5k | always, first |
| **global** | one file per topic, sharded when larger | at most 4k per shard; a task names at most 6k of shards in all | by a task card's `reads` |
| **module / app** | purpose, its screens, local rules and data, dependencies | at most 3k | by task |
| **screen** | one file: the card with its generated closure below it, the *slice* | card 1 to 2.5k (hard 3k); slice at most 5k | by task |
| **contract** | one file per resource plus schemas | at most 4k per resource | on demand |
| **build** | `build-plan.json` (at most 8k); a task card (at most 1.5k) | | orchestrator; worker |
**The budget set must be self-consistent, and the configuration loader refuses one that is not.** A one-screen task must always fit: prefix cap (index 6k + common global files 4k = 10k) + task shards 6k + module 3k + one slice 5k + the front end's allowance 4k = **28k = `task_read_budget`**. `whole_pack_tokens` is derived (§4.8). The prefix cap of 10k replaces doc 04's "Tier 0 at most 8k" (AD-16, listed in §16).

### 5.4 Sharding, closure and overflow (what keeps a worker inside its budget)

> **Amended (doc 18 G-2):** every slice's closure also carries the bodies of the blocks it uses and the route targets of its navigation effects (route name, typed parameters, guard); a task bundle dedupes block bodies at read time; slices grow by about 0.3 to 0.8k and the cap stays 5k (V-26).

- **Shard by topic**: `data-model` by entity cluster, `copy` by module and locale, `contract` by resource, `behaviors` by topic (session, offline, errors, permissions, notifications, i18n, analytics), `rules` by module.
- **Closure = dependency closure projected to use** (V-27 checks each item): for each entity the screen binds, only the fields it uses **and the lifecycle states and transitions it names**; for each operation, only the request and response fields it uses **and its error codes with their recovery rows**; **the access rows** (role by this screen and its entities); the rules and derived-field formulas it cites; the source-locale text of every copy key it uses, **including the library's standard-state copy**; token *names* (not values). A worker needs no lookup to build one screen. **Doc 17 adds** the needs, asset, mock and story excerpts of the screen; a block used by several screens of one task is defined **once, in the task's shard part**.
- **A screen whose slice still exceeds the cap** is split at inventory time (preferred), or its bulky detail (long validation tables, big enumerations) moves to `screens/<id>.detail.md`, referenced from the card and loaded on demand.
- **Counting** uses the target consumer's tokenizer (a profile value, §11) or a conservative factor (characters over 3 for YAML and Indic scripts) until measured. **Splits that change content** (splitting a screen at inventory time) are decided with a **fixed reference tokenizer (characters over 3), independent of any consumer**, so switching the consumer normally only re-shards and regroups tasks. If a heavier consumer tokenizer still pushes one slice over its cap, the packager first moves bulky detail to the `.detail.md` file; if that is not enough it reports the screen and Spec re-runs it incrementally (a recorded content change), so re-packaging is **normally**, not always, free. The packager reports every file and every task total and **fails the build on a breach**, after trying to re-shard automatically.

### 5.5 Byte-stable bodies and the prefix bundle (caching)
File **bodies hold no volatile data** (no version, hash, token size, dependents or timestamp); those live only in `MANIFEST.json` and `index.json`. Ordering is deterministic (keys sorted, stable sort, LF endings). `prefix.bundle.md` is the concatenation of `AGENTS.md`, `INDEX.md` and the files named in `build-plan.prefix_files`, in a fixed order, stable files first and churn-prone ones (tokens, copy) last so a token change invalidates only the tail. It is identical for every worker, so it can be cached under any provider style (§11). **G2 approval binds the content hash** (the graph and every content file, independent of the consumer); the **package hash** (the bytes packaged for one consumer profile) is recorded beside it, so re-packaging for another consumer never voids an approval.

### 5.6 File format
Markdown with a small YAML header (id, kind, tier map, provenance references) and compact tables or flat lists in the body. **No deep YAML nesting**: it costs tokens and weak models mis-read it. The packager reports the token cost per format; if a format is wasteful for a file type it is changed there (data, not design). **The model never writes these files**: each call writes JSON against a portable call schema (flat, closed records, **arrays of keyed records instead of open maps**, because ADR 0014 bans open objects), and code renders the Markdown and YAML from it.

### 5.7 The screen file: the card, then its closure (example from the real Standard export)
SCR-014, ENT-006 to ENT-008, FCT-005 and FCT-028 are real; operation, rule, confirm, research-item, story and need ids (OP-021, OP-022, RUL-011, CF-007, RI-0412, US-012, N-021) are illustrative. The model writes the card as JSON; this is its render. **COULD ideas are not in the card**; they are in `screens/SCR-014.could.md` (doc 17 §1).
```yaml
---
id: SCR-014   kind: screen   name: Pay for order
app: APP-002 (Buyer app)   module: MOD-003 (Orders and payments)   screen_type: checkout
roles: [ROL-001 Buyer, ROL-006 Purchase manager, ROL-007 Owner, ROL-009 Staff]
tiers: {purpose: Locked, regions: Default, actions: Default, states: Default, copy: Default}
provenance: {purpose: user FCT-005 "Buyers pay online" and FCT-028; methods: confirm CF-007; states: library:state/checkout@1}
claims_used: [RI-0412]
stories: [US-012, US-013]
---
purpose: A buyer pays for a placed order online so the farmer can be paid.
route: {path: /orders/:order_id/pay, params: [{name: order_id, type: id, source: ENT-006.id}],
        guard: [signed_in, role_has_access, {entity: ENT-006, field: status, equals: payment_pending}]}
reached_from: [SCR-013.pay_order, SCR-011.pay_order]     leads_to: [SCR-015]
role_conditions: none       # the four roles see and do the same here (ENT-007 access is create for all four)
regions:                    # listed alphabetically by key; the order carries no meaning
  - {key: delivery_info,   shape: key_values, what: delivery day and drop-off place, bind: [ENT-006.delivery_day, ENT-008.dropoff_location], importance: supporting}
  - {key: order_summary,   shape: collection {item: order_line}, use_block: order_summary@full, what: items, quantities, produce total, delivery fee and amount due, bind: [ENT-006.items, ENT-006.produce_total, ENT-006.delivery_fee, ENT-006.amount_due], cardinality: {typical: 3, max: 40}, importance: primary}
  - {key: payment_methods, shape: options, what: the ways this buyer can pay now, bind: OP-021, cardinality: {typical: 3, max: 6}, importance: primary, freshness: on_open}
  - {key: provider_note,   shape: notice {severity: info}, what: a plain statement that a payment provider processes the payment, importance: optional}
actions:
  - {key: cancel,        importance: secondary, intent: cancel,     what: leave without paying,          effect: [go SCR-011]}
  - {key: change_method, importance: secondary, intent: select_one, what: choose another payment method, effect: [stay]}
  - {key: pay,           importance: primary,   intent: pay,        what: pay the amount due with the chosen method,
     precondition: a method is chosen and the order is payment_pending,
     effect: [call OP-022, external_flow payment_provider, go SCR-015],
     errors: [payment_declined, provider_unavailable, order_not_payable, network], flags: [idempotent]}
states:                     # what each must communicate, not how
  - {kind: loading,   message: the amount and methods are not available yet}
  - {kind: error,     message: the methods could not be loaded; the user can retry}
  - {kind: offline,   message: the amount is shown from the last saved order; paying is unavailable until online}
  - {kind: forbidden, message: this account may not pay for this order; a way out is offered}
  - {kind: not_found, message: the order no longer exists}
  - {kind: stale,     message: the order changed since it was opened; the user must review before paying}
  - {kind: domain:payment_pending, message: waiting for the provider's result}
  - {kind: domain:payment_failed,  message: the reason is shown; retry is allowed}
  - {kind: domain:already_paid,    message: show the result instead}
copy_keys: [pay.title, pay.amount_due, pay.method_label, pay.provider_note, pay.action, pay.change_method, pay.cancel, pay.pending, pay.failed, pay.declined, pay.provider_down, pay.already_paid, pay.not_payable, pay.stale, pay.offline]
analytics: [payment_started, payment_succeeded, payment_failed]      # the consent rule is global (behaviors)
accessibility: the amount due and the payment result are announced to assistive technology when they change
acceptance:
  - GIVEN a payment_pending order and a chosen method WHEN the buyer pays THEN one payment is created and the buyer reaches the result with the order marked paid
  - GIVEN the same pay action is repeated within seconds THEN only one payment exists
  - GIVEN the provider declines THEN the buyer stays here with the reason and can retry or change method
usually: [{note: UPI and card are the usual online methods for Indian marketplaces, evidence: single_source, item: RI-0412}]
mock: {profiles: [typical, broken, extreme], hero: [order-1002, order-1003]}
confirm: [CF-007 payment provider and fee handling (money)]
```
Below the card the packager appends the **closure** (generated, read-only), so the worker needs no lookup:
```
## closure
ENT-006 Order, fields used: items (list of {produce, quantity, unit_price}), delivery_day (date), produce_total (money INR), delivery_fee (money INR),
  amount_due (money INR, derived: produce_total + delivery_fee, RUL-011), status (enum: placed, payment_pending, paid, cancelled; payment_pending -> paid on payment_succeeded)
ENT-008 Delivery, field used: dropoff_location (location)
OP-021 getPaymentMethods(order_id) -> [{method_id, kind, label_key}]; errors: network, forbidden, order_not_payable
OP-022 createPayment(order_id, method_id, idempotency_key) -> {payment_id, status, provider_session}; errors: payment_declined, provider_unavailable, order_not_payable, duplicate
access (this screen): ROL-001, ROL-006, ROL-007, ROL-009 -> Order: view; Payment: create
error recovery: payment_declined -> stay and show the reason; provider_unavailable -> stay, retry allowed; order_not_payable -> go SCR-011; network -> offline state
copy (en): pay.title "Pay for order"; pay.amount_due "Amount due"; pay.method_label "Pay with"; pay.action "Pay"; ... (11 more; standard state copy comes from library:state.*)
needs: N-021 options (few); N-007 collection of order lines (few); N-033 notice (info)   assets: none for this screen
mock: typical, broken, extreme; hero order-1002 (payment_pending), order-1003 (paid)
stories: US-012 "as a purchase manager I want to pay online so that the order is confirmed"; US-013 "as a purchase manager I want to try again after a failed payment so that I can still complete the order"
block order_summary@full: defined once, in this task's shard part
tokens: names only; none used by value
```
**Size, measured on the text above:** 4,377 characters for the card (about 1100 to 1500 tokens by §5.4's own factors, characters over 4 to over 3) and 1,624 for the closure (about 400 to 500); with the 15 copy strings written out the slice is **about 1600 to 2100 tokens**, inside the 5k cap and below the 3k typical (richer `standard` and `deep` cards, with more scenarios, reach it; block bodies sit in the task's shard part, not here). **What the card does not say:** where anything sits, what any widget is, how a state looks, what triggers an action. **What a developer still gets without asking:** the route, its typed parameters and guards, what to bind (including the derived amount), what each action does and when, its errors and recovery, idempotency, who may do it, what to announce, what proves it works, and what is not yet decided (the provider).

### 5.7a What each screen type adds to the card (meaning only; the base card above applies to all)
| `screen_type` | Adds |
|---|---|
| `list` | `list`: source operation, paging (`cursor`, `page`, `none`), sort fields, filter fields, search fields, selection (`none`, `single`, `multi`), expected count (typical, maximum), row actions (action keys); the empty state says why it is empty |
| `form` | `inputs`: key, semantic type (text, number, money, date, phone, email, choice, file, location, code, secret, quantity, toggle), required, constraints, default, source; when validation runs (`on_change`, `on_blur`, `on_submit`); submission states (`idle`, `validating`, `submitting`, `server_error`, `success`, `conflict`); whether leaving with unsaved input asks first; whether a draft persists |
| `wizard` | ordered steps (each a set of regions and inputs), progress persistence, back rules, exit behaviour |
| `search` | query sources, suggestions and history (yes or no), no-results state, result entity kinds |
| `dashboard` | metrics (name, definition, source, refresh), range options, drill-down targets |
| `calendar` | range kinds, selection kinds, availability source, time-zone rule |
| `map` | places to show and their source, whether the user's location is needed (permission), what selecting a place leads to, whether a route is needed, offline behaviour |
| `chat` | thread source, message states (`sending`, `failed`, `delivered`, `read`), allowed attachment kinds and limits, unread rules, report and moderation |
| `status` | stages (from the lifecycle), the message of each stage, whether it is live or polled |
| `checkout`, `confirmation` | as the card above; what is confirmed (entity reference) and the next actions |
| `auth` | methods, code length and timeout, lockout, recovery path |
| `profile`, `settings` | editable fields (as inputs), destructive actions (account deletion), consent switches |
| `report` | filters, export formats, definitions of the figures |
| `notifications` | list source, grouping rules, read and unread, deep-link targets of each kind |
| `onboarding` | the points to convey (messages), permissions asked and why, skip rules |
| `permission` | the permission, why it is needed (copy), the denied path, the way to the system settings |
| `legal` | document ids and versions, whether acceptance is required |
| `media` | kinds, limits, capture or pick sources, upload states and resume |
| `detail` | the base card |

### 5.8 Global files
| File | Holds | Produced by |
|---|---|---|
| `product` | purpose, audience, apps, scope ledger summary (in, later, out), phases, non-functional targets | Product |
| `roles-and-access` | roles, apps, access matrix (role by module, screen and entity action), tenancy and account model, auth method per role | Product |
| `navigation` (+ `routes.json`) | primary destinations per app (three importance levels), entry points, deep and universal links, auth gates, exits, cross-app handoffs; the route table with typed params and guards | Product |
| `data-model` (sharded) | entities, fields (type, required, constraints, enum values), relations, **lifecycles** (states, transitions, who, timers), derived fields, visibility per role, **expected volumes** (typical, maximum), format rules | Product (+ Research shapes) |
| `rules` | business rules with ids, formulas, limits, `confirm` flags | Product |
| `behaviors` (sharded) | session and refresh, **secure token storage**, error taxonomy and recovery, loading policy, permission prompts, **notifications (remote push and local reminders)**, deep links, **app lifecycle and state restoration (backgrounding, kill and restore, cold-start links) and background work**, i18n, right-to-left and formats, analytics and consent, accessibility floors, feature flags, force-update and maintenance; **an interrupted external flow** and **pending timeouts** whenever an integration has a provider-side flow | Screens (SCRN-2) + library |
| `state-and-sync` | client state slices, what persists, cache and offline queue, conflict policy, optimistic-update rules, media fallbacks | Product + library |
| `errors` | error codes to recovery behaviour by type | Product + library |
| `integrations` | per integration: mode (mock, adapter, real), provider options with evidence class, auth model, sandbox, webhooks, whether Expo needs a development build or a config plugin, the mock plan, the credentials needed later (names only) | Product (+ Research dossiers) |
| `platform` | Stack Kit version (or `unpinned`), capability tiers A, B, C, applicable store rules (policy ids), permission texts needed, deep-link schemes, account deletion, in-app purchase rules, privacy and data-safety disclosures | Product + policy sheet + Research |
| `design-direction`, `DESIGN.md`, `tokens.dtcg.json` | mood, audience, voice, what to avoid, imagery and icon policy (sources and rights), motion principles, platform-fidelity setting; the tokens | Design |
| `copy/<locale>.json` | keys to text per locale (ICU plurals, no concatenated sentences); source locale complete, others flagged `needs_native_review` | Screens (SCRN-2) |
| `scope-ledger.json`, `glossary` | feature statuses and phases; terms | code from the graph |

### 5.9 Contract v0 (the screens' API; the backend engine builds on it)
OpenAPI 3.1 (split per resource when large), JSON Schemas for entities, **realistic localized fixtures**, a default-deny auth matrix, events, and conventions: identifiers, timestamps, enums from lifecycle states, cursor pagination, filter and sort parameters, a stable error model, idempotency keys, optimistic concurrency (version or ETag), the realtime channel where one is needed. Operations carry `phase`. The G3 usage map is reconciled into Contract v1 as in doc 04 §7 (unchanged).

### 5.10 Tokens and direction
DTCG tokens with light and dark roles, contrast checked (V-12), script-aware type stacks, **`ext.*` reserved for the front end**. Where the user supplied brand colours, fonts or logo they are Locked roles; the rest is Default. A brand colour that fails contrast in dark mode gets an accessible variant of the same hue, flagged.

### 5.11 Copy
Cards carry copy **keys**; the slice's closure carries the source-locale text of the keys the screen uses. Other locales are produced in a separate batched pass, flagged `needs_native_review` (native reviewers for Hindi, Arabic and Swahili are an open item, doc 00). Right-to-left is stated as a behaviour of the app, not as a layout.

### 5.12 Module, app, journeys, scenarios
`module.md`: purpose, screens (id, type), owned entities, local rules, dependencies. `app.md`: identity, surface (phone, tablet, web portal), roles, primary destinations, the release (screen count against the cap) and the backlog outline, shell behaviours (auth gate, role switching). `journeys/<FLW>.md`: steps (actor, screen, intent, expected observable result), alternates, failure paths. `scenarios/*.json`: golden journeys (doc 04 §8, unchanged).

### 5.13 Build plan and task cards
`build-plan.json` is produced by **assemble** (code) from the inventory, dependencies and the complexity rubric (doc 04 §6, kept), now with phases, `prefix_files` and per-task reads with token sizes. A task card:
```yaml
id: T-0142   wave: W3   app: APP-002   module: MOD-003   title: Place order, pay and result screens
depends_on: [T-0030 tokens, T-0045 nav-shell, T-0051 api-client]
owns: [the files of these routes and their feature folder]        # nobody else writes here
reads: {prefix: prefix.bundle.md, global: [data-model#order-cluster, behaviors#errors, integrations#payments], module: MOD-003, screens: [SCR-013, SCR-014, SCR-015]}
read_tokens: {spec: 19900, front_end: 4000, budget: 28000}         # prefix 8.0k + shards 4.7k + module 2.0k + three slices 5.2k (1.7k, 1.9k, 1.6k); the budget includes the front end's own interface files and recipes
checks: [tsc, lint, render-all-states, a11y-labels, token-lint, journey FLW-002 steps 3-5]
complexity: M   capability_class: standard   escalate_after: 2
```
`capability_class` (`weak`, `standard`, `strong`) replaces the older `model_hint` wording: Spec recommends a class, never a model (rule 14); the front end decides.

## 6. How the front-end engine consumes the pack (AD-70)

Spec **guarantees** the properties below; the front-end engine chooses its own harness (files, the Spec Server, any agent loop).

### 6.1 Three consumption modes

> **Amended (doc 18 §2.3):** the whole mode needs a consumer that reads about 80k or more; `AGENTS.md` says plainly that visual consistency across parallel workers needs the front end's pilot step.

| Pack form | What a worker reads | Workers |
|---|---|---|
| **Whole** (pack within `whole_pack_tokens`) | the one readable file, or the tree in any order | one to three in parallel |
| **Sliced** (medium) | the prefix bundle, the global shards its task card names, the module file and its screens | three to six |
| **Sliced with waves** (big) | the same, per task card, inside waves W0 to W7 with an interface freeze after the foundation wave | eight to sixteen, bounded by the provider's rate limit (§10) |

### 6.2 The read budget composes (starting values [J])
`task_read_budget` (default 28k tokens) covers **everything the worker is given before it starts thinking, including the front end's own interface files and recipes**; Spec's share is about 24k.
| Part | Typical | Cap |
|---|---|---|
| index (inside the prefix bundle) | 5k | 6k |
| common global files (inside the prefix bundle) | 3k | 4k |
| task-specific global shards (a data-model cluster, a behaviours topic, an integration) | 3k | 6k |
| module file | 2k | 3k |
| one screen slice (card plus closure plus excerpts) | 3k | 5k |
| the front end's interface files and recipes, its component-map and its design-language record (not Spec's; an agreed allowance) | 4k | 4k |
The caps sum to the budget (6 + 4 + 6 + 3 + 5 + 4 = 28k), so a one-screen task always fits (§5.3). The packager chooses **how many screens a task carries** so the sum fits. The fixed parts (prefix, shards, module, interface) come to about 17k typical, which leaves about 11k: **about three typical slices per task** (the v2 slice of doc 17 is about 3k), fewer for heavy screens (a slice at its 5k cap travels with at most one neighbour). A task that cannot fit is split by the packager, never truncated. (Working this out for the real exports in §14 exposed that the shards must be counted, and the independent review exposed that the caps must sum to the budget.)

### 6.3 Rules every consumer can rely on
1. **The prefix bundle comes first and is byte-identical for every worker**; where the consumer profile supports caching, one cheap call warms the cache before the fan-out.
2. **Self-sufficiency**: a slice is closed under dependency (§5.4); a worker needs no lookup to build one screen.
3. **Lookups are bounded**: a worker may read any file by id through `index.json`, at most `lookups_before_raise` (default 3 [J]) times per task; then it **raises a decision** instead of guessing. In file mode the decision is a file in `decisions/`; in server mode it is the `raise_decision` tool (doc 04 §5). It is routed back to the Context Engine.
4. **Ownership is disjoint**: a task writes only the files it owns; shared files (route files, types, API client, token files, i18n key files, fixture loaders, navigation param types, stamp headers) are generated by code from the graph's language-neutral sources (OpenAPI, JSON Schema, DTCG, `routes.json`, copy JSON). Models write screen bodies and bespoke components only.
5. **Interface freeze** after the foundation wave: route map, types, token names, store-slice shapes, client signatures, i18n namespaces and analytics event names are published once; a change needs an interface-change request.
6. **Stamps and keys**: generated files carry `@spec SCR-014@v15`; regions and actions become `testID`s from their stable keys (`SCR-014.pay`), so traceability and Locked-compliance can be checked by code.
7. **Freedom**: §2.1 applies; deviations are recorded in `deviations.json` (node, field, what changed, why) and shown at G3; screens or routes outside the inventory are flagged `unplanned`.
8. **Changes during a build** follow doc 05: a token or copy change (L1) recompiles with no re-plan; a feature change (L2) regenerates only the affected slices and tasks; unstarted tasks are hot-patched; structural changes wait at a wave barrier. Because bodies are byte-stable, the consumer finds what changed by comparing hashes in `index.json`, never by re-reading.
9. **The weakest configured consumer is the design target** (§11): cards are explicit and flat; a larger-window consumer raises `task_read_budget` through its profile (fewer, bigger tasks) and the content does not change.
10. **The playbook and `get_task`** (doc 17 §2.1, §2.9): a recommended order for the front end (a mapping wave, foundation and freeze, the pilot as the W2 slice, canary fan-out, a polish pass, self-checks) and one call per task; the pack's descriptions and notes are data, and the only instructions are in `AGENTS.md`.

## 7. Developer readiness (AD-71)

### 7.1 The builder-needs contract, extended (BN-01 to BN-14 as in doc 12 §5; three added)
| # | The builder needs | Where in the pack | Produced by | Proven by |
|---|---|---|---|---|
| BN-01 | What it is, who uses it, apps, roles | `product`, `roles-and-access`, `apps/*/app.md` | Product | V-08; critic |
| BN-02 | What first: in, later, out, priority | `scope-ledger.json`, `product` | Product | V-16, V-19, V-28 |
| BN-03 | Screens and movement: entry points, deep links, role access | `navigation`, `routes.json`, `journeys`, `module.md` | Product | V-04, V-05, V-07, V-29 |
| BN-04 | Data: entities, fields, relations, statuses, who sees what | `data-model` | Product (+ Research shapes) | V-06, V-09, V-11 |
| BN-05 | Rules, formulas, validations; unknown numbers flagged `confirm` | `rules`, `behaviors` | Product (+ Research norms) | V-08, V-09, V-36; confirm list |
| BN-06 | Contract: operations, errors, pagination, auth, fixtures | `contract/` | Product | V-10, V-15, V-30 |
| BN-07 | Realistic data: local names, prices, formats, edge cases | `contract/fixtures`, mock plan | Research + Product | V-15 |
| BN-08 | Look: tokens, density, tone, imagery and icon policy, accessibility, dark mode | `design-direction`, `DESIGN.md`, tokens | Design | V-12, V-24 |
| BN-09 | Per screen: regions, bindings, actions, every state, copy, acceptance | `screens/*.md` | Screens | V-06, V-07, V-13, V-14, V-22, V-24 to V-27, V-31; canary |
| BN-10 | The outside world and what is mocked | `integrations` | Product (+ Research dossiers) | V-16, V-36 |
| BN-11 | What is allowed: SDK, packages, kit | `platform` | Stack Kit (fixed data) | V-23 |
| BN-12 | Build order, ownership, interface freeze, capability class | `build/` | Spec assemble (code) | build-plan checks, V-26, V-28 |
| BN-13 | What done means; Locked, Default, Open | cards, `journeys`, `scenarios`, tier headers | Spec | V-17; journey probe |
| BN-14 | Unknown, assumed, degraded; how to ask | `INDEX` (assumptions, degraded), `evidence` | Spec + Verify | assumptions register complete |
| **BN-15** | **Platform and store rules**: permission texts, account deletion, sign-in and in-app-purchase rules, privacy disclosures | `platform` | Product + policy sheet + Research | V-33 |
| **BN-16** | **Global behaviour**: session and secure token storage, offline and sync, error taxonomy and recovery, loading, permissions, notifications (remote and local), app lifecycle and state restoration, background work, interrupted external flows and pending timeouts, i18n and right-to-left, analytics and consent, state slices, media fallbacks | `behaviors`, `state-and-sync`, `errors` | Screens (SCRN-2) + library | V-35 |
| **BN-17** | **Phases and scope cap**: what is in this build, what is later and why | `product`, `apps/*/app.md`, `build/` | Product | V-28 |
Coverage is scored by Verify as `satisfied`, `defaulted`, `degraded` or `missing`; `missing` blocks G2 (AD-37, unchanged).

### 7.2 Libraries (data files, instantiated by code; each has an owner and a stage)
| Asset | Holds | Authored by | Stage |
|---|---|---|---|
| **State library** | per `screen_type`: required states, standard behaviours, default acceptance scenarios | builder, from standard references, reviewed by me | S1 |
| **System screen set** | the §4.3 definitions and conditions | builder | S1 |
| **Store-policy sheet** | dated store and platform rules that affect a build (account deletion, sign-in with Apple, in-app purchase, permission purpose strings, privacy manifest and labels, data-safety form, push permission, background location, age ratings), each with source URL and date | builder from the official pages; reviewed by me; re-checked on a schedule | S1 |
| **Consumer profiles** | per consumer route: windows, tokenizer, output limit, caching, limits (§11) | builder from the providers' current documentation, dated | S0 |
| **Scenario library** | standard scenarios: first launch, sign-in, expired session, permission denied, no network, slow network, server error, empty data, last page, validation error, deep link while signed out, push while signed out, forced update, language switch, account deletion, payment declined, pending, duplicate, edit conflict | builder | S2 |
| **Module Library v1** (about 12 modules) | auth, profile and settings, notifications, list-detail-edit, search and filter, order or booking lifecycle, checkout and payments, location, chat, reviews, admin tables, onboarding: entities, operations, screen skeletons, states, rules, mock fixtures | builder, reviewed by me and a human sample (OI-5) | S2 |
| **Vocabularies** | the information shapes, interaction intents, motion intents, asset kinds and mock profiles (doc 17) | builder, reviewed by me | S0 |
| **Platform capability sheet** and **delight-pattern library** | dated: what the platforms and the stack can do, with tiers; named delight patterns with their basis (doc 17 §5) | builder from official and public guidance, with source URLs and dates | S0 |
| **Error catalogue** | code, severity, audience (agent or owner), plain text, fix hint, repair class (doc 17 §2.9) | builder | S0 |
| **Stack Kit capability matrix** | what Expo can and cannot do (exists unpinned in `stack-profile.yaml`) | front-end engine design; **a pin is required before S5** | before S5 |
Until a library exists Spec uses the minimum (state library and system set) and marks the rest `assumed`.

### 7.3 Honest capability tiers
**A**: runs in the standard Expo flow. **B**: needs a development build, a config plugin or a native module (Bluetooth, NFC, background location, widgets, some payment SDKs). **C**: not supportable in the fixed stack. Spec flags B and C on the feature and at G2; it does not turn C into scope and does not hide it.

### 7.4 Policy evidence (amends I-7 and V-17)
Store rules, accessibility floors and the fixed stack are non-negotiable whatever the user said, so they cannot honestly be "Default"; but they have no user quote, so under I-7 they could not be Locked. **Amendment: Locked may also rest on policy evidence**, recorded as `provenance: policy:<sheet id>@<date>`, validated by V-33 and accepted by V-17. **Only rules that are in the policy sheet itself** (official store pages, accessibility standards, the Stack Kit) are policy-Locked. **Precedence:** Research's `platform_constraints` are Default at most (I-6 and AD-04: research never creates Locked); where one is stricter than the sheet it becomes a Default requirement plus a confirm item; where it contradicts the sheet, the sheet stands and the contradiction goes to confirm. Design opinions (one primary action per screen, at most two type families) are Default guidance, never policy.

## 8. Inside Spec (AD-72)

### 8.1 Steps

> **Amended (doc 18 §1.2):** for compact and standard the steps and calls are those of doc 18: `SPEC-PLAN`, `PROD-MODULE`, `PROD-INTEG`, `PROD-JOURNEYS` and `DSGN` after the plan; the Freeze also freezes the glossary and the hero skeleton; then `SCRN` (cards, source copy, COULD), `MOCK-HERO`, `BEHAV`, `GLOSS-L` and streamed `COPY-L`; assemble with `G2-SUMMARY` in parallel. The table below is the split form (used when the fusion rule says so).

| # | Step | Kind | Route class | Reads | Writes |
|---|---|---|---|---|---|
| 0 | **Intake** | code | | Compass export, Research output | `SpecInput`, trust grades, readiness and drift flags |
| 1 | **Plan** (`SPEC-PLAN`) | code, then one call | `deep` | `SpecInput` summary | profile, derived inventory, merges and splits, release proposal (what is `in`, what is deferred `later`, with reasons) |
| 2a | Product skeleton (`BLUE-1/2`) | call (`standard` and `deep` profiles only) | `deep` | summary of the Compass view, Research digest | modules, roles and access matrix skeleton, entity owners |
| 2b | Product per module (`BLUE-2/3`) | calls, parallel | `standard` | that module's Compass slice, `pack(need_card)` | module inventory, entities, lifecycles, rules, operations |
| 2c | Contract and mock (`BLUE-4/5`) | calls, parallel by resource and entity batch | `standard` | module outputs | operations, schemas, fixtures, integrations, mock plan |
| 3 | Design (`DSGN-1..3`) | calls, **parallel with 2** | `standard` | Design view, Research design slice | direction, tokens, voice and policy |
| 4 | **Join** | code | | the graph | V-01..V-11, V-16, V-12, V-19, V-24, V-28, V-29, V-32 |
| 5 | **Freeze** | event | | | roles, entities, operations, inventory, navigation, tokens read-only for Screens; a gap raises `needs_decision` to Product |
| 6 | Screens (`SCRN-1`) | calls, parallel batches | `fast` or `standard` | frozen slice per batch | cards |
| 7 | Global behaviours and copy (`SCRN-2`) | calls | `standard` | frozen graph | behaviours, source-locale copy |
| 8 | Locale passes (`COPY-L`) | calls, batched | `fast` | source copy | other locales, flagged |
| 9 | **Assemble** | code | | the graph | commit, render, shard, slice, task cards, build plan, prefix bundle, manifest, hash; V-26, V-27, V-34 |
Doc 17 §10.5 adds PROD-PERSONAS, PROD-STORIES, PROD-JOURNEYS, MOCK-HERO, asset needs, BLOCK-NAME and DSGN-4 and fixes their order (stories and journeys before the Freeze; needs, blocks and asset needs after Screens). Verify streams beside steps 5 to 9 (§9). In the `compact` profile steps 2a to 2c collapse into one call and 7 into step 6.

### 8.2 Calls, by profile (a formula, not a promise)

> **Amended (doc 18 §1.2, §1.3):** the formula below is the unfused count; the fused counts are about 14 (compact) and about 38 (standard) when the writer can output 32k tokens or more, about 25 and 70 at 8k [J]; Verify's calls (structure critic, canaries, final critic) are not made for compact and standard.

`calls ≈ 1 (plan) + (modules + 1) (product) + 3 (design) + ceil(release screens / screens per call) + 2 (behaviours, copy) + (extra locales × ceil(strings / batch)) + ceil(entities / fixture batch) + (canaries + 3)`. Screens per call is `clamp(floor(0.5 × writer max output / average card tokens), 1, 4)`: it follows the writer route's profile, never a constant. **A canary runs per task card**, the unit the consumer actually receives: `compact` samples at least two cards (about 40%), `standard` and `deep` every card; the other three Verify calls are the structure critic, the final critic and the summary. Typical totals [J]: `compact` about 10 to 15 calls, `standard` about 25 to 55 (more with many locales), `deep` about 70 to 110 for a release of 60 screens. The content of doc 17 adds `1 (personas) + modules (stories) + roles (journeys) + ceil(entities / batch) (hero records) + modules (asset needs) + apps (blocks) + 2 (direction extras)`; with it the typical totals are `compact` about 15 to 20, `standard` about 45 to 80, `deep` about 120 to 135 (doc 17 §7). Prices and times are in §10.

### 8.3 Per-call input budgets
Every call's input is assembled by code and capped at `min(0.5 × effective context of the writer route, route input limit, the profile's long-prompt threshold)`. If the assembled input exceeds it, the step **splits** (smaller module slice, fewer screens per call, a narrower `need_card`); it never truncates silently. Use of the Research pack is by `need_card.brief_ids` plus `token_budget`; Spec never asks a model to fetch more (an item known only by its title in the index is fetched by a second, deterministic `pack` call).

### 8.4 Failure ladder, checkpoints, resume
Retry → one repair against the schema → a deterministic default from the libraries → the node is `degraded` and shown at the next gate. **Every batch is checkpointed** under a key of (program, input hash, step, batch); a crash, a rate limit or a credit stop ends the run **`partial`** and it **resumes** from the last checkpoint (Research could not resume, D-167; Spec must). Writes are idempotent; a **single-flight lock per program** (one active Spec run per program, as doc 02 requires) stops a double click from starting two runs: the same input hash joins the running one, a different hash waits or supersedes it. **The checkpoint key also carries the call-spec, prompt, schema and library versions**, so a deployment in the middle of a run never mixes versions: a resumed run keeps the versions it began with unless it is restarted (SPC-OP-11). **At G1.5 the run pauses at Freeze** (whenever a capacity deferral is proposed, and always for `deep`): checkpoints are kept, the Design lane may finish, nothing from Screens starts before the gate is approved (I-1 and I-12: silence never approves), and a changed split re-runs only the affected screens.

### 8.5 Single writer
Spec is the only writer of the graph. After G2 the change controller does steps 1 to 6 of the doc 05 pipeline (capture, interpret into patch operations, classify, dry run, impact, approval routing) and files the resulting **change set**; Spec, as the single writer, does step 7 (apply and propagate) by incremental regeneration. Verify proposes patches; it never writes.

### 8.6 Shared packages and layering (AD-33)
`packages/spec-core` (graph model, validators V-01 to V-35, renderer, packager, slicer, build planner, library and policy loaders) and `packages/compass-intake` are shared; `agents/spec` holds the orchestrator, call specs, prompts and the Research adapter; `agents/verify` holds the critic, the canary, the coverage matrix and the G2 summary, and imports `spec-core` (so Verify can re-run packaging and the validators). The import-linter needs a lead-only layering edit; the legacy `packages/spec-builder` and `packages/spec-loader` are reference material and tests to learn from, not dependencies.

### 8.7 Build phases (detail in prompt 12)
S0 shared packages, adapters, fixtures, consumer profiles · S1 deterministic derivation, libraries, packager, slicer, build plan, at **zero model cost** · S2 Product lane · S3 Design lane · S4 Screens lane · S5 thin Verify · S6 a small standalone viewer for the owner. S1 **proves only adapters, packaging and budgets**, not the quality of any model's writing; that is what PS-5 and PS-6 are for.

## 9. Verify stays thin (AD-73)

> **Amended (doc 18 §3, AD-83 to AD-85):** for compact and standard there is **no Verify agent**: Spec checks itself in code at three gates (call, join, pack = `spec check`), every validator V-01 to V-45 runs as code, the builder-needs rule and the coverage matrix are kept, and the repair ladder of doc 18 §3.2 replaces §9.3. The Verify agent below stays the design for **deep** apps and is **not built now**. V-45 (grounding) is added to §9.2.

### 9.1 What it does
It **only checks**. It streams: a structure critic at Freeze (from a different vendor than the writer, where one exists), validators per batch, a canary per task card as its screens complete, and a final pass with the coverage matrix and the freedom checks. It never writes into the graph or the pack.

### 9.2 What it checks
Doc 07's V-01 to V-23 stay, except V-17, V-18, V-19, V-21, V-22 and V-23, which §16.1 amends. New for Spec's design:
| ID | Check | Kind |
|---|---|---|
| V-24 | Freedom schema: closed vocabularies, tier ceilings (§2.3), no forbidden fields | blocker |
| V-25 | Form-vocabulary lint on free text (§2.2) | warning in v1 |
| V-26 | Budgets (supersedes V-18): every file and shard within its cap; every task's read total within `task_read_budget`; every call input below the profile's long-prompt threshold; the budget set self-consistent; pack form matches `whole_pack_tokens` | blocker |
| V-27 | Closure completeness: a slice holds everything its bindings, operations and copy keys need | blocker |
| V-28 | Release consistency: every app's `in` screens within the cap; no `in` node depends on a `later` node; a user-evidenced `must` set `later` only with a recorded G1.5 consent; `later` features have no screen nodes | blocker |
| V-29 | Inventory completeness: no entity, role or flow without a screen; the system set present when accounts exist; class-range check | blocker, range is a warning |
| V-30 | Bindings resolve to Contract v0 fields with matching types; every action's operation exists and is role-gated | blocker |
| V-31 | Region and action keys unique per screen (they become `testID`s) | blocker |
| V-32 | ID namespace and aliases: Spec-minted IDs in their namespace; every alias resolves; no reuse | blocker |
| V-33 | Policy provenance: a policy-Locked item cites a sheet id and date | blocker |
| V-34 | Byte stability (extends V-21): no volatile data in bodies; the prefix bundle and the content hash recompute identically | blocker |
| V-35 | Global behaviour completeness: every applicable BN-16 category present for the profile | blocker |
| V-36 | Research drift: every research-derived value cites its item ids (`claims_used`) and matches the item's typed value; no `unverified` item is cited; every research-derived Default cites an `official` or `corroborated` item (R21 to R23) | blocker |
| V-37 to V-44 | Mobile-fit, shapes and intents, block integrity, story coverage, mock coverage, opportunity discipline, asset and motion needs, journey completeness (doc 17 §10.3) | see doc 17 |

### 9.3 Repair: defects return to Spec
Each defect carries a **repair class**: **A** code fix (a deterministic correction Spec applies in at most 3 passes that must converge, otherwise it is reclassified B; not counted as a round), **B** lane call (a model call re-writes the node; counts as a round), **C** needs decision (routed to Product; counts inside the same limit). **At most 2 LLM rounds in total across Freeze and the final pass** (Spec's own `needs_decision` loops count inside); then the node takes the simplest valid library default, is marked `degraded`, and appears on the G2 list. Verify proposes **structured patches** (node, field, change, reason); Spec applies them; **touched nodes are re-verified by hash**, not the whole pack.

### 9.4 Vendor independence, honestly
The critic's route carries a **different-vendor constraint** with a preflight. If only one vendor is configured the critic still runs and the run says `critic_vendor: same` (as Research's `checker_vendor: same`), so independence is never claimed falsely. **A bigger or dearer route is not assumed to be a better critic**: Research measured a larger model at low effort catching fewer planted errors than a smaller one at its effort floor (D-172), so the critic's route is chosen by PS-7, not by price or size. The canary uses the **weakest configured consumer profile** and is asked for an implementation outline and a list of blocking questions, not code; **its unit is the task card** (§8.2): a share for `compact`, every card for `standard` and `deep` (doc 03 §1 sampled per slice; amended in §16.1).

### 9.5 What the user sees (doc 06, amended)
**G1.5 structure view:** the app map, roles and access, entities in plain words, the inventory, and the **phase split for the user's yes** (§4.5). **G2:** summary first; the app map; flows; a **content map** per screen (what each shows and does, schematic, labelled "the final layout is the front end's"; this replaces wireframes rendered from the spec, AD-08); data in plain words; **what differs from the approved Brief** (merged, split, added, phased); assumptions; the **confirm list ranked by code (money, legal, identity first, the top 20 shown)**; capability tiers; store rules in plain words ("the stores require a way to delete the account"); research suggestions with include and exclude toggles; `degraded` nodes; risks. The G2 approval binds the **content hash** (§5.5). **A pack with any builder need `missing` never reaches G2.**

## 10. Scale and cost (AD-74)

**The point first:** at the prices recorded in doc 07 money is not Spec's binding limit; **provider rate limits and wall-clock time are.** The design therefore spends effort on concurrency, queues and resumability, and keeps cost low by doing in code whatever code can do. All numbers below are **[J]**, computed from configuration (prices, profile limits), measured in PS-8.

### 10.1 A worked estimate: one Big app, Phase 1 (60 screens, 11 modules, 22 entities, source locale plus two)
| Step | Calls | Input per call | Output per call | Input total | Output total |
|---|---|---|---|---|---|
| Plan | 1 | 20k | 4k | 20k | 4k |
| Product skeleton | 1 | 22k | 6k | 22k | 6k |
| Product per module | 11 | 20k | 6k | 220k | 66k |
| Contract and mock | 8 | 16k | 5k | 128k | 40k |
| Design | 3 | 12k | 4k | 36k | 12k |
| Screens (3 to 4 per call) | 18 | 22k | 7k | 396k | 126k |
| Behaviours and source copy | 5 | 14k | 6k | 70k | 30k |
| Locale passes (2 locales) | 12 | 8k | 5k | 96k | 60k |
| Verify (structure critic, 22 task-card canaries, final critic, summary) | 25 | about 22k | about 3k | 540k | 78k |
| **Total** | about 84 | | | **about 1.5M** | **about 0.42M** |
This agrees in order of magnitude with doc 07 §6 (about 3 to 4M input and 0.4M output for 100 screens over two apps, including probes). At the prices doc 07 recorded for a Haiku-class model (USD 0.10 and 0.50 per million), which the builder read again from the provider on 2026-11-13 (D-171; **not checked by me**), one such run is about **USD 0.36**; at ten times those prices about USD 3.6. Two facts from D-171 shape the estimate: the price rises **fivefold above 100K prompt tokens**, so every call in this design stays far below that threshold (V-26); and the gateway's Claude route sends **no cache markers today**, so **no caching is assumed here** (a profile that enables it lowers input cost and time to first token, but nothing depends on it). The starting proof budget of USD 5.00 therefore covers many Small and Medium runs and a few Big ones; the estimate is computed from configured prices **before** a run, shown as a range, and a run that would exceed its budget stops **`partial` and resumable** rather than lowering quality. **With doc 17's content a deep release is about 129 calls, 2.1M input and 0.64M output tokens, about USD 0.53, and about 14 minutes at 8 workers (41 at 2, 76 at 1).**

### 10.2 Latency
**Every fan-out is a number of waves, not one call:** `T ≈ Σ over steps of ceil(n_step / k) × T_step`, with the Design lane overlapped behind Product and the canaries overlapped with Screens (they share the same `k`). For the Big example, with call latencies of 30 to 60 s:
| Step | Calls × time | k = 8 | k = 2 | k = 1 |
|---|---|---|---|---|
| Plan, skeleton | 1 × 30 s, 1 × 40 s | 70 s | 70 s | 70 s |
| Product per module | 11 × 50 s | 100 s | 300 s | 550 s |
| Contract and mock | 8 × 40 s | 40 s | 160 s | 320 s |
| Join, Freeze critic | 5 s + 40 s | 45 s | 45 s | 45 s |
| Screens, behaviours, source copy | 23 × 50 s | 150 s | 600 s | 1,150 s |
| Locale passes | 12 × 30 s | 60 s | 180 s | 360 s |
| Canaries (overlapped; the part that spills over) | 22 × 20 s | 20 s | 220 s | 440 s |
| Final critic, summary, assemble | 45 s + 25 s + 15 s | 85 s | 85 s | 85 s |
| **Total** | | **about 9.5 min** | **about 28 min** | **about 50 min** |
The Design lane (3 calls) hides behind Product. Small and Medium at their own `k`: about 4 to 6 and 7 to 9 minutes. **Concurrency decides time**, which is why §10.3 computes it.

### 10.3 Concurrency is computed, not chosen
`k = max(1, floor(TPM_share / (input_tokens_per_call × calls_per_minute_per_worker)))`, with `calls_per_minute_per_worker = 60 / T_call`, also bounded by the requests-per-minute and output-tokens-per-minute limits and by `max_parallel` in the profile. With 22k-token inputs and 50 s calls one worker uses about 26k input tokens a minute: a 400k tokens-per-minute share allows about 15 workers, 100k about 3, 50k only 1. Whether cached input counts toward the limit differs by provider and is measured. The scheduler adapts: add one worker after a clean window, halve on a rate-limit response (additive increase, multiplicative decrease), per provider route.

### 10.4 Many users
- **Weighted fair queue per tenant**: classes interactive (a user waiting at G2, weight 3), resume (2), background re-package or regeneration (1) [J]; a tenant may hold at most `max(1, floor(0.5 × k_total))` workers while others wait. The queue position and an estimate are shown; the wait target for interactive work is 60 s and an alert fires above 120 s [J]; when the target is exceeded, new background work is deferred and interactive work is never dropped. **A slow queue never lowers quality.**
- **Quotas**: runs and tokens per tenant per period (starting at 20 runs and 10M tokens a day [J], configuration), the scope cap, and a ceiling on apps and entities per program, as abuse and runaway protection; a run whose estimate exceeds the remaining quota is refused with a plain message before it starts.
- **The front end's workers share provider limits too.** The build plan carries `parallelism_hint = min(wave size, k_fe)` with `k_fe` from the §10.3 formula applied to the front-end consumer's own profile, so a wave of 16 workers is never promised on a tier that allows 3.
- **Circuit breaker and failover** per route; a credit or rate stop ends the run `partial` and resumable (§8.4).
- **Caches are tenant-scoped.** A result cache keyed by (tenant, step, input hash, route class, prompt version) answers a resume or a double click. **Only library data is shared across tenants, and it holds no user content.**
- **Provider prompt caching**, where a profile enables it, carries the stable prefix of Spec's own calls (instructions, schemas, library excerpts) and of the front end's workers (the prefix bundle); nothing depends on it.

**What a load needs from the provider** (from §10.1 and §14; concurrent runs = runs per hour × minutes ÷ 60; per-run rates are input or output tokens over the run's minutes):
| Load per hour | Concurrent runs | Input and output tokens per minute needed |
|---|---|---|
| 30 Quick runs (0.2M in, 0.055M out, about 5 min) | 2.5 | about 100k in, 28k out |
| 30 Standard runs (0.9M in, 0.26M out, about 8 min) | 4 | about 450k in, 130k out |
| 5 Big runs (2.1M in, 0.64M out, about 14 min, with doc 17's content) | 1.2 | about 175k in, 53k out |
| **All three together** | about 7.7 | **about 0.73M in, 0.22M out** (Quick and Standard also grow by roughly a tenth to a quarter with doc 17's content) |
Below that provider tier the queue grows and waits lengthen; the numbers are inputs to the quota and tier decision, not promises.

### 10.5 Incremental regeneration (the cheapest call is the one not made)
Every node records the hash of the inputs it was written from. A change recomputes only nodes whose input hash changed; unchanged files keep their bytes, so consumers see only a diff.
| Change | Regenerated | Typical model calls |
|---|---|---|
| Token or copy edit (L1) | tokens or copy files; no re-plan | 0 to 1 |
| Add or change a feature (L2) | that module's Product call, its new or changed cards, contract additions, a Verify pass on the touched nodes | about 3 to 6 |
| Re-approved Brief | nodes whose input hash changed | proportional to the diff |
| Switch the front-end consumer | **re-package only (code)** | 0 |
| Start Phase 2 | outline screens to full cards, incremental | as a Phase 1 of that size |
| Add an app (L5) | a new program version | as a new run |

### 10.6 Observability and storage
A per-run report: calls and tokens by step, cache hits, latency by step, retries, validator results, degraded nodes, cost against estimate. Alerts: estimate drift above 30%, repeated rate limits, queue wait above its target. Packs are content-addressed (versions share unchanged files); approved baselines are kept indefinitely, working versions for a configured number; `evidence/` never goes to the front-end store.

## 11. Model and provider independence (AD-76)

**The owner's rule: a temporary Haiku-class model now; Gemini or OpenAI models later; nothing may depend on a model or a provider.**

### 11.1 What is forbidden and what replaces it
Forbidden in Spec's prompts, schemas, packs and logic: a model or provider name; vendor-specific prompt syntax; an assumption about window size; a token number outside configuration. Replaced by: **routes** (`fast`, `standard`, `deep`; repo rule 14: engines name routes, never models, and only the gateway maps a route to a model), **consumer profiles** (data), and **validators** that judge outputs by what they contain, never by who wrote them. A design that holds when every route maps to the same weak model is the test: it does today.

### 11.2 Route use
| Step | Route | Why |
|---|---|---|
| Plan, Product skeleton | `deep` | the few steps where judgment spans the whole app |
| Product per module, Contract and mock, Design, behaviours and copy | `standard` | narrow, schema-bound |
| Screens | `fast` or `standard` | many small cards; library states are instantiated by code |
| Locale passes | `fast` | translation of short strings |
| Verify critic | `standard`, different vendor if configured | independence |
| Canary and consumer probe | the weakest configured consumer profile | the design target |
Today all routes may map to one Haiku-class model; the design does not change.

### 11.3 Weak-model-safe design (the principles that make this hold)
Narrow calls with strict JSON Schemas and closed vocabularies; few-shot examples drawn from the libraries; **code does counting, ordering, ID minting, arithmetic and sizing**; retrieval by id instead of recall; inputs assembled by code and small; one repair then a deterministic fallback; no step asks a model to hold more than its profile's effective window; flat, explicit cards (no cross-file inference); a stronger route is tried first for the plan and the skeleton, and the critic's route is chosen by measurement (PS-7), not by size; a check the model must pass is a validator, not a hope. **Portable schemas**: flat, closed records in arrays instead of open maps (ADR 0014 bans open objects), few optional or nullable fields per call (a provider refused a schema with more than 16 nullable parameters, D-172), shallow nesting, small enums; an offline compile check runs every call schema against every configured profile's limits (PS-0). **No over-filling**: a weak model fills optional structured fields the source does not state (D-172), so unknown fields stay null or `assumed`, every filled field needs provenance (user, library, policy, research id), and validators check provenance and bindings. Effort is configured per model in the gateway, never in a call spec.

### 11.4 Consumer profile (a data file; values come from each provider's current documentation, dated, never from memory)
| Field | Meaning |
|---|---|
| `id`, `capability_class` | `weak`, `standard` or `strong` |
| `nominal_context_tokens`, `effective_context_tokens` | the advertised window, and the working size at which our probe quality stays within tolerance (start at a fraction of nominal [J], then measure); **budgets use the effective value** |
| `max_output_tokens` | bounds `screens per call` |
| `tokenizer` | a counter reference or a conservative factor |
| `caching` | `explicit_breakpoints`, `automatic_prefix`, `cached_object` or `none`; minimum cacheable tokens, lifetime, breakpoint count |
| `structured_output` | `schema`, `json_mode` or `none`, with the **schema limits**: maximum optional or nullable parameters, depth, enum size, compiled-grammar size |
| `limits` | requests, input tokens and output tokens per minute; `max_parallel` |
| `price` | input, output and cached input per million tokens; the **long-prompt surcharge** (threshold tokens and multiplier: a Haiku-class price is five times above 100K prompt tokens, D-171); whether **reasoning tokens** are billed as output and count against `max_output_tokens` (configuration) |
| `effort` | owned by the gateway (an `effort_floor` per model, D-172); never in a call spec |
| `source_url`, `checked_on` | provenance of the numbers |
Initial entries: `weak-default` (the temporary Haiku-class route), `standard-default`, `large-context-default`. Filling them is part of S0.

### 11.5 What changes when the provider changes
| Concern | Handling |
|---|---|
| The front-end consumer changes | **re-package** for the new profile (code, no model run); content is unchanged, except in the rare case of §5.4 (a heavier tokenizer pushing one slice over its cap) |
| Spec's own route changes | remap in the gateway; prompts and schemas unchanged; call specs live in the agent package (ADR 0014) and carry no vendor detail; vendor, price, effort, schema dialect and token accounting live only in the gateway's provider request building and configuration (rule 14) |
| Caching differs (explicit breakpoints, automatic prefix, cached objects) | the stable prefix leads every prompt; the gateway adapter owns the mechanics |
| Structured output differs | schema mode where it exists; otherwise validated JSON plus repair |
| Token counting differs | the profile's counter or conservative factor; content splits use the fixed reference tokenizer (§5.4); never a hard-coded guess |
| A model refuses or filters (health, identity, adult categories) | fallback route; the node is `degraded`; never a crash |
| Outputs differ run to run and route to route | **validated, not compared**: two routes need not give identical specs; both must pass the same validators and probes; variance bands are recorded (single runs decide nothing, the lesson of Research) |
The graph's content hash excludes route and model identity; `MANIFEST.json` records `produced_with` (route classes, consumer profile id, route-map hash) for traceability only.

### 11.6 Credits, limits and failover are first-class
Research's live proof stopped on an exhausted account balance (HTTP 429 on every call). Spec treats that as a normal event: the circuit breaker waits out an open circuit instead of using its fallback silently, a failover route is used where configured, the run ends `partial` with checkpoints, and `resume` continues. The provider-switch test (PS-9) runs the same fixtures on two routes.

## 12. Edge-case catalogue

Every case has a **handling rule** and a **fixture** that doc 16 PS-9 runs. IDs carry the prefix `SPC-` because doc 05 and doc 13 already use IN-, RS-, SC-, CH- and OP- (and `OP-` is the operation prefix). There are **113 cases here and 35 more in doc 17 §11 (148 in all)**. Fixture kinds: **real** (an existing export in `docs/compass/research-inputs/`), **mut** (a mutated copy of one), **syn** (a synthetic graph or export), **mock** (a mock route behaviour in the gateway), **cfg** (a configuration or consumer-profile variant). Handling rules are the design; the fixtures prove them.

### 12.1 Input from Compass (IN)
| ID | Case | Handling | Fixture |
|---|---|---|---|
| SPC-IN-01 | Format 1 export (the four views, no structure) | reader builds `SpecInput` from the views and the Brief; Product derives the structure; flagged `structure_derived` | real: delhi, pune |
| SPC-IN-02 | Trimmed export (no views; locales is a sentence) | views derived from the Brief; locales parsed, else Default `en` plus a drift flag | real: hospital |
| SPC-IN-03 | Unknown future `format_version` or renamed keys | tolerant read; drift flags per missing or changed field; proceeds if an idea, a role and a feature are found, else a readiness fail with a plain list | mut: standard with version 3 |
| SPC-IN-04 | Approved while `degraded: true` | every Default stays Default; more confirms; G2 leads with "weak inputs" | real: standard |
| SPC-IN-05 | Approval unverified (`input_binding: unverified_*`) | proceed with a flag and a G2 banner | mut: hash removed |
| SPC-IN-06 | Junk or duplicate roles and apps (18 roles; "Manager side" is a role and an app) | cleaned with recorded merges (singular and plural, role against app); unresolved goes to G1.5 | real: thorough |
| SPC-IN-07 | Duplicate features | merged with an alias; a user-evidenced `must` is never dropped | real: standard |
| SPC-IN-08 | All or most priorities `nice` | priority is Default; order comes from journeys; a Locked `must` is never demoted | real: thorough |
| SPC-IN-09 | An integration implied but missing ("Buyers pay online" and no integrations; payments `real_later` while the user says "pay in the app") | derive the need from facts and flows; Phase 1 mock-first; flag the mismatch at G2 | real: standard, quick |
| SPC-IN-10 | Conflicting facts (login by email and password against farmers' phone code) | the user-evidenced fact wins; the conflict is recorded; a confirm item | real: standard |
| SPC-IN-11 | Country-only or joined multi-country geography | parsed to regions; per-region currency, language and legal list; unresolved goes to confirm | real: central-america, thorough |
| SPC-IN-12 | Mixed scripts and right-to-left | script-aware type stacks in tokens; right-to-left as a global behaviour; copy flagged for native review | real: standard (mr, hi); mut: Arabic |
| SPC-IN-13 | No brand | direction derived from audience and category; preset palette checked for contrast; `assumed` | real: standard |
| SPC-IN-14 | Default tone (confidence 0.4) | Default and `assumed`; never used as user evidence | real: all three |
| SPC-IN-15 | Very large export (4 MB; blueprint view about 53k tokens) | views only, per-module slices, never the whole | real: thorough |
| SPC-IN-16 | Locked facts with low confidence | graded per field (§3.4): Default | real: thorough |
| SPC-IN-17 | Structure with errors (58 unreachable screens; access conflicts) | the structure is a proposal; validators repair by derivation; the issue list goes to G1.5 | real: thorough, standard |
| SPC-IN-18 | Mixed-language Brief (Hinglish quotes) | quotes kept; technical fields in English; app copy in the app locales; G2 in the chat language | mut: Hinglish |
| SPC-IN-19 | The Brief changes after Spec ran | input-hash diff; incremental regeneration; G2 reopens for touched sections | mut: standard plus one feature |
| SPC-IN-20 | A Locked `must` with no screen | V-29 blocks; Product adds a screen or records why | mut: standard minus a screen |
| SPC-IN-21 | A feature deferred by the user ("live tracking can wait") against one deferred for capacity | both are `later` with different reasons (`reason: user`, `reason: capacity`); the user's `later` is never relabelled; neither has screen nodes (V-19); the outline lives in the ledger | real: standard (FCT-030) |
| SPC-IN-22 | Existing systems named in the Brief (the Thorough Brief names Tally) | each becomes an integration need with `mode: adapter` and a data import or sync flow in `integrations`; no assumption that an API exists; confirm | real: thorough |

### 12.2 Input from Research (RS)
| ID | Case | Handling | Fixture |
|---|---|---|---|
| SPC-RS-01 | Research failed or skipped | every lane thin; libraries fill, marked `assumed`; G2 says "research incomplete" | real: standard, no research file |
| SPC-RS-02 | Research `partial` (ceiling reached) | use what is verified; gaps flagged | syn: partial store |
| SPC-RS-03 | Mostly `unverified` or `single_source` | no defaults; suggestions and confirm only | syn |
| SPC-RS-04 | Dossier field in `conflict` | both values kept; confirm; no default | syn |
| SPC-RS-05 | No in-market payment provider found | provider-neutral payment flow, mock adapter, confirm "choose a provider" | syn: global-only providers |
| SPC-RS-06 | `brief_hash` differs from the Brief | flag `research_stale`; proceed or request a delta; G2 banner | mut |
| SPC-RS-07 | A `pack` unit over budget, or an item known only by title | a second deterministic `pack` with a narrower `need_card`; an over-budget unit travels alone | syn: Big store |
| SPC-RS-08 | Screens lane `none` | state library and Compass patterns only; no invented peer practice | syn |
| SPC-RS-09 | `NEW` items not in the Brief | suggestions at G2, never scope; Default only if `official` or `corroborated` and the user toggles it in | syn |
| SPC-RS-10 | Compliance register thin or regimes unnamed | compliance is information; consent, deletion and disclosure flows come from signals and the policy sheet; confirm | real: hospital |
| SPC-RS-11 | Research schema moves to a new version | adapter; unknown fields ignored; contract tests | mut |
| SPC-RS-12 | A number: Research's against the user's | a Research number (fee, limit) is never defaulted; a user-stated number (FCT-015, 4% commission) is a Locked rule | real: standard |

### 12.3 Scale and kinds of app (SC)
| ID | Case | Handling | Fixture |
|---|---|---|---|
| SPC-SC-01 | Tiny app (3 to 6 screens, no accounts) | `compact`; system set trimmed to what applies; one whole-pack file; no module folders | syn: tiny |
| SPC-SC-02 | No backend (local only) | no Contract; local persistence in `state-and-sync`; fixtures are seed data | syn |
| SPC-SC-03 | Read-only content app | minimal states; no forms | syn |
| SPC-SC-04 | Offline-first use | sync queue, conflict policy, offline states first-class, idempotent actions | real: standard + offline flag |
| SPC-SC-05 | Realtime (live tracking, chat) | events and a realtime channel in the contract; polling fallback; background limits noted | real: thorough (Trip Tracking) |
| SPC-SC-06 | Multi-currency, multi-region tax | money typed with currency; formats per locale; tax rules per region with confirm | real: central-america |
| SPC-SC-07 | Sensitive data (health, minors, identity) | consent, deletion and export flows, audit trail, age gate, store disclosures | real: hospital, dakar |
| SPC-SC-08 | Six apps with cross-app handoffs | shared entities owned once; cross-app scenarios; the cap and the deferrals applied per app; shared modules once | real: thorough |
| SPC-SC-09 | One purpose for nine roles | one screen with role-conditioned regions and actions; split only when purposes differ | real: standard |
| SPC-SC-10 | Hundreds of entities | `data-model` shards by cluster; an entity index; closure projection | syn: ERP |
| SPC-SC-11 | Twenty integrations | a dossier each; mock-first order in the build plan | syn |
| SPC-SC-12 | Three locales with 500 strings each | copy sharded by module and locale; locale passes batched; cards carry keys | real: standard |
| SPC-SC-13 | Double click or repeated runs | single-flight lock; result cache | mock: concurrent start |
| SPC-SC-14 | Derived inventory far outside its tier (a Quick app deriving 40 screens) | V-29 range warning; ask at G1.5 whether the idea is bigger than the tier; never trimmed silently | mut: quick plus features |
| SPC-SC-15 | An app's `in` screens above the cap after the pull-in; the user wants everything | G1.5 shows the conflict; allowed as one release with a recorded warning; more waves | syn: 90 core screens |
| SPC-SC-16 | Web portals (dispatcher, finance) | surface `web portal`; needs stated (dense tables, keyboard use), no layout | real: thorough |
| SPC-SC-17 | Marketplace payouts and splits | payout lifecycle, commission rule, reconciliation states; provider on confirm | real: standard |
| SPC-SC-18 | Scheduling, time zones, recurring items | time-zone rule per entity; recurrence rules; calendar behaviours | real: quick |
| SPC-SC-19 | Media and document upload | size and type limits, permission, failure and resume states | real: thorough (driver documents) |
| SPC-SC-20 | Account deletion and data export | policy-Locked flow, always present when accounts exist | any with accounts |
| SPC-SC-21 | Organisation hierarchy and approvals (multi-level roles, approval workflows, delegated access) | roles carry a level; approvals are lifecycle transitions gated by an approver role; the access matrix is per level; G1.5 shows who approves what | real: hospital, thorough |

### 12.4 Model and provider (MD)
| ID | Case | Handling | Fixture |
|---|---|---|---|
| SPC-MD-01 | Weak judgment on a merge or split | deterministic rules first, the model only for the residue; validators catch the rest | real: standard |
| SPC-MD-02 | Invalid JSON or schema violation | one repair; then the library default; the node is `degraded` | mock: invalid JSON |
| SPC-MD-03 | Output truncated by the length limit | detect the finish reason; retry with a smaller batch | mock: truncation |
| SPC-MD-04 | Refusal or content filter | fallback route; `degraded`; never a crash | mock: refusal |
| SPC-MD-05 | Rate-limit responses mid-run | back off, adapt concurrency, resume from checkpoints | mock: 429 storm |
| SPC-MD-06 | Credits exhausted | the breaker waits out an open circuit; the run ends `partial`; resumes after a top-up | mock: "no credits" |
| SPC-MD-07 | Provider switch (Haiku-class to Gemini or OpenAI) | profile changes; re-package; the same validators and probes pass | cfg: two profiles |
| SPC-MD-08 | A tokenizer 30% heavier | conservative factor until measured; the packager re-shards | cfg: factor 1.3 |
| SPC-MD-09 | A smaller or much larger window | the **effective** window sets budgets; a larger one raises the task budget, content unchanged | cfg |
| SPC-MD-10 | No structured-output mode | validated JSON plus repair | cfg: `none` |
| SPC-MD-11 | No prompt caching, or another style | the gateway adapter; the prefix still leads; the estimate uses `cache_share` from the profile | cfg: `none` |
| SPC-MD-12 | Same input, different outputs | the graph commit is the checkpoint; replay from checkpoints; variance bands recorded | real: three runs |
| SPC-MD-13 | One vendor only | ~~the critic still runs; `critic_vendor: same` is flagged~~ **amended (doc 18):** no critic runs for compact and standard; one vendor matters only for the proof-only critic arm and for a future Verify, which flags `critic_vendor: same` | cfg: single vendor |
| SPC-MD-14 | Prose instead of JSON despite the schema | as SPC-MD-02; the rate is logged | mock |
| SPC-MD-15 | Prompt injection in a Brief quote or a Research item | data inside delimiters; no tools; output validated; instructions in data ignored | mut: poisoned quote |
| SPC-MD-16 | The provider refuses a call schema (too many nullable fields, a grammar too large) | schemas are authored in the portable subset and compile-checked offline against every profile's limits; the gateway sanitises the dialect; a refusal falls back once to instructions-only JSON with validation | cfg: schema limits |
| SPC-MD-17 | A weak model over-fills optional fields the source does not state | unknown stays null or `assumed`; every filled field needs provenance; validators check bindings and provenance; effort is a gateway setting | mock: over-fill |
| SPC-MD-18 | A long-prompt price surcharge above a prompt-size threshold | every call input stays below the profile's threshold (V-26); the estimate uses the profile's multiplier | cfg: surcharge |

### 12.5 The front end (FE)
| ID | Case | Handling | Fixture |
|---|---|---|---|
| SPC-FE-01 | A fact is missing from a slice | V-27 prevents it; at run time at most 3 lookups, then a raised decision | mut: field removed |
| SPC-FE-02 | Two workers need one shared file | shared files are code-generated; `owns` disjoint; build checks | syn: overlapping `owns` |
| SPC-FE-03 | Change after the build started | L1 and L2 hot-patch unstarted tasks; L3 and above wait at a wave barrier | syn: change set |
| SPC-FE-04 | A worker invents screens or ignores the spec | files without a valid `@spec` stamp are `unplanned`; the usage map reconciles | syn |
| SPC-FE-05 | A capability outside the Stack Kit | tier B or C flagged at Spec time; never silently specified | syn: NFC feature |
| SPC-FE-06 | Performance risk from volume (a 5,000-item list) | expected volumes in `data-model`; list behaviour states paging; no implementation advice | syn: catalogue |
| SPC-FE-07 | A task too large for the budget | the packager splits it; a heavy screen travels alone | syn: checkout-class slice |
| SPC-FE-08 | Deep link before sign-in, or to a screen the role cannot see | gate, remember the target, route after sign-in; a forbidden state | syn |
| SPC-FE-09 | Push tap while signed out | as SPC-FE-08; the notification payload maps to a route | syn |
| SPC-FE-10 | A consumer with a larger window | the profile raises the budget; fewer tasks; same content | cfg |
| SPC-FE-11 | Generic, look-alike output | the freedom probe (PS-4); direction axes differ per app | syn |
| SPC-FE-12 | Large text, screen reader, reduced motion | floors are policy-Locked; announcements are in the cards | syn |

### 12.6 Change and versioning (CH)
| ID | Case | Handling | Fixture |
|---|---|---|---|
| SPC-CH-01 | Token-only change | recompile tokens; only the tail of the prefix bundle changes | syn |
| SPC-CH-02 | Copy change | per-key patch; the copy shard only | syn |
| SPC-CH-03 | Add a feature | module Product call, new cards, additive contract | mut |
| SPC-CH-04 | Add an app | L5: a new program version; G2 reopens | syn |
| SPC-CH-05 | Rename a screen | alias; stamps stay valid through it | syn |
| SPC-CH-06 | Merge or split screens | tombstones and aliases; V-32 | syn |
| SPC-CH-07 | Re-approved Brief | input-hash diff (SPC-IN-19) | mut |
| SPC-CH-08 | Undo | restore a previous manifest by hash | syn |
| SPC-CH-09 | Start Phase 2 | the backlog moves `later` to `in` as one batch through the Scope Change Protocol; incremental regeneration | syn |
| SPC-CH-10 | Compass later issues `SCR-029` when Spec minted `SCR-S029` | separate namespaces; no collision | syn |
| SPC-CH-11 | A feature moves between the release and the backlog after G2 or G3 | an L4 change set with consent; incremental regeneration; the waves re-plan; the approved baseline stays immutable | syn |

### 12.7 Operations (OP)
| ID | Case | Handling | Fixture |
|---|---|---|---|
| SPC-OP-01 | Crash mid-run | checkpoints; resume | mock: kill at batch 7 |
| SPC-OP-02 | Some screen batches fail | those screens `degraded` with library defaults; ~~one retry on a stronger route~~ **amended (doc 18 §3.2):** the ladder (code fixes, one re-ask per node batched per call kind, flag); a pack with a `missing` builder need never reaches G2 | mock |
| SPC-OP-03 | Concurrent runs for one program | single-flight lock | mock |
| SPC-OP-04 | Secrets or personal data in inputs | no keys in packs; fixtures are synthetic; personal data in the Brief is not copied into fixtures; a secret scan runs on packs | mut: Brief with a phone number |
| SPC-OP-05 | Pack storage growth | content addressing; retention policy | syn |
| SPC-OP-06 | A slow route | per-step time budgets; timeout means `degraded` | mock: slow |
| SPC-OP-07 | The store-policy sheet is stale | `policy_sheet_stale` flag at G2 | cfg: old date |
| SPC-OP-08 | The validator library is upgraded | packs record the validator version; re-verify on upgrade | syn |
| SPC-OP-09 | Machine locale or clock affects output | byte stability: locale-independent sorting, no timestamps in bodies | real: run under two locales |
| SPC-OP-10 | Cost of the backlog outlines | bounded: outlines are ledger entries written inside the module Product calls, not separate calls | syn |
| SPC-OP-11 | A prompt, schema or library version changes while runs are in flight | checkpoint keys carry the versions; a resumed run keeps the versions it began with unless restarted; the pack records them | mock: version bump mid-run |

### 12.8 The gates (UX)
| ID | Case | Handling | Fixture |
|---|---|---|---|
| SPC-UX-01 | The user rejects the deferral | everything in one release with a warning, or the user chooses the deferrals; recorded | syn |
| SPC-UX-02 | G2 too long to read | summary first, diff against the Brief, ranked confirm top 20, content maps | real: thorough |
| SPC-UX-03 | A non-technical reader | plain words, no JSON, store rules explained | any |
| SPC-UX-04 | The user's confirm answer differs from a Research default | the user wins (Locked by user); the override is recorded | syn |
| SPC-UX-05 | Time to the first view | the G1.5 view appears as soon as the skeleton and inventory exist | real |
| SPC-UX-06 | A requested feature is tier C | said plainly at G1.5 and G2; not hidden | syn |

## 13. Optimization register

> **Amended (doc 18 §5, §10):** the "canary on the weakest consumer, independent critic" entry of the Quality row and the "skipping the canary to save time" entry of the list below are **deliberately reversed for compact and standard**, on the owner's direction (latency); both stay in the proof (PS-6, PS-7). The optimizations of doc 18 §5 (free wins A1 to A7 and switches B1 to B5) are added.

| Objective | Technique | What it must never cost |
|---|---|---|
| **Latency** | Product ‖ Design; per-module fan-out; screens in parallel batches; Verify streaming; the G1.5 view early; the backlog only as ledger outlines; assembly in code (seconds); concurrency **computed** from limits and adapted (§10.3) | the Freeze (no Screens before it); the canary probe; the structure critic |
| **Cost** | libraries instantiate the system set, states and CRUD skeletons (no model); models only at judgment; route classes by step; stable prefixes cached; closure projection and need-card packs; result cache; incremental regeneration; locale passes batched and separate; the backlog on demand | provenance, validators, quote-backed Locked facts |
| **Context efficiency** | layers and shards; closure projected to use; ids not prose; compact tables over nested YAML, measured with the consumer's tokenizer; byte-stable bodies so consumers read diffs | self-sufficiency of a slice (V-27) |
| **Reliability** | checkpoints, resume, idempotent writes, single-flight, breaker, failover, honest `partial`, per-step time budgets | an honest status; no silent fallback |
| **Quality** | schema-constrained output, library standard states and scenarios, validators V-01 to V-35, canary on the weakest consumer, independent critic, coverage matrix | none of these is dropped for speed or money |
| **Maintainability** | data-driven libraries, adapters with one fixture per format, shared packages, versioned schemas, contract tests, consumer profiles | |
| **Observability** | per-run report and per-tenant counters (§10.6) | |

**Optimizations deliberately not taken:** skipping the canary to save time; truncating a pack to fit; sharing result caches across tenants; one giant call for the whole app; writing every locale inside the screens call; dropping the backlog outlines (the release's completeness check needs them); choosing the critic's route by price or size instead of measurement (PS-7), or the cheapest route for the plan or skeleton without evidence that it holds; asking the front end to re-derive decisions from prose.

## 14. Worked examples (arithmetic on the real exports; every size is an estimate [J] to be measured in PS-2)

### 14.1 Quick: yoga studio, Bengaluru (`compact`)
**Compass:** 1 app, 4 roles (Members, Studio owner, Studio staff, Instructors), 4 modules, **13 screens (10 core, 3 extended)**, 4 flows, 5 entities (Class, Booking, Class Pack, Payment, Reminder), locales English and Kannada, payments marked `real_later` although the user said "Each member pays the studio directly inside the app by UPI or card". The four views total about 16k tokens (blueprint 8.7k).
**Derivation.** Start from the 13. The three `extended` screens are phone sign-in, code verification and language settings: **pull-in moves them to Phase 1** (nobody can book without signing in). System set adds profile, help and contact, account deletion (+3; terms and privacy are links out). **Result: 16 screens in the release, nothing deferred, no cap pressure.** Payments is promoted from `real_later` to "needed in Phase 1, mock-first" and the mismatch is listed at G2 (SPC-IN-09).
**Pack (about 60k tokens in all):** index and `AGENTS.md` 3k; global files about 22k (including copy in two languages, Kannada heavier); Contract v0 about 9k (14 operations); 16 cards about 17.6k; modules and app 4k; journeys 4k. **A worker reads about 17k from Spec** (prefix 8k, shards 3k, module 1k, three slices about 5k) and carries about three to four screens: **five or six screen tasks**. 60k is above `whole_pack_tokens` (22k), so even this small app ships as a tree.
**Calls and cost:** about 15 calls (the plan, product 1, design 2, screens 4, behaviours and copy 1, Kannada 1, fixtures 1, verify 4: two sampled task canaries, the final critic and the summary); about 0.2M input and 0.055M output tokens; **about USD 0.05** at doc 07's recorded prices; about 4 to 6 minutes.

### 14.2 Standard: produce marketplace, Nashik (`standard`)
**Compass:** 4 apps (Farmer or FPO app, Buyer app, Transport partner app, "Manager side"), 9 roles, 5 modules, **28 screens (23 core, 5 extended)**, 7 flows, 10 entities (Order has six fields), locales Marathi, Hindi, English, `degraded: true`, 3 access-conflict errors, no integrations listed although the user said "Buyers pay online" (FCT-005), "About 4% commission is paid by the farmer on each sale" (FCT-015) and "Live delivery tracking for buyers can wait for a later version" (FCT-030). The four views total about 32k tokens (blueprint 19.7k).
**Derivation.** Start from the 28. Sign-in (`extended` in Compass) is pulled into the first release. Additions from the system set: a verify-code screen (phone-code login has none) **+1**, forgot or reset password (the manager signs in by email and password) **+1**, help and contact **+1**, account deletion **+1**; create listing and edit listing become **one form with a mode (−1)**. "Manager side" is split into the role Manager and the app Manager side (SPC-IN-06). Payments is derived from FCT-005 as an integration need (SPC-IN-09); the 4% commission is a **Locked rule** (user-stated; SPC-RS-12); live tracking is a user `later` (SPC-IN-21): a ledger entry with an outline and **no screen node**, and **Delivery details shows status only**. The three access errors in Compass's structure are real and specific: "Choose account" (SCR-002) views Business account (ENT-002), which the buyer role may only `none`; "Weekly produce" (SCR-005) views Farmer (ENT-004) and FPO (ENT-005), which buyers may only `none`. Spec repairs each by narrowing the binding to what the role may see (buyers see a seller's display name through the produce listing, not the Farmer record) and records it for G1.5. **Result: 31 screens in the release, nothing deferred by capacity**, split over four apps (shared account screens counted once per app that needs them); role apps of 6 to 15 screens are normal, which is why the medium range of §4.7 is a program total.
**Pack (about 155k tokens in all):** cards 31 × 1.5k = 46k; global files about 36k; copy in three languages about 19k (Devanagari is token-heavy); Contract v0 about 27k; modules and apps 11k; journeys and scenarios 10k; index 5k.
**One task card (the example in §5.13):** prefix 8.0k + shards 4.7k (order cluster 2.0k, errors and offline 1.5k, payments 1.2k) + module 2.0k + slices SCR-013 (1.7k), SCR-014 (1.9k) and SCR-015 (1.6k) = 19.9k from Spec, plus 4k of the front end's own files = **23.9k, inside the 28k budget**; a fourth slice of about 2k would still fit, and the packager stops at the module's boundary.
**Developer test: can a developer build SCR-014 from the prefix bundle plus its card alone?** Reading it as the front end would, the questions that arise are: which provider? (answered: undecided, a provider-neutral flow with a mock adapter, on the confirm list); what if the app is killed during the provider's flow? (**not answered**: gap); how long may a payment stay pending? (**not answered**: gap); who may pay for a business account? (answered by the access matrix); what languages? (answered by the copy keys). The two gaps are real: **the design therefore makes "interrupted external flow" a required `behaviors` category (V-35) and "pending timeouts" a required `rules` entry for any integration with a provider-side flow**, with a Default and a confirm flag.
**Calls and cost:** about 52 calls (above the typical standard range because of ten locale-pass calls and a canary for each of eleven task cards); about 0.9M input and 0.26M output tokens; **about USD 0.22** at doc 07's prices; about 7 to 9 minutes at six workers.

### 14.3 Thorough: freight and fleet, India (`deep`)
**Compass:** 6 "apps" (one, "Fleet maintenance screens", is a module), 18 roles (group names plus specific roles plus singular copies), 11 modules, **132 screens (34 core, 98 extended)**, 20 flows, 22 entities (3.3 fields on average, 44 of 72 typed `text`), payments and messaging only; 76 structure issues (58 unreachable screens); `fallback_count` 9. The four views total about 93k tokens (blueprint about 50k).
**Compass's `core` is not a product.** Per module (core / extended): Account 3/2; Customer Bookings 7/3; Trip Tracking 4/0; **Dispatch 0/19**; Driver Trips 8/12; Driver Expenses 4/0; Fleet Maintenance 0/25; Finance 8/8; Compliance 0/13; Operations Dashboard 0/4; Organisation Admin 0/12. A freight app whose first phase has **no dispatch** cannot dispatch a truck.
**What Spec's pull-in does:** Account all 5; Dispatch the core journey (receive or create a booking, assign a driver and a vehicle, watch trip status, handle an exception) about 7 to 9 screens; Driver Trips 8 plus acceptance and proof of delivery, about 10 to 12; Finance 8 plus invoice basics; Organisation Admin users and roles 3 to 4 (nobody can be added otherwise); vehicle and driver document upload 2 to 3 (dispatch needs verified drivers). Fleet Maintenance, Compliance (beyond documents), Operations Dashboard and the rest of Organisation Admin stay `later` (`reason: first_release`), kept as outlines in the ledger. With the system set per app, **the release is about 70 to 75 screens over five apps, each far below 60; the backlog holds about 40 to 60 screens' worth of outlined features.** G1.5 is a hard gate for `deep`: the user is shown that the first release contains dispatch although Compass's core did not, and says yes or changes it (the consent is the I-4 record for the features that move).
**Pack:** release cards about 75 × 2.2k = 165k; global files about 90k (22 entities enriched from about 3 to about 15 fields from Research shapes and the Module Library; five data-model shards); Contract v0 about 70k; journeys and scenarios about 30k; backlog outlines about 10k (in `scope-ledger.json`); index 6k: **about 370k tokens in all, so the pack cannot be read whole by any consumer; about 20 to 25 screen tasks of 20 to 24k each**, run as 8 to 16 workers per wave.
**Calls and cost:** about 90 calls (about 66 to write the pack and 25 for Verify, including a canary for each of about 22 task cards); about 1.75M input and 0.47M output tokens; **about USD 0.41** at doc 07's prices (about USD 4.1 at ten times); about 11 to 14 minutes at eight workers.

### 14.4 A synthetic big fixture: an Uber Eats-class program (to be built as an S1 fixture; my enumeration of typical screens, not measured)
| App | How the derived inventory arises | Derived | Cap 60 binds? |
|---|---|---|---|
| Customer | account and settings 14 (sign-in, code, profile, addresses, add address, payment methods, notification settings, language, help, legal, deletion); discovery 5; restaurant 4; cart and checkout 8; orders 9 (confirmation, tracking, courier contact, history, detail, reorder, rating, report issue); support 4; offers and loyalty 5; favourites and inbox 3 | about 52 | no |
| Courier | onboarding and documents 8; online and dashboard 1; incoming offer 1; navigation 2; pickup and delivery confirmation 2; earnings 3; ratings 1; support 2; profile and settings 3 | about 23 | no |
| Restaurant | orders board and detail 3; menu management 6; hours 1; promotions 2; reports 4; staff 2; store settings 3; support 2; account and legal 5 | about 28 | no |
| Admin (web portal) | dashboard; restaurants, couriers, customers (list, detail, approval or verification); orders, disputes, refunds; promotions; zones and fees; payouts; reports 4; support 2; roles and users; audit log; settings | about 40 | no |
**The answer to "how many for a big app":** the program is about **140 screens**, but **no single app reaches the cap**, because the unit is the app: four front-end runs of 23 to 52 screens, not one run of 140. **If one app derived 75**, the release would be about 58 (system set 14, core journeys 28, pulled-in dependencies 6, highest-criticality extras up to the cap) and about 17 screens' worth of features would be `later` (`reason: capacity`), with the user's yes at G1.5. **If the cap were per program instead** (60 for all four apps), the release would be about 60 of about 140 screens, a very different product; that is why the unit is a registered open item (OI-25). All four apps' release cards are written in one Spec run (the program has one G2), about 140 screens: roughly 40 batches and about 55 task-card canaries, about 14 minutes at eight workers, about 2.3 times the cost of the 60-screen estimate in §10.1.

## 15. Risks and open items

| Risk | Handling |
|---|---|
| Over-specification flattens the front end's design | freedom by schema, tier ceilings, no hints, the freedom probe (PS-4), direction axes that differ per app; G3 is where the owner judges the result |
| Under-specification makes the front end guess | the canary and consumer probe count questions and assumptions; the developer test of §14.2; library states; V-27 and V-35 |
| The budgets are guesses | a mechanism with measured defaults; the packager fails the build; tuned in PS-2 on real tokenizers |
| The libraries are a lot of authoring | staged (S1 minimum, S2 the rest); data not code; owners named (§7.2); `assumed` where missing |
| Compass format churn | one shared reader, one fixture per format, drift flags, readiness pre-check |
| Research gaps (payments, screens) | degradation ladder, `assumed`, the confirm list, no invented facts |
| Model variance and a weak model on judgment steps | deterministic first; validators; replay; variance bands; PS-5 and PS-6 decide per step whether a Haiku-class route is enough |
| A deferral surprises the user | mandatory consent at G1.5; plain words; the backlog outlines visible |
| Rate limits stretch the run | computed concurrency, queue, honest estimate |
| The form-vocabulary lint misfires | a warning in v1, tuned on the corpus |
| Shared packages need a layering edit | lead-only change; fallback is a copy with contract tests |
| Older docs still say six stages or fixed caps | pointer edits (§16) |
| The owner meant 60 per whole program | one rule changes (the partition), not the design |

**Open items** (registered in doc 00): **OI-25** scope-cap unit and value (per app, 60, starting values; the owner has not set a limit). **OI-26** budget defaults (index 6k, global shard 4k with 6k of shards per task, module 3k, slice 5k, front-end allowance 4k, task 28k, whole pack 22k), self-consistent by construction (§5.3), tuned in PS-2 with real tokenizers. **OI-27** Module Library v1 contents and reviewers (extends OI-5). **OI-28** store-policy sheet owner and re-check cadence. **OI-29** G1.5 consent flow, and whether G1.5 appears for `compact` when no split is proposed (default: not shown). **OI-30** Stack Kit pin before S5 (extends OI-6). **OI-31** consumer-profile values for the temporary Haiku-class route, Gemini and OpenAI, from current provider documentation. **OI-32** the active route's prices for the estimate. **OI-33** native reviewers for non-English locales. **OI-34** Compass changes Spec would like (richer entity fields, reliable priority, role and app cleanup, a defined meaning of `phase`), to be raised as a Compass change-request prompt later, not now.

## 16. Amendments and what changes in the other documents

### 16.1 Amendments to accepted decisions (none is silent)
| Was | Becomes |
|---|---|
| AD-35 / AD-37 / doc 12 §4.4 "Verify and package"; Verify repairs and packages | **Verify** checks and proposes patches; Spec applies them; packaging is shared deterministic code run by Spec last and re-run by Verify |
| Doc 03 §1 scope caps 12 / 60 / policy-set and the screen-count sizes | One cap per app (configuration, default 60); the detail profile comes from the size band; the existing Phase 1 / Phase 2 split at G1.5 is reused |
| Doc 05 §5 scope caps ("the engine proposes a phase 1 and moves the rest to `later`") | Kept and made explicit: a `reason` of `user`, `capacity` or `first_release`; the G1.5 consent is the I-4 record; a pull-in rule; outlines in the ledger |
| Doc 05 §2 step 7 ("commit a new graph version") | The change controller does steps 1 to 6; Spec, the single writer, does step 7 |
| Doc 03 §7 and doc 04 §4: sections name a kit component, section order Default, a sample slice with `sticky_tabs`, `skeleton(...)`, `banner` and kit signatures | Information regions with importance levels; no component names, no order, no presentation words; the sample slice is replaced by §5.7 |
| Doc 03 §6 DSGN-2: component variants and composition rules per screen type | Tokens, direction, voice and policy plus Default design invariants; variants and per-type composition belong to the front end's design system |
| BN-08 "composition rules" | Design invariants are Default guidance; accessibility floors are policy-Locked |
| I-7 and V-17 "Locked requires user or document evidence" | Also **policy** evidence (`provenance: policy:<id>@<date>`), only for rules in the policy sheet itself (§7.4) |
| V-18 budgets (slice tokens, screen counts against the profile, total size) | Superseded by V-26 (files, shards, task reads, call inputs); counts are derived, and the class range is a V-29 warning |
| V-19 scope ledger consistency | Each screen satisfies at least one `in` feature **or a system-set requirement (policy)**; `later` and `out` features have no screen nodes (unchanged, and now relied on) |
| V-21 determinism | Extended by V-34 (the prefix bundle; no volatile data in bodies) |
| V-22 accessibility labels and targets; V-23 allowlisted icons and packages | V-22 applies to actions and inputs (a label key and an announcement); touch targets are checked on the front end's output. V-23 applies to capabilities against the Stack Kit; icons are the front end's choice |
| V-12 and token-lint "no raw values in screens" | `ext.*` token names are allowed in front-end code |
| AD-08 "wireframes rendered by code from the spec" at G2 | **Content maps**, labelled illustrative, never a build input |
| Doc 03 §2 G2 "bound to the `APP_SPEC.md` hash and section hashes" | The **content hash** (consumer-independent) and section hashes; the package hash is recorded beside it |
| AD-16 and doc 04 §4: "Tier 0 at most 8k"; slice "tiers 0 to 3" | Layers named in words; a prefix bundle of at most 10k (index 6k plus common global 4k); a self-consistent budget set (§5.3, §6.2) |
| Doc 03 §1 sufficiency sampling per slice | The canary's unit is the task card (§8.2) |
| Doc 04 §6 task card `budget: {in: 18k, out: 6k}` and `model_hint` | `read_tokens` against the budget set (default 28k including the front end's allowance); `capability_class` (weak, standard, strong), never a model name (rule 14) |
| Doc 04 §3 package paths | Renamed (`slices/` merged into the `screens/` files, `design/` into `global/`, `build-plan.json` into `build/`, `manifest.json` into `MANIFEST.json`); no file dropped. AD-03 and AD-16 are otherwise unchanged: the graph is canonical and the Spec Server reads the same files |
| Doc 02 §4.1 call catalog | New calls `SPEC-PLAN`, `COPY-L`, per-module Product calls and the canary per task card |
| Doc 09 step 5 "Module Library v1 (~20 modules)" | About 12 modules in S2; the rest later (OI-27) |
| Doc 09 steps 8 and 9 (Verify and package with the Spec Server; the change controller) | Packaging is in Spec's assemble (S1); S5 is the thin Verify; the change controller files change sets to Spec |
| Doc 12 §10 Backbone as a separate build step | Folded into Spec's first build phase, delivered as shared packages |
| Doc 15 §8, §9, §13 (Verify, the canary, the critic) and parts of §4.8, §5.4, §6.1 | **Amended by [doc 18](18-spec-final-architecture.md) §10:** no Verify agent for compact and standard; the self-check at three code gates; the reversals are listed there |

### 16.2 Documents to touch
| Document | Change |
|---|---|
| 00 | AD-65 to AD-76; OI-25 to OI-34; the amendments above; version line v3.7 |
| 02 | call catalog additions (§4.1) |
| 03 | banner pointing here for the Spec lanes, caps, sections, the design lane and the G2 binding; no body rewrite |
| 04 | banner: §3 paths, §4 layers and prefix cap, the sample slice and §6 task card superseded by doc 15; Build Plan §6 otherwise kept |
| 05 | I-7 amendment; notes at §2 step 7 and §5 |
| 06 | G1.5 consent and content maps |
| 07 | V-24 to V-36 added; V-17, V-18, V-19, V-21, V-22, V-23 amended |
| 09 | roadmap steps 5, 8 and 9 read "Spec build S0 to S6" |
| 12 | §4.3, §4.4, §5 (BN-15 to BN-17) and §10 point here |
| 13 | one line: Spec's consumption of Research is in doc 15 §3.2 |
| atlas | optional refresh |

## Appendix. Words that meant two things (resolved)
| Word | Use now |
|---|---|
| **lane** | only Spec's three lanes (Product, Design, Screens); change lanes (doc 05) are always "change lanes L0 to L5"; Research's slices are "views" |
| **layer** | index, global, module, screen (replaces slice "tiers 0 to 3") |
| **tier** | Locked, Default, Open; "by policy" is a kind of evidence, not a tier |
| **Freeze** | Spec's Freeze; the build's is the "interface freeze" |
| **size** | the user's tier (Quick, Standard, Thorough), the detail profile (`compact`, `standard`, `deep`) and informal small, medium, big are one ladder; screen counts never define a size |
| **pattern** | only in provenance (`pattern:<id>`); a screen's kind is its `screen_type` |
| **phase** | Phase 1 is the current release (`in`), Phase 2 the backlog (`later`), as in doc 05 §5 |
| **content hash, package hash** | the hash of the graph and the content files (bound at G2), and the hash of the bytes packaged for one consumer |
| **Blueprint** | the Product lane |

