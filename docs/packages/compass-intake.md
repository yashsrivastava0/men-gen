# compass-intake

Package `mde_compass_intake` (`packages/compass-intake/`). One tolerant reader for Compass exports (format 1, format 2, trimmed) and Research outputs, copied from Research's reader (D-175) and extended for Spec: `SpecInput` with per-field trust grades, drift flags, cleaning flags, readiness, derived (assumed) roles, `ResearchView` and `pack(research, need_card)`.

- `read_export`, `intake`, `check_readiness` (fatal only for no idea or no feature; everything else proceeds as `thin_input`), `read_research` (partial or failed research is a thin view, never a crash), `pack`.
- Trust per doc 15 3.4: a feature is Locked only with a verified quote; priority Default; structure Default; low-confidence facts Default.
- Research is untouched. `tests/contract/test_intake_parity.py` keeps both copies in step on the nine exports; the two data folders and the format fixtures must stay identical.
- To support a new Compass version: change `formats.py` here and in Research, add one trimmed fixture and one expected row.

Test: `uv run pytest packages/compass-intake tests/contract`.
