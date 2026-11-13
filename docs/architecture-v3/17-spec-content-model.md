# 17. The Spec Pack content model: what a mobile app needs, and how the front-end engine takes it (v1.1)

- **Status:** active, created 2026-11-14; v1.1 the same day, after an independent review. **Proposed** until its gates in [doc 16](16-spec-proof-plan.md) pass (PS-10). Decisions **AD-77 to AD-82** ([doc 00](00-index-and-decisions.md)). It **extends [doc 15](15-spec-agent-design.md)**: for *what the pack contains*, this document wins; for *how the pack is built, budgeted and checked*, doc 15 wins. Every change to doc 15 is listed in §12.
- **Amended 2026-11-14 by [doc 18](18-spec-final-architecture.md) (AD-83 to AD-91): where doc 18 differs, it wins.** Changed here: §4.2 and §4.8 (mock: code mints a **hero skeleton** at the Freeze, the model fills the hero content, `empty`, `busy`, `extreme`, `broken`, `offline`, demo accounts and recipes are made by code, compact also ships `offline`), §2.5 and §2.7 (block names by code; data-bound asset needs by code, unbound needs written by the Design call; slice closures carry block bodies), §5.2 and §5.3 (signature moments are written with the COULD entries in the Screens call, not by the Design call), §7 and §10.5 (calls and order of steps: doc 18 §1.2), §12 (three directions stay for standard apps, one automatic direction for compact).
- **Owner directions (2026-11-14, a later message; voice, lightly cleaned):**
  - The front-end engine "will have a components library attached, it will be fetching the component library. We cannot tell him, but you need to consider that also."
  - Think about "mock data, possible mock data, user journey, and user story", and "the MCP logic ... a lot of MCP while it will be generating the frontend for the components, either for images or for various kinds of things".
  - "An advanced frontend engine will be generating a beautiful, advanced frontend with various animation effects in React Native, either for iOS or Android." Not "any generic web app"; "an advanced app for 2026".
  - Small, medium and big apps cannot get the same thing. "The biggest app I usually have has 50 to 60 screens. How are we going to provide that? How will the [front-end] engine be taking it, and how are we managing it?"
  - "Think in a rational, logical, and AI-agentic way." "Do not worry too much about cost."
  - "The frontend engine cannot be limited or hard-coded by the spec engine, because if the spec engine does not tell it, the frontend engine should be doing the things on its own." Spec "can tell the frontend engine how things usually are, how it can be, and how we can enhance it."
  - "It should not be too complicated for mobile. It should throw an error. Create a tactic, something very easy to consume, even with a lot of users." "For everything, think on what extra thing it can provide, every role and factor."
- **Evidence labels:** as in doc 00. **[J]** marks a judgment starting value; every one is configuration.

## 0. In plain words
- The front end is an AI that writes the app. It will have a **component library** and many **tools (MCP)** for components, images and more. Spec **never names a component or a tool.** Instead it says **what each part of a screen is made of** (an *information shape*: a sequence of stages, a media set, a price breakdown) and **what the user can do** (an *intent*: choose a date, pay, rate). The front end can then look up the right components in *whatever* library it has.
- Spec speaks in **three voices**: **MUST** (has to be true), **USUALLY** (how comparable apps do it), **COULD** (ways to lift the app, kept in their own file). **Anything Spec does not say, the front end decides.**
- Beyond screens, Spec gives the **why and the feel**: personas, **user stories**, **journeys with emotion**, **mock data** (profiles, curated hero records), **shared blocks**, **asset needs**, **motion intents**, and a **design ambition** for a 2026 app.
- **Size matters.** A small app gets the core, simply. A big app gets everything, deeper, plus a way to build it consistently.
- **A 50 to 60-screen app** is never read whole. The front end reads the map, looks up its components once from the *needs inventory*, builds **a few pilot screens** to set its design language, then fans out workers who each read at most 28k tokens.
- **Mobile guard.** A screen too dense for a phone, or built from web-only ideas (hover, right-click), is flagged; a gross breach **throws an error** with a plain reason and must be split or knowingly waived.

## 1. The rule of silence and the three voices (AD-77)

### 1.1 The rule of silence
**If the pack does not say it, the front end decides it.** Everything not marked is Open. Spec never fills a gap with a hidden default; the only things it fixes are meaning, policy and the starting look system (doc 15 §2).

### 1.2 Three voices
| Voice | It means | Firmness | Based on | Lives in | The front end's duty |
|---|---|---|---|---|---|
| **MUST** | what the app has to satisfy | Locked (user, document or policy evidence) or Default | the Brief, rules, the policy sheet | the card's regions, actions, states and acceptance; the contract; global behaviours | satisfy it; record a deviation only if the *meaning* changes |
| **USUALLY** | how comparable apps do it | Default only if `official` or `corroborated`, otherwise Open | Research norms (evidence and prevalence), the state and scenario libraries | the card's `usually` lines; `global/norms.md` | take it, adapt it or leave it; nothing to record |
| **COULD** | ways to lift the app, or other ways it could work | Open only | the journey's moments that matter, the design direction, the capability sheet, the delight-pattern library | **its own file per screen, `screens/<id>.could.md`** (read through `get_opportunities`); the ambition and the direction (global) reach every worker | free to use, change or ignore, mostly in the polish pass; nothing to record |

**Why COULD has its own file.** A weak build worker over-complies with whatever it reads. Keeping per-screen ideas out of the build slice means workers build the MUSTs correctly first; the **polish pass** (and any worker that asks) applies COULD. The *ambition*, the *motion personality* and the *signature moments* live in the global direction files, which are in the shared prefix, so every worker still knows how bold the app should be.

### 1.3 Example: "Delivery details" (SCR-020, real Standard export)
SCR-020 is shared by three apps and eight roles (buyers, farmers, transport partners, managers); it "shows pickup and drop-off information for an assigned delivery and its current status". The buyer's side is **status only**: FCT-030 defers live tracking.
- **MUST:** the delivery's stages (`unassigned`, `assigned`, `picked_up`, `delivered`, `unable_to_deliver`, the last an exit, not a step) with the current one; the pickup and drop-off places.
- **USUALLY:** "buyers expect an expected delivery day" (evidence `single_source`, so Open); the delivery day is an ENT-006 field the buyer may see.
- **A suggestion, not a card line:** "show the buyer who is delivering" would bind ENT-009, which buyers may only `none`; Spec records it as `needs_access_change` and offers it at G2 as a suggestion. **USUALLY and COULD bindings must pass the access and contract checks (V-08, V-30), or they become suggestions.**
- **COULD** (own file): when the delivery completes, a calm moment that says what the buyer can do next (basis: the end of FLW-002 and FLW-006); make a stage change feel alive without being noisy (basis: the `state_change` motion intent). Each says *what it does for the user*, never how it looks.

### 1.4 How COULD stays honest
- **Intent and effect only:** each entry says *when*, *what it does for the user*, and *its basis*; no layout, widget or animation words (V-42, a warning-level lint, because words such as card or map can name content).
- **A basis is required** (`journey moment`, `research item`, `capability sheet`, `library pattern`); an idea with no basis is dropped, so Spec never invents trends. **An `assumed` basis makes the entry `assumed`.**
- **Kinds:** `delight`, `clarity`, `speed`, `trust`, `personalization`, `accessibility`, `platform`, `alternative`. An `alternative` that would add scope is **not** a COULD line: it is a suggestion for the owner's toggles at G2 (research and Spec never set scope, I-6).
- **Capped** at 3 per screen and a per-app total by profile. **Outside the G2 hash:** COULD is Open and ignorable, so it has its own hash and editing it never voids an approval.
- **Motion carries an accessibility alternative** (§5.5) and, where it needs a native capability, its tier (A, B, C).
- **Measured, not assumed:** PS-10 measures *between-app diversity* and *within-app consistency* separately, and runs the front end with and without COULD (§10.4).

