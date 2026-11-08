"""Importable test helpers (D-017: spawned children cannot import ``--import-mode=importlib`` tests).

Contents: multi-process workers for the ledger/budget concurrency tests, and test-only call specs
with their templates (``testing_fixtures/``) shared by the gateway and gateway-API tests. Nothing in
here is used by production code paths.
"""

from __future__ import annotations

import re
import warnings
from collections.abc import Callable
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from typing import cast

from mde_contracts.gateway import Usage
from mde_core.errors import BudgetExceeded
from mde_core.money import usd_to_micros
from mde_llm_gateway.budget import BudgetConfig, BudgetGuard
from mde_llm_gateway.ledger import FileLedger
from mde_llm_gateway.pricing import Pricing

GuardFactory = Callable[..., BudgetGuard]  # the ``make_guard`` fixture's type

# Scope kinds that only tests use (no scope cap): ``reserve`` refuses kinds it does not know.
TEST_SCOPE_KINDS: frozenset[str] = frozenset({"adhoc"})


def budget_config_for_tests(cap_usd: str | Decimal = "6.00") -> BudgetConfig:
    """The default ``budget.yaml`` config plus the uncapped ``adhoc`` scope kind for tests."""
    config = BudgetConfig.load(cap_usd=cap_usd)
    return replace(config, scope_kinds=config.scope_kinds | TEST_SCOPE_KINDS)


def _worker_guard(ledger_path: str, effective_cap_micros: int) -> BudgetGuard:
    config = BudgetConfig(
        cap_micros=effective_cap_micros,
        effective_fraction=Decimal(1),
        scope_caps_micros={},
        scope_kinds=TEST_SCOPE_KINDS,
        warn_fraction=Decimal(1),
        must_run_fraction=Decimal(1),
        refuse_fraction=Decimal(1),
    )
    ledger = FileLedger(ledger_path)
    return BudgetGuard(config, Pricing.load(), ledger, bills=True)


def reserve_commit_worker(
    ledger_path: str, effective_cap_micros: int, attempts: int, worst_case_micros: int
) -> int:
    """Try ``attempts`` times to reserve ``worst_case_micros`` and commit exactly that cost.

    Returns how many reservations were granted. Across all processes the total granted can never
    exceed ``effective_cap_micros // worst_case_micros`` (lost updates would break this).
    """
    guard = _worker_guard(ledger_path, effective_cap_micros)
    granted = 0
    for i in range(attempts):
        try:
            reservation = guard.reserve(
                ["adhoc:mp"],
                worst_case_micros,
                purpose="test",
                call_id=f"call_{i}",
                spec_id="TEST.worker",
                model="gpt-6-luna",
            )
        except BudgetExceeded:
            continue
        granted += 1
        guard.commit_cost(reservation, worst_case_micros, Usage())
    return granted


def reserve_hold_worker(
    ledger_path: str, effective_cap_micros: int, attempts: int, worst_case_micros: int
) -> int:
    """Like :func:`reserve_commit_worker` but never closes its reservations (they stay open)."""
    guard = _worker_guard(ledger_path, effective_cap_micros)
    granted = 0
    for i in range(attempts):
        try:
            guard.reserve(
                ["adhoc:mp"],
                worst_case_micros,
                purpose="test",
                call_id=f"call_{i}",
                spec_id="TEST.worker",
                model="gpt-6-luna",
            )
        except BudgetExceeded:
            continue
        granted += 1
    return granted


