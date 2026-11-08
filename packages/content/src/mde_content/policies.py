"""Policy profiles loader and inheritance resolver (spec section 10, brief 8.6).

A policy is configuration, never a code path. Files in ``data/policies/<id>.yaml`` validate as
``mde_contracts.policy.Policy`` plus one file-level key, ``version`` (the N in ``id@N`` used by
``inherits``), which the loader strips before validation.

Inheritance (``inherits: "platform-default@1"``) is at most three levels (platform defaults,
organization or client profile, app). ``resolve_policy`` merges parent into child field by field:
a field the child sets overrides the parent's; dict fields are merged key by key; lists and
scalars are replaced. Cycles, a fourth level, an unknown parent and a version mismatch raise
``PolicyInheritanceError`` with the chain in the message.
"""

from __future__ import annotations

import copy
import re
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources

import yaml

from mde_contracts.policy import Policy

MAX_INHERITANCE_LEVELS = 3
_REF = re.compile(r"^(?P<id>[a-z][a-z0-9_-]*)(?:@(?P<version>[0-9]+))?$")


class PolicyInheritanceError(ValueError):
    """Raised for a cycle, a chain deeper than three levels, or an unresolvable ``inherits``."""


@dataclass(frozen=True)
class _Entry:
    version: int
    policy: Policy


@lru_cache(maxsize=1)
def _catalog() -> dict[str, _Entry]:
    entries: dict[str, _Entry] = {}
    folder = resources.files("mde_content").joinpath("data/policies")
    for item in sorted(folder.iterdir(), key=lambda p: p.name):
        if not item.name.endswith(".yaml"):
            continue
        raw = yaml.safe_load(item.read_text(encoding="utf-8"))
        version = int(raw.pop("version", 1))
        policy = Policy.model_validate(raw)
        if policy.id in entries:
            raise ValueError(f"duplicate policy id {policy.id!r} in {item.name}")
        entries[policy.id] = _Entry(version=version, policy=policy)
    return entries


def _split_ref(ref: str) -> tuple[str, int | None]:
    match = _REF.match(ref)
    if match is None:
        raise PolicyInheritanceError(f"malformed policy reference {ref!r}; expected 'id@version'")
    version = match.group("version")
    return match.group("id"), (int(version) if version is not None else None)


def policy_ref(policy_id: str) -> str:
    """Canonical ``id@version`` of a packaged policy; ``KeyError`` if unknown."""
    entry = _catalog().get(policy_id)
    if entry is None:
        raise KeyError(f"unknown policy: {policy_id!r}")
    return f"{policy_id}@{entry.version}"


def load_policy(ref: str) -> Policy:
    """Return a packaged policy exactly as authored (not resolved). ``ref`` is ``id`` or ``id@N``."""
    policy_id, version = _split_ref(ref)
    entry = _catalog().get(policy_id)
    if entry is None or (version is not None and version != entry.version):
        raise KeyError(f"unknown policy: {ref!r}")
    return entry.policy


def list_policies() -> list[Policy]:
    """All packaged policies as authored, sorted by id."""
    return [e.policy for _, e in sorted(_catalog().items())]


def _lookup(ref: str, available: Mapping[str, Policy] | None) -> tuple[str, Policy]:
    policy_id, version = _split_ref(ref)
    if available:
        if version is not None and f"{policy_id}@{version}" in available:
            return f"{policy_id}@{version}", available[f"{policy_id}@{version}"]
        if version is None:
            hits = sorted(k for k in available if k.split("@", 1)[0] == policy_id)
            if hits:
                return hits[-1], available[hits[-1]]
    entry = _catalog().get(policy_id)
    if entry is not None and (version is None or version == entry.version):
        return f"{policy_id}@{entry.version}", entry.policy
    raise PolicyInheritanceError(f"unknown parent policy {ref!r}")


def _chain(
    policy: str | Policy, available: Mapping[str, Policy] | None
) -> list[tuple[str, Policy]]:
    """The policy and its ancestors, child first, as ``(ref, policy)`` pairs."""
    chain = [_lookup(policy, available)] if isinstance(policy, str) else [(policy.id, policy)]
    seen = {chain[0][1].id}
    while (parent_ref := chain[-1][1].inherits) is not None:
        ref, parent = _lookup(parent_ref, available)
        path = " -> ".join([r for r, _ in chain] + [ref])
        if parent.id in seen:
            raise PolicyInheritanceError(f"inheritance cycle: {path}")
        if len(chain) + 1 > MAX_INHERITANCE_LEVELS:
            raise PolicyInheritanceError(
                f"inheritance deeper than {MAX_INHERITANCE_LEVELS} levels: {path}"
            )
        chain.append((ref, parent))
        seen.add(parent.id)
    return chain


def inheritance_chain(
    policy: str | Policy, available: Mapping[str, Policy] | None = None
) -> list[str]:
    """Refs from the policy itself up to the root, e.g. ``['acme@1', 'platform-default@1']``.

    The spec builder copies these into the spec header's ``inherits`` list so the file stays
    self-contained.
    """
    return [ref for ref, _ in _chain(policy, available)]


def _merge(base: dict[str, object], over: Mapping[str, object]) -> dict[str, object]:
    out = dict(base)
    for key, value in over.items():
        current = out.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            out[key] = _merge(current, value)  # pyright: ignore[reportUnknownArgumentType]
        else:
            out[key] = copy.deepcopy(value)
    return out


def resolve_policy(policy: str | Policy, available: Mapping[str, Policy] | None = None) -> Policy:
    """Resolve a policy against its ancestors into one flat ``Policy`` (parent first, child wins).

    ``policy`` is a packaged id (``"guided-default"``, ``"acme@2"``) or a ``Policy`` object, for
    example an organization profile loaded from the database. ``available`` adds policies that are
    not packaged, keyed by ``"id@version"``; it is consulted before the packaged ones.
    """
    merged: dict[str, object] = {}
    for _, item in reversed(_chain(policy, available)):
        merged = _merge(merged, item.model_dump(exclude_unset=True))
    return Policy.model_validate(merged)
