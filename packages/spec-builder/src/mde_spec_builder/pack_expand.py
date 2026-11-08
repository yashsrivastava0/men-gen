"""Pack-level transformations of B2: everything that reshapes a ``DomainPack`` before it becomes
spec blocks. All functions are pure (they return a new pack) and keep the pack's invariants:
no dangling ``@key`` references, no navigation node for a removed screen, no empty container, no
unreachable screen, no flow with fewer than two screens (a primary flow is kept while it has one).

``mde_content.packs.select_features`` does the feature half; this module does surfaces and roles,
the sign-in variants, the payment switch and the screen-cap guard (scope guard).
"""

from __future__ import annotations

import re
from collections import deque
from collections.abc import Collection, Iterable, Iterator

from mde_content.recipes import load_recipes
from mde_contracts.content import DomainPack, PackFlow, PackNavNode, PackScreen
from mde_contracts.spec import ApiOperation, BacklogItem, Integration, ScreenAction, ScreenBlock
from mde_spec_builder.plan_view import screen_title
from mde_spec_builder.rules import AuthScreenOverride, Rules

_REF = re.compile(r"^@([a-z][a-z0-9_]*)$")
PUBLIC = "public"
AUTHENTICATED = "authenticated"
MAX_HINTS = 8


def auth_recipe_ids() -> frozenset[str]:
    """Recipes of kind ``auth``: the screens every surface shares (welcome, login, signup)."""
    return frozenset(r.id for r in load_recipes().recipes if r.kind == "auth")


def is_role(auth: str) -> bool:
    return auth.startswith("ROLE-")


def walk_pack_nav(node: PackNavNode) -> Iterator[PackNavNode]:
    yield node
    for child in node.children:
        yield from walk_pack_nav(child)


def nav_screen_keys(node: PackNavNode) -> list[str]:
    """Screen keys in navigation order (depth first, parents before children)."""
    keys: list[str] = []
    for item in walk_pack_nav(node):
        if item.screen is not None:
            key = item.screen[1:]
            if key not in keys:
                keys.append(key)
    return keys


def pack_screen_title(screen: PackScreen) -> str:
    """A short plain title for backlog hints: the ``title`` copy, else from the route."""
    return screen_title(
        ScreenBlock(
            id="SCR-000",
            route=screen.route,
            recipe=screen.recipe,
            purpose=screen.purpose,
            texts=screen.texts,
        )
    )


# --------------------------------------------------------------------------- pruning


def _prune_nav(node: PackNavNode, gone: Collection[str]) -> PackNavNode | None:
    if node.screen is not None and node.screen[1:] in gone:
        return None
    kids = [k for k in (_prune_nav(c, gone) for c in node.children) if k is not None]
    if node.kind in ("tabs", "stack", "modal") and node.screen is None and not kids:
        return None
    return node.model_copy(update={"children": kids})


def _alive_action(action: ScreenAction, gone: Collection[str], drop_ops: Collection[str]) -> bool:
    if action.go is not None:
        match = _REF.match(action.go)
        if match is not None and match.group(1) in gone:
            return False
    return action.calls is None or action.calls not in drop_ops


def _bound_api(bind: str) -> str | None:
    match = re.match(r"^(API-[A-Za-z][A-Za-z0-9]*)(?:\(.*\))?$", bind)
    return match.group(1) if match else None


def _trim(screen: PackScreen, gone: Collection[str], drop_ops: Collection[str]) -> PackScreen:
    actions = [a for a in screen.actions if _alive_action(a, gone, drop_ops)]
    data = [d for d in screen.data if _bound_api(d.bind) not in drop_ops]
    return screen.model_copy(update={"actions": actions, "data": data})


def _reachable(screens: Iterable[PackScreen], nav: PackNavNode) -> set[str]:
    by_key = {s.key: s for s in screens}
    seen = {k for k in nav_screen_keys(nav) if k in by_key}
    queue = deque(sorted(seen))
    while queue:
        for action in by_key[queue.popleft()].actions:
            match = _REF.match(action.go) if action.go is not None else None
            if match is not None and match.group(1) in by_key and match.group(1) not in seen:
                seen.add(match.group(1))
                queue.append(match.group(1))
    return seen