## 2. The bridge to any component library and any tool (AD-78)

### 2.1 The playbook (recommended in `AGENTS.md`, not required)
```
 ① read INDEX + build plan (the map, about 14k)
 ② MAPPING WAVE: read needs.json ─► ask the component library (its MCP) per need
        ─► component-map.json (the front end's own artifact: need ─► component, or a thin wrapper
           of its own) + a GAP LIST for shapes the library lacks
 ③ read assets.json ─► ask the asset tools; apply the fallbacks
 ④ FOUNDATION and INTERFACE FREEZE v1 (routes, types, tokens, blocks, component-map)
 ⑤ PILOT = the W2 vertical slice (3 to 5 screens, one task each; it also feeds G2.5)
        ─► the front end writes its DESIGN-LANGUAGE RECORD (structured JSON, plus the pilot
           files as golden examples, plus a component-usage lint) ─► FREEZE v2
 ⑥ CANARY FAN-OUT: 2 tasks first, check, then all the rest; one task card each (≤ 28k read)
        per-wave checks: types, mock journeys, 200%-text and right-to-left screenshots
 ⑦ POLISH PASS: apply COULD and the motion intents (get_opportunities)
 ⑧ SELF-CHECK: scenarios on the mock profiles
 Single owners: one owner (a worker or code) for the ext.* token file and for the shared-component registry;
 others request additions, so parallel workers cannot fork them.
```
Spec produces what steps ② to ⑧ read. It does not run them and does not name the library or the tools. This follows doc 04's wave order: freeze after W1, the W2 slice, then fan-out.

### 2.2 Information shapes (closed vocabulary; a versioned data file)
A region declares a `shape` plus attributes. Shapes say *what the information is*, never how it is drawn. Common attributes: `item` (a block or entity), `cardinality` (a bucket: `one`, `few` 2 to 7, `many` 8 to 50, `very_many` over 50, with typical and maximum), `live` (`none`, `polled`, `realtime`), `editable`. A shape the vocabulary lacks is written `other:<name>` and goes to the gap list.
| Shape | What it is | Attributes | Example |
|---|---|---|---|
| `value` | one labelled value | `kind`: `plain`, `metric` (with a comparison or trend), `rating` (score, count, optionally a distribution), `progress`, `flag` (a small status) | balance; sales against yesterday; 4.5 from 120; open or busy |
| `text` | prose | `length`: `short`, `long`, `rich` | a description |
| `key_values` | labelled facts, including settings rows | `editable` | order details; preferences |
| `collection` | homogeneous items | `item`; `grouping` (`none`, `by_field`); `columns` (0 for items, 2 or more for comparable columns, the table case); `stream` (mixed item kinds, the feed case); `selection` (`none`, `single`, `multi`) | my orders; a menu by category; a ledger; activity |
| `hierarchy` | a tree | | categories |
| `sequence` | ordered things | `kind`: `events` (history) or `stages` (ordered statuses with a current one, optionally `exits`) | order history; delivery progress |
| `media` | images, video or audio | `count`: `single` or `set`; `kind` | produce photos |
| `places` | locations | `kind`: `points` or `route` | stores nearby; a trip |
| `series` | numbers | `kind`: `over_axis` or `parts` | earnings by week; expense split |
| `schedule` | time | `kind`: `dates` (availability) or `slots` | a class timetable |
| `inputs` | a set of inputs (listed separately) | | create listing |
| `conversation` | messages | | support chat |
| `document` | a file or long attachment | | an invoice |
| `pages` | ordered pages of content | | onboarding |
| `notice` | a message about the app's or the user's situation | `severity`: `info`, `warning`, `blocking` | an announcement; maintenance |
| `options` | alternatives to choose between, with comparable attributes | | plan chooser |
| `suggestions` | candidate values offered for an input | | search suggestions |

### 2.3 Interaction intents (closed vocabulary) and input types
**Intents** (on actions): `view`, `select_one`, `select_many`, `enter_text`, `enter_number`, `enter_money`, `enter_code`, `enter_secret`, `adjust_quantity`, `toggle`, `choose_date`, `choose_time`, `choose_range`, `pick_place`, `capture_media`, `pick_media`, `scan_code`, `authenticate`, `confirm`, `cancel`, `undo`, `reorder`, `filter`, `sort`, `search`, `refresh`, `load_more`, `expand_detail`, `act_on_item`, `share`, `navigate`, `pay`, `rate`, `report`, `call_contact`, `sign_in`, `sign_out`, `grant_permission`. An intent may be flagged `destructive`, `irreversible` or `needs_confirmation`. **Inputs** keep doc 15 §5.7a's semantic types (`text`, `number`, `money`, `date`, `phone`, `email`, `choice`, `file`, `location`) plus `code`, `secret`, `quantity`, `toggle`. There is **no hover, right-click or tooltip intent** (§5.6).

### 2.4 The needs inventory (`global/needs.json`, code-built)
The distinct needs across the app, each with what a library lookup requires, built **after** the Screens lane. It is the list the front end hands to its component library **once**.
```
need: N-014   shape: sequence {kind: stages, exits: [unable_to_deliver]}   cardinality: few (typical 4, max 5)
live: polled   intents: [view]   states: std.detail   anatomy: [label, current-marker]
gestures: []   motion: [state_change]   a11y_role: progressbar-like status   used_by: [SCR-020.delivery_status]
```
**One need is one combination of** shape, attributes, intents, cardinality **bucket**, normalised state set, anatomy, gestures, motion and accessibility role. The order's own four states (`SCR-011.order_status`) are a *different* need (different value set), not merged by shape alone. Because raw cardinalities and state lists would make nearly every region distinct, they are **bucketed and normalised**; the resulting count (expected to be a few dozen for 60 screens) is **measured in S1**, not promised. The front end returns `component-map.json` and a **gap list**; a gap is filled by composing or building, and is **not a deviation** (the meaning is satisfied).

### 2.5 Shared blocks (`global/blocks.md`, detected by code after Screens)
A **block** is a recurring piece of information: the **same entity fields, in the same shape, with the same role visibility** in three or more places (or two or more apps). Detection runs **after the Screens lane** on the cards' explicit binds, by **exact** bind set plus a visibility hash (never fuzzy, so the deterministic render and I-5 hold); near-duplicates are reported as `block_candidate` warnings for a Product call, not merged. The block id is **minted once, with an alias if it ever changes**. A narrow call names the block and describes its **depth variants** (`brief`, `full`: which fields each shows), never its look. The render replaces repeats by `use_block: order_summary@brief`; **inside a task, each block definition appears once, in the task's shard part**, not in every slice, which is where the saving is. Role visibility is part of the match: an order summary a buyer sees (with an amount due) and one a seller sees (with a commission, FCT-015) are different blocks.
```
block: order_summary   shape: collection {item: order_line} + value {kind: plain}   entity: ENT-006
bind: [items, produce_total, delivery_fee, amount_due (derived)]   visible_to: [ROL-001, ROL-006, ROL-007, ROL-009]
depth: brief = [produce_total, amount_due]   full = all      used_by: [SCR-012, SCR-014, SCR-011.row]
```

