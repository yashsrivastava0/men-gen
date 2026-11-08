"""Domain Pack loader, feature selection and validation (brief 8.4 step B2, 8.6).

A pack file in ``data/packs/<id>.yaml`` is a ``mde_contracts.content.DomainPack`` plus one
sidecar key, ``feature_parts`` (see ``FeatureParts``): the pieces of the pack that exist only
when a feature is on but that the contract cannot gate by itself (API operations, entities,
integrations, and sections or actions inside a screen that is always on).

Reference convention (normative; mirrors the ``mde_contracts.content`` docstring):

- Screens are identified by a local key and referenced as ``@key`` (``go: "@cart"``, navigation
  ``screen: "@cart"``). The spec builder assigns ``SCR-###`` ids and rewrites ``@key``.
- Entities, API operations, roles and integrations already carry their final ids.
- A screen or flow with ``feature`` set is included only when that feature is selected;
  core features are always on.

``DomainPack.navigation`` is a ``PackNavNode`` (``screen`` is ``@key``) and the feature sidecar is
``DomainPack.feature_parts`` (contracts 1.0.1, ADR-0003). ``LoadedPack`` is kept as a thin wrapper
so existing callers keep working.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Iterable, Mapping
from functools import lru_cache
from importlib import resources

import yaml

from mde_content.recipes import recipe_ids
from mde_content.themes import theme_ids
from mde_contracts.common import Frozen
from mde_contracts.content import (
    DomainPack,
    FeatureParts,
    FeatureScreenParts,
    PackFeature,
    PackFlow,
    PackNavNode,
    PackScreen,
)
from mde_contracts.spec import ApiOperation, ScreenAction

SENSITIVE_DOMAINS = frozenset(
    {"health", "finance", "children", "identity_documents", "precise_location"}
)
SCREEN_CAP = 50  # guided cap (spec 5); the effective cap is policy.limits.max_screens

_KEY = re.compile(r"^[a-z][a-z0-9_]*$")
_REF = re.compile(r"^@([a-z][a-z0-9_]*)$")
_API_BIND = re.compile(r"^(API-[A-Za-z][A-Za-z0-9]*)(?:\(.*\))?$")
_ENT_BIND = re.compile(r"^(ENT-[A-Za-z][A-Za-z0-9]*)\.([a-z][a-z0-9_]*)$")
_AUTH_ROLE = re.compile(r"^ROLE-[a-z][a-z0-9_]*$")


# --------------------------------------------------------------------------- models


class LoadedPack(Frozen):
    """A pack plus its feature sidecar (``feature_parts`` mirrors ``pack.feature_parts``)."""

    pack: DomainPack
    feature_parts: dict[str, FeatureParts] = {}  # feature id -> parts


# --------------------------------------------------------------------------- loading


def build_pack(raw: Mapping[str, object]) -> LoadedPack:
    """Validate a raw pack mapping (the parsed YAML) into a ``LoadedPack``."""
    pack = DomainPack.model_validate(dict(raw))
    return LoadedPack(pack=pack, feature_parts=pack.feature_parts)


@lru_cache(maxsize=1)
def _all() -> dict[str, LoadedPack]:
    folder = resources.files("mde_content").joinpath("data/packs")
    loaded: dict[str, LoadedPack] = {}
    for item in sorted(folder.iterdir(), key=lambda p: p.name):
        if not item.name.endswith(".yaml"):
            continue
        entry = build_pack(yaml.safe_load(item.read_text(encoding="utf-8")))
        if entry.pack.id in loaded:
            raise ValueError(f"duplicate pack id {entry.pack.id!r} in {item.name}")
        loaded[entry.pack.id] = entry
    return loaded


def pack_ids() -> list[str]:
    """Ids of every packaged Domain Pack, sorted."""
    return sorted(_all())


def load_pack_file(pack_id: str) -> LoadedPack:
    """The full pack (every feature on) plus its ``feature_parts`` sidecar."""
    try:
        return _all()[pack_id]
    except KeyError:
        raise KeyError(f"unknown domain pack: {pack_id!r}") from None


def load_pack(pack_id: str) -> DomainPack:
    """The full pack with every feature on; ``KeyError`` for an unknown id."""
    return load_pack_file(pack_id).pack


def load_packs() -> dict[str, DomainPack]:
    """Every packaged pack by id."""
    return {pid: lp.pack for pid, lp in _all().items()}


# --------------------------------------------------------------------------- features


def core_features(pack: DomainPack) -> list[str]:
    """Feature ids that are always on (tier ``core``)."""
    return [f.id for f in pack.features if f.tier == "core"]


def default_features(pack: DomainPack) -> list[str]:
    """Core features plus the ones ticked by default, in pack order."""
    return [f.id for f in pack.features if f.tier == "core" or f.default_on]


def feature(pack: DomainPack, feature_id: str) -> PackFeature:
    for item in pack.features:
        if item.id == feature_id:
            return item
    raise KeyError(f"unknown feature {feature_id!r} in pack {pack.id!r}")


def _bound_api(bind: str) -> str | None:
    m = _API_BIND.match(bind)
    return m.group(1) if m else None


def _bound_entity(bind: str) -> str | None:
    m = _ENT_BIND.match(bind)
    return m.group(1) if m else None


def _prune_nav(node: PackNavNode, gone: set[str]) -> PackNavNode | None:
    if node.screen is not None and node.screen[1:] in gone:
        return None
    kids = [k for k in (_prune_nav(c, gone) for c in node.children) if k is not None]
    if node.kind in ("tabs", "stack", "modal") and node.screen is None and not kids:
        return None
    return node.model_copy(update={"children": kids})


def select_features(
    source: str | LoadedPack, features: Collection[str] | None = None
) -> DomainPack:
    """The pack as it expands for a feature selection (the data half of B2).

    ``features`` is ``scope.features``: ``None`` means the pack defaults. Core features are always
    on. Screens and flows of an unselected feature are removed; so are the parts the feature owns
    (operations, entities, integrations, sections and actions inside always-on screens) and every
    reference left dangling (actions and navigation nodes that pointed at a removed screen or
    operation, bindings to a removed operation or entity, unused enums). An unknown feature id
    raises ``ValueError``.
    """
    loaded = load_pack_file(source) if isinstance(source, str) else source
    pack = loaded.pack
    known = {f.id for f in pack.features}
    chosen = set(default_features(pack)) if features is None else set(features)
    if unknown := sorted(chosen - known):
        raise ValueError(f"unknown feature(s) for pack {pack.id!r}: {', '.join(unknown)}")
    on = chosen | set(core_features(pack))
    off = sorted(known - on)
    parts = [loaded.feature_parts[f] for f in off if f in loaded.feature_parts]

    drop_ops = {o for p in parts for o in p.operations}
    drop_ents = {e for p in parts for e in p.entities}
    drop_ints = {i for p in parts for i in p.integrations}
    kept = [s for s in pack.screens if s.feature is None or s.feature in on]
    gone = {s.key for s in pack.screens} - {s.key for s in kept}

    def alive_action(a: ScreenAction) -> bool:
        if a.go is not None and a.go.startswith("@") and a.go[1:] in gone:
            return False
        return a.calls not in drop_ops

    def alive_bind(bind: str) -> bool:
        return _bound_api(bind) not in drop_ops and _bound_entity(bind) not in drop_ents

    def trim(screen: PackScreen) -> PackScreen:
        sections = {
            x for p in parts if screen.key in p.screens for x in p.screens[screen.key].sections
        }
        actions = {
            x for p in parts if screen.key in p.screens for x in p.screens[screen.key].actions
        }
        binds = {x for p in parts if screen.key in p.screens for x in p.screens[screen.key].data}
        return screen.model_copy(
            update={
                "sections": [s for s in screen.sections if s not in sections],
                "actions": [a for a in screen.actions if a.name not in actions and alive_action(a)],
                "data": [d for d in screen.data if d.bind not in binds and alive_bind(d.bind)],
            }
        )

    flows: list[PackFlow] = []
    for flow in pack.flows:
        if flow.feature is not None and flow.feature not in on:
            continue
        keys = [k for k in flow.screens if k not in gone]
        if len(keys) >= 2 or (flow.primary and keys):
            flows.append(flow.model_copy(update={"screens": keys}))

    entities = [e for e in pack.entities if e.id not in drop_ents]
    used_enums = {f.enum for e in entities for f in e.fields if f.enum}
    nav = _prune_nav(pack.navigation, gone)
    return pack.model_copy(
        update={
            "features": [f for f in pack.features if f.id in on],
            "screens": [trim(s) for s in kept],
            "flows": flows,
            "navigation": nav if nav is not None else pack.navigation,
            "entities": entities,
            "enums": [e for e in pack.enums if e.name in used_enums],
            "api_operations": [o for o in pack.api_operations if o.id not in drop_ops],
            "integrations": [i for i in pack.integrations if i.id not in drop_ints],
            # an expanded pack only carries the sidecar of the features that stayed on
            "feature_parts": {
                f: p.model_copy(
                    update={"screens": {k: v for k, v in p.screens.items() if k not in gone}}
                )
                for f, p in pack.feature_parts.items()
                if f in on
            },
        }
    )


# --------------------------------------------------------------------------- validation


def _dups(label: str, values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for v in values:
        if v in seen:
            out.append(f"duplicate {label}: {v}")
        seen.add(v)
    return out


def _nav_nodes(node: PackNavNode) -> Iterable[PackNavNode]:
    yield node
    for child in node.children:
        yield from _nav_nodes(child)


def _auth_rank(auth: str) -> int:
    return {"public": 0, "authenticated": 1}.get(auth, 2)


def _screen_refs(pack: DomainPack) -> tuple[set[str], set[str]]:
    """API ids and entity ids referenced by screen bindings and actions."""
    apis: set[str] = set()
    ents: set[str] = set()
    for s in pack.screens:
        apis.update(a.calls for a in s.actions if a.calls)
        for d in s.data:
            if (api := _bound_api(d.bind)) is not None:
                apis.add(api)
            if (ent := _bound_entity(d.bind)) is not None:
                ents.add(ent)
    return apis, ents


def _check_entities(pack: DomainPack) -> list[str]:
    out = _dups("entity id", (e.id for e in pack.entities))
    out += _dups("enum name", (e.name for e in pack.enums))
    ent_ids = {e.id: e for e in pack.entities}
    enum_names = {e.name for e in pack.enums}
    for e in pack.entities:
        names = [f.name for f in e.fields]
        out += _dups(f"field in {e.id}", names)
        for f in e.fields:
            if not _KEY.match(f.name):
                out.append(f"{e.id}.{f.name}: field names are snake_case")
            if f.type == "ref" and (f.ref is None or f.ref not in ent_ids):
                out.append(f"{e.id}.{f.name}: ref to unknown entity {f.ref!r}")
            if f.type != "ref" and f.ref is not None:
                out.append(f"{e.id}.{f.name}: ref set on a non-ref field")
            if f.type == "enum" and (f.enum is None or f.enum not in enum_names):
                out.append(f"{e.id}.{f.name}: unknown enum {f.enum!r}")
            if f.type != "enum" and f.enum is not None:
                out.append(f"{e.id}.{f.name}: enum set on a non-enum field")
        sm = e.state_machine
        if sm is not None:
            if sm.field not in names:
                out.append(f"{e.id}: state machine field {sm.field!r} is not a field")
            for t in sm.transitions:
                if t.source not in sm.states or t.target not in sm.states:
                    out.append(f"{e.id}: transition {t.source}->{t.target} uses an unknown state")
    used_enums = {f.enum for e in pack.entities for f in e.fields if f.enum}
    out += [f"unused enum: {n}" for n in sorted(enum_names - used_enums)]
    return out


def _check_operations(pack: DomainPack, roles: set[str]) -> list[str]:
    out = _dups("api id", (o.id for o in pack.api_operations))
    out += _dups("api method+path", (f"{o.method} {o.path}" for o in pack.api_operations))
    ent_ids = {e.id for e in pack.entities}
    for o in pack.api_operations:
        if not o.path.startswith("/"):
            out.append(f"{o.id}: path must start with '/'")
        for label, ent in (("request", o.request_entity), ("response", o.response_entity)):
            if ent is not None and ent not in ent_ids:
                out.append(f"{o.id}: {label} entity {ent} does not exist")
        if o.response_many and o.response_entity is None:
            out.append(f"{o.id}: response_many without a response entity")
        out += [f"{o.id}: unknown role {r}" for r in o.roles if r not in roles]
        if _auth_rank(o.auth) == 2 and (o.auth not in roles or o.auth not in o.roles):
            out.append(f"{o.id}: auth {o.auth} must be a defined role listed in roles")
    return out


def _check_screens(pack: DomainPack, roles: dict[str, str], features: set[str]) -> list[str]:
    out = _dups("screen key", (s.key for s in pack.screens))
    out += _dups("screen route", (s.route for s in pack.screens))
    recipes = recipe_ids()
    ops = {o.id: o for o in pack.api_operations}
    ents = {e.id: {f.name for f in e.fields} for e in pack.entities}
    keys = {s.key for s in pack.screens}
    surfaces = set(roles.values())
    for s in pack.screens:
        where = f"screen {s.key}"
        if not _KEY.match(s.key):
            out.append(f"{where}: key must be snake_case")
        if not s.route.startswith("/"):
            out.append(f"{where}: route must start with '/'")
        if s.recipe not in recipes:
            out.append(f"{where}: unknown recipe {s.recipe!r}")
        if s.feature is not None and s.feature not in features:
            out.append(f"{where}: unknown feature {s.feature!r}")
        if s.surface not in surfaces:
            out.append(f"{where}: no role has surface {s.surface!r}")
        if _auth_rank(s.auth) == 2:
            if s.auth not in roles:
                out.append(f"{where}: auth names unknown role {s.auth}")
            elif roles[s.auth] != s.surface:
                out.append(
                    f"{where}: auth {s.auth} is a {roles[s.auth]} role on a {s.surface} screen"
                )
        if not 1 <= len(s.checks) <= 3:
            out.append(f"{where}: needs 1 to 3 checks, has {len(s.checks)}")
        out += _dups(f"section in {where}", s.sections)
        out += _dups(f"action in {where}", (a.name for a in s.actions))
        out += [
            f"{where}: section {x!r} is not snake_case" for x in s.sections if not _KEY.match(x)
        ]
        out += [f"{where}: copy key {x!r} is not snake_case" for x in s.texts if not _KEY.match(x)]
        for a in s.actions:
            if a.calls is None and a.go is None:
                out.append(f"{where}: action {a.name} neither calls nor goes anywhere")
            if a.go is not None:
                target = _REF.match(a.go)
                if target is None:
                    out.append(f"{where}: action {a.name} go {a.go!r} is not an @key")
                elif target.group(1) not in keys:
                    out.append(f"{where}: action {a.name} goes to unknown screen {a.go}")
            if a.calls is not None:
                _check_call(out, where, a.name, a.calls, s.auth, ops, action=True)
        for d in s.data:
            if (api := _bound_api(d.bind)) is not None:
                _check_call(out, where, d.bind, api, s.auth, ops, action=False)
            elif (m2 := _ENT_BIND.match(d.bind)) is not None:
                if m2.group(1) not in ents or m2.group(2) not in ents[m2.group(1)]:
                    out.append(f"{where}: binding {d.bind} names an unknown entity field")
            else:
                out.append(f"{where}: binding {d.bind!r} is neither API-x(...) nor ENT-x.field")
    return out


def _check_call(
    out: list[str],
    where: str,
    label: str,
    api: str,
    screen_auth: str,
    ops: dict[str, ApiOperation],
    *,
    action: bool,
) -> None:
    """A screen may not read or call anything its audience may not.

    One relaxation: an *action* on a public screen may call an operation that needs sign-in
    (the app asks the person to sign in first, e.g. "add to cart" on a product page). Reading
    data and role-gated operations stay strict.
    """
    op = ops.get(api)
    if op is None:
        out.append(f"{where}: {label} uses unknown operation {api}")
        return
    op_rank, screen_rank = _auth_rank(op.auth), _auth_rank(screen_auth)
    prompts_sign_in = action and op_rank == 1 and screen_rank == 0
    if (op_rank == 2 and op.auth != screen_auth) or (op_rank > screen_rank and not prompts_sign_in):
        out.append(f"{where}: {label} uses {api} ({op.auth}) from a {screen_auth} screen")


def _check_flows(pack: DomainPack, features: set[str], links: bool) -> list[str]:
    out = _dups("flow key", (f.key for f in pack.flows))
    by_key = {s.key: s for s in pack.screens}
    core = set(core_features(pack))
    primaries = [f for f in pack.flows if f.primary]
    if len(primaries) != 1:
        out.append(f"expected exactly one primary flow, found {len(primaries)}")
    for f in pack.flows:
        where = f"flow {f.key}"
        if not _KEY.match(f.key):
            out.append(f"{where}: key must be snake_case")
        if f.feature is not None and f.feature not in features:
            out.append(f"{where}: unknown feature {f.feature!r}")
        if len(f.screens) < 2:
            out.append(f"{where}: a flow needs at least two screens")
        missing = [k for k in f.screens if k not in by_key]
        out += [f"{where}: unknown screen {k}" for k in missing]
        if f.primary:
            if f.feature is not None and f.feature not in core:
                out.append(f"{where}: the primary flow must not depend on an optional feature")
            out += [
                f"{where}: primary flow screen {k} depends on optional feature {by_key[k].feature}"
                for k in f.screens
                if k in by_key and by_key[k].feature is not None and by_key[k].feature not in core
            ]
        if links and not missing:
            for a, b in zip(f.screens, f.screens[1:], strict=False):
                if not any(x.go == f"@{b}" for x in by_key[a].actions):
                    out.append(f"{where}: {a} has no action that goes to {b}")
    return out


def _check_navigation(pack: DomainPack, roles: set[str]) -> list[str]:
    nodes = list(_nav_nodes(pack.navigation))
    out = _dups("navigation node id", (n.id for n in nodes))
    keys = {s.key for s in pack.screens}
    listed: list[str] = []
    for n in nodes:
        if not n.route.startswith("/"):
            out.append(f"nav {n.id}: route must start with '/'")
        if n.auth not in ("public", "authenticated") and n.auth not in roles:
            out.append(f"nav {n.id}: unknown auth {n.auth!r}")
        if n.screen is not None:
            m = _REF.match(n.screen)
            if m is None or m.group(1) not in keys:
                out.append(f"nav {n.id}: screen {n.screen!r} is not a pack screen @key")
            else:
                listed.append(m.group(1))
                route = next(s.route for s in pack.screens if s.key == m.group(1))
                if n.route != route:
                    out.append(f"nav {n.id}: route {n.route} differs from screen route {route}")
        elif n.kind in ("screen", "modal") and not n.children:
            out.append(f"nav {n.id}: a {n.kind} node needs a screen")
        if n.kind == "tabs" and any(c.kind not in ("stack", "screen") for c in n.children):
            out.append(f"nav {n.id}: tabs may only contain stack or screen nodes")
    out += _dups("screen in navigation", listed)
    # Reachability: listed in navigation, or the target of an action of a reachable screen.
    by_key = {s.key: s for s in pack.screens}
    reach = set(listed) & set(by_key)
    queue = list(reach)
    while queue:
        for a in by_key[queue.pop()].actions:
            target = _REF.match(a.go) if a.go is not None else None
            if target is not None and target.group(1) in by_key and target.group(1) not in reach:
                reach.add(target.group(1))
                queue.append(target.group(1))
    out += [
        f"screen {k} is not reachable from navigation or an action"
        for k in sorted(set(by_key) - reach)
    ]
    return out


def _check_parts(loaded: LoadedPack, features: set[str]) -> list[str]:
    pack = loaded.pack
    out: list[str] = []
    ops = {o.id for o in pack.api_operations}
    ents = {e.id for e in pack.entities}
    ints = {i.id for i in pack.integrations}
    screens = {s.key: s for s in pack.screens}
    owner: dict[str, str] = {}
    for fid, p in loaded.feature_parts.items():
        if fid not in features:
            out.append(f"feature_parts: unknown feature {fid!r}")
        for label, ids, universe in (
            ("operation", p.operations, ops),
            ("entity", p.entities, ents),
            ("integration", p.integrations, ints),
        ):
            for x in ids:
                if x not in universe:
                    out.append(f"feature_parts[{fid}]: unknown {label} {x}")
                if x in owner and owner[x] != fid:
                    out.append(f"feature_parts: {x} owned by both {owner[x]} and {fid}")
                owner[x] = fid
        for key, sp in p.screens.items():
            s = screens.get(key)
            if s is None:
                out.append(f"feature_parts[{fid}]: unknown screen {key}")
                continue
            if s.feature == fid:
                out.append(f"feature_parts[{fid}]: screen {key} is already gated by the feature")
            out += [
                f"feature_parts[{fid}].{key}: unknown section {x}"
                for x in sp.sections
                if x not in s.sections
            ]
            out += [
                f"feature_parts[{fid}].{key}: unknown action {x}"
                for x in sp.actions
                if x not in {a.name for a in s.actions}
            ]
            out += [
                f"feature_parts[{fid}].{key}: unknown binding {x}"
                for x in sp.data
                if x not in {d.bind for d in s.data}
            ]
    return out


def _check_entity_use(pack: DomainPack) -> list[str]:
    apis, bound_ents = _screen_refs(pack)
    op_ents = {x for o in pack.api_operations for x in (o.request_entity, o.response_entity) if x}
    ref_ents = {f.ref for e in pack.entities for f in e.fields if f.ref}
    used = op_ents | bound_ents | ref_ents
    out = [f"unused entity: {e.id}" for e in pack.entities if e.id not in used]
    out += [f"unused API operation: {o.id}" for o in pack.api_operations if o.id not in apis]
    return out


def validate_pack(source: LoadedPack | DomainPack, *, check_flow_links: bool = True) -> list[str]:
    """Every problem found in a pack (empty list means valid).

    Checks that every referenced id exists (entities, roles, recipes, themes, features, screen
    keys, API ids), ids and keys are unique and well formed, every screen has a recipe from the
    library, flows resolve and are linked screen to screen, every screen is reachable from the
    navigation tree or an action, screens never call operations they may not use, and the screen
    count stays within the guided cap. Works on a full pack and on a ``select_features`` result
    (pass ``check_flow_links=False`` there: removing a screen can break a flow's chain).
    """
    loaded = (
        source
        if isinstance(source, LoadedPack)
        else LoadedPack(pack=source, feature_parts=source.feature_parts)
    )
    pack = loaded.pack
    features = {f.id for f in pack.features}
    roles = {r.id: r.surface for r in pack.roles}
    out = _dups("feature id", (f.id for f in pack.features))
    out += _dups("role id", roles)
    out += _dups("integration id", (i.id for i in pack.integrations))
    out += _dups("compliance trigger id", (t.id for t in pack.compliance_triggers))
    out += [f"feature {f.id}: ids are snake_case" for f in pack.features if not _KEY.match(f.id)]
    out += _check_entities(pack)
    out += _check_operations(pack, set(roles))
    out += _check_screens(pack, roles, features)
    out += _check_flows(pack, features, check_flow_links)
    out += _check_navigation(pack, set(roles))
    out += _check_parts(loaded, features)
    out += _check_entity_use(pack)
    if len(pack.screens) > SCREEN_CAP:
        out.append(f"{len(pack.screens)} screens exceeds the cap of {SCREEN_CAP}")
    themes = set(theme_ids())
    out += [f"unknown theme candidate {t!r}" for t in pack.theme_candidates if t not in themes]
    if pack.default_theme is not None and pack.default_theme not in pack.theme_candidates:
        out.append(f"default theme {pack.default_theme!r} is not one of the candidates")
    out += [
        f"trigger {t.id}: unknown sensitive domain {t.sensitive_domain!r}"
        for t in pack.compliance_triggers
        if t.sensitive_domain not in SENSITIVE_DOMAINS
    ]
    out += [
        f"{i.id}: payments integrations must default to mock"
        for i in pack.integrations
        if i.id.startswith("INT-payments") and i.mode != "mock"
    ]
    out += [
        f"entity {e.id}: plain_name is empty" for e in pack.entities if not e.plain_name.strip()
    ]
    return out


def screens_of(pack: DomainPack, surface: str | None = None) -> list[PackScreen]:
    """Screens of a pack, optionally only one surface (``customer``, ``staff``, ...)."""
    return [s for s in pack.screens if surface is None or s.surface == surface]


__all__ = [
    "SCREEN_CAP",
    "SENSITIVE_DOMAINS",
    "FeatureParts",
    "FeatureScreenParts",
    "LoadedPack",
    "PackNavNode",
    "build_pack",
    "core_features",
    "default_features",
    "feature",
    "load_pack",
    "load_pack_file",
    "load_packs",
    "pack_ids",
    "screens_of",
    "select_features",
    "validate_pack",
]
