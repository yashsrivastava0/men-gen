# mde_spec_builder

Step 4 of the Context Engine: build `APP_SPEC.md`. Principle (brief 8.4): **a valid baseline that the model only refines.** Code owns validity; the model only proposes, and code checks every proposal.

Public interface: `mde_spec_builder.engine.SpecBuilderImpl(gateway, *, clock=None, summary_loader=None, settings=None, specs=None, new_id_fn=new_id)` implements `mde_contracts.engines.SpecBuilderEngine.build(req, ctx) -> BuildResult`. It imports only `mde_core`, `mde_contracts`, `mde_prompts`, `mde_content` and `mde_spec_loader`; the gateway is passed in as a `GatewayClient`.

## Pipeline (spec 5)

| Step | What | Module | LLM |
| --- | --- | --- | --- |
| B1 | Freeze the inputs (Brief, slots, `answer_slots`, Research Note, policy) | `engine`, `slots` | no |
| B2 | Deterministic baseline from the Domain Pack | `baseline`, `pack_expand`, `rules` | no |
| B3 | Section writers C9a (screens, navigation), C9b (data, API, roles, integrations), C9c (design tokens) | `writers`, `context` | yes |
| B4 | C10 screen briefs in batches of 3 to 4 screens | `writers`, `context` | yes |
| B5 | Assemble: canonical order, research `affects`, build sequence | `engine`, `assemble` | no |
| B6 | The nine validators (`max_tokens = policy.limits.max_spec_tokens`) | `validators` | no |
| B7 | Critic C12: spec outline against the Brief | `critic` | yes |
| B8 | Repair C13, at most two rounds, else one question (`needs_answer`) | `repair` | yes |
| B9 | Build sequence, plan summary (C11), render, hash, Plan view | `engine`, `summary`, `plan_view` | C11 |

Progress goes out as `ctx.progress("build", step, label, done, total)` with plain labels ("Reading your approved brief", "Laying out the screens and data", "Drafting the screens, data and look", "Writing each screen" with batch counts, "Checking everything fits together", "Checking it matches what you asked", "Fixing small problems", "Putting the plan together"). `ctx.cancel.check()` runs before every step and every call; cancelling raises `CallCancelled` and no further call is made. Gateway calls use `budget_scope = ctx.session_scope` and `purpose = "build:<step>"`.

## The baseline (B2)

`build_baseline(req) -> AppSpec` is deterministic (same request, same bytes) and passes all nine validators for every archetype and feature selection (property tests over archetypes x feature subsets x sign-in methods x surfaces x payment models; no validator *error*; development-build flags stay warnings).

