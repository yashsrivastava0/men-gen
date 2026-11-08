# `mde_spec_loader`

The read-only library the frontend engine uses to consume `APP_SPEC.md` (brief 8.5, spec 7.2). Pure: it reads a file path or a string and does no other I/O; it never edits a spec. Depends only on `mde_core` and `mde_contracts` (plus PyYAML) and is checked with pyright strict. Owner: spec-engineer.

Format: [specs/app-spec-format.md](../specs/app-spec-format.md). How it is used: [specs/frontend-consumption.md](../specs/frontend-consumption.md).

## Entry point

```python
from mde_spec_loader import SpecIndex

index = SpecIndex.from_path("APP_SPEC.md")      # or SpecIndex.from_text(text)
```

Construction is a cheap scan (front matter, headings, fences). The parsed `AppSpec` (`index.spec`) and the reference graph (`index.graph`) are built lazily, once.

| Call | Returns |
| --- | --- |
| `index.index()` | `IndexView`: spec version, app id, hash, `sections` (number, key, title, ids, tokens) and `blocks` (id, kind, section, line range, chars, tokens), total tokens |
| `index.get(ids)` | `dict[id, text]`: blocks as the exact fenced text of the file (front matter for `HEADER`, prose for `SUMMARY`); items (`ENT-*`, `API-*`, `ROLE-*`, `INT-*`, `NAV-*`, `S0`..`S10`) re-dumped canonically as `kind=entity` ... fragments. Unknown id: `UnknownIdError` |
| `index.has(id)` | bool |
| `index.slice(target, kit_signatures=None, stack_rules=(), tier3=False, max_neighbors=6)` | `Slice` for a step, screen or flow: tiers 0 to 3 (see below) |
| `index.neighbors(id)` | Screens linked by navigation, actions or flow order (both directions). For a non-screen id: the screens that touch it |
| `index.refs(id, deep=True)` | `Refs`: entities, operations, integrations, roles, screens, flows, enums, research. `deep` follows operation to entity and entity to entity (one hop) and collects enums |
| `index.diff(other)` | `SpecDiff(added, removed, changed, stale_screens)` from this version (vA) to `other` (an index or text) |
| `index.verify(expected=None)` | True if the embedded hash matches the recomputed one (and `expected`) |
| `index.locked_screens()` | Ids of screens with `locked: true` |
| `index.spec_tag(id)` | `"@spec SCR-014@v7"` |
| `index.unresolved_refs()` | Human-readable references that do not resolve (empty for a valid spec) |
| `index.header`, `.spec`, `.graph`, `.text`, `.document` | The parsed header, whole `AppSpec`, `RefGraph`, LF-normalised text, tokenized `Document` |

Module-level helpers: `parse_spec(text) -> AppSpec`, `split_blocks(text)`, `diff_specs(a, b)`, `diff_texts(text_a, text_b)`, `estimate_tokens(text)`, `compute_spec_hash` / `embed_hash` / `verify_hash` / `blank_hash` / `embedded_hash` / `normalize_newlines`, `format_spec_tag` / `parse_spec_tag`, `parse_binding`, `build_ref_graph`, `simulate_consumer`, and the codec (`dump_yaml`, `load_yaml`, `model_data`, `block_data`) the renderer shares.

## `slice()` and the tiers

`Slice(id, spec_tag, text, block_ids, token_estimate, tiers, locked)`. `text` is the whole bundle; `tiers` lists, per tier, the block ids and the token estimate.

- **Tier 0:** the header front matter, a summary line, tone, naming conventions, stack rules. Always.
- **Tier 1:** the `DESIGN` and `NAV` blocks, exact. Every run.
- **Tier 2:** the task's own blocks (screen; flow and its screens; or a step with the blocks it loads, its flow and its screens), then the items they reference carved out of `DATA`, `API`, `ROLES`, `INTEGRATIONS`, then the enums used (one `ENUMS` fragment), then kit-component signatures for the screen's recipe if the caller passed them.
- **Tier 3** (`tier3=True`, screens only): the blocks of neighbouring screens.

A screen slice is far below 10k estimated tokens at any app size (about 3k for a 50-screen sample; tested). `S6` returns the step with its groups, not every screen; each screen is its own task.

## Other types

- `RefGraph` (`mde_spec_loader.graph`): nodes by id and kind, typed `Edge(src, dst, relation, detail)`; `unresolved`, `reachable_screens()`, `neighbors`, `refs`, `referenced_by`, `out_edges`, `in_edges`. The validators of `mde_spec_builder` are built on it, so the loader and the validators agree on what a reference is.
- `SpecDiff`: `added`, `removed`, `changed` (blocks and items), `stale_screens`; properties `all_ids`, `empty`.
- `ConsumerReport` (from `simulate_consumer`): per-slice sizes, `unresolved`, `unscheduled`, `hash_ok`, `problems`, `ok`, `max_tokens`.
- Errors: `SpecError` (base), `SpecParseError` (message, optional line), `UnknownIdError`.

## Modules

| Module | Job |
| --- | --- |
| `blocks` | Tokenizer: front matter, section headings, fenced blocks with offsets |
| `codec` | Canonical YAML dump and load; model to block data and back (renames, compact actions) |
| `parse` | Document to `AppSpec`; summary escaping |
| `graph` | `RefGraph` construction and queries; binding grammar; auth tokens |
| `fragments` | Canonical text of blocks and items; ids of everything addressable |
| `slicing` | Tiered bundles; `@spec` tag helpers |
| `diff` | Changed ids and stale screens |
| `hashing` | The hash rule |
| `tokens` | The token estimator |
| `consumer` | The consumer simulator |
| `index` | `SpecIndex` |
| `models` | The typed results |

## Design notes

- The renderer (`mde_spec_builder.render`) composes the file out of `mde_spec_loader.codec`, so the dump and the load are one code path.
- The pure-Python PyYAML loader and dumper are used, not the C ones, so bytes and parse results are identical on every machine. A 50-screen file (about 20k tokens) parses in roughly a tenth of a second.
- The loader is strict about structure (unknown block kinds, duplicate ids, out-of-order headings are errors) and about the schema (every block is validated by its `mde_contracts` model) but never repairs anything.

## Tests

`uv run pytest packages/spec-loader` (coverage above 95%; add `--cov=packages/spec-loader/src --cov-report=term-missing`). The tests use `mde_spec_builder.testing.make_sample_spec` and `render_spec` as test tools (the loader itself never imports the builder). Contract check on any file: `uv run python scripts/simulate_frontend_consumer.py <APP_SPEC.md>`.
