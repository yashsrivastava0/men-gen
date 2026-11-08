# Domain Pack format

A Domain Pack is the data the Spec Builder expands into a valid baseline spec (step B2, [BRIEF 8.4](../implementation/BRIEF.md), [spec section 5](../product/context-engine-spec.md)). One file per archetype lives in `packages/content/src/mde_content/data/packs/<archetype>.yaml`. It is validated by `mde_contracts.content.DomainPack`; the loader and validator are `mde_content.packs`. The recipes a pack refers to are in [`data/recipes.yaml`](#recipes), the looks in `data/themes.yaml`.

Packs shipped: `commerce`, `food_delivery`, `booking_services`, `internal_workforce` and `custom` (a generic pack built on the base recipes, used when nothing else fits). With every feature on, a pack expands to 30 to 35 screens (the guided cap is 50).

Nothing in a pack belongs to one company or app type. A pack describes an archetype; the Brief, the research note and the section writers turn it into a specific app.

## File layout

```yaml
id: commerce                  # equals the archetype id
archetype: commerce
label: Online store
description: ...
theme_candidates: [clean-bright, bold-dark, warm-friendly]   # the three look cards, recommended first
default_theme: clean-bright   # equals theme_candidates[0]
features:   [...]             # PackFeature
roles:      [...]             # Role        (ROLE-...)
enums:      [...]             # EnumDef
entities:   [...]             # Entity      (ENT-...)
api_operations: [...]         # ApiOperation (API-...)
integrations: [...]           # Integration (INT-..., mode mock by default)
compliance_triggers: [...]    # ComplianceTrigger
screens:    [...]             # PackScreen
flows:      [...]             # PackFlow
navigation: {...}             # one NavNode tree, screens referenced as "@key"
feature_parts: {...}          # sidecar, see "Feature gating"
```

### Features

`id`, `label`, `description`, `tier` (`core`, `standard`, `extra`), `default_on`, `i18n.hi` (Hindi label). The feature ids are fixed because the question bank offers exactly these in the `scope.features` checklist; the pre-ticked set must equal `default_on` (a test compares them with the question bank table in `docs/packages/content.md`).

| Tier | Meaning | `default_on` |
| --- | --- | --- |
| `core` | always on; cannot be unticked | true |
| `standard` | ticked by default | true |
| `extra` | offered, not ticked | false |

| Archetype | Feature ids |
| --- | --- |
| `commerce` | catalog_browse, search, cart_checkout, payments, order_tracking, reviews, favorites, push_notifications, loyalty, support_chat |
| `food_delivery` | restaurant_browse, menu_item_options, cart_checkout, payments, live_order_tracking, ratings, saved_addresses, push_notifications, promo_codes, support |
| `booking_services` | service_catalog, provider_profiles, slot_booking, payments, reminders, reviews, reschedule_cancel, push_notifications, memberships, support |
| `internal_workforce` | task_lists, forms_checklists, approvals, timesheets, directory, announcements, offline_sync, push_notifications, kiosk_mode, reports |
| `custom` | list_detail, search, forms, auth_profile, notifications, settings, media_gallery, maps, payments_mock |

### Roles, surfaces and apps

A **surface** is who a screen is for. Surfaces are the app ids of the `scope.apps` slot: `customer`, `business`, `courier`, `admin`, `employee`. The first role and the first screen of a pack are the **main app** (`scope.apps` defaults to `["main"]`, meaning that surface). Other surfaces are separate apps and only expand when chosen. Role gates inside one app (a manager in the same app as employees) use role ids, not a second surface: both roles then share a surface.

Role ids use the `ROLE-` prefix. The vocabulary of the `users.roles` slot maps to `ROLE-user`, `ROLE-staff`, `ROLE-admin`, `ROLE-employee`, `ROLE-manager`; `internal_workforce` defines the employee, manager and admin roles and `custom` the user, staff and admin roles. A screen with `auth: ROLE-x` sits on the same surface as that role; a test enforces it.

### Entities, enums, operations

- Every entity has an implicit string `id` that is **not** listed in `fields`. Relations use `type: ref` with `ref: ENT-...`.
- Money is `type: money` (integer minor units downstream), time worked is whole minutes. `type: number` is only for ratings and coordinates; a test rejects it elsewhere.
- Entities carry a plain-language `plain_name` (shown at Gate 2) and, where it helps, `seed_guidance` and a state machine. In YAML write the transition event key as `"on"` (quoted), because an unquoted `on` is a boolean in YAML 1.1.
- API operations are skeletons: id, method, path, request and response entity, `response_many`, `auth` (`public`, `authenticated` or `ROLE-x`, with the role in `roles`). Paths use `{id}` placeholders. Card or account numbers are never fields: payment methods hold a display label only.
- Integrations default to `mode: mock` and `needs_dev_build: false`. A real provider, and what it needs, is written in `notes`. Payments are always mock by default.
- Compliance triggers name a sensitive domain (`health`, `finance`, `children`, `identity_documents`, `precise_location`), keywords, the must-confirm card (`Q-comp-01` to `Q-comp-05`), the conservative default (`compliance.flags` set to the flag id: `health_data_privacy`, `financial_data`, `child_safety`, `identity_documents`, `precise_location`) and a `research_hint` topic for R4. Food delivery and booking trigger `precise_location`.

### Screens

```yaml
- key: product_detail            # local key, snake_case, unique in the pack
  route: /product/[id]           # unique; navigation nodes repeat it
  recipe: detail-with-sticky-cta # a recipe id from the library
  purpose: Show one product and let the shopper add it to the cart
  surface: customer              # default customer
  auth: public                   # public | authenticated | ROLE-x
  feature: catalog_browse        # included only when this feature is on
  data:    [{bind: "API-getProduct(id)", show: [name, price]}]  # API-x(...) or ENT-x.field
  sections: [gallery, title_price, sticky_cta]                  # snake_case
  actions: [{name: add_to_cart, calls: API-addCartItem}, {name: view_cart, go: "@cart"}]
  states:  {out_of_stock: {disable: add_to_cart}}               # only deviations from the recipe
  texts:   {cta: "Add to cart"}                                 # copy keys, snake_case
  checks:  [one to three testable sentences]
```

Rules enforced by `validate_pack`: every screen has a recipe from the library, a purpose, one to three checks, and does something (data, actions or sections). Every action either calls an operation or goes to a screen. A public screen may not read data from an operation that needs sign-in. An action on a public screen may call an `authenticated` operation (the app asks the person to sign in first); role-gated operations need the same role on the screen.

Loading, empty, error and offline states come from the recipe. A screen lists only deviations in `states`.

### The `@key` convention

Screens refer to each other by local key: `go: "@cart"`, navigation `screen: "@cart"`, flows list bare keys. B2 assigns `SCR-###` ids and rewrites every `@key`; flows get `FLW-###`. Entities, operations, roles and integrations already carry final ids and are copied as they are. A navigation node's `route` equals its screen's `route`.

### Flows

`key`, `name`, `primary`, `screens` (ordered keys), optional `feature` and `purpose`. Every pack has exactly one primary flow (the vertical slice, build step S5) of at least four screens that uses only always-on screens. Consecutive screens in a flow are linked by an action (`go: "@next"`), so a flow is a path a person can actually walk.

### Navigation

One `NavNode` tree: an `auth_gate` root, a `stack` for the signed-out screens, a `tabs` node of three to five tab roots, and one stack per extra surface. Each tab root is a `stack` node whose `screen` is the stack's first screen and whose children are the screens pushed inside that stack. Node ids are `NAV-###`. `auth` on a node is `public`, `authenticated` or a role.

Every screen must be reachable: listed in the tree, or the target of an action on a reachable screen.

## Feature gating

`feature` on a screen or flow includes it only when the feature is selected (`scope.features`; core features are always on). The contract cannot gate anything else, so a pack carries a sidecar, `feature_parts`, listing what a feature owns beyond the screens and flows that name it:

```yaml
feature_parts:
  reviews:
    operations: [API-listReviews, API-createReview]
    entities: [ENT-Review]
    integrations: []
    screens:
      product_detail: {sections: [reviews_preview], actions: [], data: []}  # parts of an always-on screen
```

Core features own nothing in `feature_parts` (they are never removed). A pack must never let an always-on entity point at an entity owned by an optional feature, and the validator expands the pack for the core-only selection, the defaults, every single feature off and every single feature on, and fails on any dangling reference.

`mde_content.packs.select_features(pack_id_or_loaded, features)` implements the data half of B2 and is the reference for the rules below.

## How B2 expands a pack

1. Resolve the feature selection: `features=None` means pack defaults; core features are added; an unknown id is an error.
2. Drop screens and flows whose `feature` is off. Drop the parts that off features own (operations, entities, integrations, sections, actions and bindings listed in `feature_parts`).
3. Prune everything left dangling: actions that go to a removed screen or call a removed operation, bindings to a removed operation or entity, navigation nodes whose screen was removed (with their subtree) and then empty tabs or stacks, enums no entity uses any more, flows left with fewer than two screens (a primary flow is kept while it has a screen).
4. Keep only the surfaces of the chosen apps; role-gated screens need their role to be selected.
5. Assign ids in pack order: `SCR-###` for screens, `FLW-###` for flows, rewrite `@key` everywhere, and build the spec blocks (navigation, data, api, roles, integrations, screens, flows). Theme tokens come from the chosen theme in `themes.yaml`. The result passes the nine validators of spec 6.4 by construction.

## Recipes

`data/recipes.yaml` holds 27 recipes (`mde_content.recipes.load_recipes()`, `recipe(id)`). A recipe has an `id`, `kind`, the `states_provided` (loading, empty, error, offline), `default_sections` and the `kit_components` it uses (PascalCase names from the component kit). The set: welcome, login-otp, login-password, login-sso, signup, profile, settings, list, list-with-filters, approvals-queue, detail, detail-with-sticky-cta, form, multi-step-form, checklist, calendar-slot-picker, search, notifications, dashboard, checkout-review, confirmation, tracking, empty-state-only, chat, map-view, media-gallery, kiosk-home.

## Themes and the Stack Profile

Six themes (`clean-bright`, `bold-dark`, `warm-friendly`, `minimal-mono`, `vibrant-playful`, `corporate-calm`) each carry light and dark palettes, type scale, spacing, radius, elevation and motion. Every pair in `contrast_pairs` passes WCAG AA (4.5:1) in both modes; `mde_content.themes.contrast_ratio` does the math and a test checks it. The Stack Profile (`data/stack-profile.yaml`, `mde_content.stack`) is the capability matrix with development-build flags and the unsupported types with English, Hindi and Hinglish keywords; no SDK version number is stored. `match_unsupported(text)` returns the unsupported types a text mentions.

## Adding or extending a pack

This follows the `add-domain-pack` skill.

1. Choose the archetype id (snake_case, unique) and add `data/packs/<id>.yaml`. Add its entry to the catalogs and the question bank (the questions half) so the checklist offers exactly your feature ids, with the same pre-ticked set.
2. Write features (one or two core, the rest standard or extra), roles, enums, entities, operations, integrations, compliance triggers, screens, flows, navigation and `feature_parts`. Use only recipes from the library; if none fits, add a recipe first.
3. Run `uv run pytest packages/content`. The pack tests run `validate_pack` on the full pack and on every feature selection, and check the screen count (25 to 45, cap 50), the primary flow, the feature ids, themes, flag and card ids and the round trip through the contract models.
4. Check the Stack Profile: anything the pack needs that is not in the capability matrix, or that needs a development build, must be added or flagged there.
5. Add the golden brief and the mock research note for the archetype (`evals/datasets/briefs/`, `packages/prompts`), then run `uv run pytest packages/spec-builder -k baseline`.

### YAML pitfalls

Quote hex colors (`"#FFFFFF"`), `"@key"` references, routes containing `[id]` or `{id}` inside flow mappings, and any text containing a comma, a colon followed by a space, or a brace inside `{...}`. Quote the `"on"` key of state transitions. Write files with LF line endings.

## Notes on the contract

Two limits of the v1 contract were found while writing the packs and were fixed by [ADR 0003](../adr/0003-pack-navigation-and-feature-parts.md) (contracts 1.0.1):

- `DomainPack.navigation` is a `PackNavNode` tree (`screen` is an `@key`); the builder expands it into real `NavNode` objects with `SCR-###` ids in B2.
- `DomainPack.feature_parts` carries the feature sidecar. `load_pack_file(id)` still returns a `LoadedPack(pack, feature_parts)` wrapper for older callers (`feature_parts` mirrors `pack.feature_parts`); `load_pack(id)` returns the plain `DomainPack`.
