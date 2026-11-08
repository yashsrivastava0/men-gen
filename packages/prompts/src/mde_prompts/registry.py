"""Call-spec registry.

Convention (so three engineers can add specs in parallel without touching shared files):

    packages/prompts/src/mde_prompts/calls/<folder>/
        __init__.py       (empty)
        spec.py           defines ``SPEC: CallSpec[I, O]`` (id from ``mde_contracts.registry.CALL_IO``)
        system.jinja      system prompt (short; one job; points at the output schema)
        user.jinja        user prompt (renders the input model)
        mock.py           ``def handle(input: I) -> O | FreeTextResult``  (deterministic, schema-valid)
        fixtures/         golden examples used by the mock handler and the tests

``<folder>`` is ``mde_contracts.registry.call_folder(call_id)``, e.g. ``c1_understand``.
No prompt text is ever inlined in an engine.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

from mde_contracts.gateway import CallSpec
from mde_contracts.registry import CALL_IO, call_folder


class SpecNotFound(KeyError):
    pass


def load_spec(call_id: str) -> CallSpec[Any, Any]:
    """Import ``mde_prompts.calls.<folder>.spec`` and return its ``SPEC``."""
    if call_id not in CALL_IO:
        raise SpecNotFound(f"unknown call id: {call_id}")
    module_name = f"mde_prompts.calls.{call_folder(call_id)}.spec"
    try:
        module = import_module(module_name)
    except ModuleNotFoundError as exc:
        if exc.name == module_name or (exc.name or "").startswith("mde_prompts.calls."):
            raise SpecNotFound(f"call spec not written yet: {call_id}") from exc
        raise
    spec: CallSpec[Any, Any] = module.SPEC
    if spec.id != call_id:
        raise ValueError(f"{module_name}.SPEC.id is {spec.id!r}, expected {call_id!r}")
    inp, out = CALL_IO[call_id]
    if spec.input_model is not inp or spec.output_model is not out:
        raise ValueError(f"{call_id}: spec IO models differ from mde_contracts.registry.CALL_IO")
    return spec


def all_specs(*, missing_ok: bool = False) -> dict[str, CallSpec[Any, Any]]:
    """Load every spec in the catalog. With ``missing_ok`` skip specs not written yet."""
    specs: dict[str, CallSpec[Any, Any]] = {}
    for call_id in CALL_IO:
        try:
            specs[call_id] = load_spec(call_id)
        except SpecNotFound:
            if not missing_ok:
                raise
    return specs


class PromptRegistry:
    """Gateway-facing lookup (implements the gateway's ``SpecRegistry`` protocol)."""

    def get(self, call_id: str, version: int | None = None) -> CallSpec[Any, Any]:
        spec = load_spec(call_id)
        if version is not None and spec.version != version:
            raise SpecNotFound(f"{call_id} v{version} not available (current is v{spec.version})")
        return spec

    def catalog(self) -> list[CallSpec[Any, Any]]:
        return list(all_specs(missing_ok=True).values())
