# Question cards: format, semantics and the `ask_if` language

Normative for the pre-written question bank in `packages/content` (`mde_content`). The product rules are in the [spec](../product/context-engine-spec.md) sections 3.3 to 3.4 and 9; the models are `mde_contracts.cards.Card` and `mde_contracts.content.QuestionBankFile`. Authoring procedure: skill `add-question-card`. Package overview and id lists: [packages/content.md](../packages/content.md).

A card is **data**. It has no logic: what it asks, what each option writes (`sets`), when it applies (`ask_if`), and what a skip assumes. The Discover engine (`mde_discovery`) decides *when* to show one. An LLM writes a card (C5) only when the bank has none for a slot.

## 1. Where cards live

```
packages/content/src/mde_content/data/
  catalogs.yaml                  archetypes, sensitive domains, one SlotDef per slot (spec 3.3)
  questions/
    shared.yaml                  cards for several archetypes, compliance, Enterprise, help, nudges,
                                 greeting chips, canned UI copy, knowledge base for C3
    commerce.yaml  food_delivery.yaml  booking_services.yaml  internal_workforce.yaml  custom.yaml
```

Each file is a `QuestionBankFile`: `archetype`, `cards`, `inspiration_cards`, `starter_chips`, `ui_copy`, `knowledge`. The file name equals `archetype`. A card in an archetype file applies to that archetype only (the loader fills `Card.archetypes` from the file); in `shared.yaml`, an empty `archetypes` means "every archetype". Files are found by listing the folder, so a new archetype is a new file.

## 2. Card fields

| Field | Meaning |
| --- | --- |
| `id` | `Q-<area>-NN`, unique across all files. Areas: `idea`, `type`, `apps`, `feat`, `roles`, `auth`, `look`, `brand`, `pay`, `comp`, `ent`, `fix`, `help`, `insp`, `nudge` |
| `slot` | the slot the card is for (a real slot id) |
| `type` | `choice3`, `multi`, `visual3`, `text`, `upload`, `confirm` |
| `kind` | `standard` (default), `compliance`, `resolution`, `help`, `inspiration`, `nudge` |
| `text`, `help` | the question and a one-line explanation shown for "I don't know" |
| `options` | list of `{id, label, description?, preview?, recommended, sets}` |
| `other` | the "Something else" box; for `text` and `upload` cards it is the input itself (its `placeholder` is the input placeholder) |
| `skip` | `{enabled, assumes}`: what "You decide", skip, or silence after one nudge picks (silence never picks for must-confirm slots, contradiction and company-rule cards) |
| `ask_if` | safe expression (section 4). Every card has one |
| `archetypes`, `modes` | filters; empty means all archetypes, both modes |
| `i18n` | `hi` (Hindi, Devanagari) and `hi-Latn` (Hinglish) copy |

### Types and what they write

| Type | Shape | Answer |
| --- | --- | --- |
| `choice3` | three options, exactly one `recommended` | the chosen option's `sets` map is applied (slot id to value) |
| `confirm` | two options: yes (recommended), not quite | same |
| `visual3` | three options with `preview: "theme:<theme-id>"` | same; `sets` is always `{design.direction: <theme-id>}` |
| `multi` | a checklist; options with `recommended: true` are pre-ticked | the card's `slot` receives the list of ticked **option ids**; option `sets` is ignored, but each option declares `{slot: [option id]}` so the card documents itself |
| `text` | no options | the typed text goes to `card.slot` |
| `upload` | no options | the upload reference goes to `card.slot` |

"Something else" sends the typed text through the understander (C1) and also answers the card. "You decide" and skip pick the recommended option (or `skip.assumes`) and mark the slot `assumed`; for a `multi` card that is the pre-ticked set, for a `text` card the slot default from `catalogs.yaml`. Skipping is disabled only for the idea itself (`Q-idea-01`, `Q-help-02`) and for nudge cards.

### Kinds

