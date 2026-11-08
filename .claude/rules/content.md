---
paths:
  - "packages/content/**"
---

# Content rules

- Content is **data**: YAML/JSON validated by Pydantic models in `mde_contracts`, loaded by `mde_content`. No logic in data files; no code in content.
- Nothing is hardcoded to one company or app type. Archetype ids are plain strings resolved from content, not Python literals.
- Ids are unique across their kind (cards, options, packs, screens, flows, themes, policies). Every referenced id must exist; every pack screen has a recipe; every flow resolves; every `ask_if` parses with the safe expression evaluator (`==`, `!=`, `in`, `not in`, `and`, `or`, `not`, the literals `true`, `false` and `null`; never `eval`). Card ids are `Q-<area>-NN`.
- Every `must` slot has a card per archetype; every option declares `sets`; every card has `ask_if` (`true` when nothing else applies, `false` for situation cards only code may show), a skip assumption and exactly one recommended option (a `multi` card is exempt: its pre-ticked options are the recommended set; text and upload cards have no options).
- Every card, starter chip, UI-copy entry and knowledge snippet has `hi` (Hindi) and `hi-Latn` (Hinglish) copy; option ids and `sets` are never translated. Format: `docs/specs/question-cards.md`, `docs/specs/domain-pack-format.md`.
- Themes pass WCAG AA contrast for text/background pairs (a test checks it). Card previews are token descriptions, no images in v1.
- Stack Profile: do not invent an Expo SDK number; use a field pinned at build time.
- Copy is short, plain-language, non-technical, and available in the user's language where the content is user-facing.
- Add or change content through the `add-question-card` / `add-domain-pack` skills and run `uv run pytest packages/content`.
