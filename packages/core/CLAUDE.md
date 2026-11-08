# mde_core

**Job:** leaf utilities shared by every package. No dependency on other `mde_*` packages, no business logic.

**Public interface:** `settings` (pydantic-settings base; `load_settings()` honours `MDE_ENV_FILE`, `SecretStr` for the API key), `logging` (structlog JSON + secret redaction), `ids` (prefixed ULIDs: `ses_`, `evt_`, `call_`), `clock` (`Clock`, `SystemClock`, `FakeClock`), `errors` (`BudgetExceeded`, `StructuredOutputError`, `LiveCallForbidden`, ...), `hashing` (canonical-JSON sha256), `filelock` (portable: msvcrt on Windows, fcntl elsewhere; used by the budget ledger), `CancellationToken`, money helpers (integer micro-dollars, `Decimal`), `paths` (`main_repo_root`, `anchor_to_main_repo`: a relative shared-data path means "in the MAIN repository", never the cwd; `git_executable`: git from PATH only), `redact`.

**Test:** `uv run pytest packages/core`

**Conventions**
- Timezone-aware UTC only; money never float; no `Any` in public signatures; pyright strict.
- Never log or repr a secret; `SecretStr` and the redaction helper are the only way key-like text may leave a function.
- Owner: the lead (shared foundation).
- `redact`: every pattern is linear (a quadratic regex here is a denial of service on every log line); text is normalised (NFKC, invisible characters, dashes) before it is scanned, so `redact_text` returns the *normalised* text; long inputs are scanned in overlapping windows. Do not add a pattern without adding its family to `tests/test_redact_hardening.py` (it must stay under 0.5 s at 100k characters).
- `Settings`: `database_url` is hidden from `repr`/`model_dump` (it carries the password); `llm_budget_ledger`, `projects_dir` and `cassette_dir` are anchored to the main repository root at construction when relative (absolute values untouched); `record_cassettes` (env `MDE_RECORD_CASSETTES`) and `cassette_dir` (env `CASSETTE_DIR`) replace the gateway factory's direct env reads (D-034).
- `logging.configure_logging` scrubs structlog events AND stdlib `logging` records (GAP-6): `ScrubFilter` on the root and existing handlers plus a `LogRecord` factory wrapper (covers handlers attached later, such as uvicorn's), working on the formatted message, `exc_text`, stack and `extra`. Records with nothing to redact are left untouched; a record that cannot be scrubbed is withheld, never passed on. Do not remove either layer: the filter alone misses uvicorn, the factory alone misses `extra` fields.
