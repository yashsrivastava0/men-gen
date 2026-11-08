# `APP_SPEC.md` file format

The single output of the Spec Builder and the single input of the frontend engine (spec section 6). Markdown for people, fenced YAML blocks for machines. This page is normative for the file; the models live in `mde_contracts.spec`, the renderer in `mde_spec_builder.render`, the reader in `mde_spec_loader` ([package page](../packages/spec-loader.md), [how the frontend engine consumes it](frontend-consumption.md)).

Guarantees:

- **Deterministic.** The same `AppSpec` always renders to the same bytes. No timestamps, random ids or machine-specific text appear in the file.
- **Self-checking.** The file embeds its own sha256; `verify` recomputes it.
- **Round trip.** `parse_spec(render_spec(spec)) == spec` (the spec is compared with `header.hash` set to the rendered hash; see the hash rule).
- **Addressable.** Every block, and every entity, operation, role, integration, navigation node and build step inside a block, has an id the loader can fetch on its own.

## 1. Layout

```
---                                   front matter (block HEADER)
spec_version: 7 ... hash: "..." ...
---

# <app name> - APP_SPEC                title and a one-line read-only notice

## 0. Header and index                 table of sections: id(s) and ~tokens
## 1. Summary                          plain-language PRD view (prose)
## 2. App          ... ## 15. Revision one section each, fixed order
```

The section order is fixed (`mde_contracts.spec.SECTION_ORDER`). Section 0 is the head of the file (front matter, title, table); every other section starts with the heading line `## <n>. <title>`.

| # | Section | Block ids | Block kind | Contents |
| --- | --- | --- | --- | --- |
| 0 | Header and index | `HEADER` | `header` | Front matter; table of sections with ids and approximate token sizes |
| 1 | Summary | `SUMMARY` (prose, not a block) | | Purpose, users, features by flow, the look, what is mocked, what was assumed |
| 2 | App | `APP` | `app` | Name, purpose, audience, platforms, tone, language |
| 3 | Design | `DESIGN` | `design` | Tokens (colours light and dark, type scale, spacing, radius, elevation, motion), direction, imagery rules, component variants |
| 4 | Navigation | `NAV` | `navigation` | Route tree to screen ids, including auth gates |
| 5 | Data | `DATA` | `data` | Entities, fields, enums, state machines, seed-data guidance |
| 6 | API | `API` | `api` | Operations: id, method, path, request and response entities, auth, roles; mock guidance |
| 7 | Roles | `ROLES` | `roles` | Roles and permissions per screen and operation |
| 8 | Integrations | `INTEGRATIONS` | `integrations` | Each with purpose, mode (`mock`, `adapter`, `real`), provider, notes |
| 9 | Behavior | `BEHAVIOR` | `behavior` | Offline, locale, deep links, notifications, analytics events, permission prompts, error conventions, accessibility |
| 10 | Screens | `SCR-###` (one per screen) | `screen` | One block per screen, each under a `### SCR-014 - /route` heading |
| 11 | Flows | `FLW-###` (one per flow) | `flow` | Ordered journeys, each under a `### FLW-001 - Name` heading |
| 12 | Build sequence | `BUILD` | `build_sequence` | Steps S0..S10 ([consumption](frontend-consumption.md)) |
| 13 | Acceptance | `ACCEPT` | `acceptance` | Global checks (per-screen checks live in the screen blocks) |
| 14 | Assumptions and sources | `ASSUME`, `BACKLOG` (only when non-empty) | `assumptions`, `backlog` | Assumed items with impact; research additions with toggles and sources; the Phase 2 backlog |
| 15 | Revision | `REV` | `revision` | Version, previous version, changed ids |

The Phase 2 backlog (`AppSpec.backlog`) has no section of its own in the contract; it is rendered as an extra block `id=BACKLOG kind=backlog` (body `items: [...]`) at the end of section 14.

## 2. Front matter

```yaml
---
spec_version: 7
schema: mde.app-spec/1          # SpecHeader.schema_id
app_id: app_01JABC...
mode: guided
stack_profile: expo-managed
inherits: []                    # Enterprise policy profiles copied in (the file is self-contained)
language: en
hash: "<64 hex>"                # always present, always written as hash: "..."
brief_hash: <64 hex>
research_note_hash: <64 hex>    # omitted when there was no Research Note
---
```

The front matter is the `HEADER` block. Keys are always written in this order. The `hash:` line is the only line that depends on the rest of the file.

## 3. Blocks

A block is a fenced YAML block whose info string carries an id and a kind:

````
```yaml id=SCR-014 kind=screen
route: /product/[id]
...
```
````

