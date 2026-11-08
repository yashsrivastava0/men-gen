# mde_content

Data plus loaders: everything that varies by archetype, company or policy is YAML validated by Pydantic models in `mde_contracts`. No logic in data, nothing hardcoded to one company. Tests: `uv run pytest packages/content`.

<!-- BEGIN questions half (content-author-questions). The packs half is appended below by the lead. -->

## Question bank half

Owner: `content-author-questions`. Card format, answer semantics and the `ask_if` language: [specs/question-cards.md](../specs/question-cards.md).

### Layout

```
packages/content/src/mde_content/
  expr.py        safe expression language for ask_if (never eval)
  catalogs.py    load_catalogs(), slot_def(id), archetype(id), archetype_ids(), sensitive_domain(id),
                 slots_for_mode(mode, levels=None)
  questions.py   load_question_banks() -> QuestionBanks; lang_chain(), resolve_text(), localize()
  ui_copy.py     copy(id, lang, **values), copy_ids()
  data/catalogs.yaml
  data/questions/{shared,commerce,food_delivery,booking_services,internal_workforce,custom}.yaml
```

The loaders are cached (`functools.lru_cache`) and return frozen models. They import only `mde_contracts`, so discovery can use them without layering trouble. `mde_content/__init__.py` and `bundle.py` are the lead's; tests import the modules directly.

### Loader API

| Call | Returns |
| --- | --- |
| `catalogs.load_catalogs()` | `Catalogs` (archetypes, sensitive domains, one `SlotDef` for every slot id) |
| `catalogs.slot_def(slot_id)`, `archetype(id)`, `sensitive_domain(id)` | one definition; `KeyError` if unknown |
| `catalogs.slots_for_mode(mode, levels=None)` | `SlotDef` list in asking order, optionally filtered by level (`("must",)`) |
| `questions.load_question_banks()` | `QuestionBanks` |
| `QuestionBanks.card(id)`, `all_cards()` | a card, or every card including inspiration cards |
| `.cards_for_slot(slot, archetype, mode, kinds=...)` | cards that apply (archetype and mode filters), stable order; `ask_if` is not evaluated |
| `.inspiration(archetype)` | the archetype's example-idea card, then the shared help card |
| `.chips(archetype, lang)` | six starter chips with localized labels; `None` gives the greeting chips |
| `.knowledge(lang)` | `list[KnowledgeLite]` (id, text) ready for the C3 call; `.snippets()` gives the raw snippets |
| `.ui_copy(id)`, `.ui_copy_ids()` | one canned-copy entry, or the ids |
| `ui_copy.copy(id, lang, **values)` | text in `lang` with `{placeholders}` filled; falls back to English |
| `expr.parse`, `evaluate`, `holds`, `referenced_slots`, `unknown_slots` | the `ask_if` language |

Loading fails loudly on a duplicate card id or a duplicate UI-copy id.

### Contract between this half and the engines