def prune_pack(
    pack: DomainPack,
    gone: Collection[str],
    drop_ops: Collection[str] = frozenset(),
    drop_integrations: Collection[str] = frozenset(),
) -> DomainPack:
    """Remove screens (by key) and operations, and everything left dangling or unreachable."""
    removed = set(gone)
    ops = set(drop_ops)
    while True:
        nav = _prune_nav(pack.navigation, removed)
        if nav is None:
            nav = pack.navigation.model_copy(update={"children": []})
        kept = [_trim(s, removed, ops) for s in pack.screens if s.key not in removed]
        reach = _reachable(kept, nav)
        orphans = {s.key for s in kept} - reach
        if not orphans:
            break
        removed |= orphans
    keys = {s.key for s in kept}
    flows: list[PackFlow] = []
    for flow in pack.flows:
        flow_keys = [k for k in flow.screens if k in keys]
        if len(flow_keys) >= 2 or (flow.primary and flow_keys):
            flows.append(flow.model_copy(update={"screens": flow_keys}))
    if flows and not any(f.primary for f in flows):
        flows[0] = flows[0].model_copy(update={"primary": True})
    return pack.model_copy(
        update={
            "screens": kept,
            "flows": flows,
            "navigation": nav,
            "api_operations": [o for o in pack.api_operations if o.id not in ops],
            "integrations": [i for i in pack.integrations if i.id not in set(drop_integrations)],
        }
    )


# --------------------------------------------------------------------------- surfaces and roles


def surfaces_of(pack: DomainPack) -> list[str]:
    """Surfaces in pack order: the first role's surface first (the main app)."""
    seen: list[str] = []
    for surface in [r.surface for r in pack.roles] + [s.surface for s in pack.screens]:
        if surface not in seen:
            seen.append(surface)
    return seen


def restrict_to_surface(
    pack: DomainPack, surface: str, kept_roles: Collection[str]
) -> tuple[DomainPack, dict[str, list[str]]]:
    """Keep one surface (plus the shared auth screens) and the given roles.

    Other surfaces are separate apps: they are not built here. Returns the narrowed pack and, per
    other surface, a few screen titles (hints for a backlog item). Role-gated screens of a role
    that is not kept go, and so do operations only those roles could call.
    """
    shared = auth_recipe_ids()
    kept = set(kept_roles)
    gone: set[str] = set()
    other: dict[str, list[str]] = {}
    for screen in pack.screens:
        if screen.surface != surface and screen.recipe not in shared:
            gone.add(screen.key)
            hints = other.setdefault(screen.surface, [])
            if len(hints) < MAX_HINTS:
                hints.append(pack_screen_title(screen))
        elif is_role(screen.auth) and screen.auth not in kept:
            gone.add(screen.key)
    ops: list[ApiOperation] = []
    dropped_ops: set[str] = set()
    for op in pack.api_operations:
        if not op.roles:
            ops.append(op)
            continue
        allowed = [r for r in op.roles if r in kept]
        if not allowed:
            dropped_ops.add(op.id)
            continue
        auth = op.auth if not is_role(op.auth) or op.auth in kept else allowed[0]
        ops.append(op.model_copy(update={"roles": allowed, "auth": auth}))
    screens = [
        s.model_copy(update={"surface": surface})
        if s.recipe in shared and s.surface != surface
        else s
        for s in pack.screens
    ]
    roles = [r for r in pack.roles if r.id in kept]
    narrowed = pack.model_copy(update={"roles": roles, "api_operations": ops, "screens": screens})
    return prune_pack(narrowed, gone, dropped_ops), other


# --------------------------------------------------------------------------- sign-in variants


def _override(screen: PackScreen, patch: AuthScreenOverride) -> PackScreen:
    update: dict[str, object] = {}
    if patch.recipe is not None:
        update["recipe"] = patch.recipe
    if patch.purpose is not None:
        update["purpose"] = patch.purpose
    if patch.sections is not None:
        update["sections"] = list(patch.sections)
    if patch.texts:
        update["texts"] = {**screen.texts, **patch.texts}
    if patch.checks is not None:
        update["checks"] = list(patch.checks)
    if patch.states is not None:
        update["states"] = {k: dict(v) for k, v in patch.states.items()}
    return screen.model_copy(update=update)


def _login_actions(
    login: PackScreen, patch: AuthScreenOverride, signup_ref: str | None
) -> list[ScreenAction]:
    """The login screen's actions for a variant: success goes where the old success action went."""
    old = login.actions
    success = next((a.go for a in old if a.go is not None and a.go != signup_ref and a.calls), None)
    if success is None:
        success = next((a.go for a in old if a.go is not None and a.go != signup_ref), None)
    actions: list[ScreenAction] = []
    for item in patch.actions or []:
        if item.to_signup:
            if signup_ref is None:
                continue
            go: str | None = signup_ref
        else:
            go = success if item.success else None
        actions.append(
            ScreenAction(name=item.name, calls=item.calls, go=go, on_success=item.on_success)
        )
    return actions