- The opening fence is at column 0 and reads `` ```yaml id=<ID> kind=<KIND> ``; the closing fence is a lone `` ``` `` at column 0. YAML written by the renderer can never contain a line that starts with a backtick at column 0, so a fence is unambiguous.
- Singleton blocks use the fixed ids of `SINGLETON_BLOCK_IDS` (`APP`, `DESIGN`, `NAV`, `DATA`, `API`, `ROLES`, `INTEGRATIONS`, `BEHAVIOR`, `BUILD`, `ACCEPT`, `ASSUME`, `REV`, plus `BACKLOG`). Screens are `SCR-###` and flows `FLW-###` (three digits, zero padded, assigned in navigation order).
- A block's id is unique in the file. A `screen` or `flow` body omits `id`: the fence carries it (a body `id` that disagrees with the fence is an error).
- Anything between blocks (headings, the table, the summary) is prose. A fenced block without `id=`/`kind=` is prose too and hides headings inside it.

### Items inside blocks

Some ids live inside a block. The loader can return each on its own (`get`), which is how a screen task receives only the entities and operations it needs instead of the whole `DATA` and `API` blocks.

| Id shape | Item | Lives in | Fragment kind |
| --- | --- | --- | --- |
| `ENT-Order` | entity | `DATA` | `entity` |
| `API-getProduct` | operation | `API` | `operation` |
| `ROLE-admin` | role | `ROLES` | `role` |
| `INT-payments` | integration | `INTEGRATIONS` | `integration` |
| `NAV-003` | navigation node (without its children) | `NAV` | `nav` |
| `S5` | build step | `BUILD` | `build_step` |

Id patterns: `SCR-\d{3}`, `FLW-\d{3}`, `ENT-[A-Za-z][A-Za-z0-9]*`, `API-[A-Za-z][A-Za-z0-9]*`, `ROLE-[a-z][a-z0-9_]*`, `INT-[a-z][a-z0-9_]*`, `RA-\d{3}` (research additions), `S0`..`S10`, `NAV-###`.

## 4. Canonical YAML

Implemented once, in `mde_spec_loader.codec` (dump and load), so the renderer and the loader cannot drift.

1. **Field order** is the declared order of the model fields.
2. **Omitted keys.** A field that is `None` or equal to its model default is not written; the reader fills the default back in. Raw-YAML consumers must apply the model defaults (`mde_contracts` JSON Schemas list them). Required fields are always written, even when empty.
3. **Free-form maps are key-sorted** (colour tokens, `copy`, `states`, elevation, motion, ...), so two equal specs have equal bytes whatever order they were built in. Lists keep their order: it is meaningful (navigation order, flow order, steps).
4. **Style.** Short leaf mappings and scalar lists use flow style (`sections: [gallery, title_price]`, `{id: S1, name: Theme}`); anything longer or nested uses block style, and a list of mappings uses one style for all its items. Multi-line strings use `|` literal blocks. Sequences are indented under their key. No anchors or aliases. `allow_unicode`; LF newlines; plain `\x85` (NEL) is always escaped (PyYAML would otherwise fold it into a space).
5. **Three renames** between model and file: a screen's `texts` is the key `copy`; a state-machine `Transition.source` is `from`; the header's `schema_id` is `schema`.
6. **Compact actions.** A screen action is written as a one-key mapping: `- add_to_cart: {calls: API-addCartItem, on_success: toast + cart badge}`. The expanded form (`- {name: add_to_cart, calls: ...}`) is also accepted on read.
7. **Body ends with one newline**, which is part of the body (a string value ending in a line break must survive).
8. The emitter is PyYAML's pure-Python `SafeDumper` (not the C emitter) so bytes are identical on every machine.

### Screen block (example)

```yaml id=SCR-003 kind=screen
route: /product/[id]
recipe: detail-with-sticky-cta
purpose: Show one product and let the shopper add it to the cart
flow: FLW-001
data:
  - bind: API-getProduct(id)
    show: [name, images, price, rating, description, stock]
  - bind: API-listReviews(productId)
    show: [rating, body]
sections: [gallery, title_price, description, reviews_preview, back_button]
actions:
  - add_to_cart: {calls: API-addCartItem, on_success: toast + cart badge}
  - see_reviews: {go: SCR-018}
states:
  out_of_stock: {disable: add_to_cart, show: notify_me}
copy: {cta: Add to cart, retry: Try again}
checks:
  - Add to cart increments the cart badge
  - An out-of-stock item disables the button
```

Loading, empty and error states are not repeated: the recipe supplies them and only deviations appear.

### Reference semantics

