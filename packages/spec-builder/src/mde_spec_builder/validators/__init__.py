"""The nine validators of spec 6.4 (code, run before the file is accepted).

Each is a function returning ``list[ValidatorFinding]`` and named after the finding's
``validator`` field: ``schema``, ``navigation_targets``, ``data_bindings``, ``action_targets``,
``role_gates``, ``flow_endpoints``, ``limits``, ``stack_profile``, ``no_secrets``.
``run_validators`` runs them all in that order; a finding with severity ``error`` blocks the spec,
``warn`` and ``info`` are advisory.
"""

from __future__ import annotations

from collections.abc import Sequence

from mde_contracts.calls import ValidatorFinding
from mde_contracts.content import StackProfile
from mde_contracts.policy import Limits
from mde_contracts.spec import AppSpec
from mde_spec_builder.render import render_spec
from mde_spec_builder.validators import budget as _budget
from mde_spec_builder.validators import capabilities as _capabilities
from mde_spec_builder.validators.actions import action_targets
from mde_spec_builder.validators.bindings import data_bindings
from mde_spec_builder.validators.budget import DEFAULT_MAX_SPEC_TOKENS
from mde_spec_builder.validators.capabilities import stack_profile
from mde_spec_builder.validators.flows import flow_endpoints
from mde_spec_builder.validators.gates import role_gates
from mde_spec_builder.validators.leaks import no_secrets
from mde_spec_builder.validators.navigation import navigation_targets
from mde_spec_builder.validators.structure import schema

limits = _budget.limits

VALIDATOR_NAMES: tuple[str, ...] = (
    "schema",
    "navigation_targets",
    "data_bindings",
    "action_targets",
    "role_gates",
    "flow_endpoints",
    "limits",
    "stack_profile",
    "no_secrets",
)


def run_validators(
    spec: AppSpec,
    *,
    stack_profile: StackProfile,
    limits: Limits,
    max_tokens: int = DEFAULT_MAX_SPEC_TOKENS,
) -> list[ValidatorFinding]:
    """Run all nine validators (rendering the spec once) and return every finding in order."""
    rendered = render_spec(spec)
    return [
        *schema(spec, rendered=rendered),
        *navigation_targets(spec),
        *data_bindings(spec),
        *action_targets(spec),
        *role_gates(spec),
        *flow_endpoints(spec),
        *_budget.limits(spec, limits, max_tokens=max_tokens, rendered=rendered),
        *_capabilities.stack_profile(spec, stack_profile),
        *no_secrets(spec, rendered=rendered),
    ]


def errors(findings: Sequence[ValidatorFinding]) -> list[ValidatorFinding]:
    """The blocking findings (severity ``error``)."""
    return [f for f in findings if f.severity == "error"]


def has_errors(findings: Sequence[ValidatorFinding]) -> bool:
    return any(f.severity == "error" for f in findings)


__all__ = [
    "DEFAULT_MAX_SPEC_TOKENS",
    "VALIDATOR_NAMES",
    "action_targets",
    "data_bindings",
    "errors",
    "flow_endpoints",
    "has_errors",
    "limits",
    "navigation_targets",
    "no_secrets",
    "role_gates",
    "run_validators",
    "schema",
    "stack_profile",
]