def apply_auth_method(
    pack: DomainPack, method: str, rules: Rules, user_entity: str | None
) -> DomainPack:
    """Reshape the auth screens (and the operations and integrations behind them) for ``method``.

    A method without a variant, or one that changes nothing, returns the pack as it is.
    """
    variant = rules.auth_methods.get(method)
    if variant is None:
        return pack
    shared = auth_recipe_ids()
    gone: set[str] = set()
    screens = list(pack.screens)
    if variant.drop_auth_screens:
        gone |= {s.key for s in screens if s.recipe in shared}
    for recipe in variant.drop_recipes:
        gone |= {s.key for s in screens if s.recipe == recipe}
    signup = next((s for s in screens if s.recipe == "signup" and s.key not in gone), None)
    signup_ref = f"@{signup.key}" if signup is not None else None
    patched: list[PackScreen] = []
    for screen in screens:
        if screen.key in gone:
            patched.append(screen)
        elif variant.login is not None and screen.recipe.startswith("login-"):
            new = _override(screen, variant.login)
            if variant.login.actions is not None:
                new = new.model_copy(
                    update={"actions": _login_actions(screen, variant.login, signup_ref)}
                )
            patched.append(new)
        elif variant.signup is not None and screen.recipe == "signup":
            patched.append(_override(screen, variant.signup))
        else:
            patched.append(screen)
    ops = list(pack.api_operations)
    drop_ops: set[str] = set()
    drop_ints: set[str] = set()
    if not variant.otp:
        drop_ops |= set(rules.otp_operations)
        drop_ints |= set(rules.otp_integrations)
    have = {o.id for o in ops} - drop_ops
    for template in variant.operations:
        if template.id not in have:
            response = user_entity if template.response_user else None
            ops.append(
                ApiOperation(
                    id=template.id,
                    name=template.name,
                    method=template.method,
                    path=template.path,
                    response_entity=response,
                    auth=PUBLIC,
                    notes=template.notes,
                )
            )
    integrations = [i for i in pack.integrations if i.id not in drop_ints]
    have_ints = {i.id for i in integrations}
    for tpl in variant.integrations:
        if tpl.id not in have_ints:
            integrations.append(
                Integration(id=tpl.id, name=tpl.name, purpose=tpl.purpose, notes=tpl.notes)
            )
    if variant.drop_auth_screens:  # no sign-in at all: nothing is gated any more
        patched = [s.model_copy(update={"auth": PUBLIC}) for s in patched]
        ops = [
            o.model_copy(update={"auth": PUBLIC, "roles": []}) if o.auth != PUBLIC else o
            for o in ops
        ]
    reshaped = pack.model_copy(
        update={"screens": patched, "api_operations": ops, "integrations": integrations}
    )
    return prune_pack(reshaped, gone, drop_ops, drop_ints)


# --------------------------------------------------------------------------- scope guard

_TIER_RANK = {"core": 1, "standard": 2, "extra": 3}


def scope_guard(pack: DomainPack, cap: int) -> tuple[DomainPack, list[BacklogItem]]:
    """Keep at most ``cap`` screens: phase one is the primary flow, then core, then standard,
    then extra features; the rest become backlog items grouped by feature (Phase 2)."""
    if len(pack.screens) <= cap:
        return pack, []
    tiers = {f.id: f.tier for f in pack.features}
    primary = next((f for f in pack.flows if f.primary), None)
    primary_keys = set(primary.screens) if primary is not None else set()
    order = {s.key: i for i, s in enumerate(pack.screens)}

    def rank(screen: PackScreen) -> tuple[int, int]:
        if screen.key in primary_keys:
            return (0, order[screen.key])
        tier = tiers.get(screen.feature, "core") if screen.feature else "core"
        return (_TIER_RANK[tier], order[screen.key])

    by_priority = sorted(pack.screens, key=rank)
    gone = {s.key for s in by_priority[cap:]}
    original = list(pack.screens)
    trimmed = prune_pack(pack, gone)
    # pruning cascades (orphans go too), so the result can be below the cap: that is fine
    survivors = {s.key for s in trimmed.screens}
    removed = [s for s in original if s.key not in survivors]
    labels = {f.id: f.label for f in pack.features}
    groups: dict[str, list[PackScreen]] = {}
    for screen in removed:
        title = labels.get(screen.feature, "More screens") if screen.feature else "More screens"
        groups.setdefault(title, []).append(screen)
    backlog = [
        BacklogItem(
            title=title,
            reason="phase 2: over the screen limit",
            screens_hint=[pack_screen_title(s) for s in items[:MAX_HINTS]],
        )
        for title, items in groups.items()
    ]
    return trimmed, backlog
