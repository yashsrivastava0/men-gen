# What Spec gets from Research, and how to read it

Reader: the Spec lanes (Blueprint, Integrations, Design, Screens, Mock, Verify) and, through Spec, the engine that writes an Expo / React Native app. Nobody reads `research.json` by eye, so every finding is exact, typed, sourced and tied to a Brief id. This note is the contract: what Research guarantees, how to use each part, and the reference function `pack(research, need_card)` (`agents/research/src/mde_research_agent/pack.py`) that selects what one Spec call should read. Design basis: doc 13 sections 9 and 21; decisions D-159 and D-162. The file stays `research.v5`; everything described here as new is additive.

## What Research guarantees

1. **Every item has a Brief link or `NEW`.** `applies_to` is never empty (RV-28); every id in it exists in the Brief or is `NEW` (RV-16). A provider fact also names the integration need it serves (`INT:payments`). Selecting by `applies_to` therefore cannot miss an item that applies.
2. **Every item has an evidence label**, computed by code from the sources and the check: `official`, `corroborated`, `single_source`, `unverified` (doc 13 section 9.4).
3. **Atoms are single facts.** Provider facts, platform constraints and compliance notes each carry one fact and one quote. Everything aggregated (dossiers, platform constraints list, compliance register, lane decisions) is built by code from verified atoms, so each value points at its items and carries their label.
4. **Unknown stays an explicit null with a reason** (R27). A dossier field that is empty says `not_stated_by_sources`, `not_researched` or `conflict`. A platform rule says `effect_on_build: null` unless the page states the effect. Nothing is filled from what is usual.
5. **Money is a string.** A typed value is `{amount, currency, unit, period, condition}`; `amount` is a decimal string that is a figure of the quote, `currency` is shown by the quote. Code rejects a fact whose amount or currency the quote does not back.
6. **Quotes never reach the file.** They stay in the internal evidence; Verify's drift check reads them there.

## Use rules (R21 to R27)

* Default only on `official` or `corroborated`. `single_source` is an Open suggestion or goes to the owner's confirm list. `unverified` is never used (it is ranked last and is the first thing the appendix takes).
* Numbers, fees, limits and legal facts are on the confirm list. Do not default them; present them as "to confirm".
* A thin lane (`lane_quality` thin or none) or a decision with a `gap` means: write "assumed" from the module library, never invent a source, a number or a provider.
* Record the item ids behind every research-derived value (`claims_used`).
* Compliance information is information, never advice; every entry of the register is on the confirm list.

## The parts, and what a lane does with each

