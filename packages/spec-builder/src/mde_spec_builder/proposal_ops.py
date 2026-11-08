"""The 21 proposal operations: each one checks a ``Proposal`` against the spec and, if it holds,
returns the changed spec. Pure functions; any problem raises ``Reject`` (the proposal is dropped).

Field conventions of ``mde_contracts.calls.Proposal`` as used here (also in the call prompts):

=====================  ==========================================================================
kind                   fields
=====================  ==========================================================================
add_screen             ``route``, ``recipe``, ``name`` (title), ``purpose``; optional ``target``
                       (a screen it opens from), ``flow`` (a non-primary flow), ``surface``
rename_screen          ``target`` SCR, ``name``
set_screen_purpose     ``target`` SCR, ``purpose``
set_screen_copy        ``target`` SCR, ``pairs`` (copy key -> text)
add_screen_action      ``target`` SCR, ``name``, ``pairs`` with ``calls`` (API id) and/or ``go``
                       (SCR / NAV id / back / close) and optional ``on_success``
add_screen_section     ``target`` SCR, ``items`` (section names)
add_screen_check       ``target`` SCR, ``items`` (testable sentences)
add_screen_state       ``target`` SCR, ``name`` (state), ``pairs`` (deviation key -> text)
add_flow_screen        ``target`` FLW (not primary), ``items`` (SCR ids appended)
add_entity             ``name``, ``fields``; optional ``purpose`` (plain plural name)
add_entity_fields      ``target`` ENT, ``fields``
add_api_operation      ``name``, ``method``, ``path``; optional ``request_entity``,
                       ``response_entity``, ``items`` (ROLE ids), ``purpose`` (notes), ``pairs``
                       (``auth``, ``response_many``)
add_role               ``name``; optional ``purpose`` (description), ``surface``
set_design_token       ``pairs`` (``colors_light.primary`` -> ``#RRGGBB``, ``radius.md`` -> ``12``,
                       ``type_scale.body.size`` -> ``16``)
add_imagery_rule       ``items``
add_integration        ``name``, ``purpose``; optional ``pairs`` (``provider``, ``notes``)
set_integration_mode   ``target`` INT, ``name`` (mock / adapter / real)
set_behavior_item      ``pairs`` (field -> text; list fields are appended to)
add_acceptance_check   ``items``
fix_reference          ``target`` (the item holding the reference), ``pairs`` (dangling id -> an
                       existing id of the same kind)
remove_item            ``target`` (SCR / FLW / ENT / API / ROLE / INT id)
=====================  ==========================================================================
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import cast

from mde_content.stack import capability
from mde_contracts.calls import FieldProposal, Proposal, ProposalKind
from mde_contracts.content import Recipe
from mde_contracts.policy import Limits
from mde_contracts.research import ResearchNote
from mde_contracts.spec import (
    ApiOperation,
    AppSpec,
    DesignTokens,
    Entity,
    FieldDef,
    FlowBlock,
    Integration,
    NavNode,
    Role,
    ScreenAction,
    ScreenBlock,
    TypeStyle,
)
from mde_spec_builder import design
from mde_spec_builder import sanitize as sx
from mde_spec_builder.sanitize import Reject
from mde_spec_builder.sequence import primary_flow
from mde_spec_builder.validators.leaks import has_secret
from mde_spec_loader.fragments import walk_nav

# caps (characters unless noted)
MAX_TITLE = 60
MAX_PURPOSE = 200
MAX_COPY = 120
MAX_COPY_KEYS = 12
MAX_CHECK = 160
MAX_CHECKS = 8
MAX_SECTION = 32
MAX_SECTIONS = 14
MAX_ACTIONS = 12
MAX_STATES = 6
MAX_FLOW_SCREENS = 12
MAX_ENTITIES = 60
MAX_FIELDS = 30
MAX_NEW_FIELDS = 12
MAX_OPERATIONS = 120
MAX_ROLES = 8
MAX_INTEGRATIONS = 16
MAX_IMAGERY = 8
MAX_ACCEPTANCE = 24
MAX_BEHAVIOR_ITEMS = 16
MAX_SCREEN_NUMBER = 999

_ID = re.compile(
    r"(?<![A-Za-z0-9_-])((?:SCR|FLW)-\d{3}|(?:ENT|API|INT)-[A-Za-z][A-Za-z0-9]*|ROLE-[a-z][a-z0-9_]*|NAV-\d{3})(?![A-Za-z0-9_])"
)
_FIELD_TYPES = {
    "string",
    "text",
    "int",
    "number",
    "bool",
    "datetime",
    "date",
    "money",
    "ref",
    "image",
}
_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}
_BEHAVIOR_LISTS = (
    "deep_links",
    "notifications",
    "analytics_events",
    "permission_prompts",
    "error_conventions",
    "accessibility",
)
_PATH = re.compile(r"^/[A-Za-z0-9_\-/{}.]*$")


@dataclass(frozen=True)
class OpsContext:
    """What the operations need besides the spec."""

    recipes: Mapping[str, Recipe]
    limits: Limits
    note: ResearchNote | None = None
    excluded: frozenset[str] = frozenset()
    allow_real: bool = False


@dataclass(frozen=True)
class Change:
    spec: AppSpec
    touched: list[str]  # ids edited or created
    created: list[str] = field(default_factory=list)  # ids created


# --------------------------------------------------------------------------- lookups


def _screen(spec: AppSpec, screen_id: str | None) -> ScreenBlock:
    for screen in spec.screens:
        if screen.id == screen_id:
            return screen
    raise Reject("unknown_target", f"screen {screen_id} does not exist")


def _flow(spec: AppSpec, flow_id: str | None) -> FlowBlock:
    for flow in spec.flows:
        if flow.id == flow_id:
            return flow
    raise Reject("unknown_target", f"flow {flow_id} does not exist")


def _entity(spec: AppSpec, entity_id: str | None) -> Entity:
    for entity in spec.data.entities:
        if entity.id == entity_id:
            return entity
    raise Reject("unknown_target", f"entity {entity_id} does not exist")


def _role_ids(spec: AppSpec) -> set[str]:
    return {r.id for r in spec.roles.roles}


def _surfaces(spec: AppSpec) -> list[str]:
    seen: list[str] = []
    for surface in [r.surface for r in spec.roles.roles] + [s.surface for s in spec.screens]:
        if surface not in seen:
            seen.append(surface)
    return seen


def all_item_ids(spec: AppSpec) -> set[str]:
    ids = {s.id for s in spec.screens} | {f.id for f in spec.flows}
    ids |= {e.id for e in spec.data.entities} | {o.id for o in spec.api.operations}
    ids |= {r.id for r in spec.roles.roles} | {i.id for i in spec.integrations.items}
    ids |= {n.id for n in walk_nav(spec.navigation.root)}
    return ids


def _set_screen(spec: AppSpec, new: ScreenBlock) -> AppSpec:
    return spec.model_copy(update={"screens": [new if s.id == new.id else s for s in spec.screens]})


def _source(p: Proposal) -> str:
    return "research" if p.source == "research" else "brief"


def _ref(p: Proposal) -> str | None:
    return p.research_ref if p.source == "research" else None


def _next_screen_id(spec: AppSpec) -> str:
    numbers = [int(s.id[4:]) for s in spec.screens]
    number = max(numbers, default=0) + 1
    if number > MAX_SCREEN_NUMBER:
        raise Reject("limit", "no screen ids left")
    return f"SCR-{number:03d}"


def _next_nav_id(spec: AppSpec) -> str:
    numbers = [int(n.id[4:]) for n in walk_nav(spec.navigation.root) if n.id[4:].isdigit()]
    return f"NAV-{max(numbers, default=0) + 1:03d}"


# --------------------------------------------------------------------------- navigation helpers


def _path_to(node: NavNode, screen_id: str, trail: list[NavNode]) -> list[NavNode] | None:
    here = [*trail, node]
    if node.screen == screen_id:
        return here
    for child in node.children:
        found = _path_to(child, screen_id, here)
        if found is not None:
            return found
    return None


def _container_id(spec: AppSpec, target: str | None) -> str:
    """The navigation node a new screen is added under: the stack that holds ``target``, else the
    first stack under the tabs, else the root."""
    root = spec.navigation.root
    if target is not None:
        path = _path_to(root, target, [])
        if path is not None:
            for node in reversed(path):
                if node.kind == "stack":
                    return node.id
            return path[-2].id if len(path) > 1 else root.id
    for node in walk_nav(root):
        if node.kind == "tabs":
            stacks = [c for c in node.children if c.kind == "stack"]
            if stacks:
                return stacks[0].id
    return root.id


def _add_child(node: NavNode, parent_id: str, child: NavNode) -> NavNode:
    if node.id == parent_id:
        return node.model_copy(update={"children": [*node.children, child]})
    return node.model_copy(
        update={"children": [_add_child(c, parent_id, child) for c in node.children]}
    )


def _remove_screen_nodes(node: NavNode, screen_id: str) -> NavNode | None:
    if node.screen == screen_id and not node.children:
        return None
    kids = [k for k in (_remove_screen_nodes(c, screen_id) for c in node.children) if k]
    if node.kind in ("tabs", "stack", "auth_gate") and node.screen is None and not kids:
        return None
    return node.model_copy(update={"children": kids})


# --------------------------------------------------------------------------- screens


def _default_auth(spec: AppSpec) -> str:
    return "authenticated" if any(s.auth != "public" for s in spec.screens) else "public"


def add_screen(spec: AppSpec, p: Proposal, ctx: OpsContext) -> Change:
    path = sx.route(p.route)
    title = sx.clean(p.name, MAX_TITLE, "name")
    purpose = sx.clean(p.purpose, MAX_PURPOSE, "purpose")
    recipe_id = sx.clean(p.recipe, 40, "recipe")
    recipe = ctx.recipes.get(recipe_id)
    if recipe is None:
        raise Reject("invalid", f"recipe {recipe_id} is not in the recipe library")
    if len(spec.screens) >= ctx.limits.max_screens:
        raise Reject("limit", f"the app already has {ctx.limits.max_screens} screens")
    if any(s.route == path for s in spec.screens):
        raise Reject("duplicate", f"a screen already uses the route {path}")
    parent = _screen(spec, p.target) if p.target else None
    flow = _flow(spec, p.flow) if p.flow else None
    if flow is not None and flow.primary:
        raise Reject("protected", "the primary journey is not changed by proposals")
    if flow is not None and len(flow.screens) >= MAX_FLOW_SCREENS:
        raise Reject("limit", f"flow {flow.id} already has {MAX_FLOW_SCREENS} screens")
    surface = sx.clean(p.surface, 32, "surface", required=False) or (
        parent.surface if parent else (spec.screens[0].surface if spec.screens else "customer")
    )
    if surface not in _surfaces(spec):
        raise Reject("invalid", f"surface {surface} is not one of the app's surfaces")
    screen_id = _next_screen_id(spec)
    auth = parent.auth if parent is not None else _default_auth(spec)
    block = ScreenBlock(
        id=screen_id,
        route=path,
        surface=surface,
        recipe=recipe_id,
        purpose=purpose,
        title=title,
        auth=auth,
        flow=flow.id if flow is not None else None,
        sections=list(recipe.default_sections),
        source=_source(p),  # type: ignore[arg-type]
        research_ref=_ref(p),
    )
    node = NavNode(
        id=_next_nav_id(spec),
        route=path,
        kind="screen",
        screen=screen_id,
        label=title,
        auth=auth,
    )
    root = _add_child(spec.navigation.root, _container_id(spec, p.target), node)
    screens = [*spec.screens, block]
    touched = [screen_id]
    if parent is not None:
        name = sx.snake_case(f"open {title}")[:MAX_SECTION].rstrip("_")
        existing = {a.name for a in parent.actions}
        if name and name not in existing and len(parent.actions) < MAX_ACTIONS:
            linked = parent.model_copy(
                update={"actions": [*parent.actions, ScreenAction(name=name, go=screen_id)]}
            )
            screens = [linked if s.id == parent.id else s for s in screens]
            touched.append(parent.id)
    flows = spec.flows
    if flow is not None:
        grown = flow.model_copy(update={"screens": [*flow.screens, screen_id], "end": screen_id})
        flows = [grown if f.id == flow.id else f for f in spec.flows]
        touched.append(flow.id)
    changed = spec.model_copy(
        update={
            "screens": screens,
            "flows": flows,
            "navigation": spec.navigation.model_copy(update={"root": root}),
        }
    )
    return Change(changed, touched, [screen_id])


def rename_screen(spec: AppSpec, p: Proposal, ctx: OpsContext) -> Change:
    screen = _screen(spec, p.target)
    title = sx.clean(p.name, MAX_TITLE, "name")
    return Change(_set_screen(spec, screen.model_copy(update={"title": title})), [screen.id])


def set_screen_purpose(spec: AppSpec, p: Proposal, ctx: OpsContext) -> Change:
    screen = _screen(spec, p.target)
    purpose = sx.clean(p.purpose, MAX_PURPOSE, "purpose")
    return Change(_set_screen(spec, screen.model_copy(update={"purpose": purpose})), [screen.id])


def set_screen_copy(spec: AppSpec, p: Proposal, ctx: OpsContext) -> Change:
    screen = _screen(spec, p.target)
    given = sx.pairs(p.pairs, "copy")
    texts = dict(screen.texts)
    for key, value in given.items():
        name = sx.snake(key, 32, "copy key")
        text = sx.clean(value, MAX_COPY, f"copy {name}")
        if has_secret(f"{name}: {text}"):
            raise Reject("secret", f"copy {name} looks like a secret")
        texts[name] = text
    if len(texts) > MAX_COPY_KEYS:
        raise Reject("limit", f"a screen has at most {MAX_COPY_KEYS} copy entries")
    return Change(_set_screen(spec, screen.model_copy(update={"texts": texts})), [screen.id])


def add_screen_action(spec: AppSpec, p: Proposal, ctx: OpsContext) -> Change:
    screen = _screen(spec, p.target)
    name = sx.snake(p.name, 32, "action name")
    given = sx.pairs(p.pairs, "action", limit=4)
    unknown = set(given) - {"calls", "go", "on_success"}
    if unknown:
        raise Reject("invalid", f"an action has calls, go and on_success, not {sorted(unknown)}")
    if "calls" not in given and "go" not in given:
        raise Reject("invalid", "an action must call an operation or go to a screen")
    if any(a.name == name for a in screen.actions):
        raise Reject("duplicate", f"{screen.id} already has an action named {name}")
    if len(screen.actions) >= MAX_ACTIONS:
        raise Reject("limit", f"a screen has at most {MAX_ACTIONS} actions")
    calls = given.get("calls")
    if calls is not None and calls not in {o.id for o in spec.api.operations}:
        raise Reject("unknown_target", f"operation {calls} does not exist")
    go = given.get("go")
    if go is not None and go not in {*all_item_ids(spec), "back", "close"}:
        raise Reject("unknown_target", f"{go} is not a screen, sheet, back or close")
    if go is not None and go.startswith(("FLW-", "ENT-", "API-", "ROLE-", "INT-")):
        raise Reject("invalid", f"{go} is not a place an action can go to")
    on_success = given.get("on_success")
    note = sx.clean(on_success, MAX_COPY, "on_success", required=False) or None
    action = ScreenAction(name=name, calls=calls, go=go, on_success=note)
    updated = screen.model_copy(update={"actions": [*screen.actions, action]})
    return Change(_set_screen(spec, updated), [screen.id])


def add_screen_section(spec: AppSpec, p: Proposal, ctx: OpsContext) -> Change:
    screen = _screen(spec, p.target)
    names = [
        sx.snake(item, MAX_SECTION, "section")
        for item in sx.strings(p.items, MAX_SECTION, "sections", max_items=6)
    ]
    sections = list(dict.fromkeys([*screen.sections, *names]))
    if len(sections) > MAX_SECTIONS:
        raise Reject("limit", f"a screen has at most {MAX_SECTIONS} sections")
    if sections == screen.sections:
        raise Reject("duplicate", "the screen already has these sections")
    return Change(_set_screen(spec, screen.model_copy(update={"sections": sections})), [screen.id])


def add_screen_check(spec: AppSpec, p: Proposal, ctx: OpsContext) -> Change:
    screen = _screen(spec, p.target)
    checks = sx.strings(p.items, MAX_CHECK, "checks", max_items=3)
    merged = list(dict.fromkeys([*screen.checks, *checks]))
    if len(merged) > MAX_CHECKS:
        raise Reject("limit", f"a screen has at most {MAX_CHECKS} checks")
    if merged == screen.checks:
        raise Reject("duplicate", "the screen already has these checks")
    return Change(_set_screen(spec, screen.model_copy(update={"checks": merged})), [screen.id])


def add_screen_state(spec: AppSpec, p: Proposal, ctx: OpsContext) -> Change:
    screen = _screen(spec, p.target)
    name = sx.snake(p.name, 32, "state name")
    given = sx.pairs(p.pairs, "state", limit=4)
    deviation: dict[str, str] = {}
    for key, value in given.items():
        field = sx.snake(key, 24, "state key")
        text = sx.clean(value, MAX_COPY, f"state {field}")
        if has_secret(f"{field}: {text}"):
            raise Reject("secret", f"state {field} looks like a secret")
        deviation[field] = text
    if name in screen.states:
        raise Reject("duplicate", f"{screen.id} already has the state {name}")
    if len(screen.states) >= MAX_STATES:
        raise Reject("limit", f"a screen has at most {MAX_STATES} states")
    states = {**screen.states, name: deviation}
    return Change(_set_screen(spec, screen.model_copy(update={"states": states})), [screen.id])


def add_flow_screen(spec: AppSpec, p: Proposal, ctx: OpsContext) -> Change:
    flow = _flow(spec, p.target)
    if flow.primary:
        raise Reject("protected", "the primary journey is not changed by proposals")
    if not p.items:
        raise Reject("invalid", "items must list the screens to add")
    known = {s.id: s for s in spec.screens}
    added: list[str] = []
    for item in p.items:
        if item not in known:
            raise Reject("unknown_target", f"screen {item} does not exist")
        if item not in flow.screens and item not in added:
            added.append(item)
    if not added:
        raise Reject("duplicate", "the flow already lists these screens")
    members = [*flow.screens, *added]
    if len(members) > MAX_FLOW_SCREENS:
        raise Reject("limit", f"a flow has at most {MAX_FLOW_SCREENS} screens")
    grown = flow.model_copy(update={"screens": members, "end": members[-1]})
    flows = [grown if f.id == flow.id else f for f in spec.flows]
    screens = [
        s.model_copy(update={"flow": flow.id}) if s.id in added and s.flow is None else s
        for s in spec.screens
    ]
    return Change(spec.model_copy(update={"flows": flows, "screens": screens}), [flow.id, *added])


# --------------------------------------------------------------------------- data


def _fields(
    spec: AppSpec,
    items: list[FieldProposal] | None,
    existing: set[str],
    extra_ref: str | None = None,
) -> list[FieldDef]:
    if not items:
        raise Reject("invalid", "fields is missing")
    if len(items) > MAX_NEW_FIELDS:
        raise Reject("limit", f"at most {MAX_NEW_FIELDS} fields at a time")
    entities = {e.id for e in spec.data.entities} | ({extra_ref} if extra_ref else set())
    out: list[FieldDef] = []
    seen = set(existing)
    for item in items:
        name = sx.snake(item.name, 40, "field name")
        if name in seen:
            raise Reject("duplicate", f"the field {name} already exists")
        seen.add(name)
        if item.type == "enum":
            raise Reject("invalid", "enum fields need a defined enum; use a string field")
        if item.type not in _FIELD_TYPES:
            raise Reject("invalid", f"field type {item.type} is not supported")
        if item.type == "ref":
            if item.ref is None or item.ref not in entities:
                raise Reject(
                    "unknown_target", f"field {name} refers to an entity that does not exist"
                )
        elif item.ref is not None:
            raise Reject("invalid", f"field {name} names an entity but is not a reference")
        description = sx.clean(item.description, 120, f"field {name} description", required=False)
        out.append(
            FieldDef(
                name=name,
                type=item.type,
                required=item.required,
                ref=item.ref if item.type == "ref" else None,
                description=description or None,
            )
        )
    return out


def add_entity(spec: AppSpec, p: Proposal, ctx: OpsContext) -> Change:
    name = sx.clean(p.name, 40, "name")
    ident = sx.pascal(name)
    if not ident or not ident[0].isalpha():
        raise Reject("invalid", "the entity name needs letters")
    entity_id = f"ENT-{ident}"
    if entity_id in {e.id for e in spec.data.entities}:
        raise Reject("duplicate", f"{entity_id} already exists")
    if len(spec.data.entities) >= MAX_ENTITIES:
        raise Reject("limit", f"the app already has {MAX_ENTITIES} entities")
    fields = _fields(spec, p.fields, set(), extra_ref=entity_id)
    plain = sx.clean(p.purpose, 40, "plain name", required=False) or f"{name}s"
    entity = Entity(
        id=entity_id,
        name=name,
        plain_name=plain,
        fields=fields,
        source=_source(p),  # type: ignore[arg-type]
    )
    data = spec.data.model_copy(update={"entities": [*spec.data.entities, entity]})
    return Change(spec.model_copy(update={"data": data}), [entity_id], [entity_id])


def add_entity_fields(spec: AppSpec, p: Proposal, ctx: OpsContext) -> Change:
    entity = _entity(spec, p.target)
    fields = _fields(spec, p.fields, {f.name for f in entity.fields}, extra_ref=entity.id)
    if len(entity.fields) + len(fields) > MAX_FIELDS:
        raise Reject("limit", f"an entity has at most {MAX_FIELDS} fields")
    grown = entity.model_copy(update={"fields": [*entity.fields, *fields]})
    entities = [grown if e.id == entity.id else e for e in spec.data.entities]
    data = spec.data.model_copy(update={"entities": entities})
    return Change(spec.model_copy(update={"data": data}), [entity.id])


def _bool(text: str, what: str) -> bool:
    lowered = text.strip().lower()
    if lowered in ("true", "yes"):
        return True
    if lowered in ("false", "no"):
        return False
    raise Reject("invalid", f"{what} must be true or false")


def add_api_operation(spec: AppSpec, p: Proposal, ctx: OpsContext) -> Change:
    name = sx.clean(p.name, 60, "name")
    ident = sx.camel(name)
    if not ident or not ident[0].isalpha():
        raise Reject("invalid", "the operation name needs letters")
    op_id = f"API-{ident}"
    if op_id in {o.id for o in spec.api.operations}:
        raise Reject("duplicate", f"{op_id} already exists")
    if len(spec.api.operations) >= MAX_OPERATIONS:
        raise Reject("limit", f"the app already has {MAX_OPERATIONS} operations")
    method = (p.method or "").upper()
    if method not in _METHODS:
        raise Reject("invalid", "method must be GET, POST, PUT, PATCH or DELETE")
    path = sx.clean(p.path, 80, "path")
    if not _PATH.match(path):
        raise Reject("invalid", "path must look like /things/{id}")
    if any(o.method == method and o.path == path for o in spec.api.operations):
        raise Reject("duplicate", f"{method} {path} already exists")
    entities = {e.id for e in spec.data.entities}
    for label, value in (("request", p.request_entity), ("response", p.response_entity)):
        if value is not None and value not in entities:
            raise Reject("unknown_target", f"{label} entity {value} does not exist")
    known_roles = _role_ids(spec)
    roles = list(dict.fromkeys(p.items or []))
    for role in roles:
        if role not in known_roles:
            raise Reject("unknown_target", f"role {role} does not exist")
    extra = sx.pairs(p.pairs, "operation", limit=4) if p.pairs else {}
    if set(extra) - {"auth", "response_many"}:
        raise Reject("invalid", "operation pairs are auth and response_many")
    auth = extra.get("auth") or (roles[0] if roles else "authenticated")
    if auth not in ("public", "authenticated") and (auth not in known_roles or auth not in roles):
        raise Reject("invalid", "a role-gated operation must list that role in items")
    many = _bool(extra["response_many"], "response_many") if "response_many" in extra else False
    if many and p.response_entity is None:
        raise Reject("invalid", "response_many needs a response entity")
    notes = sx.clean(p.purpose, 160, "notes", required=False) or None
    op = ApiOperation(
        id=op_id,
        name=name,
        method=method,  # type: ignore[arg-type]
        path=path,
        request_entity=p.request_entity,
        response_entity=p.response_entity,
        response_many=many,
        auth=auth,
        roles=roles,
        notes=notes,
        source=_source(p),  # type: ignore[arg-type]
    )
    api = spec.api.model_copy(update={"operations": [*spec.api.operations, op]})
    return Change(spec.model_copy(update={"api": api}), [op_id], [op_id])


def add_role(spec: AppSpec, p: Proposal, ctx: OpsContext) -> Change:
    name = sx.clean(p.name, 40, "name")
    ident = sx.snake_case(name)
    if not ident or not ident[0].isalpha():
        raise Reject("invalid", "the role name needs letters")
    role_id = f"ROLE-{ident}"
    if role_id in _role_ids(spec):
        raise Reject("duplicate", f"{role_id} already exists")
    if len(spec.roles.roles) >= MAX_ROLES:
        raise Reject("limit", f"an app has at most {MAX_ROLES} roles")
    surfaces = _surfaces(spec)
    surface = sx.clean(p.surface, 32, "surface", required=False) or (
        surfaces[0] if surfaces else "customer"
    )
    if surface not in surfaces:
        raise Reject("invalid", f"surface {surface} is not one of the app's surfaces")
    description = sx.clean(p.purpose, 120, "description", required=False)
    role = Role(id=role_id, name=name, description=description, surface=surface)
    roles = spec.roles.model_copy(update={"roles": [*spec.roles.roles, role]})
    return Change(spec.model_copy(update={"roles": roles}), [role_id], [role_id])


# --------------------------------------------------------------------------- design


def _int(text: str, low: int, high: int, what: str) -> int:
    try:
        value = int(text.strip())
    except ValueError:
        raise Reject("invalid", f"{what} must be a whole number") from None
    if not low <= value <= high:
        raise Reject("invalid", f"{what} must be between {low} and {high}")
    return value


def _apply_token(tokens: DesignTokens, key: str, raw: str) -> DesignTokens:
    group, _, rest = key.partition(".")
    value = sx.clean(raw, 24, f"token {key}")
    if group in ("colors_light", "colors_dark"):
        colors = dict(getattr(tokens, group))
        if rest not in colors:
            raise Reject("unknown_target", f"there is no colour token {rest}")
        hexed = design.normalize_hex(value)
        if hexed is None:
            raise Reject("invalid", f"{key} must be a hex colour like #1B5FD9")
        colors[rest] = hexed
        return tokens.model_copy(update={group: colors})
    if group == "radius":
        radius = dict(tokens.radius)
        if rest not in radius or rest in ("none", "full"):
            raise Reject("unknown_target", f"there is no editable radius token {rest}")
        radius[rest] = _int(value, 0, 48, key)
        return tokens.model_copy(update={"radius": radius})
    if group == "type_scale":
        name, _, field = rest.partition(".")
        scale = dict(tokens.type_scale)
        if name not in scale or field not in ("size", "weight", "line_height"):
            raise Reject("unknown_target", f"there is no type token {rest}")
        style = scale[name]
        if field == "size":
            scale[name] = style.model_copy(update={"size": _int(value, 8, 64, key)})
        elif field == "weight":
            weight = _int(value, 100, 900, key)
            if weight % 100:
                raise Reject("invalid", f"{key} must be a multiple of 100")
            scale[name] = style.model_copy(update={"weight": weight})
        else:
            try:
                height = float(value)
            except ValueError:
                raise Reject("invalid", f"{key} must be a number") from None
            if not 1.0 <= height <= 2.2:
                raise Reject("invalid", f"{key} must be between 1.0 and 2.2")
            scale[name] = TypeStyle(
                size=style.size, weight=style.weight, line_height=round(height, 2)
            )
        return tokens.model_copy(update={"type_scale": scale})
    raise Reject("unknown_target", f"{key} is not a design token")


def set_design_token(spec: AppSpec, p: Proposal, ctx: OpsContext) -> Change:
    given = sx.pairs(p.pairs, "tokens")
    tokens = spec.design.tokens
    for key, value in given.items():
        tokens = _apply_token(tokens, key, value)
    pairs = design.pairs_for(spec.design.direction)
    broken = design.contrast_failures(tokens, pairs) - design.contrast_failures(
        spec.design.tokens, pairs
    )
    if broken:
        raise Reject("breaks_validator", "the change would make some text hard to read")
    block = spec.design.model_copy(update={"tokens": tokens})
    return Change(spec.model_copy(update={"design": block}), ["DESIGN"])


def add_imagery_rule(spec: AppSpec, p: Proposal, ctx: OpsContext) -> Change:
    rules = sx.strings(p.items, 140, "rules", max_items=3)
    merged = list(dict.fromkeys([*spec.design.imagery_rules, *rules]))
    if len(merged) > MAX_IMAGERY:
        raise Reject("limit", f"the design has at most {MAX_IMAGERY} imagery rules")
    if merged == spec.design.imagery_rules:
        raise Reject("duplicate", "the design already has these rules")
    block = spec.design.model_copy(update={"imagery_rules": merged})
    return Change(spec.model_copy(update={"design": block}), ["DESIGN"])


# --------------------------------------------------------------------------- integrations, behavior, acceptance


def _needs_dev_build(integration_id: str) -> bool:
    try:
        found = capability(integration_id.removeprefix("INT-"))
    except KeyError:
        return False
    return found.needs_dev_build


def add_integration(spec: AppSpec, p: Proposal, ctx: OpsContext) -> Change:
    name = sx.clean(p.name, 60, "name")
    ident = sx.snake_case(name)
    if not ident or not ident[0].isalpha():
        raise Reject("invalid", "the integration name needs letters")
    integration_id = f"INT-{ident}"
    if integration_id in {i.id for i in spec.integrations.items}:
        raise Reject("duplicate", f"{integration_id} already exists")
    if len(spec.integrations.items) >= MAX_INTEGRATIONS:
        raise Reject("limit", f"an app has at most {MAX_INTEGRATIONS} integrations")
    purpose = sx.clean(p.purpose, 160, "purpose")
    extra = sx.pairs(p.pairs, "integration", limit=2) if p.pairs else {}
    if set(extra) - {"provider", "notes"}:
        raise Reject("invalid", "integration pairs are provider and notes")
    provider = sx.clean(extra.get("provider"), 60, "provider", required=False) or None
    notes = sx.clean(extra.get("notes"), 200, "notes", required=False) or None
    item = Integration(
        id=integration_id,
        name=name,
        purpose=purpose,
        mode="mock",  # a proposal never connects anything for real
        provider=provider,
        notes=notes,
        source=_source(p),  # type: ignore[arg-type]
        research_ref=_ref(p),
        needs_dev_build=False,
    )
    block = spec.integrations.model_copy(update={"items": [*spec.integrations.items, item]})
    return Change(
        spec.model_copy(update={"integrations": block}), [integration_id], [integration_id]
    )


def set_integration_mode(spec: AppSpec, p: Proposal, ctx: OpsContext) -> Change:
    target = next((i for i in spec.integrations.items if i.id == p.target), None)
    if target is None:
        raise Reject("unknown_target", f"integration {p.target} does not exist")
    mode = sx.clean(p.name, 10, "mode").lower()
    if mode not in ("mock", "adapter", "real"):
        raise Reject("invalid", "mode must be mock, adapter or real")
    if mode == "real" and not ctx.allow_real:
        raise Reject(
            "unsupported",
            "an integration is only connected for real when the person asks and the policy allows it",
        )
    if mode in ("adapter", "real") and not target.provider:
        raise Reject("invalid", f"{mode} needs a named provider")
    updated = target.model_copy(
        update={
            "mode": mode,
            "needs_dev_build": mode != "mock" and _needs_dev_build(target.id),
        }
    )
    items = [updated if i.id == target.id else i for i in spec.integrations.items]
    block = spec.integrations.model_copy(update={"items": items})
    return Change(spec.model_copy(update={"integrations": block}), [target.id])


def set_behavior_item(spec: AppSpec, p: Proposal, ctx: OpsContext) -> Change:
    given = sx.pairs(p.pairs, "behavior", limit=6)
    behavior = spec.behavior
    for key, raw in given.items():
        if key == "offline":
            behavior = behavior.model_copy(update={"offline": sx.clean(raw, 200, "offline")})
        elif key in _BEHAVIOR_LISTS:
            text = sx.clean(raw, 160, key)
            current = cast("list[str]", getattr(behavior, key))
            if text in current:
                raise Reject("duplicate", f"{key} already lists this")
            if len(current) >= MAX_BEHAVIOR_ITEMS:
                raise Reject("limit", f"{key} has at most {MAX_BEHAVIOR_ITEMS} entries")
            behavior = behavior.model_copy(update={key: [*current, text]})
        else:
            raise Reject("invalid", f"{key} is not a behavior item")
    return Change(spec.model_copy(update={"behavior": behavior}), ["BEHAVIOR"])


def add_acceptance_check(spec: AppSpec, p: Proposal, ctx: OpsContext) -> Change:
    checks = sx.strings(p.items, MAX_CHECK, "checks", max_items=3)
    merged = list(dict.fromkeys([*spec.acceptance.checks, *checks]))
    if len(merged) > MAX_ACCEPTANCE:
        raise Reject("limit", f"the app has at most {MAX_ACCEPTANCE} global checks")
    if merged == spec.acceptance.checks:
        raise Reject("duplicate", "the app already has these checks")
    block = spec.acceptance.model_copy(update={"checks": merged})
    return Change(spec.model_copy(update={"acceptance": block}), ["ACCEPT"])


# --------------------------------------------------------------------------- repairs


def _kind_of(item_id: str) -> str:
    return item_id.split("-", 1)[0]


def _rewrite(value: object, old: str, new: str) -> tuple[object, int]:
    """Replace the id ``old`` by ``new`` in every string of a plain data tree; count the hits."""
    pattern = re.compile(rf"(?<![A-Za-z0-9_-]){re.escape(old)}(?![A-Za-z0-9_])")
    if isinstance(value, str):
        text, count = pattern.subn(new, value)
        return text, count
    if isinstance(value, list):
        total = 0
        items: list[object] = []
        for item in cast("list[object]", value):
            fixed, n = _rewrite(item, old, new)
            items.append(fixed)
            total += n
        return items, total
    if isinstance(value, dict):
        total = 0
        out: dict[str, object] = {}
        for key, item in cast("dict[str, object]", value).items():
            fixed, n = _rewrite(item, old, new)
            out[key] = fixed
            total += n
        return out, total
    return value, 0


def _rebuild_nav(node: NavNode, node_id: str, fixed: NavNode) -> NavNode:
    if node.id == node_id:
        return fixed.model_copy(update={"children": node.children})
    return node.model_copy(
        update={"children": [_rebuild_nav(c, node_id, fixed) for c in node.children]}
    )


def fix_reference(spec: AppSpec, p: Proposal, ctx: OpsContext) -> Change:
    target = sx.clean(p.target, 60, "target")
    given = sx.pairs(p.pairs, "fix", limit=1)
    old, new = next(iter(given.items()))
    new = sx.clean(new, 60, "replacement")
    known = all_item_ids(spec)
    if not _ID.fullmatch(old) or not _ID.fullmatch(new):
        raise Reject("invalid", "fix_reference replaces one id by another id")
    if old in known:
        raise Reject("invalid", f"{old} exists, so it is not a dangling reference")
    if new not in known:
        raise Reject("unknown_target", f"{new} does not exist")
    if _kind_of(old) != _kind_of(new):
        raise Reject("invalid", f"{old} and {new} are not the same kind of item")
    if target not in known:
        raise Reject("unknown_target", f"{target} does not exist")
    prefix = _kind_of(target)
    if prefix == "SCR":
        screen = _screen(spec, target)
        data, hits = _rewrite(screen.model_dump(mode="python"), old, new)
        if hits == 0:
            raise Reject("invalid", f"{target} does not mention {old}")
        return Change(_set_screen(spec, ScreenBlock.model_validate(data)), [target])
    if prefix == "FLW":
        flow = _flow(spec, target)
        data, hits = _rewrite(flow.model_dump(mode="python"), old, new)
        if hits == 0:
            raise Reject("invalid", f"{target} does not mention {old}")
        fixed = FlowBlock.model_validate(data)
        return Change(
            spec.model_copy(update={"flows": [fixed if f.id == target else f for f in spec.flows]}),
            [target],
        )
    if prefix == "ENT":
        entity = _entity(spec, target)
        data, hits = _rewrite(entity.model_dump(mode="python"), old, new)
        if hits == 0:
            raise Reject("invalid", f"{target} does not mention {old}")
        fixed_entity = Entity.model_validate(data)
        entities = [fixed_entity if e.id == target else e for e in spec.data.entities]
        block = spec.data.model_copy(update={"entities": entities})
        return Change(spec.model_copy(update={"data": block}), [target])
    if prefix == "API":
        op = next(o for o in spec.api.operations if o.id == target)
        data, hits = _rewrite(op.model_dump(mode="python"), old, new)
        if hits == 0:
            raise Reject("invalid", f"{target} does not mention {old}")
        fixed_op = ApiOperation.model_validate(data)
        ops = [fixed_op if o.id == target else o for o in spec.api.operations]
        return Change(
            spec.model_copy(update={"api": spec.api.model_copy(update={"operations": ops})}),
            [target],
        )
    if prefix == "NAV":
        node = next(n for n in walk_nav(spec.navigation.root) if n.id == target)
        flat = node.model_copy(update={"children": []})
        data, hits = _rewrite(flat.model_dump(mode="python"), old, new)
        if hits == 0:
            raise Reject("invalid", f"{target} does not mention {old}")
        root = _rebuild_nav(spec.navigation.root, target, NavNode.model_validate(data))
        return Change(
            spec.model_copy(
                update={"navigation": spec.navigation.model_copy(update={"root": root})}
            ),
            [target],
        )
    raise Reject("unsupported", f"references of {target} are not repaired this way")


def _critical_screens(spec: AppSpec) -> set[str]:
    """Screens that must stay: the primary journey, the sign-in screens and the first screen."""
    keep: set[str] = set()
    flow = primary_flow(spec)
    if flow is not None:
        keep |= set(flow.screens)
    for screen in spec.screens:
        if screen.recipe in ("welcome", "signup") or screen.recipe.startswith("login"):
            keep.add(screen.id)
    for node in walk_nav(spec.navigation.root):
        if node.screen is not None and node.kind in ("stack", "screen"):
            keep.add(node.screen)
            break
    return keep


def remove_item(spec: AppSpec, p: Proposal, ctx: OpsContext) -> Change:
    target = sx.clean(p.target, 60, "target")
    prefix = _kind_of(target)
    if prefix == "SCR":
        _screen(spec, target)
        if target in _critical_screens(spec):
            raise Reject("protected", f"{target} is part of the main journey or sign-in")
        if len(spec.screens) <= 1:
            raise Reject("protected", "the last screen cannot be removed")
        node = next((n for n in walk_nav(spec.navigation.root) if n.screen == target), None)
        if node is not None and node.children:
            raise Reject("protected", f"other screens sit under {target} in the navigation")
        root = _remove_screen_nodes(spec.navigation.root, target)
        if root is None:
            raise Reject("protected", "the navigation would be empty")
        screens: list[ScreenBlock] = []
        for s in spec.screens:
            if s.id == target:
                continue
            actions = [
                a.model_copy(update={"go": None}) if a.go == target and a.calls else a
                for a in s.actions
                if not (a.go == target and not a.calls)
            ]
            screens.append(s.model_copy(update={"actions": actions}) if actions != s.actions else s)
        flows: list[FlowBlock] = []
        for flow in spec.flows:
            members = [m for m in flow.screens if m != target]
            if not members:
                continue
            flows.append(
                flow.model_copy(
                    update={"screens": members, "start": members[0], "end": members[-1]}
                )
                if members != flow.screens
                else flow
            )
        kept_flows = {f.id for f in flows}
        screens = [
            s.model_copy(update={"flow": None}) if s.flow and s.flow not in kept_flows else s
            for s in screens
        ]
        perms = [
            q.model_copy(update={"screens": [x for x in q.screens if x != target]})
            for q in spec.roles.permissions
        ]
        roles = spec.roles.model_copy(update={"permissions": perms})
        return Change(
            spec.model_copy(
                update={
                    "screens": screens,
                    "flows": flows,
                    "roles": roles,
                    "navigation": spec.navigation.model_copy(update={"root": root}),
                }
            ),
            [target],
        )
    if prefix == "FLW":
        flow = _flow(spec, target)
        if flow.primary:
            raise Reject("protected", "the primary journey cannot be removed")
        flows = [f for f in spec.flows if f.id != target]
        screens = [
            s.model_copy(update={"flow": None}) if s.flow == target else s for s in spec.screens
        ]
        return Change(spec.model_copy(update={"flows": flows, "screens": screens}), [target])
    if prefix == "ENT":
        _entity(spec, target)
        entities = [e for e in spec.data.entities if e.id != target]
        block = spec.data.model_copy(update={"entities": entities})
        return Change(spec.model_copy(update={"data": block}), [target])
    if prefix == "API":
        if target not in {o.id for o in spec.api.operations}:
            raise Reject("unknown_target", f"operation {target} does not exist")
        ops = [o for o in spec.api.operations if o.id != target]
        return Change(
            spec.model_copy(update={"api": spec.api.model_copy(update={"operations": ops})}),
            [target],
        )
    if prefix == "ROLE":
        roles_list = spec.roles.roles
        if target not in _role_ids(spec):
            raise Reject("unknown_target", f"role {target} does not exist")
        if roles_list and roles_list[0].id == target:
            raise Reject("protected", "the first role of the app cannot be removed")
        kept = [r for r in roles_list if r.id != target]
        perms = [q for q in spec.roles.permissions if q.role != target]
        block = spec.roles.model_copy(update={"roles": kept, "permissions": perms})
        return Change(spec.model_copy(update={"roles": block}), [target])
    if prefix == "INT":
        item = next((i for i in spec.integrations.items if i.id == target), None)
        if item is None:
            raise Reject("unknown_target", f"integration {target} does not exist")
        items = [i for i in spec.integrations.items if i.id != target]
        block = spec.integrations.model_copy(update={"items": items})
        return Change(spec.model_copy(update={"integrations": block}), [target])
    raise Reject("unsupported", f"{target} cannot be removed this way")


# --------------------------------------------------------------------------- registry

Handler = Callable[[AppSpec, Proposal, OpsContext], Change]

HANDLERS: dict[ProposalKind, Handler] = {
    "add_screen": add_screen,
    "rename_screen": rename_screen,
    "set_screen_purpose": set_screen_purpose,
    "set_screen_copy": set_screen_copy,
    "add_screen_action": add_screen_action,
    "add_screen_section": add_screen_section,
    "add_screen_check": add_screen_check,
    "add_screen_state": add_screen_state,
    "add_flow_screen": add_flow_screen,
    "add_entity": add_entity,
    "add_entity_fields": add_entity_fields,
    "add_api_operation": add_api_operation,
    "add_role": add_role,
    "set_design_token": set_design_token,
    "add_imagery_rule": add_imagery_rule,
    "add_integration": add_integration,
    "set_integration_mode": set_integration_mode,
    "set_behavior_item": set_behavior_item,
    "add_acceptance_check": add_acceptance_check,
    "fix_reference": fix_reference,
    "remove_item": remove_item,
}

__all__ = ["HANDLERS", "Change", "OpsContext", "Reject", "all_item_ids"]
