# mde_discovery: the Discover engine

Step 1 of the Context Engine: the interview that fills the slot table and ends in the Brief (spec section 3, brief 8.2). **Code decides, the LLM helps with language.** The engine implements `mde_contracts.engines.DiscoveryEngine` (`start`, `handle`, `reopen_slot`, `apply_changes`, `compile_brief`), talks to LLMs only through `GatewayClient`, never writes anywhere (it returns the new `DiscoveryState` and the events to emit) and works completely offline with the mock handlers.

```
uv run pytest packages/discovery packages/prompts        # all offline, deterministic
uv run pytest packages/discovery --cov=packages/discovery/src --cov-report=term-missing
```

Imports allowed: `mde_core`, `mde_contracts`, `mde_prompts` (specs and the keyword fallbacks), `mde_content`. Never another engine, never the gateway package.

## Components

| Component | Module | Job |
| --- | --- | --- |
| `IntentRouter` | `router.py` | Deterministic fast paths **before any LLM**: greeting, "you decide", "finish with smart defaults", stop, pause, start over, "none of these" (visual card only), starter chips. English, Hindi, Hinglish. Taps never reach it (they are handled by event type). |
| `SafetyScreen` | `safety.py` | Redacts secrets (`mde_core.redact`) and strips hidden characters **before** text reaches a slot, the summary, a turn, an event or a prompt; flags injection phrases; canned decline text. |
| `Understander` (C1) | `components.py` | One call per typed message: intent, slot updates with evidence, references, language, safety flags. |
| `BriefInterpreter` (C2) | `components.py` | First idea or long upload: app type candidates, regions, "like X", multi-app, sensitive domains, unsupported type, size. |
| `SlotStore` | `slots.py` | The slot table with provenance and the spec 3.3 rules. Pure working copy; `snapshot()` goes back into the state. |
| `QuestionSelector` | `selector.py` | Which card next: order, preconditions (`ask_if`), levels by mode and policy, caps, the visual-card limit, assumptions. |
| `CardRenderer` | `cards.py` | Bank card to `CardInstance`: localised (`mde_content.questions.localize`), checklist pre-ticked from a volunteered value, progress counter; the "describe the look", "finish with defaults" and re-roll cards. |
| `CardWriter` (C5) | `components.py` | Writes a card **only** when the bank has none for a slot. |
| `Responder` (C3) | `components.py` | Questions and chit-chat only; answers from the knowledge base, never invents price or time. |
| `BriefCompiler` | `compiler.py` | Six deterministic rows, the assumptions list with reasons, and the C11 sentence (template fallback). |
| `ChangeInterpreter` (C14) | `components.py` | A change request or a gate comment as `ChangeOp`s. |
| `PolicyGuard` | `policy_guard.py` | Company rules: a banned library or a region the policy does not allow, found in the user's words or in the `regions` slot, and the allowed alternatives to offer. Deterministic, no LLM, data-driven (`policy_alternatives.yaml`). |
| `OrgProfileMapper` | `org_profile.py` | Enterprise only: the tenant's Org Profile (a tolerant JSON document) into `assumed` slot seeds, through the key-to-slot mapping in `data/org_profile.yaml`. Screens every value, checks the company rules, validates option ids against the bank. Pure, no LLM. See "Org Profile". |
| `Llm` | `llm.py` | The one place that calls the gateway: builds `CallContext`, counts calls, falls back. |
| `DiscoverEngine` | `engine.py` | The turn loop that wires them together. |

Helpers: `vocab.py` (allowed values, labels and prompt hints derived from the bank), `summary.py` (rolling summary), `copy.py` + `data/copy.yaml` (engine copy in en/hi/hi-Latn; the Brief rows, sentence pieces and assumption reasons are content-bank copy, see "Languages"), `helpers.py` (sensitive-domain detection, region ids), `testing.py` (gateways and a conversation driver for tests).

## The turn loop

```
receive event ──► understand ──► apply ──► reply ──► next
```

