---
name: add-question-card
description: Add or edit a question card in the pre-prepared question bank (choice3, multi, visual3, text, upload, confirm) with options, sets, ask_if, skip assumption, recommended option and hi / hi-Latn copy. Use when covering a slot or archetype with a card.
---

# Add a question card

The normative format is [docs/specs/question-cards.md](../../../docs/specs/question-cards.md) (card fields, what each type writes, the `ask_if` language, the slot value vocabulary, copy rules). This skill is the procedure; if they ever disagree, the spec page and the tests win and this file is the bug.

The bank is data in `packages/content/src/mde_content/data/questions/`: one file per archetype (`commerce`, `food_delivery`, `booking_services`, `internal_workforce`, `custom`) plus `shared.yaml` (cards for several archetypes, compliance, Enterprise, help, nudges, starter chips, canned UI copy, the knowledge base). Slots and their levels are in `data/catalogs.yaml`. A card in an archetype file applies to that archetype only; in `shared.yaml` an empty `archetypes` means every archetype. Check `packages/content/CLAUDE.md` if the layout changed.

1. **Slot and level.** Find the slot in `catalogs.yaml` (spec 3.3). A `must` slot needs a card for every archetype where its precondition can hold, in each mode where it is `must`; Enterprise also needs cards for its `should` slots (`modes: [enterprise]`).
2. **Type.**
   - `choice3`: three options (`A`, `B`, `C`), **exactly one `recommended`**, a "Something else" box, a "You decide" link.
   - `confirm`: two options, yes (recommended) and not quite.
   - `multi`: a checklist; options with `recommended: true` are **pre-ticked** and the card writes the list of ticked option ids to its slot. A `multi` card is **exempt from "exactly one recommended"** (any number may be pre-ticked); its option `sets` is ignored, but each option still declares `{slot: [option id]}` so the card documents itself.
   - `visual3`: three options with `preview: "theme:<theme-id>"`; `sets` is always `{design.direction: <theme-id>}`. At most two visual cards can be reachable in a Guided session, look first.
   - `text`, `upload`: no options; the input goes to `card.slot`.
3. **Write the card.** Unique **`id` of the form `Q-<area>-NN`** (two digits; areas in use: `idea`, `type`, `apps`, `feat`, `roles`, `auth`, `look`, `brand`, `pay`, `comp`, `ent`, `fix`, `help`, `insp`, `nudge`; take an unused number). Fields: `slot`, `type`, `kind` (`standard` unless it is a compliance, resolution, help, inspiration or nudge card), plain-language `text` and `help`, `options` (`id`, `label`, `recommended`, `sets` with real slot ids and values from the slot vocabulary in the spec page), `other` (kept enabled on option cards), `skip` (`enabled`, `assumes` = the recommended option id; disabled only for the idea itself and nudge cards), and the `archetypes` and `modes` filters.
4. **`ask_if`** is mandatory on every card and is a safe expression, never code: operators `==`, `!=`, `in`, `not in`, `and`, `or`, `not`; literals `true`, `false` and `null`; slot paths with at most one dot (`idea.archetype`, `scope.features`) plus the context name `sensitive.domains`. An unfilled slot (missing, `""`, `[]`) makes `!=` true and `in` false; `null` means unfilled. Use `true` when nothing else is needed and `false` for situation cards (help, inspiration, nudge, the visual pool) that only code may show. Limits: 500 characters, nesting depth 16.
5. **Languages.** Every card needs `i18n` for **both `hi` (Hindi) and `hi-Latn` (Hinglish)**: text, a label for every option, and placeholder and help wherever the English card has them. Option ids and `sets` are never translated. The same applies to starter chips, UI-copy entries and knowledge snippets.
6. **Copy.** Short, warm, plain language for non-technical founders; no jargon (the tests reject words such as API, SDK, schema, token); no invented prices, times or guarantees; no emoji; no "(recommended)" in a label (the flag drives the badge); compliance cards say they are not legal advice.
7. **Tests.** `uv run pytest packages/content` checks unique ids and the id pattern, valid slots and values, one recommended option (except `multi`), that `skip.assumes` is the recommended option, that `ask_if` parses over real slots, themes, `hi` and `hi-Latn` completeness, copy length and tone, `must` coverage per archetype and mode, and the simulated Guided path (about seven cards, never more than ten).
8. **Discover.** If the card changes what the selector asks, run `uv run pytest packages/discovery`.
9. **Docs.** Update `docs/specs/question-cards.md` if you changed the format or the slot vocabulary, and the card inventory in `docs/packages/content.md`.
10. **Rules.** `.claude/rules/content.md`. Cards are data: no logic, nothing hardcoded to one company or app type.
