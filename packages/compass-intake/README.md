# mde-compass-intake

One tolerant reader for what Spec receives: a **Compass export** (format 1, format 2, trimmed, a future one) and a
**Research output** (`research.v5`). It is a copy of Research's intake (D-175), not an extraction: Research stays
untouched and `tests/contract/test_intake_parity.py` keeps the two copies in step. It imports no agent, no engine and no
model vendor (only `mde_core`, pydantic, pyyaml, regex).

## Public functions

| Function | What it does |
| --- | --- |
| `read_export(data, program_id=None) -> SpecInput` | Compass export in, trust-graded `SpecInput` out. Raises `ExportRefused` only for `not_an_export` (no Brief) and `unusable_brief` (no idea, no facts, no research requests). |
| `check_readiness(spec_input) -> Readiness` | Fatal only when there is no idea statement or no in-scope feature; `missing` is a plain list of what Compass must still ask. |
| `intake(data) -> Intake` | `read_export` plus `check_readiness`; a refused file is a failed readiness with a plain list, not an exception. |
| `normalise(data, languages=None) -> Normalised` | The adapter alone: the export in one internal shape (the only place that knows the file layout). |
| `read_research(research) -> ResearchView` | A `research.v5` dict in, a small typed view out; missing, partial or failed research is `thin=True`, never an exception. |
| `pack(research, need_card)` | Select what applies to a need card by Brief id (direct, one hop, lane essentials, title index); split, never truncate. |

`SpecInput` (`spec_input.py`) carries the idea, audience, geography, locales (with their source), tone, density, devices,
sign-in methods, features, roles, apps, modules, the structure as a proposal, integrations, facts, rules and numbers,
sensitive data, existing systems, tier and size band, a `trust` profile, `merges`, `assumptions`, `thin_input` and three
flag lists: `flags` (the ones Research also raises), `drift_flags` (unknown format version, missing or empty expected
field, changed enum, size jump) and `cleaning_flags` (duplicate or junk roles, duplicate features, mostly-nice priorities).

Trust is graded per field (doc 15 section 3.4), not taken from Compass: a feature is `locked` only when a quote of the
person's own words supports it and is found in the interview record; facts, geography, locales and style values also need
a confidence of 0.7 or more; priority is default unless the person deferred it; the structure is always `default`. The
words and thresholds are in `data/cleaning.yaml`.

## Adding a Compass version

1. Change `formats.py` only (where the envelope, a view, the seal or the structure block moved; keep the old place as the
   fallback). `reader.py` must not name an envelope key (`tests/test_formats.py` checks).
2. Add one small export to `tests/fixtures/formats/` as `format<N>_<what>.json` (and the same file to
   `agents/research/tests/fixtures/formats/`: the parity test compares the folders) and one row to `EXPECTED` in
   `tests/test_formats.py`.
3. A file that belongs to the nine inputs also goes to `docs/compass/research-inputs/` and a row in `TABLE` of
   `tests/test_nine_inputs.py`.
4. If Research's adapter changed, copy the change here (and the other way round), then run
   `uv run pytest packages/compass-intake tests/contract -q`.