- `standard`: normal interview cards, picked by the order logic.
- `compliance`: must-confirm cards for a sensitive domain (section 6).
- `resolution`: one-tap fixes for a contradiction between answers; `ask_if` is the contradiction. The engine evaluates every resolution card after each applied answer and shows the first that holds.
- `help`, `inspiration`, `nudge`: shown by situation, never by the order logic. Their `ask_if` is the literal `false`. A `help` card (`Q-help-01`, `Q-help-02`) answers a vague or confused first message; an `inspiration` card (`inspiration_cards`, three example ideas per archetype) follows when the type is known but the idea is thin. `Q-nudge-02` (after three skips in a row) uses semantic option ids (`finish`, `continue`) and declares empty `sets`, because it acts on the session, not on a slot. There is no card for the first nudge (one ignored card): it is a message (`ui_copy` `nudge`, or `nudge_wait` where nobody may answer for the user) beside the same card, sent after silence (`UiAction(idle)`) or two off-topic turns.
- Visual pool cards: `Q-look-11` to `Q-look-15` hold the three themes the primary look card does not show. Their `ask_if` is `false`; the engine uses them for "none of these" (spec 3.6 item 5). In Guided, at most two visual cards can be reachable, and the bank has one (look first).

## 3. Order, caps and the Guided path

Slots carry the order (`catalogs.yaml`: `order`, `depends_on`, `ask_if`): idea, type, sensitive-domain confirmations, apps, features, roles, login, look, name, payments. `mde_content.questions.QuestionBanks.cards_for_slot(slot, archetype, mode)` returns the cards that apply, stable: standard before compliance before resolution, archetype-specific before shared, file order within a group. It does **not** evaluate `ask_if`; the caller does, with `mde_content.expr.holds`.

A Guided session about commerce asks: confirm type, apps, features, login, look, name, payments (seven cards, plus the idea). Food ordering and bookings ask the same seven; team apps and custom ask confirm type, features, roles, login, look, name (six). A sensitive domain adds one compliance card per domain. `tests/test_questions.py` simulates this for every archetype and fails above ten cards.

## 4. The `ask_if` language

A small expression language evaluated by `mde_content.expr` (hand-written tokenizer, parser and evaluator; no `eval`, `exec`, `compile` or `ast`; anything outside the grammar raises `ExprError`).

```
expr     := or_expr
or_expr  := and_expr ( "or" and_expr )*
and_expr := not_expr ( "and" not_expr )*
not_expr := "not" not_expr | compare
compare  := operand [ ( "==" | "!=" | "in" | "not" "in" ) operand ]
operand  := "(" expr ")" | list | string | number | true | false | null | slot
list     := "[" [ item ( "," item )* ] "]"
```

- **Slot paths** are bare names with at most one dot: `idea.archetype`, `scope.features`, `locale`. Anything else (`a.b.c`, `a.b()`, `a[0]`, names with `__`, uppercase names) is an error.
- **Strings** are quoted (`'commerce'` or `"commerce"`); `\'` and `\\` are the only escapes. Barewords are strings **only inside lists**: `[commerce, food_delivery]`. Inside lists `true`, `false`, `null` are literals and a bareword may contain `-` and `_` (`clean-bright`).
- **Numbers** are plain (`3`, `-1`, `2.5`). **Booleans** are `true` / `false`; they are never equal to numbers or strings. `null` is the unfilled value.
- **Unfilled** means the slot is missing, `None`, `""` or `[]`. `x == null` is true for an unfilled slot and false for a filled one; `x != null` is the opposite.
- `==` compares scalars by type and value, and lists as sets (order and duplicates do not matter). A scalar never equals a list. Against an unfilled slot, `==` is false and `!=` is true (`!=` is exactly `not ==`).
- `x in L` is membership when `L` is a list (a literal or a list-valued slot). If `x` is itself a list, it means "any element of `x` is in `L`". If either side is unfilled or `L` is not a list, `in` is false and `not in` is true.
- A bare operand is truthy when it is filled, not `false` and not zero. `true` and `false` are valid whole expressions (`false` is how situation cards say "never by the order logic").
- Limits: 500 characters, nesting depth 16, 64 list items, 200-character strings.

API: `parse(text) -> Expr`, `evaluate(expr_or_text, ctx) -> bool`, `holds(ask_if_or_none, ctx) -> bool` (no expression means always), `referenced_slots(expr) -> set[str]`, `unknown_slots(expr) -> set[str]` (names that are neither slot ids nor context variables), `ExprError`.

