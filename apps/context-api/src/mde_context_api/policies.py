"""Policy choice for a new session and the resolver the flow controller calls.

Rules (BRIEF 8.7): ``guided`` uses ``guided-default``; ``enterprise`` uses a configurable default
(``DEFAULT_ENTERPRISE_POLICY``, ``enterprise-it-services-firm`` out of the box); a ``policy_id`` in
the request overrides both and must exist; a policy whose mode differs from the session's mode is
refused. Policies are data (``mde_content``), never code paths.

**Bound callers.** When a ``PolicyBinding`` applies (a ``TENANT_POLICIES`` entry for the tenant, the
caller's token entry, or both), the request may only name a mode and a policy inside it, and the
defaults come from it (``resolve_new``). Without one the request decides, as above: that is the
development behaviour, and it lets any caller open a Guided session, so a deployment that has
stricter policies for some tenants must bind them.
"""

from __future__ import annotations

from mde_content.policies import PolicyInheritanceError, load_policy, resolve_policy
from mde_context_api.bindings import PolicyBinding
from mde_contracts.common import Mode
from mde_contracts.policy import Policy
from mde_core.errors import ConfigError, ValidationFailed
from mde_flow.errors import Forbidden

MODES: tuple[Mode, ...] = ("guided", "enterprise")


class PolicyCatalog:
    """``Callable[[str], Policy]`` for ``FlowService`` plus the id choice for new sessions."""

    def __init__(self, *, guided_default: str, enterprise_default: str) -> None:
        self._defaults: dict[Mode, str] = {
            "guided": guided_default,
            "enterprise": enterprise_default,
        }
        self._cache: dict[str, Policy] = {}
        for mode, pid in self._defaults.items():  # a bad default is a deployment error: fail early
            if self(pid).mode != mode:
                raise ValueError(f"default {mode} policy {pid!r} is not a {mode} policy")

    def choose(self, mode: Mode, requested: str | None) -> str:
        """The policy id for a new session: the request's (must exist and match) or the default."""
        pid = requested or self._defaults[mode]
        policy = self(pid)  # raises ValidationFailed when unknown
        if policy.mode != mode:
            raise ValidationFailed(
                f"policy {pid!r} is a {policy.mode} policy, not {mode}",
                details={"policy_id": pid, "mode": mode},
            )
        return pid

    # ------------------------------------------------------------------ bound sessions

    def validate_binding(self, binding: PolicyBinding, where: str) -> None:
        """A binding that names an unknown policy, or a default mode that its default policy does
        not have, is a deployment error: fail at start-up, never on a user's first request."""
        for pid in binding.mentioned():
            try:
                self(pid)
            except ValidationFailed:
                raise ConfigError(f"{where}: unknown policy {pid!r}") from None
        if (
            binding.default_policy is not None
            and binding.default_mode is not None
            and self(binding.default_policy).mode != binding.default_mode
        ):
            raise ConfigError(f"{where}: default_policy is not a {binding.default_mode} policy")

    def resolve_new(
        self, mode: Mode | None, requested: str | None, binding: PolicyBinding | None
    ) -> tuple[Mode, str]:
        """The ``(mode, policy_id)`` of a new session for a caller bound by ``binding``.

        ``mode`` is ``None`` when the request did not name one. Without a binding this is exactly
        ``choose`` with the plain defaults. With one: a mode or policy outside it is ``Forbidden``
        (403, plain words), and an omitted mode or policy comes from the binding.
        """
        if binding is None:
            plain: Mode = mode or "guided"
            return plain, self.choose(plain, requested)
        allowed = binding.allowed
        allowed_modes = self._modes_of(allowed) if allowed is not None else set(MODES)
        chosen = mode or self._default_mode(binding, allowed_modes)
        if chosen not in allowed_modes:
            raise Forbidden(_mode_text(chosen, allowed_modes))
        if requested is not None:
            if allowed is not None and requested not in allowed:
                raise Forbidden(_policy_text(allowed))
            return chosen, self.choose(chosen, requested)
        return chosen, self.choose(chosen, self._default_policy(chosen, binding, allowed))

    def _modes_of(self, policy_ids: frozenset[str]) -> set[Mode]:
        modes: set[Mode] = set()
        for pid in policy_ids:
            try:
                modes.add(self(pid).mode)
            except ValidationFailed:  # validated at start-up; a later content change is not a 500
                continue
        return modes

    def _default_mode(self, binding: PolicyBinding, allowed_modes: set[Mode]) -> Mode:
        if binding.default_mode is not None:
            return binding.default_mode
        if binding.default_policy is not None:
            return self(binding.default_policy).mode
        if len(allowed_modes) == 1:
            return next(iter(allowed_modes))
        return "guided"

    def _default_policy(
        self, mode: Mode, binding: PolicyBinding, allowed: frozenset[str] | None
    ) -> str | None:
        """The policy for a session of ``mode`` when the request named none: the binding's default
        (if it has this mode), else the catalog default (if allowed), else the first allowed one.
        ``None`` means "the catalog default" (the binding restricts only the default mode)."""
        if allowed is None:
            return None
        if binding.default_policy is not None and self(binding.default_policy).mode == mode:
            return binding.default_policy
        if self._defaults[mode] in allowed:
            return self._defaults[mode]
        for pid in binding.allowed_policies or tuple(sorted(allowed)):
            if pid in allowed and self(pid).mode == mode:
                return pid
        raise Forbidden(_mode_text(mode, self._modes_of(allowed)))

    def __call__(self, policy_id: str) -> Policy:
        cached = self._cache.get(policy_id)
        if cached is not None:
            return cached
        try:
            load_policy(policy_id)  # unknown ids raise KeyError (resolve_policy would too, later)
            policy = resolve_policy(policy_id)
        except (KeyError, PolicyInheritanceError):
            raise ValidationFailed(
                f"unknown policy {policy_id!r}", details={"policy_id": policy_id}
            ) from None
        self._cache[policy_id] = policy
        return policy


def _mode_text(mode: Mode, allowed_modes: set[Mode]) -> str:
    if not allowed_modes:
        return "Your organisation has no session type available to start here."
    names = " or ".join(sorted(allowed_modes))
    return (
        f"Your organisation does not allow {mode} sessions here. "
        f"You can start a {names} session instead."
    )


def _policy_text(allowed: frozenset[str]) -> str:
    names = ", ".join(sorted(allowed))
    return f"That policy is not available to your organisation. Available: {names}."