These are what the validators check ([validators](#7-validators)) and what the loader's graph follows.

- **Data binding** (`ScreenBlock.data[].bind`): `API-name` or `API-name(arg, ...)`, or `ENT-Name` / `ENT-Name.field[.path]`. `show` entries of an operation binding that look like plain field names must exist on the operation's response entity (a warning otherwise).
- **Action** (`ScreenAction`): `calls` is an operation id; `go` is a screen id, a navigation node id (a sheet or modal), a route that equals a screen's route (`/cart`), or `back` / `close`. An `on_success` that is exactly a screen id is a navigation too; any other `on_success` is free text. An action with neither `calls` nor `go` is a gap and an error.
- **Auth** (`auth` on screens, operations and navigation nodes): `public`, `authenticated`, a `ROLE-x` id, or a list of those separated by `,` or `|`.
- **Mentions.** An id written in the free text of a screen, flow, operation or integration (`notes: "Pays through INT-payments"`) is recorded as a reference when it exists, so it ends up in the slice.

## 5. Summary section

The summary is prose under `## 1. Summary`. To keep the file parseable whatever the text is, a summary line that looks like a section heading (`## 3. ...`), starts with a fence (```` ``` ```` or `~~~`) or starts with a backslash gets one extra leading backslash when written and loses it when read. Newlines are normalised to LF and leading and trailing blank lines are dropped (`mde_spec_loader.normalize_summary`).

## 6. Section table, token estimates and the hash

**Section table.** Section 0 lists every section with its block id(s) and `~Tokens`. The size of section *n* (1..15) is the estimate of its exact text from its heading line to the next heading; the size of section 0 is the estimate of the front matter block alone with the hash blanked (the table cannot contain its own size). `SpecIndex.index()` computes the same numbers from the file.

**Token estimate.** `ceil(UTF-8 bytes / 4)` (`mde_spec_loader.estimate_tokens`). For ASCII this is exactly characters / 4; non-Latin text is not under-counted. No tokenizer library, so it is identical everywhere.

**Hash rule (normative).** `header.hash` is the sha256 (hex, UTF-8, LF newlines) of the whole file text with the front-matter line `hash: "<hex>"` replaced by `hash: ""`. The rendered file embeds the result as `hash: "<64 hex>"`.

- `render_spec(spec)` ignores whatever `spec.header.hash` holds and embeds the computed hash; `BuildResult.spec_hash` and `SpecVersionRecord.hash` equal it (`mde_spec_builder.render.spec_hash(spec)`).
- `with_rendered_hash(spec)` returns the spec with `header.hash` set, so the parsed file equals it exactly.
- `compute_spec_hash(text)`, `embed_hash(text)` and `verify_hash(text, expected=None)` (renderer re-exports of `mde_spec_loader.hashing`). A CRLF checkout or a leading BOM does not change the result (text is normalised to LF first). A blank, missing or wrong hash never verifies.
- Only the `hash:` line inside the front matter is special; the same text elsewhere is content.

## 7. Validators

`mde_spec_builder.validators.run_validators(spec, stack_profile=..., limits=...)` runs the nine checks of spec 6.4 before the file is accepted. Each returns `ValidatorFinding(validator, severity, target_ids, message)`; `error` blocks the spec, `warn` and `info` do not.

| # | Validator | Checks |
| --- | --- | --- |
| 1 | `schema` | Unique ids; header schema and revision version; data-model integrity (enum and entity references, state machines, hex colours); research and build-sequence references; every block reads back exactly as written after render and parse |
| 2 | `navigation_targets` | Every navigation target exists; screen entries name a screen; routes are unique; **every screen is reachable** from the navigation tree or an action `go` |
| 3 | `data_bindings` | Bindings parse and name a real operation, entity and field; operations' request and response entities exist; entity `ref` fields resolve; `show` fields exist (warning) |
| 4 | `action_targets` | Every action calls a real operation and/or goes to a screen, sheet, route, `back` or `close` |
| 5 | `role_gates` | Every `auth` value, operation role and permission names a defined role, real screens and real operations; screens on a surface that no role has (warning) |
| 6 | `flow_endpoints` | Every flow starts and ends on a real screen that belongs to it and lists only real screens; a screen's `flow` exists; primary flow count (warning) |
| 7 | `limits` | `len(screens) <= limits.max_screens`; file within a token budget (default 60 000, warning from 80%); one screen block over 1 500 tokens (warning) |
| 8 | `stack_profile` | Capabilities mentioned by integrations, behavior and screens are supported by the Stack Profile (error if not); those needing a development build are flagged (warning); the header names the profile |
| 9 | `no_secrets` | No pattern of `mde_core.redact.find_secrets` in any block or the summary (findings name the pattern and the block, never the value) |

## 8. Producing and reading the file

| Task | API |
| --- | --- |
| Render, hash | `mde_spec_builder.render.render_spec`, `spec_hash`, `with_rendered_hash` |
| Assign ids, graph | `mde_spec_builder.assemble` (`assign_screen_ids`, `renumber_screens`, `reference_graph`, `spec_ids`, `changed_ids`) |
| Build sequence | `mde_spec_builder.sequence.build_sequence` |
| Plan view | `mde_spec_builder.plan_view.build_plan_view` |
| Validate | `mde_spec_builder.validators.run_validators` |
| Sample app for tests | `mde_spec_builder.testing.make_sample_spec(n_screens, seed=...)` |
| Parse, index, slice | `mde_spec_loader` ([package page](../packages/spec-loader.md)) |

Writing the file to disk: write the text as UTF-8 with LF newlines (`newline="\n"`); the repo's `.gitattributes` keeps it that way.

## 9. Errors on read

`SpecParseError` (with a line number when known) for: no front matter; an unclosed fence or front matter; a fence with only one of `id=` and `kind=`; section headings out of order; a duplicate block id; an unknown block kind; a block id that does not belong to its kind; invalid YAML; a block that fails its model; a missing required block or summary section. `UnknownIdError` for an id that is not in the file.