### 2.6 Tokens are the handshake
Any library can be themed from role-named tokens, so Spec's tokens (DTCG) use stable **roles**: brand, neutral, surface, text, border, semantic (success, warning, error, info), **state roles** (pressed, focused, disabled, selected), data-visualisation palette, elevation, radii, spacing, type scale with script fallbacks, and motion **durations and named easings** (as doc 15 §2.1; **spring presets and blur stay `ext.*`**, the front end's). The tokens are the **Default starting theme**: the front end themes whatever library it uses with them and may extend them; where a library's default look differs it is overridden, and that is not a deviation.

### 2.7 Asset needs (`global/assets.json`, code plus one call per module, after Screens)

> **Amended (doc 18 §5):** data-bound needs are made by code from media fields and hero briefs; **unbound needs** (brand mark, app icon, splash, empty-state art, clips, audio and haptic cues) are written by the Design call; there is no per-module asset call for compact and standard.

For images, icons, illustrations, animation clips, audio and haptic cues. Each need has: `kind` (`photo`, `illustration`, `icon`, `avatar`, `brand_mark` including the **app icon, adaptive icon and splash**, `animation_clip`, `video`, `audio_cue`, `haptic_cue`, `texture`, `map_style`), `purpose` (primary visual, supporting, decorative, empty-state, celebration), a `brief` (**subject, count and an avoid-list**; the mood comes from the design direction by reference and stays Open), `source_policy` (`user_upload`, `generate`, `licensed_stock`, `none`), `license_rule`, `alt_text_rule`, `fallback` (what to show when missing), `used_by`. **No lighting, placement, size, crop or variant** (that is the front end's). Mock placeholders are marked synthetic.

### 2.8 Motion intents (`global/motion-intents.md`)
Motion is described by what it *means*: **feedback** (`confirm_success`, `signal_error`, `show_progress`, `draw_attention`, `reward`); **orientation** (`enter_deeper`, `return_back`, `switch_peer`, `overlay_in`, `overlay_out`, `identity_continuity` meaning "this item is that item", `reveal_more`); **change** (`content_arrival`, `state_change`, `live_update`, `reorder_feedback`). Each is `essential` (it conveys state; **MUST have a non-motion alternative**) or `decorative` (may be dropped). **The intents themselves are guidance (COULD); only the alternative for an essential one is MUST.** The motion tokens give durations and easings; the front end chooses the animation.

### 2.9 The Spec Server for agents (a thin reader over the same files, AD-16)
The doc 04 tools stay (`neighbors`, `get_tokens`, `get_contract_op`, `get_scenario`, `list_tasks`, `trace`, `diff`, `verify`, `search`, `raise_decision`); the tier tools are renamed **`get_index`** and **`get_screen(id, depth)`**; **added:** **`get_task(task_id)`** (one call returns exactly the task's reads: prefix, shards with the task's blocks once, module, screen files with closure and needs, asset, mock and story excerpts, never over the budget), `get_block`, `get_needs(scope)`, `get_assets(scope)`, `get_mock(entity, profile)`, `get_story`, `get_journey`, `get_opportunities(id)`. **Errors are structured and written for an agent:** `{code, severity, where, why, fix}`, drawn from an **error catalogue** (data: code, severity, audience agent or owner, plain text, fix hint, repair class), for example `TASK_OVER_BUDGET T-0142: 31k read against 28k; split the screens or shard`. A pack that is too heavy or too dense **throws such an error at build time**; it never reaches the front end half-broken. The pack's descriptions and notes are **data about the app**; the only instructions in it are in `AGENTS.md` (so text that came from the Brief or Research can never act as an instruction).

### 2.10 What Spec never does
Name a component library, a component, a tool, an MCP server, a package or a version; state a placement, size, crop, breakpoint behaviour or animation; require the playbook. It describes needs; the front end fulfils them however it can.

### 2.11 Failure modes
| Case | Handling |
|---|---|
| The library or its tool is unavailable | the front end composes from primitives; the needs stay valid; the fallback policy is in `behaviors` |
| The library lacks a shape or an attribute | gap list; compose or build; no deviation |
| The library's default look differs from the tokens | tokens are the starting theme; theme the library |
| The library changes version mid-build | needs unchanged; the front end re-queries; Spec is unaffected |
| Two libraries are attached (a base and an animation kit) | needs are library-neutral |
| An asset tool returns nothing or something off-brand | the need's fallback and alt-text rule apply; flagged for the owner |

## 3. The "why" layer: personas, user stories, journeys (AD-79)
The front end builds better screens when it knows *who is using them, why, and how they feel at that moment*. This layer is mostly derived by code from the Brief's roles, features and flows and written by narrow calls. **Everything in it needs provenance, and nothing in it creates scope.**

### 3.1 Personas (`global/personas.md`)
One per role: the goal (job to be done), context of use (where, when, device, connectivity), frequency, skill, pain points and what success looks like. Each field cites a Brief fact or is marked `assumed`.

### 3.2 User stories (`stories/<MOD>.md`)
```
US-012  as: ROL-006 Purchase manager   want: pay online for the order   so that: the order is confirmed
features: [FTR-…]   screens: [SCR-014, SCR-015]   journey: FLW-002 step 5
voice: must   evidence: user FCT-005, FCT-028   acceptance: [ACC-031, ACC-032]
```
- **A story cites an `in` feature or a policy id** (a system-set story cites its store rule). **An `assumed` story is never `voice: must`.** The counts below are outputs of the Brief, never quotas.
- **Coverage (V-40):** every `in` feature has a story; every screen cites at least one story; every story has Given, When, Then. Stories of `later` features stay in the ledger as backlog stories, with no screens (V-19).
- **How they are made:** code seeds stories from features, roles and flow steps; **one narrow call per module** writes them in the standard form.
- **Typical count:** `compact` about 10 to 20, `standard` about 30 to 60, `deep` 100 and more.

### 3.3 Journeys (`journeys/<FLW>.md`, richer than doc 15 §5.12)
Each stage has a goal, the screens it touches, the actions, the **emotion** (`calm`, `curious`, `confident`, `anxious`, `frustrated`, `delighted`), pain points and opportunities. A journey also names its **moments that matter**, its **unhappy paths** (the flow's own `alternates` first), a **first-run and a returning** variant, the **mock profile** it runs on and, across apps, the handoffs. **The emotion line is Spec's reading and is marked `assumed` unless a fact states it** (V-44 requires the line, not certainty). For `compact` apps only the primary journey carries emotion.

### 3.4 What the front end does with them
Stories and acceptance become its self-checks; emotion and moments shape the design and the motion; the first-run and returning variants decide what the empty and the busy app look like. None of it is an instruction about form.

### 3.5 Example (real Standard export, FLW-002 "Order and pay", role Purchase manager; the emotion line is illustrative and `assumed`)
```
1 browse produce (curious) ─► 2 review a listing (deciding) ─► 3 confirm quantities, day, fee (careful)
  ─► 4 review the order total (checking) ─► 5 PAY ONLINE (anxious: money) ─► 6 check the outcome (relieved)
alternates (from the flow): payment fails ─► the failed outcome and another attempt; leave before confirming ─► back to produce selection
```
It tells the front end where reassurance matters (step 5), where a small reward fits (the outcome, a moment that matters), and which failures must be graceful.

## 4. The mock data model (AD-80)
The user approves the app by looking at a preview built on mock data (G3), so **mock data is part of how good the app looks**. Spec supplies a plan, curated records, recipes, behaviours and image briefs; the front end's mock adapter serves them.

### 4.1 Profiles (a closed set; scenarios and journeys name the one they run on)
`empty` (a new account), `typical` (a normal user), `busy` (a heavy user, long lists), `extreme` (very long text, large numbers, 0, 1 and many, emoji, right-to-left, mixed scripts), `broken` (errors by code, timeouts, an expired session, a conflict), `offline` (cached data, queued actions).

### 4.2 Hero records and recipes
- **Hero records** (`contract/fixtures/`): a handful of curated, attractive, realistic records per entity for the pilot and first-view screens (written after the contract, before the Screens lane), so the preview looks real.
- **Recipes** (`contract/mock/recipes/`): seeded, deterministic generators for volume, with distributions, relationships and edge classes.
- **Time anchoring:** dates are relative to "now"; the seed fixes the rest.

### 4.3 Behaviours (`contract/mock/behaviors.json`)
Latency profiles, failure injection per operation (from the error taxonomy), paging, an auth expiry at a chosen moment, realtime event scripts (a delivery advancing stage by stage), and provider callbacks simulated (a payment succeeding or failing).

### 4.4 Image briefs
For every image field the need is in `assets.json` (§2.7): subject, count and an avoid-list. Mock images are marked synthetic; the front end's asset tools fill them.

### 4.5 Demo accounts
One per role with seeded data, used for the preview and, where the store requires it, as the reviewer's demo account.

### 4.6 Locale realism and coherence
Names, addresses, phone and currency formats and units follow the app's locales (Marathi names and rupees for the Nashik example); non-English content is flagged `needs_native_review`. **Across apps the same data is coherent**: one order has one id and one timeline in the buyer, farmer, transport and manager apps.

### 4.7 Example (real Standard export, ENT-006 Order; values illustrative)
| Profile | What it shows |
|---|---|
| `empty` | a buyer with no orders: the empty states |
| `typical` | three orders: one `payment_pending`, one `paid`, one `cancelled` |
| `busy` | 60 orders over six months in every state, some with many lines |
| `extreme` | an order with 40 lines, long produce names in Marathi and Hindi, amounts in lakhs |
| `broken` | `payment_declined`, `provider_unavailable`, `order_not_payable`, a network failure, an expired session |
| `offline` | the last list from the cache; actions queued |
Hero records: six curated produce listings with realistic quantities and prices, each with an image brief (subject: the produce in a harvest setting; count: up to six; avoid: brand marks).

### 4.8 By size

> **Amended (doc 18 §5):** compact ships `empty`, `typical`, `broken` and `offline`; standard adds `busy`; deep all six. Only `typical` is written by the model (12 to 20 hero records per primary entity; compact 8 to 12), filling a skeleton of ids minted by code; the other modes, demo accounts and recipes are made by code; V-41 checks every lifecycle state has a record.

`compact`: `empty`, `typical`, `broken`, hero records for the first screens, one demo account. `standard`: plus `busy`, recipes, behaviours, a demo account per role. `deep`: all six profiles, realtime scripts, cross-app coherence, locale packs, extreme-data performance checks.

## 5. Designing for a 2026 mobile app, and not a generic web app (AD-81)

### 5.1 The ambition dial (Open guidance)
`refined` (clean, restrained motion), `expressive` (rich motion and depth), `immersive` (signature moments, heavy motion). The default follows the audience and category: consumer apps `expressive`, dense enterprise, admin and operations `refined`, lifestyle and entertainment `immersive`. The user's pick of a direction at G2 sets it; `compact` apps set it automatically. **It is a floor of intent, not a ceiling: the front end may do more.**

### 5.2 What the direction contains (`global/design-direction.md`, in the shared prefix)
Mood and personality; audience context; **emotional goals per journey stage**; **motion personality** (snappy, springy, calm); **depth language** (flat, layered, glass-like, soft); **shape language**; **density**; **imagery stance** (photo-led, illustration-led, abstract, data-led); typographic personality; the **delight budget** (a suggested one to three signature moments per primary journey); platform fidelity; dark-mode stance; and **what to avoid** (the generic template: a hero plus three feature cards, stock gradients, a library's default look left untouched).

### 5.3 Signature moments (COULD)
The few places in a journey where care pays off most (a first order placed, a goal reached, a long wait ending), each with its *basis*, *what it does for the user* and, if it needs a capability, its tier.

### 5.4 The platform capability sheet (dated data, like the store-policy sheet)
What the platforms and the fixed stack can do and at what tier: haptics, shared-element and gesture-driven transitions, canvas and shader effects, vector animation clips, biometrics and passkeys, wallet payments, dynamic type, predictive back, edge-to-edge display, glass-like materials, live activities, widgets. Each entry has a tier (A standard, B needs a development build or a native module, C not supportable), a source URL and a date; the builder authors it from the official documentation.

### 5.5 Accessibility alternatives
Every **essential** motion intent has a non-motion alternative; **decorative** motion may be dropped under a reduced-motion setting; haptics and sound are never the only signal; colour is never the only signal; text scales to 200%. These are MUST (policy-Locked, doc 15 §7.4).

### 5.6 The mobile-fit rule (V-37)
On a `phone` surface the defaults [J] are: at most **3 `primary` regions** and **8 regions** in all; at most **one primary action per decision** (a view-only screen has none; an approve and reject pair is one decision); a form step has at most **7 inputs** (more becomes a wizard); a `collection` with columns is allowed only with a stated "what matters on a phone" (at most 4 key columns); **no hover, right-click, tooltip or breadcrumb intents**; a text block over about 600 characters is marked for progressive disclosure. **Web-portal surfaces** are exempt from the column limit and keep the rest.
- **Severity:** a **warning** by default. An **error** only for intents that cannot exist on a phone (hover, right-click, tooltip) and for a **gross breach** (twice a limit). A gross breach goes to repair (a Product call splits the screen) and, if it stays, to a recorded **`mobile_fit_waiver`** with a reason; an unresolved breach blocks G2, not the whole run.
- **A split after Freeze** raises `needs_decision` under the Freeze rules, and the G1.5 view shows any added screens.
- **The thresholds are tuned before they block:** PS-10 measures how many real screens of the nine exports trip each limit, and the limits move until at most about one screen in ten warns.

### 5.7 Anti-generic mechanisms
Three candidate directions per app that differ along set axes (hue family, shape, density, type personality, motion personality) so no two apps share a template; the `what to avoid` line; the freedom probe measured as **between-app diversity** (different apps from the same generator must differ) and, separately, **within-app consistency** (the pilot's design language must hold); and a visual-quality check by the front end's own critic.

## 6. The factor checklist: everything a mobile app needs, and who carries it
Voice: **M** must, **U** usually, **C** could. "Where" names the pack file. Depth by size is in §7. Nothing in this table adds scope: rows about extras are suggestions (§9).

| # | Factor | Spec gives | Voice | Where |
|---|---|---|---|---|
| **Product and users** | | | | |
| 1 | Purpose, audience, scope (in, later, out) | the product statement and the ledger | M | `product`, `scope-ledger.json` |
| 2 | Personas, jobs to be done | goal, context, frequency, pain points | M (assumed flagged) | `personas` |
| 3 | User stories | story, acceptance, evidence | M | `stories/` |
| 4 | Journeys, emotion, moments that matter | stages, emotion, unhappy paths, first-run and returning | M, C | `journeys/` |
| 5 | Success measures | what to track and why | U | `product`, analytics plan |
| 6 | How comparable apps work | norms with prevalence and evidence | U | `norms` |
| 7 | Monetisation (ads, subscription, commission) | its rules and screens | M | `product`, `rules` |
| **Structure** | | | | |
| 8 | Navigation, primary destinations, role shells | the map and importance levels (no widget) | M | `navigation`, `apps/` |
| 9 | Deep links, entry points, cold-start links | routes with typed params and guards | M | `navigation`, `routes.json` |
| 10 | Screen inventory, types, release and backlog | derived, with reasons | M | `modules/`, ledger |
| 11 | Shared blocks | recurring information, depth variants | M | `blocks` |
| 12 | Search, filter, discovery | as list behaviour | M, U | cards |
| 13 | Notifications | types, timing, deep links, permission priming | M, U | `behaviors` |
| 14 | Onboarding content | what the pages must convey | U | cards (`pages`) |
| **Per screen** | | | | |
| 15 | What it shows | regions with shape, importance, binding | M | card |
| 16 | What the user can do | intents with preconditions, effects, errors | M | card |
| 17 | Every state | message each must carry | M | card, library |
| 18 | Inputs and validation | semantic types, constraints, drafts | M | card |
| 19 | Lists | paging, sort, filter, volumes | M | card |
| 20 | Words | starter copy, voice | M, U | copy store, direction |
| 21 | Accessibility | per screen specifics, accessibility labels | M | card, `behaviors` |
| 22 | Analytics | events and consent | M | card, analytics plan |
| 23 | Acceptance | Given, When, Then | M | card, `stories`, `scenarios/` |
| 24 | Usually and could | norms; at most 3 enhancements, in their own file | U, C | card, `.could.md` |
| 25 | Mock data | profile and hero record references | M | card closure |
| **Data and logic** | | | | |
| 26 | Entities, lifecycles, derived fields | fields, states, transitions | M | `data-model` |
| 27 | Rules and formulas | with confirm flags | M | `rules` |
| 28 | Permissions | role by entity by action, row-level | M | `roles-and-access` |
| 29 | Realtime, offline, sync, conflicts | policies | M | `state-and-sync` |
| 30 | Formats | locale, currency, units, time zones | M | `behaviors` |
| 31 | Volumes | typical and maximum per list | M | `data-model` |
| 32 | Data inventory and export | what personal data exists, how it is exported and deleted | M (store and privacy rules) | `platform`, system set |
| **Contract and mock** | | | | |
| 33 | API | operations, schemas, errors, paging, idempotency | M | `contract/` |
| 34 | Events and callbacks | realtime events, provider callbacks | M | `contract/events.json` |
| 35 | Mock data | profiles, hero records, recipes, behaviours | M | `contract/fixtures`, `mock/` |
| **Design (a starting system)** | | | | |
| 36 | Tokens | colour roles, type, spacing, elevation, motion durations and easings, state roles | M (Default) | `tokens` |
| 37 | Direction and ambition | mood, motion personality, depth, imagery stance | C | `design-direction` |
| 38 | Motion intents | what each motion means; the alternative for an essential one | C; alternative M | `motion-intents` |
| 39 | Asset needs | the need, subject, count, avoid-list, fallback | M (need), C (mood) | `assets.json` |
| 40 | App icon, adaptive icon, splash | as `brand_mark` needs | M | `assets.json` |
| 41 | Voice and microcopy | tone, empty-state voice | U, C | direction |
| 42 | Dark mode, right-to-left, text scaling | the stance and floors | M | tokens, `behaviors` |
| 43 | Haptics and sound | which intents deserve a cue, never the only signal | C | `motion-intents`, `assets.json` |
| **Platform** | | | | |
| 44 | Stack and capabilities | tiers A, B, C and the capability sheet | M | `platform` |
| 45 | Store rules | account deletion, sign-in and purchase rules, privacy labels | M (policy) | `platform` |
| 46 | Lifecycle and device | permission texts, background modes, secure storage, restoration, device matrix | M | `behaviors`, `platform` |
| 47 | Security experience | biometrics, step-up for sensitive actions, session timeout | M | `behaviors` |
| 48 | Tablets and foldables | whether it matters and which journeys gain from more room | U, C | `platform`, `.could.md` |
| 49 | Performance | targets (cold start, list volumes, motion class) | M, U | `platform` |
| 50 | Updates | force-update, over-the-air, versioning | M | `behaviors` |
| **Integrations** | | | | |
| 51 | Providers | dossiers, modes, sandbox, failure scripts | M, U | `integrations` |
| 52 | Interrupted flows, pending timeouts | the required behaviours | M | `behaviors`, `rules` |
| **Quality** | | | | |
| 53 | Self-checks | scenarios on mock profiles, stable `testID`s | M | `scenarios/`, keys |
| 54 | Mobile-fit | density and native-intent checks | M | V-37 |
| **Growth and operations (suggestions unless the Brief says so)** | | | | |
| 55 | Permission priming | when and why to ask | U, C | `behaviors`, journeys |
| 56 | Ratings and review prompts | timing within store policy | U | `behaviors` |
| 57 | Share, invite, referral | hooks where the category has them | C | `norms`, `.could.md` |
| 58 | Support and help | in-app help, contact, report a problem | M (store), U | system set |
| 59 | Trust and safety | report, block, moderate user content (a store rule) | M | system set, `rules` |
| 60 | Experiment hooks | feature flags | U | `behaviors` |
| 61 | Release plan | what ships first, versioning | M | ledger, `platform` |
| 62 | Store listing kit | name, subtitle, description, keywords, label and age-rating answers, reviewer demo account | U | `exports/` |
| **Spec meta** | | | | |
| 63 | Honesty | provenance, evidence, assumptions, confirm list, degraded nodes | M | INDEX, `evidence/`, G2 |

## 7. Content depth by size (AD-82)

> **Amended (doc 18 §1.2, §1.3):** the "Spec calls" row is superseded: about 14 (compact) and about 38 (standard) calls when the writer can output 32k tokens or more, about 25 and 70 at 8k, and about 100 for the 60-screen deep release without Verify [J].

The same format at every size; **the content grows, not the complexity**. Profile follows doc 15 §4.1 (size band as a floor). Counts are expectations from the Brief, never quotas.
| Bundle | `compact` (small) | `standard` (medium) | `deep` (big, 50 to 60 screens per app) |
|---|---|---|---|
| Card voices | MUST; at most one `usually`; COULD on 2 to 3 key screens (at most 5 per app) | MUST and `usually`; COULD on key screens (at most 12 per app) | MUST and `usually` on every screen; COULD **where a basis exists**, up to the cap (at most 30 per app) |
| Personas | a line per role | a short paragraph per role | plus context, frequency, accessibility needs, per app |
| Stories | about 10 to 20 | about 30 to 60 | 100 and more, including admin and operations |
| Journeys | the primary journey, with emotion | all primary roles, emotion, unhappy paths | plus first-run and returning, cross-app and recovery journeys |
| Shared blocks | only where a block repeats three times | detected and named | plus depth variants and cross-app blocks |
| Needs inventory, asset needs | small, basic | full | full, with sourcing and licence rules per kind |
| Motion intents | essential only | plus decorative per the direction | plus per-role variants |
| Direction | mood, tokens, ambition chosen automatically | three directions, ambition, motion personality, signature moments | plus per-role variants (consumer against admin) |
| Mock | `empty`, `typical`, `broken`, hero records, one demo account | plus `busy`, recipes, behaviours, a demo account per role | all six, realtime scripts, cross-app coherence, locale packs |
| Behaviours | the essential categories | all that apply | all, plus localisation depth and performance targets per screen class |
| Analytics plan | an event list | events and funnels | plus a measure per role and experiment hooks |
| Store listing kit | none | a draft | full, with privacy-label and review notes |
| QA | acceptance | plus scenarios per journey | plus a test-data matrix |
| Pilot picks (planned by code; the front end decides) | not needed | 3 screens | 3 to 5 screens |
| **Whole pack, all files** (estimate [J]) | about 70k tokens | about 240k | about 500k |
| **A worker reads** | at most 28k | at most 28k | at most 28k |
| **Spec calls** (typical [J]) | about 15 to 20 | about 45 to 80 | about 120 to 135 |

**What the new content costs for the 60-screen deep release** (doc 15 §10.1 as the base: 84 calls, 1.53M input and 0.42M output tokens). Added calls: stories 11, journeys 6, personas 1, mock hero records 6, asset needs 11 (one per module), blocks 2, direction extras 2, and about 6 more canaries (more, larger task cards): **45 more, 129 in all**. Added tokens: about 0.53M input and 0.19M output, plus larger screen and canary calls (+0.05M input, +0.03M output): **about 2.1M input and 0.64M output, about USD 0.53 at the recorded Haiku-class prices**. Time, by the per-step wave method of doc 15 §10.2 (the added steps are stories and assets together 22 calls, journeys 6, hero records 6, blocks 2): **about 14 minutes at 8 workers, 41 at 2, 76 at 1**. A typical slice grows from about 2.5k to about 3k tokens (shapes, needs, assets, mock and story excerpts; block bodies move to the task's shard part), the slice cap stays 5k, and a task carries **about three slices**.

## 8. The biggest app (50 to 60 screens): how it is provided, how the front end takes it, how it stays manageable
```
 SPEC WRITES (about 500k tokens in all, never read whole)           THE FRONT END READS
 ────────────────────────────────────────────────────────           ─────────────────────────────────────────
 INDEX map                                           ~6k    ─────►  the orchestrator: map + build plan (~14k)
 needs.json (a few dozen bucketed needs)                    ─────►  MAPPING WAVE: library lookup once ─► component-map + gap list
 assets.json (images, icons, clips, brand marks)            ─────►  ask the asset tools once ─► apply fallbacks
 shared blocks (about 12)                                   ─────►  built once, reused (defined once per task)
 60 screen files (≈ 3k each = 180k) + .could.md files       ─────►  3 at a time per task card; COULD in the polish pass
 stories, journeys, personas (~50k)                         ─────►  pulled by id when a task needs them
 contract + mock (~115k)                                    ─────►  typed client, mock adapter, 6 data profiles
 PILOT PICKS (3 to 5 screens)                               ─────►  W2 slice, one task each ─► design-language record ─► freeze v2
                                                            FAN-OUT: about 20 screen tasks + about 10 cross-cutting
                                                            tasks, canary first, 8 to 16 workers, each reads at most 28k
```
**The tactic, in order (the playbook of §2.1 applied to a big app; it follows doc 04's waves):**
1. **Map first.** The orchestrator holds only the map and the build plan (about 14k); everything else is fetched by id.
2. **One lookup pass.** Needs and assets go to the library and the tools once, in a mapping wave, so 60 screens never trigger 60 discovery passes. The result is the front end's own `component-map.json`.
3. **Foundation and freeze v1**, then **the pilot as the W2 slice.** Spec picks 3 to 5 screens by code so that each shape cluster and each screen type on the primary journey appears once, with the highest block reuse (for example browse, detail, checkout, status, a form). **Each pilot screen is its own task within the budget**; the front end then **writes its own design-language record** (structured, with the pilot files as golden examples and a component-usage lint) and adds it to the **freeze (v2)**. This is what keeps many parallel AI workers visually consistent, and the same slice feeds G2.5. It is a recommendation; the front end may skip it.
4. **Canary fan-out.** Two tasks first; if they pass the wave checks (types, mock journeys, 200%-text and right-to-left screenshots), the rest follow. Ownership is disjoint (doc 15 §6); one owner keeps the `ext.*` tokens and the shared-component registry.
5. **Polish pass.** COULD and the motion intents are applied in a separate pass; the front end's critic checks visual quality.
6. **Self-check.** Scenarios run on the six mock profiles; `testID`s come from the stable keys.

**What keeps it simple for the front end:** one map, ids for everything, one call per task (`get_task`), the same card shape on every screen, silence meaning freedom, and a build that **throws a plain error** instead of handing over something too big (`TASK_OVER_BUDGET`) or too dense for a phone (`SCREEN_TOO_DENSE_FOR_PHONE`).
**What keeps it fair with many users:** each tenant's pack is content-addressed and read-only; workers make one `get_task` call each; the prefix is identical across workers; queues, quotas and computed concurrency are doc 15 §10.
**A single 60-screen app with several roles** shares screens where the purpose is the same (doc 15 §4.2 rule 4); role differences are region and action conditions, so 60 screens do not become 120.

## 9. The extras menu: what else Spec can add, by role and by factor
**Rule: extras are suggestions, never MUST and never scope.** They are written as `suggestion` nodes (Open), listed at G2 for the owner's include and exclude toggles (as Research's `NEW` items are), and become features only if the owner says so. An extra that the Brief or a policy already requires is a MUST in its own right (a store rule, a stated feature); the menu is for the rest. Each extra carries provenance (a Research norm with its evidence, a library pattern, or the capability sheet), or it is dropped.

### 9.1 By kind of role (what Spec may suggest beyond the Brief)
| Role kind | Suggestions |
|---|---|
| Consumer, buyer | first-visit journey; trust cues the category uses; reorder and saved items; the notifications they care about; a reward at completion |
| Provider, seller | onboarding and verification path; listing-quality guidance; order handling rhythm; earnings and payout clarity; performance insight |
| Field, courier, driver | one-handed and glanceable needs; offline tolerance; permission handling for location and camera; proof of delivery; earnings |
| Admin, operations | approvals queue and exceptions; bulk actions; saved filters; audit trail; role management; dense but mobile-fit |
| Finance, compliance | statements and reconciliation; document status and deadlines; exports |
| Support | case view with context; canned responses; escalation |
| Owner, executive | the few measures that matter at a glance; alerts |
Example from the real Standard export: the transport partner role has screens for assignments and updating a delivery, but "proof of delivery" and "earnings" are **not in the Brief**, so they appear only as suggestions at G2.

### 9.2 By factor (opt-in by profile; §7)
Norms for the category; a store listing kit; an analytics plan (events, funnels, measures); experiment hooks; a QA plan and test-data matrix; performance targets; an accessibility statement; a voice and microcopy kit; a release-notes template; a permission-priming strategy; a rating-prompt policy within store rules; trust-and-safety flows for user content; share, invite and referral hooks; widget and live-activity opportunities (tier B, COULD); an onboarding strategy tied to the journeys.

## 10. The pack v2: tree, card additions, validators, proof, calls

### 10.1 The tree (doc 15 §5.2 plus the additions)
```
spec-pack/
  AGENTS.md  INDEX.md  MANIFEST.json  index.json  APP_SPEC.md  spec.json  prefix.bundle.md
  global/   product · personas · roles-and-access · navigation (+routes.json) · data-model · rules · behaviors ·
            state-and-sync · errors · integrations · platform (+ capability sheet and tiers) · norms ·
            design-direction · motion-intents · DESIGN.md · tokens.dtcg.json · blocks · needs.json · assets.json ·
            suggestions · copy/<locale>.json · scope-ledger.json · glossary
  contract/ openapi.yaml · schemas/ · fixtures/ (hero records) · mock/ (profiles.md · recipes/ · behaviors.json) ·
            auth-matrix.json · events.json
  apps/<APP>/app.md
  modules/<MOD>/module.md  +  screens/<SCR>.md  +  screens/<SCR>.could.md   (card: MUST, USUALLY, then its closure)
  stories/<MOD>.md          journeys/<FLW>.md          scenarios/*.json
  build/build-plan.json     (pilot picks, waves, task cards)
  views/  exports/ (incl. the store listing kit)  evidence/
```
The prefix bundle's common global part carries `design-direction` and `motion-intents` (about 2.5k of its 4k), so the ambition reaches every worker. `needs.json`, `assets.json` and `blocks` are sharded by app or module when a file passes its cap (doc 15 §5.4). `component-map.json` and the design-language record are the **front end's** artifacts and are not in the pack.

### 10.2 What the card gains (doc 15 §5.7 is updated to show it)
- every region adds **`shape`** and its attributes, optional **`use_block`**, **`cardinality`**, **`live`**; inputs keep doc 15's semantic types plus `code`, `secret`, `quantity`, `toggle`; actions carry an `intent` from §2.3;
- **`stories`** (ids, with their one-line text in the closure);
- **`usually`** (renamed from `peer_practice`; evidence and prevalence; bindings must pass V-08 and V-30);
- **`mock`**: the profiles the screen is shown on and its hero records.
**COULD is not in the card.** It is `screens/<SCR>.could.md`: at most 3 entries `{id, kind, when, effect, basis, motion?, reduced_motion_alt?, tier}`.
```
CP-1  kind: trust    when: a payment is pending      effect: the buyer feels sure the money is safe and the order is held
      basis: journey FLW-002 step 5 (anxious, assumed)      tier: A      (the entry is therefore assumed)
CP-2  kind: delight  when: the payment succeeds      effect: a small, warm acknowledgement that says what happens next and when
      basis: moment that matters, FLW-002 step 6      motion: reward      reduced_motion_alt: a plain text confirmation      tier: A
```

### 10.3 New validators (V-37 to V-44, added to doc 07)
| ID | Check | Kind |
|---|---|---|
| V-37 | **Mobile-fit** (§5.6) | warning by default; error for hover, right-click and tooltip intents on phone surfaces and for a gross breach unresolved after repair and without a waiver |
| V-38 | **Shapes and intents:** every region has a known shape (or `other:<name>`, which is gap-listed), lists have a cardinality bucket, intents come from the closed set, `needs.json` covers every region | blocker |
| V-39 | **Block integrity:** every `use_block` resolves, exact bind set and visibility, no block has a single use, ids minted once with aliases | blocker |
| V-40 | **Story coverage:** every `in` feature has a story, every screen cites a story, every story cites an `in` feature or a policy id and has acceptance, no `assumed` story is `must` | blocker |
| V-41 | **Mock coverage:** the profiles each size requires, every state of every card demonstrable on some profile, hero records for the pilot and first-view screens, every media field has an asset need, shared entities coherent across apps | blocker |
| V-42 | **Opportunity discipline:** COULD is Open only, within the caps, each with a basis, `assumed` basis stays `assumed`, essential motion has an alternative, the tier is recorded; the form-word lint | blocker, except the lint (warning) |
| V-43 | **Asset and motion needs complete:** kind, purpose, brief (subject, count, avoid-list), source policy, fallback and alt-text rule; motion intents reference existing tokens | blocker |
| V-44 | **Journey completeness:** primary journeys have stages with an emotion line (`assumed` allowed), moments that matter and unhappy paths, and every stage's screens exist | blocker for `standard` and `deep` |
**Identifiers.** Needs (`N-`), COULD entries (`CP-`), stories (`US-`), blocks and asset needs are **minted once, never reused, and aliased if they change** (I-5), checked by V-32.

### 10.4 New proof items (doc 16 PS-10)
- **Library-lookup probe:** the weakest consumer maps **held-out** needs from `needs.json` to the **public catalogues of two real component libraries** (chosen by the builder; their public documentation snapshotted and dated as test fixtures) and declares gaps. Measured: **recall at 3, wrong-gap rate**, and the same with a *card-only baseline* (no needs inventory). A **synthetic catalogue** (about 25 components) is used only to plant gaps and check they are found. This proves the needs are specific enough for a lookup without Spec naming a library.
- **Ablation arms:** the consumer probe is run on MUST only; plus `usually`; plus stories; plus emotion; plus COULD; plus mock and assets. Each bundle must earn its place (blocking questions down, or consistency or quality up); a bundle that does not is cut or made optional by profile. Per-call first-try validator pass is gated for each new call.
- **Diversity and consistency, separately:** between-app diversity (different apps differ) and within-app consistency (the pilot's language holds), with COULD on and off.
- **Pilot-first probe:** two workers build two different screens with and without the design-language record; a critic (and the owner for a sample) judges consistency.
- **V-37 hit rates** on the nine real exports before any threshold blocks; **stories, blocks, mock** on the real and synthetic inputs (V-38 to V-44).

### 10.5 The order of the steps and the new calls (added to doc 15 §8.1)

> **Amended (doc 18 §1.2):** for compact and standard: plan, then modules, integrations, journeys and design in parallel, join, Freeze (also freezing the glossary and the hero skeleton), then screens (with source copy and COULD), hero records, behaviours and glossary translation in parallel, locale batches streamed, assemble. Personas are written by the plan, stories by the module calls, signature moments by the screens call; asset needs and block names are made by code except the unbound asset needs the Design call writes.

```
 plan ─► skeleton (incl. PROD-PERSONAS) ─► module Product (parallel)
      ─► [PROD-STORIES (per module) ‖ PROD-JOURNEYS (per role) ‖ contract and mock plan ─► MOCK-HERO (per entity batch)]
 DSGN-1..3 (parallel with Product) ─► DSGN-4 (ambition, signature moments; needs the journeys' moments that matter)
      ─► join ─► FREEZE (now includes personas, stories, journeys, hero records and the direction)
      ─► Screens (cards: shapes, usually, mock refs; COULD files)
      ─► code: needs.json, blocks (exact detection), pilot picks
      ─► asset needs (per module) and BLOCK-NAME (per app) ─► assemble
```
All new calls use the `standard` route, are narrow and schema-bound, and carry mandatory provenance (a weak model fills optional fields it should not, doc 15 §11.3). Everything else here is derived by code from the graph.

## 11. Edge cases added (35; doc 15 §12 has 113, so PS-9 and PS-10 run 148)
The SPC-CL fixtures test that **the pack provides what a consumer needs in those situations** (library-neutral needs, a fallback policy); the consumer's behaviour is the front end's.
### 11.1 Component library and tools (SPC-CL)
| ID | Case | Handling | Fixture |
|---|---|---|---|
| SPC-CL-01 | The library's tool is unavailable | needs unchanged; fallback in `behaviors` | mock |
| SPC-CL-02 | The library lacks a shape | gap list; no deviation | syn: catalogue without `places` route |
| SPC-CL-03 | The library lacks an attribute (a table without sticky columns) | gap per attribute, not per shape | syn |
| SPC-CL-04 | The library's look differs from the tokens | tokens are the starting theme | syn |
| SPC-CL-05 | The library changes version mid-build | needs unchanged | syn |
| SPC-CL-06 | Two libraries attached | needs are library-neutral | syn |
| SPC-CL-07 | An asset tool returns nothing or off-brand | the need's fallback and alt-text rule; flagged | mock |
| SPC-CL-08 | Licence limits (the user must own the images; no stock) | `source_policy` `user_upload` or `generate`; placeholders marked synthetic | syn |
| SPC-CL-09 | Very many images (60 hero visuals) | needs grouped by purpose with a count | syn: big |
| SPC-CL-10 | A USUALLY or COULD binding fails the access check (a buyer cannot see ENT-009) | becomes a `needs_access_change` suggestion | real: standard |
### 11.2 Mock data (SPC-MK)
| ID | Case | Handling | Fixture |
|---|---|---|---|
| SPC-MK-01 | An entity with no natural mock (payments) | provider-neutral shapes and scripted callbacks | real: standard |
| SPC-MK-02 | Locale realism (Marathi names, rupees, Nashik addresses) | locale packs; `needs_native_review` | real: standard |
| SPC-MK-03 | The same order seen in four apps | shared ids and one timeline | real: standard, thorough |
| SPC-MK-04 | Time anchoring | dates relative to now; fixed seed | syn |
| SPC-MK-05 | Extreme data (very long text, 10,000 items, emoji, right-to-left) | the `extreme` profile and list volumes | syn |
| SPC-MK-06 | A `broken` profile for every operation | generated from the error taxonomy | syn |
| SPC-MK-07 | Personal data | records are synthetic and marked; the secret scan includes names | mut: Brief with a phone number |
### 11.3 Stories and journeys (SPC-ST)
| ID | Case | Handling | Fixture |
|---|---|---|---|
| SPC-ST-01 | No personas in the Brief | derived from roles and audience; `assumed` | real: quick |
| SPC-ST-02 | Stories for `later` features | backlog stories in the ledger; no screens (V-19) | real: standard (FCT-030) |
| SPC-ST-03 | Two roles want opposite things | conflict flagged; confirm | syn |
| SPC-ST-04 | A cross-app journey (customer to courier) | both apps' screens and one data timeline | real: thorough |
| SPC-ST-05 | First-run against returning | two data profiles and state flags | syn |
| SPC-ST-06 | A story with no `in` feature behind it | V-40 blocks it; it can live only as a backlog story or a suggestion | mut |
### 11.4 Design and motion (SPC-DG)
| ID | Case | Handling | Fixture |
|---|---|---|---|
| SPC-DG-01 | A dense enterprise app | ambition `refined`; the front end may exceed | real: thorough |
| SPC-DG-02 | Reduced motion is on | essential intents have alternatives | syn |
| SPC-DG-03 | Low-end Android devices | a `light` motion class as a Default floor | syn |
| SPC-DG-04 | A COULD that needs tier B or C | flagged with its tier; optional | syn |
| SPC-DG-05 | More COULD entries than the cap | V-42 error; trimmed by criticality | syn |
| SPC-DG-06 | A COULD whose basis is `assumed` | the entry stays `assumed`; it is never presented as evidence | syn |
### 11.5 Scale and mobile fit (SPC-SZ)
| ID | Case | Handling | Fixture |
|---|---|---|---|
| SPC-SZ-01 | A 60-screen app with 12 recurring blocks | block bodies once per task; consistency by construction | syn: big |
| SPC-SZ-02 | A pilot of five screens | five tasks, each inside the budget | syn |
| SPC-SZ-03 | A phone screen above the mobile-fit limits | warning; a gross breach is repaired or waived | syn |
| SPC-SZ-04 | A web-portal surface with tables | exempt from the column limit; the rest applies | real: thorough |
| SPC-SZ-05 | A later worker drifts from the pilot's design language | the record is in freeze v2; the front end's critic flags it | syn |
| SPC-SZ-06 | A view-only screen and an approve and reject pair | no primary action, and one decision, respectively (not a V-37 breach) | real: standard (SCR-020, SCR-026) |

## 12. Amendments to doc 15 and others, and open items
| Was | Becomes |
|---|---|
| Doc 15 §2.1 "Per-screen visual hints (there are none in the pack)"; §2.2 item 5 "No hints in the front-end pack" | **No form hints.** COULD is allowed: intent and effect only, Open, at most 3 per screen, in its own file, ignorable with nothing to record (§1.4). Peer practice becomes the `usually` voice |
| Doc 15 §2.2 item 4 (the form-vocabulary lint) | also applies to `could` and `usually` text |
| Doc 15 §2.3 tier ceilings | add: shapes, intents and needs (MUST); motion intents (COULD, with a MUST alternative); asset needs (the need MUST, mood Open) |
| Doc 15 §5.2 tree, §5.7 card, §5.7a inputs, §5.8 global files | the additions of §10.1 and §10.2; input types kept and extended |
| Doc 15 §5.4 closure | adds needs, asset, mock and story excerpts; block bodies once per task in the shard part |
| Doc 15 §6.2 typical slice 2.5k | about 3k, three slices per task; the front end's allowance also holds `component-map` and its design-language record (they sit in its 4k; a larger window raises it through its profile) |
| Doc 15 §6.3 and AGENTS.md | the playbook (§2.1) as a recommendation; `get_task`; pack text is data |
| Doc 15 §7.2 libraries | add: the shape, intent, motion-intent and asset-kind vocabularies; a delight-pattern library; the platform capability sheet; mock locale packs; the error catalogue |
| Doc 15 §8.1 steps, §8.2 call formula | the order and calls of §10.5; the formula gains the new calls |
| Doc 15 §9.2 validators | V-37 to V-44 |
| Doc 15 §10.1, §10.4, AD-74 | the cost and time of §7 (129 calls, about 2.1M input, 0.64M output, about USD 0.53, about 14 minutes at 8 workers); the capacity table's Big row becomes about 175k input and 53k output tokens a minute |
| Doc 15 §12 | 35 more cases (§11); 148 in all |
| Doc 02 §4.1 call catalog | the calls of §10.5 |
| Doc 04 §5 Spec Server tools | the list of §2.9; doc 04 §6 W2 slice = the pilot |
| Doc 06 §5 G2.5 | fed by the pilot slice; the G2 "Look" view shows the three directions and the ambition |
| Doc 07 §2 | V-37 to V-44 |
| Doc 16 | PS-10; the budget row; the validator range; the case count |
| AD-66, AD-69, AD-70, AD-73, AD-74 | amended as above |

**Open items (doc 00):** **OI-35** the shape and intent vocabularies v1 (reviewed after PS-6 and PS-10). **OI-36** owners of the platform capability sheet and the delight-pattern library (the builder authors from official and public guidance, dated). **OI-37** mock locale packs: which locales first and native review. **OI-38** the ambition defaults per category and whether the owner chooses at G2. **OI-39** pilot-first as a recommendation (the front end may skip it) or a requirement.