- `ask_if` context: slot id to value, plus `sensitive.domains` (list of flagged domain ids). Build it from the slot table; `holds(None, ctx)` is true.
- Multi cards write the ticked option ids to the card's slot; their option `sets` are informational.
- Compliance cards (`Q-comp-01`..`Q-comp-05`) write `compliance.flags`; the engine unions the lists across domains.
- Situation cards (`kind` of `help`, `inspiration`, `nudge`) and the visual pool cards have `ask_if: "false"`; look them up by id or kind.
- `scope.apps` default is `["main"]` (the archetype's primary app, the first surface in its pack); the Q-apps cards write `customer`, `business`, `courier` or `admin`.
- For multi-sided archetypes the chosen app implies the role, so `users.roles` is asked only for `internal_workforce` and `custom`.

### Ids the packs half must reuse

**Feature ids** (the `scope.features` options; pre-ticked ones are marked `*`, the pack's `default_on` should match):

| Archetype | Feature ids |
| --- | --- |
| `commerce` | `catalog_browse*`, `search*`, `cart_checkout*`, `payments*`, `order_tracking*`, `reviews`, `favorites`, `push_notifications*`, `loyalty`, `support_chat` |
| `food_delivery` | `restaurant_browse*`, `menu_item_options*`, `cart_checkout*`, `payments*`, `live_order_tracking*`, `ratings`, `saved_addresses*`, `push_notifications*`, `promo_codes`, `support` |
| `booking_services` | `service_catalog*`, `provider_profiles*`, `slot_booking*`, `payments*`, `reminders*`, `reviews`, `reschedule_cancel*`, `push_notifications*`, `memberships`, `support` |
| `internal_workforce` | `task_lists*`, `forms_checklists*`, `approvals*`, `timesheets`, `directory`, `announcements*`, `offline_sync`, `push_notifications*`, `kiosk_mode`, `reports` |
| `custom` | `list_detail*`, `search*`, `forms`, `auth_profile*`, `notifications`, `settings*`, `media_gallery`, `maps`, `payments_mock` |

The resolution cards also write feature lists (the "no sign-in" and "no payments" fixes); every id in them is in the table above.

**Theme ids** (`design.direction`): `clean-bright`, `bold-dark`, `warm-friendly`, `minimal-mono`, `vibrant-playful`, `corporate-calm`. The three shown first per archetype (recommended first), and the three held back for "none of these":

| Archetype | First look card | Pool card |
| --- | --- | --- |
| `commerce` | `clean-bright`, `bold-dark`, `warm-friendly` | `minimal-mono`, `vibrant-playful`, `corporate-calm` |
| `food_delivery` | `warm-friendly`, `vibrant-playful`, `clean-bright` | `bold-dark`, `minimal-mono`, `corporate-calm` |
| `booking_services` | `clean-bright`, `warm-friendly`, `minimal-mono` | `bold-dark`, `vibrant-playful`, `corporate-calm` |
| `internal_workforce` | `corporate-calm`, `clean-bright`, `minimal-mono` | `warm-friendly`, `bold-dark`, `vibrant-playful` |
| `custom` | `clean-bright`, `minimal-mono`, `vibrant-playful` | `warm-friendly`, `bold-dark`, `corporate-calm` |

Packs should set `theme_candidates` to the first three and `default_theme` to the first.

**Role and app ids.** App ids (`scope.apps`): `main`, `customer`, `business`, `courier`, `admin`, `employee`. Role ids (`users.roles`): `user`, `staff`, `admin`, `employee`, `manager`.

**Compliance.** Sensitive domains and flag ids: `health` (`health_data_privacy`), `finance` (`financial_data`), `children` (`child_safety`), `identity_documents` (`identity_documents`), `precise_location` (`precise_location`); the generic flag `needs_expert_review` marks "flag for a specialist". Card ids for `ComplianceTrigger.cards`: `Q-comp-01` (health), `Q-comp-02` (finance), `Q-comp-03` (children), `Q-comp-04` (identity documents), `Q-comp-05` (precise location).

### Card inventory (64 cards, one `QuestionBankFile` per archetype plus shared)

| Area | Ids | Slot and scope |
| --- | --- | --- |
| idea | `Q-idea-01` | `idea.summary`, text, shared |
| type | `Q-type-01`..`05` | `idea.archetype`: confirm card per archetype (custom offers three choices) |
| apps | `Q-apps-01`..`03` | `scope.apps`: commerce, food_delivery, booking_services |
| feat | `Q-feat-01`..`05` | `scope.features`: multi checklist per archetype |
| roles | `Q-roles-01`, `02` | `users.roles`: internal_workforce, custom |
| auth | `Q-auth-01` (commerce, food_delivery, booking_services, custom), `Q-auth-02` (internal_workforce) | `auth.method` |
| look | `Q-look-01`..`05`; `Q-look-11`..`15` (pool, `ask_if: false`) | `design.direction`, visual3 per archetype |
| brand | `Q-brand-01` (text), `Q-brand-02` (upload, Enterprise) | `brand.name`, `brand.assets` |
| pay | `Q-pay-01`..`03` | `pay.model`: commerce, food_delivery, booking_services (asked when `payments` is ticked) |
| comp | `Q-comp-01`..`05` | `compliance.flags`, kind `compliance` |
| ent | `Q-ent-01`..`09` | Enterprise: `auth.sso`, `integrations.list`, `data.classification`, `regions`, `release.channel`, `approvers`, `behavior.offline`, `data.source`, `pay.provider` |
| fix | `Q-fix-01`..`05` (no sign-in vs account features, per archetype), `Q-fix-06`..`08` (no payments vs payments feature) | kind `resolution` |
| help | `Q-help-01` (shared inspiration), `Q-help-02` (tell me about your business) | kind `help` |
| insp | `Q-insp-01`..`05` | three example ideas per archetype, kind `inspiration` |
| nudge | `Q-nudge-02` | kind `nudge` (the first nudge is a message beside the same card; `Q-nudge-01` was removed) |

Besides cards: six greeting chips (`shared.yaml`) and six chips per archetype (36 in all), 84 canned UI-copy entries (including the Brief row copy and one `reason.<slot id>` assumption reason per slot), and 14 knowledge snippets (what we build, how it works with its two reviews, the Brief, preview, code download, privacy, after approval, cost, time, changes, unsupported types, languages, reference apps, save and resume). All carry `en`, `hi` and `hi-Latn`. Cost and time say "I do not have pricing details" and "you will see the plan before anything is built"; code download says "I will not guess". Nothing states a price, a duration or an ownership term.

### Tests

`tests/test_expr.py` (truth tables, rejection, injection strings, hypothesis fuzz and a reference model), `test_catalogs.py` (slot table, defaults, order, levels), `test_questions.py` (coverage, ids, sets and vocabulary, ask_if, themes, i18n, copy rules, chips, knowledge, language fallback, the simulated Guided and Enterprise paths), `test_ui_copy.py`.

<!-- END questions half -->

## Packs, recipes, themes, stack profile and policies

Written by `content-author-packs` (details in `docs/specs/domain-pack-format.md` and `docs/specs/policy-format.md`).

- **Packs** (`data/packs/<archetype>.yaml`, loaded by `mde_content.packs`): `load_pack(id)` is the full pack with every feature on; `select_features(id, features)` is the data half of B2 (it drops unselected features' screens, flows, operations, entities, integrations and the references left dangling, and prunes the `feature_parts` sidecar). With everything on, the packs expand to 34 (commerce), 35 (food delivery), 35 (booking services), 30 (internal workforce) and 31 (custom) screens, under the cap of 50. Pack navigation uses `@key` screen references (`PackNavNode`); B2 rewrites them to `SCR-###`.
- **Recipes** (`data/recipes.yaml`, 27 recipes): each supplies loading, empty and error states so screen blocks list only deviations.
- **Themes** (`data/themes.yaml`): six presets with light and dark token sets; `mde_content.themes` ships the WCAG contrast maths and a test asserts AA for every declared pair.
- **Stack profile** (`data/stack-profile.yaml`): capability matrix, development-build flags, unsupported types. No Expo SDK number is stored (pinned at build time).
- **Policies** (`data/policies/`): `platform-default`, `guided-default`, `enterprise-it-services-firm`, `regulated-bank`; `resolve_policy` merges `inherits` up to three levels.

## Bundle

`mde_content.bundle.load_bundle()` loads all of the above plus the question bank; `validate_bundle()` returns cross-file problems (archetypes agree across catalogs, packs and banks; the feature checklist of each archetype equals its pack's features; visual cards and theme candidates name real themes; compliance triggers name real domains and cards; policies resolve). `tests/test_bundle.py` asserts it is clean.

`data/policy_alternatives.yaml` (added with the discovery policy guard): for each banned-library alias set, the allowed alternatives, plus the region table and pattern and generic alternatives, all in en, hi and hi-Latn. Loaded by `mde_discovery.policy_guard`; no company names, nothing hardcoded in Python.