1. **Pack**: `idea.archetype` (unknown falls back to `custom`), `mde_content.packs.select_features(pack, scope.features)`; unknown feature ids are ignored; `scope.features` empty means pack defaults.
2. **Surfaces** (`scope.apps`): `main` is the first surface of the pack; `admin` is an alias of `business` (D-029) when the pack has no admin surface. The first resolved app is **built**; every other requested app becomes a backlog item ("... app", reason "separate app"), never screens. The sign-in screens (recipes of kind `auth`) belong to every app.
3. **Roles** (`users.roles`): the first role of the surface always stays; other roles stay when named (`ROLE-<name>`) or when they gate a screen of a selected feature (then an assumption line says so). With no sign-in only the first role stays. When the slot is unset all roles of the surface stay. Role-gated operations lose the roles that are gone (and go when none is left).
4. **Sign-in** (`auth.method`; `auth.sso: required` wins and means `sso`): `password`, `social`, `sso` replace the login (and sign-up) screens' recipe, sections, copy, checks and actions and add their operations and integration; one-time-code operations and integrations go; `none` removes the auth screens and every gate; `email_phone_code` and `guest_first` keep the pack. The variants are data in `data/rules.yaml`.
5. **Payments** (`pay.*`): `pay.model` `none` or `on_delivery` leaves out the online-payment features (a feature whose `feature_parts` own an `INT-payments*` integration); `both` adds a cash-on-delivery note. `pay.provider`: empty or `mock` is `mock`; a named provider is `adapter` with `needs_dev_build` from the Stack Profile capability; `real` only for `real:<provider>` **and** an Enterprise policy, never otherwise.
6. **Scope guard**: more screens than `policy.limits.max_screens` keeps phase one (primary flow first, then core, standard, extra features, then pack order); everything else (and screens left unreachable) goes to `AppSpec.backlog` grouped by feature. Proposals respect the same cap. Screens the person removed are also kept in the backlog (reason "removed at your request", route as the hint).
7. **Ids**: `SCR-###` in navigation order (`assemble.assign_screen_ids`), `FLW-###` in pack order, navigation renumbered `NAV-001...`; `@key` rewritten; `PackNavNode` becomes `spec.NavNode`.
8. **Design**: tokens of the theme (`design.direction`, else the brand finding's nearest direction, else the pack default) with `design.accent` (hex or a named colour), `design.mode` (`component_variants["appearance"]`), `design.nav_style`, brand colours / fonts / tone from the Research Note (unless its brand suggestion is excluded). An accent is nudged only as far as needed to keep WCAG AA (4.5:1) text contrast.
9. **App, behavior, acceptance, assumptions**: name (`brand.name`, else "My App"), purpose (`idea.summary`), audience (the Brief's "who uses it" row), language (`locale`); behavior from `behavior.offline|notifications|analytics`, `a11y`, deep links from detail routes, permission prompts from the integrations; compliance flags add flagged behavior, acceptance checks and assumption lines (and switch analytics off where the rule says so); **every assumed slot** is listed with a one-line impact; research additions are listed with sources and toggles (`included` is false for exclusions).
10. **`inherits` copy-in** (Enterprise): `header.inherits` lists the policy chain (`mde_content.policies.inheritance_chain`); banned libraries, allowed regions, data classification and SSO are copied into behavior (error conventions), acceptance checks and assumption lines so the file is self-contained.

Wording and the sign-in variants are data (`data/rules.yaml`, validated by `rules.Rules`), not code.

## Research handling (spec 4.3)

Research never edits the spec silently. For every suggestion of a usable Note (status `ok` or `partial`; `unavailable` has no content) `assumptions.research_additions` lists the title, kind, up to three sources, `verified`, `included` (false when its id is in `excluded_research_ids`; `default_included` is the flow's business: it puts default-off suggestions in the exclusions) and `affects` (ids it created or touched).

* **Pack features.** A suggestion whose `applies_to` is `scope.features:<pack feature id>` switches that feature on in **B2** (code, valid by construction) unless the person had already selected it. A bare `scope.features`, a section name, a slot id, null, or an id that is not a feature of the selected pack is ignored here. The feature's screens carry `source: research` + `research_ref`; its entities and operations carry `source: research`; its integrations both; all are listed in `affects`.
* **Other suggestions** go to the writers (C9a feature and pattern, C9b feature / integration / compliance, C9c brand and pattern) as `ResearchAdditionLite` summaries (trusted, no tools). The suggestions the baseline already realised are not asked again. Their proposals use `source: research` + `research_ref` and are dropped unless the reference is in the Note and not excluded.
* **Brand finding**: colours, fonts, tone and nearest direction feed the design unless every brand suggestion is excluded.
* **Toggles** (Gate 2, spec 3.6): switching an addition off removes exactly the items it created (`remove_item` of the ids in `affects` whose source is `research`, screens first, then operations, entities, integrations), clears its `affects` and marks it `included: false`; switching it on again re-runs only the writers for that addition. An addition that names a pack feature reshapes screens, data and navigation, so toggling it is a full rebuild (ids follow navigation order). The brand addition counts as a design change.

## Proposals (B3/B4)

`proposals.validate_and_apply(spec, proposals, *, research, excluded, limits, stack_profile, max_tokens, tokens_used, allow_real, strict) -> (spec, applied, dropped)`. Proposals are applied one at a time, in order; after each the cheap validators run (structure, reference graph, screen count, Stack Profile; the full nine run once at B6 and after each repair round, or after every proposal with `strict=True`) and a proposal that introduces a new error is rolled back. Dropped proposals carry a code and a plain reason and are logged; counts go to `BuildResult.proposals_applied` / `proposals_dropped`.

Every string is cleaned (whitespace, hidden characters), length-capped and secret-scanned (`sanitize`), so the file reads back as written (fuzz-tested).

| Kind | Needs | Dropped when |
| --- | --- | --- |
| `add_screen` | new `route`, library `recipe`, `name` (60), `purpose` (200); optional `target` (opens from), non-primary `flow`, `surface` | duplicate route, unknown recipe / target / flow / surface, primary flow, screen cap, bad route, secret, research problem |
| `rename_screen`, `set_screen_purpose` | `target` SCR, `name` / `purpose` | unknown target, empty, too long, secret |
| `set_screen_copy` | `target`, `pairs` (snake_case key, text <= 120, at most 12 keys per screen) | bad key, too long, secret (also `key: value` together), too many |
| `add_screen_action` | `target`, snake_case `name`, `pairs` `calls` (existing API) and/or `go` (SCR, NAV, back, close), `on_success` | unknown `calls` / `go`, neither given, duplicate name, more than 12 |
| `add_screen_section`, `add_screen_check`, `add_screen_state` | `target`, `items` / `name` + `pairs` | duplicates, bad names, caps (14 sections, 8 checks, 6 states) |
| `add_flow_screen` | non-primary `target` FLW, `items` SCR ids | primary flow, unknown flow / screen, nothing new |
| `add_entity`, `add_entity_fields` | `name`, `fields` (snake_case, valid type, `ref` to an existing entity; no enums) | duplicate entity / field, unknown ref, enum, > 12 new or 30 total fields |
| `add_api_operation` | `name`, `method`, `path`, existing entities and roles | duplicate id or method+path, bad method / path, unknown entity or role, bad auth |
| `add_role` | `name`, optional surface | duplicate, unknown surface, > 8 roles |
| `set_design_token` | `pairs` `colors_light.x` / `colors_dark.x` (hex), `radius.x` (0 to 48), `type_scale.x.size|weight|line_height` | unknown token, bad value, **contrast would drop below AA** (the whole proposal) |
| `add_imagery_rule`, `add_acceptance_check` | `items` | duplicates, caps |
| `add_integration` | `name`, `purpose`; always created as `mock` | duplicate, secret in notes |
| `set_integration_mode` | existing `target`, `mock|adapter|real` | `real` unless `allow_real`, adapter without a provider |
| `set_behavior_item` | `pairs` of a behavior field (list fields are appended) | `locale`, unknown field, duplicate |
| `fix_reference` | `target` (the holder), one pair: dangling id -> existing id of the same kind | the old id exists, the new one does not, other kind, holder does not mention it |
| `remove_item` | SCR / FLW / ENT / API / ROLE / INT id | primary flow and its screens, sign-in screens, the first screen, the first role, screens with others beneath them in navigation, the last screen, and anything whose removal breaks a validator (rolled back) |

`source: research` additionally needs a `research_ref` that is in the Note and not excluded; what it creates is tagged `source: research` (+ `research_ref` on screens and integrations).

## Change requests and screen notes (spec 3.6, eval BUG-3)

What a person says about **one screen** reaches the spec through two inputs of `BuildRequest`; both go through `change_ops.apply_requests`, before the writers on a full build and before the C10 re-run on a patch. Nothing is edited without the checks of the proposals above or a deterministic, sanitized edit.

| Input | Becomes |
| --- | --- |
| `change_ops` kind `add_screen` (`instruction` = a few words, "photo gallery") | an `add_screen` **proposal** (name from the words, route `/photo-gallery` or `/screen-N` for another script, recipe from `rules.yaml` `change_ops.recipe_keywords`, else `detail`); the screen is marked `source: custom` and gets a C10 brief |
| `remove_screen` (`target_id`, or words in `instruction`) | a `remove_item` **proposal** (the main journey and sign-in stay); the removed screen is kept in `AppSpec.backlog` with reason "removed at your request" and its route as the hint, so a later build, full or patch, leaves it out too (it is not shown as "left for phase two") |
| `patch_screen`, `set_copy`, `other` with an `instruction` and no slot | the instruction is stored as the screen's `notes` (cleaned, at most 300 characters each and 600 per screen, secret-scanned; the same note twice is one note) and the screen is marked `source: custom` |
| `screen_notes` (`dict`: screen id, route or words -> text, from Discover) | the same note, on the matching screen |
| `set_slot`, `clear_slot`, `toggle_feature`, `toggle_research_item`, `other` with a slot (toggle assumption) | nothing here: slots, `scope.features` and `excluded_research_ids` already carry them |

A screen is named by id (`SCR-009`), route (`/cart`) or words ("the cart screen": the screen whose route and title words appear most in the text; a shorter route wins a tie; no match means no screen). **C10 sees the note**: `context.screen_draft` appends it to the screen's purpose line after `User detail:` (`ScreenDraftLite` has no notes field; the C10 prompt says to turn it into copy, a section, a check or an action; the offline handler adds one check from it) and the engine re-runs C10 for just those screens (a batch of one per screen). A request that cannot be done (no such screen, a duplicate route, the screen cap, a protected screen, a secret) is dropped with a recorded reason and one plain line (`rules.yaml` `change_ops.messages`) that heads the Plan view's list of changes (`PlanDiff.lines`); it never fails the build.

`plan_build` treats `screens` / `navigation` as covered when the build carries screen-level requests (the flow reports them for `add_screen`, `remove_screen`, `patch_screen`, `set_copy`), so a note after hand-off is a **patch**: C10 for that screen, the critic and the summary, no C9a/b/c. On a **full** rebuild with a `previous_spec` the earlier version's requests are replayed on the new baseline first (`change_ops._carry_over`: removed routes, notes on screens marked `custom`, screens the person added, matched by route).

## Calls

`C9a.screens_nav`, `C9b.data_api_roles`, `C9c.design_tokens`, `C10.screen_briefs`, `C12.critic`, `C13.repair` (specs in `mde_prompts.calls`, version 1, route standard, C13 effort low, the rest medium; max output tokens 1800 / 1800 / 1000 / 2500 / 1400 / 1800). No tools, no untrusted inputs; system prompts are one job and short ("propose only additions or edits justified by the Brief or the Research Note; reference ids from the baseline; do not restate unchanged items"). C10 gets only its batch's recipes, entities, operations and a few neighbouring screens, and the detail a person gave about a screen (after `User detail:` in that screen's purpose line). C11 (`plan_summary`) is the discovery engineer's spec: it is loaded lazily with `load_spec("C11.write_summary")`.

## Fallbacks

Every call has a deterministic fallback (`builder.no_proposals`, `builder.critic_pass`, the template summary). `LlmRunner.call` returns `None` for any gateway failure, so `BudgetExceeded`, `CircuitOpen`, `StructuredOutputError` and the like never fail a build; after the first `BudgetExceeded` or `CircuitOpen` no further call is made. C10 retries a batch once before its fallback. Only `CallCancelled` (and real bugs) propagate. With every call failing the result is the baseline with the template summary (`status: ok`).

## Critic and repair (B7/B8)

C12 sees the outline, the Brief and the validator findings; its findings (at most five, clean messages, real ids) are advice. C13 gets the error findings (validator then critic) with the YAML of the items they point at and returns proposals that go through the same `validate_and_apply`; the loop runs at most **two** rounds, re-validating after each (and re-running the critic when it had errors). If errors remain, `repair.derive_question` builds **one** plain `choice3` `CardInstance` (options A recommended, B, C; texts and `sets` in `rules.yaml`, `$core_features` / `$default_features` resolved from the pack) from the first failing check (`limits`, `stack_profile`, `no_secrets`, otherwise the general question) and the build returns `status: "needs_answer"` with `question`, `question_slot`, the draft `spec` and the findings. The flow shows the card, writes the slot and calls again with `answer_slots`; only the baseline and the affected writers run.

## Incremental builds (spec 3.6)

With `previous_spec` and `affected_sections`:

| Sections | Rebuilt | Writers |
| --- | --- | --- |
| `design` | design tokens (+ app tone and name) | C9c |
| `roles` | roles, navigation gates, sign-in screens and their operations | C9b, C10 (sign-in screens) |
| `integrations` | integrations | C10 (screens mentioning them) |
| `behavior` | behavior (+ every screen when the language changed) | C10 only then |
| `all`, `screens`, `navigation`, `data`, `api` | everything | all (`screens` / `navigation` alone are a patch when the build carries screen-level change requests, see above) |
| `[]` | research toggles only | see Research handling |

A patch grafts the new baseline's blocks onto the previous spec and keeps everything else byte-identical (tested: the other blocks read the same; `revision.changed_ids` lists what changed). It falls back to a full build when the baseline structure differs from the previous one. **Structure** (`incremental.structure_matches`) reads both sides the same way: the new baseline's routes (research feature screens included, because it was built with the same research selection and exclusions) against the routes of the previous spec's *baseline-owned* screens (`is_baseline_screen`: source `default`, source `research` tagged with the same suggestion as the baseline's screen, source `custom` at a baseline route), minus the pack screens the person removed. Screens that proposals or change requests added play no part, so research switching a pack feature on no longer turns every patch into a full rebuild (eval BUG-5), and neither does a note on a pack screen. `graft_roles` re-gates research feature screens like the pack's. Design and behavior patches skip the critic; the plan summary is kept for design-only changes. `incremental.sections_for_slots` is the slot-to-section dependency map of spec 3.6 for the flow.

## Plan view and diff

`plan_view.build_plan_view` takes the Gate 1 first draft (`req.gate1_spec`) and its ids (`gate1_spec_ids`). **Both sides of the diff are read at the same granularity** (eval BUG-4): item ids (`SCR-###`, `FLW-###`, `ENT-*`, `API-*`, `ROLE-*`, `INT-*`), from the first draft itself when it is given (`assemble.spec_ids`), else from `gate1_spec_ids` reduced to item ids, so the block ids the flow records (`APP`, `DESIGN`, `NAV` ...) never show up as "Removed item". `PlanDiff.changed_ids` compares the canonical text of every item present in both drafts and, for the singleton blocks that have no items of their own, the block itself with one plain line: `APP` "Changed the app's name or description", `DESIGN` "Changed the look", `BEHAVIOR` "Changed how the app behaves". Lines read "Added screen: Reviews", "Removed screen: Reviews" (named from the first draft when it is known, else the id), "Changed screen: Your cart"; without a first draft only added and removed ids are known. Plain lines for requests that could not be done (`notes`) come first and are never cut; they alone make a diff even when there is no baseline. Compliance pointers are in `PlanView.compliance`, never mixed into `research_note`. Screens are titled by `ScreenBlock.title` first.

## Mock behaviour

All six calls ship deterministic, schema-valid mock handlers and golden fixtures (`input.json`, `output.json`, `invalid_proposals.json`) next to the spec. C9a proposes up to two list screens from feature additions the baseline did not realise; C9b adds a field or two grounded in the Brief (sensitive areas, regions, language) and mock integrations from integration additions; C9c nudges at most the corner radius from the brand tone and a readable accent from a brand colour; C10 adds an `intro` line, a `help_hint` section and one check per screen (only what is missing); C12 passes unless there are validator errors, no screens, no journey (errors) or a Brief feature nothing mentions (warning); C13 points a dangling id at the closest existing id or removes an added item. A mock build of each archetype produces a valid `APP_SPEC.md` that parses back to the same `AppSpec` and passes the consumer simulator.

## Settings (`BuilderSettings`)

`concurrency` (1: strictly sequential; a few calls at once never change the order results are applied), `c10_batch_size` (4), `c10_retries` (1), `c10_batch_limit` (None), `repair_rounds` (2, never more), `critic` (True), `summary_words` (60), `strict_proposals` (False).

## Performance

A build validates the spec after every proposal (`proposals.quick_findings`), so the `stack_profile` validator once re-scanned every screen against every capability keyword each time and took about 40% of a build's CPU. `validators.capabilities` now compiles each capability's terms into one regex per `StackProfile` and remembers which capabilities each text mentions (a bounded per-text cache), so a pass over an unchanged screen is a dictionary lookup: 100 passes over 50 screens take a few hundredths of a second instead of about 1.8 s. Findings are identical (tested against the original scan on real and sample specs); a timing guard test (`test_stack_profile_perf.py`) keeps it from regressing.

## Tests

`uv run pytest packages/spec-builder packages/prompts`. Gateways for tests live in `packages/spec-builder/tests/helpers.py` (`DirectMockGateway` runs the spec's mock handler, `ScriptedGateway` answers by call id and records every `CallContext`).
