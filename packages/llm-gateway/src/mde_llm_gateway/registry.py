"""Spec registry protocol. The gateway never imports ``mde_prompts`` (layering): whoever builds the
gateway passes in an object that can look call specs up. ``mde_prompts.registry.PromptRegistry``
implements this protocol; tests use :class:`StaticRegistry`.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol

from pydantic import BaseModel

from mde_contracts.gateway import CallSpec
from mde_core.errors import NotFound

AnySpec = CallSpec[BaseModel, BaseModel]


class SpecRegistry(Protocol):
    def get(self, call_id: str, version: int | None = None) -> AnySpec:
        """The spec for ``call_id`` (and ``version`` when given). Raise on unknown ids."""
        ...

    def catalog(self) -> list[AnySpec]:
        """Every spec the registry currently has."""
        ...


class StaticRegistry:
    """A fixed set of specs (tests, small tools)."""

    def __init__(self, specs: Iterable[AnySpec]) -> None:
        self._specs = {spec.id: spec for spec in specs}

    def get(self, call_id: str, version: int | None = None) -> AnySpec:
        spec = self._specs.get(call_id)
        if spec is None:
            raise NotFound(f"unknown call id: {call_id}", details={"call_id": call_id})
        if version is not None and spec.version != version:
            raise NotFound(
                f"{call_id} v{version} is not available (current is v{spec.version})",
                details={"call_id": call_id, "version": version},
            )
        return spec

    def catalog(self) -> list[AnySpec]:
        return list(self._specs.values())
