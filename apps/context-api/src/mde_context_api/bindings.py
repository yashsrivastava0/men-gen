"""Policy bindings: which session modes and policies a tenant (or one API token) may open.

Without a binding the caller picks the mode and the policy of every new session (development
behaviour: ``mode`` and ``policy_id`` in the request). With one, the request must lie inside it and
the defaults come from it, so a user cannot open a Guided session to skip the organisation's banned
libraries, regions, multi-approver gates or research limits (the policy carries all of those).

A binding comes from ``TENANT_POLICIES`` (one entry per tenant id) and, in token mode, from the
token's own entry in ``API_TOKENS``. When both exist the request must satisfy both: the allowed sets
are intersected, the token's defaults win.

This module is plain data and pydantic only; the catalog lookups that need real policies are in
``mde_context_api.policies``.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator

from mde_contracts.common import Mode

POLICY_ID_PATTERN = r"^[a-z][a-z0-9_@.-]*$"
BoundPolicyId = Annotated[str, Field(min_length=1, max_length=128, pattern=POLICY_ID_PATTERN)]
MAX_BOUND_POLICIES = 50


class PolicyBinding(BaseModel):
    """What one tenant (or token) may start.

    * ``allowed_policies``: the policy ids a new session may use. Omitted = no restriction on the
      choice (but ``default_policy``, when given, is then the only allowed one: a default that is
      not also allowed would be a trap). An explicit empty list allows nothing.
    * ``default_policy``: used when the request names none; must be one of the allowed.
    * ``default_mode``: used when the request names no mode. Without it the default policy's mode,
      or the one mode every allowed policy shares, or ``guided``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    allowed_policies: tuple[BoundPolicyId, ...] | None = Field(
        default=None, max_length=MAX_BOUND_POLICIES
    )
    default_policy: BoundPolicyId | None = None
    default_mode: Mode | None = None

    @model_validator(mode="after")
    def _consistent(self) -> PolicyBinding:
        if (
            self.default_policy is not None
            and self.allowed_policies is not None
            and self.default_policy not in self.allowed_policies
        ):
            raise ValueError("default_policy must be one of allowed_policies")
        return self

    @property
    def allowed(self) -> frozenset[str] | None:
        """The allowed policy ids, or ``None`` when the choice is not restricted."""
        if self.allowed_policies is not None:
            return frozenset(self.allowed_policies)
        if self.default_policy is not None:
            return frozenset({self.default_policy})
        return None

    @property
    def restricts(self) -> bool:
        return self.allowed is not None or self.default_mode is not None

    def mentioned(self) -> tuple[str, ...]:
        """Every policy id this binding names (to check them against the catalog at start-up)."""
        names = list(self.allowed_policies or ())
        if self.default_policy is not None and self.default_policy not in names:
            names.append(self.default_policy)
        return tuple(names)


def merge(*bindings: PolicyBinding | None) -> PolicyBinding | None:
    """One binding that satisfies every given one (earlier arguments win on defaults).

    ``allowed`` is the intersection of the restricting bindings; a default is kept only while it is
    still allowed. ``None`` when no binding restricts anything.
    """
    present = [b for b in bindings if b is not None and b.restricts]
    if not present:
        return None
    allowed: frozenset[str] | None = None
    for binding in present:
        if binding.allowed is not None:
            allowed = binding.allowed if allowed is None else allowed & binding.allowed
    default_policy: str | None = next(
        (
            b.default_policy
            for b in present
            if b.default_policy is not None and (allowed is None or b.default_policy in allowed)
        ),
        None,
    )
    default_mode: Mode | None = next(
        (b.default_mode for b in present if b.default_mode is not None), None
    )
    ordered = _in_order(allowed, present)
    return PolicyBinding(
        allowed_policies=ordered,
        default_policy=default_policy,
        default_mode=default_mode,
    )


def _in_order(
    allowed: frozenset[str] | None, present: list[PolicyBinding]
) -> tuple[str, ...] | None:
    """The allowed ids in the order the first binding that lists them gave (stable defaults)."""
    if allowed is None:
        return None
    ordered: list[str] = []
    for binding in present:
        for pid in binding.allowed_policies or ():
            if pid in allowed and pid not in ordered:
                ordered.append(pid)
    for pid in sorted(allowed):  # an id only a default named
        if pid not in ordered:
            ordered.append(pid)
    return tuple(ordered)