1. **Receive.** One inbound event (the flow already de-duplicates by `event_id`). `UserMessage`, `OptionSelected`, `UploadAdded`, `UiAction` (including `idle`, silence on a pending card); a `GateDecision` is not Discover's business.
2. **Understand.** A tap costs no call. Typed text first tries the router (greeting, you decide, stop, a starter chip, ...: no call); otherwise C1. The first idea (or a pasted text over 600 characters, or a document upload) also goes to C2, with the long text as the untrusted `document_text`.
3. **Apply** (code). `SlotStore` writes values with provenance. Typed values are validated against the vocabulary derived from the bank (`vocab.py`), so a model that obeys an injected instruction still cannot write `auth.method = "rm -rf /"`.
4. **Reply.** Code acknowledges taps and plain answers in a few words (`ack_1..3`). C3 only for a question or chit-chat, then the pending card is shown again. Confusion is help mode: the card's own help line, or the inspiration cards (no call).
5. **Next.** `QuestionSelector.next` picks the card; C5 only when the bank has none; when nothing is left the engine fills defaults (`assumed`, with a reason) and returns `brief_complete=True` with no pending card.

Cost per turn (asserted in tests): tap or chip **0** calls, typed message **1** (C1), first idea or upload **2** (C1 + C2), question or chit-chat **2** (C1 + C3), typed change **2** (C1 + C14), `compile_brief` **1** (C11).

### Exit rules (spec 3.1)

