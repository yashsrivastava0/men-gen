# `mde_core`

Leaf utilities shared by every package (`packages/core`). It depends on no other `mde_*` package and holds no business logic. Pyright strict. Owner: the lead (shared foundation).

## Public interface

```python
from mde_core import (
    Settings, load_settings, describe_presence,     # settings
    new_id, is_id, SequentialIds,                   # ids
    Clock, SystemClock, FakeClock, utcnow,          # time
    canonical_json, sha256_hex, hash_json, hash_model,   # hashing
    redact_text, find_secrets, contains_secret,     # redaction
    usd_to_micros, micros_to_usd, token_cost_micros, format_usd,   # money
    CancellationToken, NeverCancelled,
)
```

| Module | What it gives you |
| --- | --- |
| `settings` | `Settings` (pydantic-settings, env names equal field names, case-insensitive): `OPENAI_API_KEY` (`SecretStr`), `OPENAI_MODEL`, `LLM_MODE` (`mock`, `replay`, `live`), `ALLOW_LIVE_LLM`, `LLM_BUDGET_USD`, `LLM_BUDGET_LEDGER`, `MODEL_ROUTES`, `DATABASE_URL`, `GATEWAY_MODE`/`GATEWAY_URL`/`GATEWAY_SERVICE_TOKEN`, `RESEARCH_DEPTH`, `RESEARCH_CONCURRENCY`, `API_DEV_TOKEN`, `PROJECTS_DIR`, `CASSETTE_DIR`, `MDE_RECORD_CASSETTES`, `LOG_LEVEL`, `LOG_PROMPTS`, `MDE_FORBID_LIVE`. `load_settings()` honours `MDE_ENV_FILE`; `describe_presence()` is the safe "is it set?" report that `mde doctor` and `scripts/doctor.py` print |
| `logging` | structlog JSON logging plus secret scrubbing of structlog events **and** stdlib `logging` records (`ScrubFilter`, a wrapped `LogRecord` factory, `register_secrets`, `get_logger`, `bind_context`) |
| `redact` | `redact_text`, `redact_value`, `find_secrets`, `contains_secret`, `normalize_for_scan`, `strip_hidden_chars`, `SECRET_PATTERNS` |
| `ids` | prefixed ULIDs: `new_id("ses")` gives `ses_<ulid>`; `is_id`, `id_prefix`; `SequentialIds` for deterministic tests |
| `clock` | `Clock` protocol, `SystemClock`, `FakeClock`, `utcnow`, `ensure_utc` (timezone-aware UTC only) |
| `errors` | the shared hierarchy: `MdeError`; gateway errors (`GatewayError`, `BudgetExceeded`, `LedgerIntegrityError`, `UnknownModelPrice`, `ProviderError`, `CircuitOpen`, `StructuredOutputError`, `CassetteMissing`, `UntrustedWithTools`); `LiveCallForbidden`, `ConfigError`, `SecretLeakError`; `NotFound`, `Conflict`, `TenantViolation`, `InvalidTransition`, `GateError`, `HashMismatch`, `ValidationFailed`, `CallCancelled` |
| `hashing` | canonical JSON (sorted keys, no whitespace) and sha256 |
| `money` | integer micro-dollars and `Decimal` helpers; never float |
| `filelock` | `FileLock`: `msvcrt.locking` on Windows, `fcntl.flock` elsewhere (D-004); used by the budget ledger |
| `cancel` | `CancellationToken` (cooperative; `check()` raises `CallCancelled`), `NeverCancelled` |
| `paths` | `main_repo_root`, `anchor_to_main_repo` (a relative shared-data path means "in the main repository", never the working directory), `git_executable` (git from `PATH` only) |
| `testing` | importable multi-process test workers (`lock_counter_worker`): `--import-mode=importlib` test modules cannot be imported by spawned children (D-017) |

## Test

```powershell
uv run pytest packages/core -q
uv run pyright packages/core ; uv run ruff check packages/core
```

## Conventions

* Timezone-aware UTC only; money never float; no `Any` in public signatures; pyright strict.
* Never log or repr a secret. `SecretStr` and the redaction helpers are the only way key-like text may leave a function. `Settings` hides `database_url` from `repr` and `model_dump` (it carries the password).
* **`redact`**: every pattern is linear (a quadratic regex is a denial of service on every log line); text is normalised (NFKC, invisible characters, dashes) before it is scanned, so `redact_text` returns the *normalised* text; long inputs are scanned in overlapping windows. Do not add a pattern without adding its family to `tests/test_redact_hardening.py` (it must stay under 0.5 s at 100,000 characters).
* **`Settings` paths**: `llm_budget_ledger`, `projects_dir` and `cassette_dir` are anchored to the main repository root at construction when relative; absolute values are untouched. `cassette_dir` and `record_cassettes` replaced the gateway factory's direct environment reads (D-034).
* **Logging**: do not remove either scrubbing layer. The filter alone misses uvicorn; the factory alone misses `extra` fields (GAP-6).
* Why a leaf package: contracts depends on it, so it can have no I/O dependencies (`.importlinter` forbids `httpx`, `sqlalchemy`, `fastapi` and others in `mde_core` and `mde_contracts`). See [repo-layout](../architecture/repo-layout.md) and [security](../architecture/security.md).
