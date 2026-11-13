"""Result cache and checkpoint keys (doc 18 4.2; FN-34, FN-25, doc 15 8.4).

The route fingerprint is an OPAQUE string computed by the gateway side from what a route resolves
to; this package never sees a model or provider name. After a route change nothing is served from
the old route's cache. A cache is scoped by tenant and never shared across tenants. The PS-9 bypass
is a harness argument (``BypassCache``), not a runtime flag.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, Protocol, runtime_checkable

from mde_spec_core.canon import canonical_json, sha256_json


def _need(name: str, value: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a non-empty string")
    return value


def result_cache_key(
    tenant: str,
    step: str,
    input_hash: str,
    route_fingerprint: str,
    prompt_version: str,
    schema_version: str,
    library_version: str,
) -> str:
    """Hex key over every component; any change in any component changes the key."""
    return sha256_json(
        {
            "kind": "result",
            "tenant": _need("tenant", tenant),
            "step": _need("step", step),
            "input_hash": _need("input_hash", input_hash),
            "route_fingerprint": _need("route_fingerprint", route_fingerprint),
            "prompt_version": _need("prompt_version", prompt_version),
            "schema_version": _need("schema_version", schema_version),
            "library_version": _need("library_version", library_version),
        }
    )


def checkpoint_key(
    program: str,
    input_hash: str,
    step: str,
    batch: str | int,
    versions: Mapping[str, str],
    route_fingerprint: str,
) -> str:
    """(program, input hash, step, batch) plus the call-spec, prompt, schema and library versions,
    so a deployment in the middle of a run never mixes versions (SPC-OP-11). ``versions`` is a
    mapping such as ``{"call_spec": ..., "prompt": ..., "schema": ..., "library": ...}``.
    """
    return sha256_json(
        {
            "kind": "checkpoint",
            "program": _need("program", program),
            "input_hash": _need("input_hash", input_hash),
            "step": _need("step", step),
            "batch": str(batch),
            "versions": {str(k): str(v) for k, v in sorted(versions.items())},
            "route_fingerprint": _need("route_fingerprint", route_fingerprint),
        }
    )


@runtime_checkable
class ResultCache(Protocol):
    """A cache of validated call results. Values are JSON-able; implementations are tenant-scoped."""

    def get(self, tenant: str, key: str) -> Any | None: ...

    def put(self, tenant: str, key: str, value: Any) -> None: ...


class InMemoryResultCache:
    """Tenant-scoped dict. Values are stored as canonical JSON and returned as fresh copies, so a
    caller can never mutate a cached result by accident."""

    def __init__(self) -> None:
        self._by_tenant: dict[str, dict[str, str]] = {}

    def get(self, tenant: str, key: str) -> Any | None:
        raw = self._by_tenant.get(_need("tenant", tenant), {}).get(key)
        return None if raw is None else json.loads(raw)

    def put(self, tenant: str, key: str, value: Any) -> None:
        self._by_tenant.setdefault(_need("tenant", tenant), {})[key] = canonical_json(value)

    def size(self, tenant: str) -> int:
        return len(self._by_tenant.get(tenant, {}))

    def clear_tenant(self, tenant: str) -> None:
        self._by_tenant.pop(tenant, None)


class BypassCache:
    """Never stores, never returns: the PS-9 provider-switch harness passes this in."""

    def get(self, tenant: str, key: str) -> Any | None:
        return None

    def put(self, tenant: str, key: str, value: Any) -> None:
        return None