def schema_violations(schema: dict[str, object], value: object, path: str = "$") -> list[str]:
    """Structural JSON-schema check for the strict subset (no third-party validator needed).

    Supports ``type``, ``enum``, ``properties``/``required``/``additionalProperties``, ``items``,
    ``anyOf``, ``minItems``/``maxItems``, ``minimum``/``maximum`` and ``pattern``.
    """
    any_of = schema.get("anyOf")
    if isinstance(any_of, list):
        branches = cast("list[dict[str, object]]", any_of)
        results = [schema_violations(b, value, path) for b in branches]
        return [] if any(not r for r in results) else [f"{path}: matches no anyOf branch"]
    errors: list[str] = []
    kind = schema.get("type")
    kinds = cast("list[str]", kind) if isinstance(kind, list) else [str(kind)] if kind else []
    if kinds and not any(_type_ok(k, value) for k in kinds):
        return [f"{path}: expected {kinds}, got {type(value).__name__}"]
    enum = schema.get("enum")
    if isinstance(enum, list) and value not in cast("list[object]", enum):
        errors.append(f"{path}: {value!r} not in enum")
    if isinstance(value, dict):
        obj = cast("dict[str, object]", value)
        props = cast("dict[str, dict[str, object]]", schema.get("properties", {}))
        for name in cast("list[str]", schema.get("required", [])):
            if name not in obj:
                errors.append(f"{path}.{name}: missing required property")
        for name, item in obj.items():
            if name in props:
                errors += schema_violations(props[name], item, f"{path}.{name}")
            elif schema.get("additionalProperties") is False:
                errors.append(f"{path}.{name}: additional property not allowed")
    if isinstance(value, list):
        seq = cast("list[object]", value)
        items = schema.get("items")
        if isinstance(items, dict):
            for i, item in enumerate(seq):
                errors += schema_violations(cast("dict[str, object]", items), item, f"{path}[{i}]")
        lo, hi = schema.get("minItems"), schema.get("maxItems")
        if isinstance(lo, int) and len(seq) < lo:
            errors.append(f"{path}: fewer than {lo} items")
        if isinstance(hi, int) and len(seq) > hi:
            errors.append(f"{path}: more than {hi} items")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        lo, hi = schema.get("minimum"), schema.get("maximum")
        if isinstance(lo, (int, float)) and value < lo:
            errors.append(f"{path}: below minimum {lo}")
        if isinstance(hi, (int, float)) and value > hi:
            errors.append(f"{path}: above maximum {hi}")
    pattern = schema.get("pattern")
    if isinstance(pattern, str) and isinstance(value, str) and not re.search(pattern, value):
        errors.append(f"{path}: does not match pattern")
    return errors


def _type_ok(kind: str, value: object) -> bool:
    if kind == "string":
        return isinstance(value, str)
    if kind == "boolean":
        return isinstance(value, bool)
    if kind == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if kind == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if kind == "null":
        return value is None
    if kind == "array":
        return isinstance(value, list)
    if kind == "object":
        return isinstance(value, dict)
    return False


_FORMAT_SAMPLES = {
    "date-time": "2026-11-08T12:00:00Z",
    "date": "2026-11-08",
    "time": "12:00:00",
    "duration": "PT1H",
    "email": "a@example.com",
    "hostname": "example.com",
    "ipv4": "127.0.0.1",
    "ipv6": "::1",
    "uuid": "00000000-0000-4000-8000-000000000000",
}


def sample_from_schema(schema: dict[str, object]) -> object:
    """A minimal instance that satisfies a strict schema (for round-trip tests)."""
    any_of = schema.get("anyOf")
    if isinstance(any_of, list):
        branches = cast("list[dict[str, object]]", any_of)
        non_null = [b for b in branches if b.get("type") != "null"]
        return sample_from_schema((non_null or branches)[0])
    enum = schema.get("enum")
    if isinstance(enum, list) and enum:
        return cast("list[object]", enum)[0]
    kind = schema.get("type")
    if kind == "object":
        props = cast("dict[str, dict[str, object]]", schema.get("properties", {}))
        return {name: sample_from_schema(sub) for name, sub in props.items()}
    if kind == "array":
        items = cast("dict[str, object]", schema["items"])
        n = int(cast("int", schema.get("minItems", 0)))
        return [sample_from_schema(items) for _ in range(n)]
    if kind == "string":
        fmt = schema.get("format")
        if isinstance(fmt, str) and fmt in _FORMAT_SAMPLES:
            return _FORMAT_SAMPLES[fmt]
        pattern = schema.get("pattern")
        if isinstance(pattern, str):
            from hypothesis import strategies as st

            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                return st.from_regex(pattern, fullmatch=True).example()
        return "x"
    if kind == "integer":
        return int(cast("float", schema.get("minimum", 0)))
    if kind == "number":
        return float(cast("float", schema.get("minimum", 0)))
    if kind == "boolean":
        return False
    if kind == "null":
        return None
    raise ValueError(f"cannot sample {schema!r}")


def usd(value: str) -> int:
    """Test shorthand: ``usd("0.50")`` -> micro-dollars."""
    return usd_to_micros(value)


FIXTURES_DIR = Path(__file__).parent / "testing_fixtures"