**Context.** `ctx` is a `Mapping[str, SlotData]`: slot id to value, taken from the slot table. The engine adds one read-only name that is not a slot:

| Name | Value |
| --- | --- |
| `sensitive.domains` | list of sensitive-domain ids flagged in this session by the interpreter (C2) and the Domain Pack rules (`DiscoveryState.sensitive_flags`) |

Examples:

| `ask_if` | Reads as |
| --- | --- |
| `idea.archetype == 'commerce'` | the detected type is a shop |
| `idea.archetype in [commerce, food_delivery, booking_services]` | any customer-facing type |
| `'payments' in scope.features` | the payments feature is ticked |
| `auth.method == 'none' and scope.features in [favorites, order_tracking, reviews, loyalty]` | "no sign-in" contradicts a feature that needs an account |
| `pay.model in [online, both]` | the app takes online payments |
| `'health' in sensitive.domains` | the health domain was flagged |
| `sensitive.domains != null` | any sensitive domain was flagged |
| `idea.summary == null` | no idea yet |
| `false` | never picked by the order logic |

Slot definitions (`SlotDef.ask_if`) use the same language; a card is asked only when its slot is empty, the slot's precondition holds, and the card's own `ask_if` holds.

## 5. Slot value vocabulary

Cards write these values. `tests/test_questions.py` rejects anything else.

