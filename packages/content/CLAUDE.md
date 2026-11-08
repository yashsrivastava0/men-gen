# mde_content

**Job:** data plus loaders. Everything that varies by archetype, company or policy is YAML/JSON here, validated by Pydantic models in `mde_contracts`. No logic in data, nothing hardcoded to one company.

**Contents:** question bank (one file per archetype plus shared cards: `commerce`, `food_delivery`, `booking_services`, `internal_workforce`, `custom`), starter chips, help-mode inspiration cards, nudge and finish-with-defaults copy, knowledge base for C3; domain packs (features, roles, entities, flows, recipe mapping, navigation, API skeletons, integrations, compliance triggers); base recipe library; six themes (light and dark scales, WCAG AA); Stack Profile (capability matrix, unsupported types); policies (`guided-default`, IT-services enterprise example, regulated example). Data lives under `src/mde_content/data/`.

**Test:** `uv run pytest packages/content` (every id referenced exists, every pack screen has a recipe, every flow resolves, every `ask_if` parses, themes pass contrast, no duplicate ids)

**Conventions**
- See `.claude/rules/content.md`, skills `add-question-card` and `add-domain-pack`.
- Archetype ids are strings from data, not Literals. Do not invent an Expo SDK number (pinned at build time).
- Owners: `content-author-questions` (question bank, chips, help/nudge copy, knowledge base) and `content-author-packs` (packs, recipes, themes, stack profile, policies). Each owns separate loader modules and tests; the shared bundle/registry is merged by the lead.