| Part | What it is | How to use it |
| --- | --- | --- |
| `lane_decisions[]` | For each decision a lane must make (`data/lanes.yaml`): the usable main-store items that inform it (`item_ids`), the unverified ones (`unverified_item_ids`), the best label, and a `gap` `{reason, note}` exactly when nothing usable exists. Reasons: `unverified_only`, `not_found`, `not_run`, `not_planned` | Start every lane here. A decision with items: read them. A decision with a gap: assume it and mark it assumed |
| `dossiers[]` | One per provider that has provider facts: per field value, typed value, items, evidence (the weakest label of the values shown), `null_reason`; `completeness {filled, required, share}`; `official_share`; `official_url` (from the provider's own official pages) | Integrations lane: write the integration from the dossier alone. A field with a `value` and `official` or `corroborated` evidence may be a default. `conflict`: both values are kept in `values[]`; confirm, do not default. `completeness.share` says how much of the contract is known |
| `dossiers_by_integration` | `{need key: [dossier id]}` | Find the providers for an integration need |
| `platform_constraints[]` | One rule per entry: capability, platform (`ios`, `android`, `both`, `expo`, `react_native`, `web` or null), rule, `effect_on_build` (`needs_development_build`, `needs_permission`, `needs_config_plugin`, `review_risk`, `none`, or null when not stated), evidence, source domain | Integrations, Screens (permission prompts) and the front-end engine: `needs_development_build` means the capability does not run in Expo Go; `needs_config_plugin` and `needs_permission` are build setup |
| `compliance_register[]` | By country, then regime: statement, authority, effective date, applies to, penalty when stated, source, evidence | Blueprint (non-functional requirements) and Verify. Legal information only |
| `confirm[]` | Entries ranked by code: `class` (`money`, `legal`, `identity`, `other`), `rank` (1 first), `group` (what it is about), `top` (first 20) | Verify and the owner: show money, legal and identity first |
| `conflicts[]` | Topics where sources disagree; both items are kept | Never default a conflicting value |
| `items[]`, `norms[]`, `by_*` | The findings with prevalence, recency, sources and `data` by kind; indexes by Brief id and integration | The evidence behind everything above |
| `slices{}` | Per lane: every main-store item (`item_ids`), split into chunks of at most `chunk_items` (120) and never truncated; a digest (an index) outside the store size | Whole-store reading; chunks per lane |
| `profile` | `store_tokens` (the compact view: items, norms, confirm, conflicts, sources and the aggregates above), `appendix_items`, `digest_tokens_total` (all six lane digests together; `digest_tokens` is the same number, kept for compatibility), `digest_tokens_cap_per_lane` (the cap of ONE lane digest: the six together can use up to six times it) | Route without reading the file |

## Ranking and the main store

Every item has a `rank` (1 is best): every verified item before any unverified one, then Brief-linked, high impact, evidence (official, corroborated, single source), the norm level as a tie-break only, then job and id. Items above the store limit (`store_tokens`) go to the appendix, lowest rank first, so unverified items go first. Protected items always stay in the main pack: Brief-linked high-impact items and rules, compliance notes and provider options, conflicts and items to confirm, and the best two verified items of every kind. An unverified item is protected only when it is a conflict or needs confirming. Nothing is deleted; the appendix is in the file and in the pack index.

## `pack(research, need_card)`: what one Spec call reads

Pure code over the parsed `research.json`: no model, no embeddings, no similarity. Selection is by structure.

```
need_card = {
  "brief_ids": ["FTR-001", "ENT-002", "INT:payments", "NEW"],   # required: the Brief ids the call is about
  "lane": "integrations",            # optional: adds the lane's essentials and decisions
  "token_budget": 8000,              # optional, estimated tokens per pack (characters over 4), at least 500
  "kinds": ["provider_fact"],        # optional: only these kinds are selected (others stay in the index)
  "essentials_per_kind": 2           # optional: how many best items per kind the lane contributes
}
```

1. **direct**: every item whose `applies_to` names a Brief id of the card (typed `FTR-001`, or `INT:payments` / `payments`, or `NEW`). The whole file is read, appendix included: a Brief-linked item in the appendix is in the pack after the main-store ones.
2. **hop**: one hop along `applies_to`: items that share a Brief id with a direct item. A hop does not continue.
3. **lane essentials** (when the card names a lane): the best items of every kind of the lane's slice, conflicts, and the best three items of each lane decision.
4. **index**: everything else as a title-only list `{id, kind, label, evidence}` (plus `store: "appendix"`): nothing in the file is lost, and Spec never asks a model to fetch more.

Selected content fills **packs** in this order: the lane's decisions, items (direct, hop, lane; rank order inside each), dossiers of the selected providers (or of a named integration), platform constraints, confirm entries and conflicts of the selected items. A pack that would pass `token_budget` is **split**: the next pack starts, nothing is cut. A single unit larger than the budget becomes its own pack, flagged `over_budget`. The result `{schema: "research.pack.v1", need_card, packs[], index[], stats}` is deterministic: the same file and card give the same packs whatever the order of `items`.

Contract tests (`agents/research/tests/assemble/test_pack.py`): it never misses an item that applies to a named Brief id (checked for every Brief id of a Medium and a Big run); every item is in exactly one of packs and index; it is deterministic and independent of the order of items; a small budget splits and gives the same items in the same order as one big pack, and every item is whole; every pack is within the budget unless one unit stands alone; no gateway, model or embedding import.

## What is defined but not proven

Depth by size (`data/depth.yaml`) is defined per size class; the items marked `data_only` there (a competitor matrix floor of 4 to 6 for Medium, required lifecycles, per-role workflow exceptions, realistic data by entity, trends) have no enforcement behind them yet and no Medium or Big live run has been read against them. Dossier completeness on real pages, the share of provider facts that reach `official`, and the real numbers per lane on a Big run are unmeasured: the offline runs use a fake web.