The loop ends when no required slot is empty, or the user taps (or types) "Finish with smart defaults", or a cap is reached: **10 cards** or **30 turns** (`policy.limits`, Guided only). At a cap the engine **offers** to finish with defaults (the bank's `Q-nudge-02` with only the finish option) instead of asking more. Three skips in a row offer the same card with "Keep going" as the alternative. "Build with defaults" in the first message sets `extra.defaults_path`: only the idea and the look are asked. Must-confirm slots of the mode (Enterprise payments, compliance, SSO) are never assumed: skip and finish leave them pending.

### State machine of a card

```
              selector picks            tap / typed answer
  (none) ─────────────────► PENDING ───────────────────────► ANSWERED (confirmed, or volunteered for help cards)
                              │  ▲
        you decide / skip ────┤  │ re-shown after a question, a nudge or "did not catch that"
                              ▼  │
                          ASSUMED (recommended option, one-line reason)      ◄── 2nd off-topic turn, or 2nd idle, after one nudge
                              │
      none of these (visual) ─┴─► RE-ROLLED (pool card, unseen options) ─► DESCRIBE-THE-LOOK ─► nearest preset
      new contradiction ─────────► DISPLACED (resolution card first; the card is asked again afterwards)
      user volunteered its slot ─► DROPPED (a checklist or a confirm card is kept: it pre-fills instead)
```

`DiscoveryState` keeps `pending_card`, `cards_asked` (card id, `:n` when repeated), `card_count` (standard, compliance and resolution cards only), `skips_in_row`, `off_topic_streak`, `nudged_card_ids`, `visual_rounds` and `seen_option_ids`.

### Silence (`idle`)

The client reports silence on a pending card with `UiAction(action="idle")` (spec 3.4 "silence after one nudge", section 9 "User ignores a card"). The flow hands the event to `handle`; the engine decides, with no LLM call and without counting a turn:

| Situation | What happens |
| --- | --- |
| First idle on a card the engine may answer (ordinary or compliance card, skip enabled, not must-confirm in this mode) | One gentle line (`nudge`: "Take your time. If you like, I can choose for you.") and the **same** card again. The card id joins `nudged_card_ids`. |
| Second idle on that card | The recommended option is assumed exactly like "You decide": `assumed`, source `default`, one-line reason ("No problem, I will go with ..."), counted as a skip, on to the next card. Three in a row offer "Finish with smart defaults". |
| A card nobody may answer for the user (must-confirm slot, contradiction, company-rule card, help or inspiration card, the "describe the look" card) | A waiting line once (`nudge_wait`), then nothing. Silence never picks for the user here. |
| The finish offer (`Q-nudge-02`) | Nothing: it is itself the nudge. |
| No pending card, a gate open, or a state other than DISCOVERING | A silent no-op (no events, state unchanged). Silence never approves anything. |

One rule serves silence and off-topic chat: a card holds one nudge, whichever path gave it. Only the pending card can hold a nudge (`_forget_stale_nudges` runs when the turn ends), so an answer, an assumption or a new card resets it, and a card asked again later gets its own nudge. `Q-nudge-01` (a card for the first nudge) was never used and is gone: the first nudge is a message beside the same card.

### Company rules (policy)

`Policy.banned_libraries` and `Policy.allowed_regions` are checked in the interview, in Enterprise and in any session whose policy lists them (spec section 9, "Enterprise: request violates org policy"). `PolicyGuard` looks at every typed text, document and "Add my own" text (cheap, no LLM; C1 still sees the text) and at the `regions` slot after every event:

- **A banned library** is recognised by the policy's id and by aliases from `policy_alternatives.yaml`, case-insensitive and whole-word (punctuation ignored: `Code-Push`, `code push`). A library whose bare id is an everyday word (`moment`) matches only through its aliases (`moment.js`).
- **A disallowed region** is a region id in the text (`us-east`, `eu-west-1`: the table and the cloud-style pattern of the data file) that the policy does not list, or a value in the `regions` slot (a group such as `us` is fine when some allowed region belongs to it; a region id must be listed). A country or a city in running text is not a data-region request by itself; when C1 or C2 turns it into a `regions` value, the slot check catches it.

What the user sees: the constraint in plain words (the library or the allowed regions are named), then a **card of allowed alternatives** instead of the question that was about to be asked (that card is withdrawn, uncounted and asked again afterwards). A disallowed region is taken out of the slot, never accepted. Library cards (`Q-policy-lib:<library>`, `choice3`) offer the library's own alternatives from the data file plus "I will ask my IT team" (a library the file does not know gets the generic two); the region card (`Q-policy-region`) lists the policy's allowed regions (`multi`, the first pre-ticked; one button when only one is allowed). Both are `resolution` cards with `skip` and "Something else" off: only a tap answers them, "You decide", a typed answer and silence do not. Several violations queue (`extra["policy_queue"]`, JSON) and are shown one at a time.

On the tap (`source=tap`): a region choice writes `regions` (`confirmed`); a library choice rewrites the mention in `idea.summary` (and "Add my own" text) with the alternative's phrase and drops the library from `integrations.list`. The original request stays in the rolling summary ("Asked for react-native-code-push, which company rules do not allow", then "Company rule: ... instead of ..."). "Finish with smart defaults" with a library card open applies the first alternative (the rule wins); a disallowed region was never written, so the policy's own regions reach the spec. Not checked: gates (a change typed at Gate 1 or 2 is the flow's to judge).


### Org Profile (starting context of an Enterprise session)

Spec 3.5 and 10 (milestone M7): an Enterprise session starts with the tenant's Org Profile preloaded (brand, identity, compliance, API catalog, approvers). The profile reaches Discover as data, `DiscoveryRequest.org_profile`, set by the flow on the **first turn of an Enterprise session only** (`FlowService.create_session`); Discover never imports persistence. `DiscoverEngine.start` seeds the slots in this order: set `greeted`, read the profile and write the seeds, the canned greeting, then one `Notice(code="org_profile_loaded")` (copy in en, hi, hi-Latn) when at least one slot was seeded. `handle` and the other entry points never read it; a Guided session ignores a profile even when one is passed.

**What a seed is.** Each value becomes a slot with `state="assumed"`, `source="org_profile"` and the evidence "From your organization profile" (said again in the session language). It is a proposal, not an answer (spec 3.5: remembered values are proposed and never applied silently the first time):

- the seeds appear in the Brief's "We assumed" list with that reason (for `should` and `must` slots; the `may` slots, accent, light or dark and locale, are never listed, as for every default);
- the selector does not ask an `assumed` slot again, so a seeded look, release channel and so on are settled until the user reopens them at Gate 1; any tap on the card overwrites the seed (`confirmed`, source `tap`);
- the **must-confirm** slots are still asked (spec 3.4): `auth.sso` always, `pay.model` when payments apply. A seeded `compliance.flags` does **not** stand in for the must-confirm compliance card: `sensitive_flags` and `compliance_confirmed` are untouched, a sensitive domain still gets its card, and answering it adds to the seeded flags (the slot is a union);
- a `choice3` or `visual3` card reopened for a seeded slot highlights the profile's option (it becomes the recommended one and the one "You decide" takes); a checklist reopens ticked with the profile's items;
- `store.changed` is restored after seeding, so a seed is not reported as a change the user made (`changed_slots` stays empty on the first turn).

**The documented keys.** `data/org_profile.yaml` is the single source. Unknown keys, unknown values and wrong types are dropped silently and only counted for one log line (`org_profile_read`: counts and a rejection code, never a key or a value).

| Profile key | Slot | Accepts |
| --- | --- | --- |
| `brand.name` | `brand.name` | one line of text, up to 80 characters |
| `brand.assets_url` | `brand.assets` | an `https` link to a named host, no credentials, up to 500 characters |
| `brand.direction` | `design.direction` | a theme id (`clean-bright`, `bold-dark`, ...) |
| `brand.accent` | `design.accent` | `#RRGGBB` or a colour word of the lexicon |
| `brand.mode` | `design.mode` | `system`, `light` or `dark` |
| `identity.sso` | `auth.sso` | `required`, `not_needed` or `ask_it` |
| `compliance.data_classification` | `data.classification` | `public`, `internal` or `confidential` |
| `compliance.regions[]` | `regions` | region groups of card Q-ent-04 (`in`, `eu`, `us`, `other`) that the policy's `allowed_regions` permit |
| `compliance.flags[]` | `compliance.flags` | compliance flag ids of the sensitive domains (and `needs_expert_review`) |
| `integrations[]` | `integrations.list` | option ids of card Q-ent-02 (the API catalog) |
| `approvers[]` | `approvers` | option ids of card Q-ent-06 |
| `release_channel` | `release.channel` | `internal`, `testers` or `public` |
| `locale` | `locale` | `en`, `hi` or `hi-Latn` |

Option and theme values are validated against the question bank (`Vocab.allowed`) and the themes, ignoring case; a slot no card offers values for (`design.mode`, `locale`) lists them in the data file. A profile can therefore only select what a card could offer. `idea.summary` and `idea.archetype` are never seeded (`never_seed` in the data file, refused when it loads): the user's own idea decides the app type.

**The profile is untrusted text.** The document is read only within bounds (64 KB of JSON, depth 6, 4,000 nodes, 32 items per list; over any bound it is ignored whole and the reason is logged). Every text value goes through the existing `SafetyScreen` (a value with a secret or an instruction-like phrase is dropped, never redacted-and-kept; hidden characters are stripped) and must be one printable line without `<`, `>` or backticks; `PolicyGuard` then drops a value that names a banned library, and a `regions` item outside `allowed_regions`. Every regex is linear (a fixed-length colour pattern; the rest is the existing screen). If the data file is inconsistent (a mapping to an unknown slot or to `idea.*`, a choice slot without values) the engine refuses to start.

How the flow and the API feed it: `POST /v1/sessions` stores a posted `org_profile` first (Enterprise only, 64 KB limit as before), otherwise reads the tenant's stored profile, and passes it to `create_session` (`docs/packages/context-api.md`). The flow's `inputs.py` and `stakeholders.py` already treat source `org_profile` as a standing, user-owned source once it is confirmed.

### Slot rules (`slots.py`)

A correction overwrites, never appends (the one exception: `compliance.flags` is a union across the flagged domains). An explicit negation clears the slot or trims the named list items; a negation of a value the slot does not hold changes nothing. An `assumed` value never replaces a `volunteered` or `confirmed` one. A slot nobody touched is never reset. A confirmation of the same value upgrades the state without counting as a change. Property tests (hypothesis) cover the first, third and fourth rules against a reference model.

States by origin: tap on a normal card `confirmed`; typed text that answers the pending card `confirmed`; unsolicited text, starter chips and help or inspiration cards `volunteered`; "You decide", skip and defaults `assumed` (source `default`); the Org Profile `assumed` (source `org_profile`, see "Org Profile"). A volunteered slot is settled (its card is skipped) **unless** a card exists that checks it: a confirm card whose `ask_if` reads its own slot (the detected app type) or a checklist (features), which are shown once, pre-filled.

Consistency: changing the app type drops features and apps that belong to the old type; a contradiction (the bank's `resolution` cards, whose `ask_if` is the conflict, for example "no sign-in" with an account feature) marks the slot `disputed` and shows a one-tap card with three options.

## How `ask_if` context is built

`SlotStore.context(sensitive_flags)` returns `{slot id: value}` for every **filled** slot (an empty list or empty text counts as unfilled) plus the read-only name `sensitive.domains` (the flagged domain ids from `DiscoveryState.sensitive_flags`, or `None`). `mde_content.expr.holds(ask_if, ctx)` evaluates it; there is no `eval`. A card is asked when its slot is unsettled, the slot's own `ask_if` holds and the card's `ask_if` holds. Compliance cards are matched to a domain by evaluating their `ask_if` with that domain alone; `DiscoveryState.compliance_confirmed` lists the domains already answered or assumed.

Sensitive domains are decided here, not in research: C2's flags, plus a deterministic safety net on every typed text (the catalog keywords and the Domain Pack's `compliance_triggers`). Each domain gets one must-confirm card; skipping in Guided means the careful option plus a visible flag, in Enterprise the card is asked again until answered.

## Fallbacks (nothing raw reaches the user)

`Llm.run` catches `GatewayError` (so `StructuredOutputError`, `BudgetExceeded`, `CircuitOpen`, `ProviderError`, ...) and timeouts and returns the spec's deterministic fallback; the turn ends with one friendly notice (`taking_longer`). `CallCancelled` is not swallowed. Each spec names its fallback in `spec.fallback`; the engine checks at construction that every name is registered in `llm.FALLBACKS`.

| Call | Fallback | Behaviour |
| --- | --- | --- |
| C1 | `discovery.keyword_understander` | the same keyword understander as the mock handler; a message it cannot place is intent `other`, which shows "I did not catch that. Here are the options." and the pending card (or help cards) |
| C2 | `discovery.keyword_archetype_match` | catalog keywords for the app type, regions, "like X", multi-app, sensitive domains |
| C3 | `discovery.canned_reply` | the knowledge snippet that matches, otherwise "I do not have that detail ... you will see the plan before anything is built" |
| C5 | `discovery.options_card` | a generic card from the slot's own allowed values |
| C11 | `discovery.template_sentence` | the facts joined into one sentence, capped at 40 words |
| C14 | `discovery.use_the_buttons` | "use the buttons or edit the row" (never guesses) |

## Call specs (in `mde_prompts`)

| Call | Route | Effort | Max out | Untrusted | Cacheable |
| --- | --- | --- | --- | --- | --- |
| C1 `understand` | fast | low | 600 | `attachment_text` | no |
| C2 `interpret_brief` | standard | low | 900 | `document_text` | yes |
| C3 `respond` | standard | low | 500 | - | no |
| C5 `write_card` | standard | low | 700 | - | no |
| C11 `write_summary` | standard | medium | 400 | - | yes |
| C14 `interpret_change` | standard | medium | 700 | - | no |

All `version=1`, no tools. Each folder has `spec.py`, `system.jinja`, `user.jinja`, `mock.py` and golden `fixtures/cases.yaml`. The keyword logic lives in `keyword.py` / `snippets.py` / `template.py` / `rules.py`; `mock.py` just calls it, and the engine fallbacks call the same functions. Phrase tables (greetings, approvals, stop and pause words, unsafe and injection phrases, feature and theme synonyms, region words, knowledge topics) are data in `c1_understand/lexicon.yaml`, shared by the mock handlers and the router.

## Other behaviours worth knowing

- **Greeting** is canned: the `greeting` copy and the six starter chips of the bank, no call. A chip (its label or its prefill text, in any language) is a tap: it sets the app type and the idea as `volunteered`, so the type is still confirmed with its card, at zero calls.
- **Uploads.** A text upload is extracted (C1 with the text as untrusted attachment, plus C2 for a first idea or a long text) and confirmed with "Here is what I understood". An image is kept as `brand.assets` and never read. `UploadMeta.summary` is never the raw text; `injection_suspected` is recorded and a `Notice` is emitted.
- **Typed approval.** `approval_confident` is true only when a gate is open, C1 says `approve` with confidence 0.75 or more and the text carries no slot value. "ok but make it blue" is checked with C14 for a change inside; a change is returned as `change_ops` and `changed_slots`. Without a gate a typed "yes" answers a confirm card or says nothing is waiting for approval.
- **Control words** (stop, pause, start over, in en/hi/Hinglish, or the buttons) set `stop_requested`, `pause_requested`, `start_over_requested` and emit no events: the flow speaks for them.
- **Rolling summary** (`summary.py`): one line per thing worth remembering (C1's `summary_update`, references, screen notes), compacted past about 1.5k tokens (oldest lines dropped, the first kept). Prompts carry the summary, the filled slots and the last four turns only.
- **Details about one screen** typed in Discover are kept in `extra.screen_notes` and returned as `change_ops`.
- **Enterprise.** Must slots first, then should slots grouped by their `SlotDef.group`; no card or turn cap; `policy.slot_policy` overrides levels; compliance and other `must_confirm_in` slots cannot pass as assumptions; banned libraries and disallowed regions are explained and offered allowed alternatives (see "Company rules"); the tenant's Org Profile pre-fills the first turn as assumptions (see "Org Profile").
- **Languages.** Everything the user reads follows the session language (`en`, `hi`, `hi-Latn`; it can change mid-session). The Brief row titles, the sentence pieces ("N features: ...", "in a ... look", "Name: ... Logo: ...") and the one-line reason of every assumption (`SlotDef.default_reason`, shown in "No problem, I will go with ... {reason}" and in the Brief's "We assumed" list) are content-bank copy: `ui_copy` entries `row_*`, `reason.<slot id>` and friends in `questions/shared.yaml`, each with `hi` and `hi-Latn`. A reason stored earlier in another language is said again in the current one when the Brief is compiled (`copy.reason_in`).

## How to add a card, a slot value or an intent

- **A card.** Content only: follow `docs/specs/question-cards.md` and run `uv run pytest packages/content packages/discovery`. The selector, vocabulary, labels and the Brief pick it up; nothing in this package names a card id except the situation cards (`Q-nudge-02`, the describe-the-look card).
- **A new archetype or feature.** Content only (catalog, bank file, pack). Feature keywords for the mock handlers come from the card labels plus `feature_keywords` in the lexicon.
- **A new phrase or language word.** `c1_understand/lexicon.yaml`; the router and the mock handlers share it. Add a test case to `c1_understand/fixtures/cases.yaml`.
- **A new intent.** The `Intent` literal is a contract (ask the lead). Then: teach the keyword understander (`c1_understand/keyword.py`) and its fixtures, add the branch in `DiscoverEngine._on_understood`, a scenario in `tests/test_situations.py`, and update the C1 system prompt examples (bump the spec `version`).
- **A new engine message.** Add it to `data/copy.yaml` in all three languages (a test checks the languages and the placeholders). Copy the user reads in the Brief or as a reason for an assumption goes to the content bank instead (`ui_copy` in `shared.yaml`; `packages/content` tests check the three languages).
- **A new Org Profile key.** `data/org_profile.yaml`: one mapping (`path`, `slot`, `kind`, and `values` when no card offers them). No code changes for a slot whose kind exists; `test_org_profile.py` checks the file against the slot ids and the bank.
- **A new company rule's alternatives.** `mde_content/data/policy_alternatives.yaml`: a library entry (aliases, alternatives with `label`, `description` and `phrase` in en/hi/hi-Latn) or a region (id, group, label). The policy still decides what is banned or allowed; no code changes. `test_policy.py` checks the file.

## Tests

| File | Covers |
| --- | --- |
| `test_slots.py` | the four slot rules, union, negation, provenance, hypothesis properties |
| `test_selector.py` | order and dependencies, levels and policy, preconditions, caps, the two-visual limit, assumptions |
| `test_router_safety_copy.py` | fast paths in three languages, chips, redaction, injection, summary compaction, copy and vocabulary |
| `test_situations.py` | one scenario per Discover row of the situation catalog (spec section 9) |
| `test_idle.py` | silence on a card: nudge once, then assume; resets, no-ops, cards that are never assumed, languages |
| `test_policy.py` | `policy_alternatives.yaml` (validity, translations), `PolicyGuard` (libraries, regions, whole-word, aliases), and the engine: explanation, card, apply by tap, queue, defaults, gates, languages |
| `test_localised_brief.py` | Hindi and Hinglish sessions: Brief rows and assumption reasons in the user's language, reasons re-said after a language change |
| `test_org_profile.py` | the Org Profile: the data file, the pure mapper (every key, closed vocabulary, hostile and oversized input, policy rules), seeding in `start`, the Enterprise interview, the Brief's assumptions, languages |
| `test_paths.py` | whole interviews by taps for every archetype (about seven cards, zero calls), sensitive domains, Enterprise, Hindi and Hinglish |
| `test_engine_protocol.py` | `start`, `handle`, `reopen_slot`, `apply_changes`, `compile_brief` and the Brief rows |
| `test_engine_scripted.py` | exact model outputs: hostile, odd and rare results |
| `test_fallbacks.py` | every call with every error type |
| `packages/prompts/tests/test_calls_c*.py` | one file per call: spec, strict-schema shape, templates (`StrictUndefined`), untrusted fields, golden fixtures |

Test helpers (`mde_discovery.testing`, re-exported by `tests/helpers.py`): `DirectMockGateway` (runs each spec's `mock_handler`), `ScriptedGateway` (queued outputs or errors by call id), `Conversation` (drives the engine like the flow does) and context factories.