| Slot | Values |
| --- | --- |
| `idea.archetype` | an archetype id (`commerce`, `food_delivery`, `booking_services`, `internal_workforce`, `custom`) |
| `scope.apps` | list of `main` (default: the archetype's primary app), `customer`, `business`, `courier`, `admin`, `employee` |
| `scope.features` | list of feature ids (see packages/content.md) |
| `users.roles` | list of role ids: `user`, `staff`, `admin`, `employee`, `manager` |
| `auth.method` | `email_phone_code` (default), `social`, `password`, `guest_first`, `sso`, `none` |
| `design.direction` | a theme id: `clean-bright`, `bold-dark`, `warm-friendly`, `minimal-mono`, `vibrant-playful`, `corporate-calm` |
| `pay.model` | `online` (default), `on_delivery`, `both`, `none` |
| `pay.provider` | `mock` (default), `company_provider`, `later` |
| `data.source` | `generated` (default), `existing_api`, `sample` |
| `behavior.offline` | `basic` (default), `full`, `none` |
| `auth.sso` | `required`, `not_needed` (default), `ask_it` |
| `data.classification` | `public`, `internal` (default), `confidential` |
| `regions` | list of `in`, `eu`, `us`, `other` |
| `release.channel` | `internal` (default), `testers`, `public` |
| `approvers` | list of role ids; `requester` (default), `product_owner`, `security_reviewer`, `delivery_lead`, `it_admin` |
| `compliance.flags` | list of flag ids from the sensitive domains, plus `needs_expert_review` |

The archetype's recommended option can differ from the slot default (for example team apps recommend `[employee, manager]` while the slot default is `[user]`). Skip uses the card's recommended option; the slot default applies when no card is shown.

## 6. Sensitive domains and compliance cards

`catalogs.yaml` lists five sensitive domains (`health`, `finance`, `children`, `identity_documents`, `precise_location`) with keywords (English, Hindi, Hinglish), `compliance_flags` and `conservative_defaults`. Each has a `compliance` card (`Q-comp-01` to `Q-comp-05`, in that order) whose `ask_if` is `'<domain>' in sensitive.domains`:

- **A** (recommended, the skip assumption): handle it carefully. Sets the domain's flags and exactly the domain's conservative defaults.
- **B**: it does not apply. Sets `compliance.flags: []`.
- **C**: flag for a specialist. Sets the domain's flags plus `needs_expert_review` and the same defaults.

Every card says it is not legal advice. In Guided a skipped card means A: conservative defaults plus a visible flag. In Enterprise `compliance.flags` is must-confirm (`must_confirm_in`), so it cannot pass Gate 1 as an assumption.

**One exception to "a correction overwrites, never appends":** with several flagged domains, each compliance card writes `compliance.flags` with that domain's flags, and the engine must **union** these lists (`DiscoveryState.compliance_confirmed` tracks which were confirmed).

## 7. Languages

`Card.i18n` holds `hi` and `hi-Latn` entries: `text`, `options` (option id to label), `other_placeholder`, `help`. Every card, starter chip, UI-copy entry and knowledge snippet has both, and `en` is the base. Fallback (`mde_content.questions.lang_chain`): the exact tag, then the tag without its region, then the base language unless the tag has a script, then `en`. So `hi-IN` falls back to `hi`, but `hi-Latn` falls back straight to `en` (Hinglish readers get English, not Devanagari). `localize(card, lang)` returns a card with the translated fields; each field falls back to English on its own. Canned copy: `mde_content.ui_copy.copy(id, lang, **values)` fills `{placeholders}` and leaves unknown ones as written.

Option **ids and `sets` are never translated**; only labels are. Spec keywords stay English.

The same three languages cover everything else the user reads that comes from content: the Brief row titles and sentence pieces and the one-line reason of every assumption are `ui_copy` entries in `shared.yaml` (`row_*`, `features_text`, `look_dark`, ..., and `reason.<slot id>` for each slot with a `default_reason`). The English `reason.<slot id>` text equals `SlotDef.default_reason` in `catalogs.yaml` (a test keeps them in step); change both, and add `hi` and `hi-Latn`. No template appends "app(s)" to a placeholder (labels such as "Desktop apps" already say it).

**Company-rule cards** (`Q-policy-region`, `Q-policy-lib:<library>`) are not in the bank: the Discover engine builds them from the policy (the allowed regions) and from `data/policy_alternatives.yaml` (alternatives and labels per library, with `hi` and `hi-Latn`), using the `policy_*` entries of `ui_copy` for the texts. They are `resolution` cards that only a tap answers.

## 8. Copy rules

Short, warm, plain language for non-technical founders. No jargon (the tests reject words such as API, SDK, schema, token; the two Enterprise cards `Q-ent-01` and `Q-ent-08` may say "single sign-on" and "backend"). No invented prices, times or guarantees: the knowledge base says "I do not have pricing details" and "you will see the plan before anything is built". No emoji, no "(recommended)" in labels (the `recommended` flag drives the badge).

## 9. How to add a card

1. Find the slot and its level in `catalogs.yaml`. A `must` slot needs a card for every archetype where its precondition can hold, in each mode where it is `must` (Enterprise also needs cards for `should` slots, with `modes: [enterprise]`).
2. Pick the file: the archetype file, or `shared.yaml` with `archetypes: [...]` for several archetypes.
3. Pick an unused `Q-<area>-NN` id. Write `type`, `slot`, `text`, `help`, and options with ids `A`, `B`, `C` (a `multi` card uses the feature or role id as the option id).
4. Every option declares `sets` with real slot ids and values from section 5; exactly one option is `recommended` (a `multi` card pre-ticks a sensible set); `skip.assumes` is the recommended option id.
5. Write `ask_if` (use `true` when nothing else is needed). Keep `other` enabled on option cards.
6. Add `i18n` for `hi` and `hi-Latn`: text, a label for every option, placeholder and help where the English card has them.
7. Run `uv run pytest packages/content`. The tests check unique ids, valid slots and values, one recommended option, `ask_if` parses over real slots, themes, i18n completeness, copy length and tone, `must` coverage per archetype and mode, and the simulated Guided path (about seven cards, at most ten).
8. If the card changes what the selector asks, run `uv run pytest packages/discovery`.

To add an **archetype**: add it to `catalogs.yaml` (label, description, keywords in three languages, `multi_sided`, i18n), add `<id>.yaml` with a type-confirm card, features card, look cards (primary and pool), login card (or reuse `Q-auth-01` by adding the id to its `archetypes`), a payments card if it takes payments, a resolution card for each contradiction, one inspiration card and six chips, and add the pack (packs author). Update `scope.apps` / `users.roles` slot preconditions if the archetype needs them.
