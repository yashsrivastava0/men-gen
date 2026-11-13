"""Seeded synthetic graphs of realistic density (doc 16 6, doc 19 2.7): tiny, small, medium, big, program.

Zero model cost. The same seed gives the same graph. These graphs are test fixtures for the packager, slicer,
budgets and validators; they are internally consistent (bindings resolve, operations exist, copy is namespaced).
"""

from __future__ import annotations

import itertools
import random
from dataclasses import dataclass

from .graph import (
    Acceptance,
    AccessRow,
    Action,
    App,
    Behavior,
    Entity,
    FieldDef,
    Flow,
    GraphMeta,
    Integration,
    Lifecycle,
    Module,
    Operation,
    OpError,
    Prov,
    Region,
    Role,
    Route,
    RouteParam,
    Rule,
    Screen,
    SpecGraph,
    Stage,
    Step,
    Story,
    Transition,
)
from .ids import namespaced_copy_key
from .libraries import state_library_for
from .rcode import instantiate_behaviors
from .vocab import behavior_categories_for

_NOUNS = [
    "order",
    "booking",
    "listing",
    "invoice",
    "payment",
    "delivery",
    "customer",
    "driver",
    "vehicle",
    "trip",
    "ticket",
    "review",
    "message",
    "document",
    "report",
    "product",
    "category",
    "address",
    "coupon",
    "shift",
    "claim",
    "policy",
    "asset",
    "task",
    "visit",
    "plan",
    "member",
    "class",
    "slot",
    "refund",
    "dispute",
    "payout",
    "zone",
    "menu",
    "store",
    "campaign",
    "device",
    "alert",
]
_FIELD_TYPES = ["text", "number", "money", "date", "phone", "email", "choice", "location", "toggle"]
_STATES = [
    "draft",
    "placed",
    "confirmed",
    "in_progress",
    "completed",
    "cancelled",
    "failed",
    "on_hold",
]
_SHAPES = [
    ("collection", "primary"),
    ("key_values", "supporting"),
    ("value", "supporting"),
    ("sequence", "supporting"),
    ("media", "optional"),
    ("notice", "optional"),
    ("options", "primary"),
    ("series", "optional"),
]
_TYPES = ["list", "detail", "form", "dashboard", "status", "checkout", "search", "settings"]


@dataclass(frozen=True)
class SynthSpec:
    name: str
    apps: int = 1
    screens_per_app: int = 16
    modules: int = 4
    entities: int = 5
    fields_per_entity: int = 5
    roles_per_app: int = 3
    integrations: int = 1
    locales: tuple[str, ...] = ("en",)
    profile: str = "compact"
    seed: int = 7
    checkout_ops: int = 1  # operations bound by checkout-class screens


PRESETS: dict[str, SynthSpec] = {
    "tiny": SynthSpec(
        "tiny",
        screens_per_app=5,
        modules=1,
        entities=2,
        fields_per_entity=3,
        roles_per_app=1,
        integrations=0,
    ),
    "small": SynthSpec("small", screens_per_app=16, modules=4, entities=5),
    "medium": SynthSpec(
        "medium",
        apps=4,
        screens_per_app=8,
        modules=5,
        entities=10,
        fields_per_entity=6,
        roles_per_app=2,
        integrations=2,
        locales=("mr", "hi", "en"),
        profile="standard",
    ),
    "big": SynthSpec(
        "big",
        apps=1,
        screens_per_app=60,
        modules=11,
        entities=22,
        fields_per_entity=12,
        roles_per_app=6,
        integrations=6,
        locales=("en", "hi", "kn"),
        profile="deep",
        checkout_ops=5,
    ),
    "program": SynthSpec(
        "program",
        apps=4,
        screens_per_app=35,
        modules=12,
        entities=24,
        fields_per_entity=10,
        roles_per_app=5,
        integrations=12,
        locales=("en", "hi", "ar"),
        profile="deep",
        checkout_ops=5,
    ),
    "erp": SynthSpec(
        "erp",
        apps=2,
        screens_per_app=40,
        modules=14,
        entities=300,
        fields_per_entity=8,
        roles_per_app=10,
        integrations=8,
        locales=("en",),
        profile="deep",
    ),
}


def make_graph(spec: SynthSpec | str) -> SpecGraph:
    sp = PRESETS[spec] if isinstance(spec, str) else spec
    rnd = random.Random(sp.seed)  # noqa: S311 - seeded fixture generator, not security
    g = SpecGraph(
        meta=GraphMeta(profile=sp.profile, program_id=f"prog-{sp.name}", languages=list(sp.locales))
    )  # type: ignore[arg-type]
    g.meta.source_language = sp.locales[0]

    # entities (whole-scope data model, one owner module each)
    mod_ids = [f"MOD-{i + 1:03d}" for i in range(sp.modules)]
    ent_ids: list[str] = []
    for i in range(sp.entities):
        eid = f"ENT-{i + 1:03d}"
        ent_ids.append(eid)
        noun = _NOUNS[i % len(_NOUNS)] + ("" if i < len(_NOUNS) else f"_{i // len(_NOUNS)}")
        n_states = 3 + rnd.randint(0, 3)
        states = _STATES[:n_states]
        fields = [FieldDef(key="id", type="id", required=True)]
        for j in range(sp.fields_per_entity):
            ft = _FIELD_TYPES[(i + j) % len(_FIELD_TYPES)]
            fields.append(
                FieldDef(
                    key=f"{noun}_f{j + 1}",
                    type=ft,
                    required=j < 2,
                    media=(ft == "location" and j == 3),
                )
            )
        fields.append(FieldDef(key="status", type="enum", enum_values=states))
        g.add(
            "entities",
            Entity(
                id=eid,
                name=noun.replace("_", " ").title(),
                owner_module=mod_ids[i % sp.modules],
                klass="primary" if i < max(1, sp.entities // 4) else "secondary",
                fields=fields,
                lifecycle=Lifecycle(
                    states=states,
                    initial=states[0],
                    transitions=[
                        Transition(from_state=a, to_state=b) for a, b in itertools.pairwise(states)
                    ],
                ),
                typical_count=20,
                max_count=500,
                provenance=[Prov(kind="library", ref="synth")],
            ),
        )

    app_ids = [f"APP-{a + 1:03d}" for a in range(sp.apps)]
    role_ids: list[str] = []
    for a, app_id in enumerate(app_ids):
        rids: list[str] = []
        for r in range(sp.roles_per_app):
            rid = f"ROL-{a * sp.roles_per_app + r + 1:03d}"
            rids.append(rid)
            role_ids.append(rid)
            g.add(
                "roles",
                Role(id=rid, name=f"Role {a + 1}.{r + 1}", app_ids=[app_id], goal="do the job"),
            )
        g.add(
            "apps",
            App(
                id=app_id,
                name=f"App {a + 1}",
                surface="web" if (sp.apps > 1 and a == sp.apps - 1) else "phone",
                role_ids=rids,
                module_ids=mod_ids,
                scope_cap=60,
            ),
        )

    # operations: CRUD per entity plus checkout-class custom operations
    op_for: dict[str, list[str]] = {}
    n_op = 0
    for eid in ent_ids:
        op_for[eid] = []
        for verb, method in (("list", "GET"), ("get", "GET"), ("save", "POST")):
            n_op += 1
            oid = f"OP-{n_op:03d}"
            op_for[eid].append(oid)
            g.add(
                "operations",
                Operation(
                    id=oid,
                    name=f"{verb}_{eid.lower()}",
                    entity=eid,
                    method=method,  # type: ignore[arg-type]
                    path=f"/{eid.lower()}" + ("" if verb == "list" else f"/{verb}"),
                    roles=list(role_ids),
                    paged=verb == "list",
                    errors=[
                        OpError(code="network", recovery="offline"),
                        OpError(code="forbidden", recovery="stay"),
                    ],
                    idempotent=verb == "save",
                ),
            )
    custom: list[str] = []
    for c in range(sp.checkout_ops):
        n_op += 1
        oid = f"OP-{n_op:03d}"
        custom.append(oid)
        g.add(
            "operations",
            Operation(
                id=oid,
                name=f"checkout_step_{c + 1}",
                entity=ent_ids[0],
                method="POST",
                kind="custom",
                path=f"/{ent_ids[0].lower()}/checkout/{c + 1}",
                roles=list(role_ids),
                idempotent=True,
                errors=[
                    OpError(code="payment_declined", recovery="stay"),
                    OpError(code="duplicate", recovery="go_back"),
                ],
            ),
        )

    g.add(
        "rules",
        Rule(
            id="RUL-001",
            subject="late fee",
            statement="a late fee applies after the due date",
            value="4",
            unit="percent",
            confirm=True,
            module_id=mod_ids[0],
        ),
    )

    # integrations (provider-side flows need interruption + pending timeout)
    for k in range(sp.integrations):
        g.add(
            "integrations",
            Integration(
                id=f"INT-{k + 1:03d}",
                name=f"Provider need {k + 1}",
                need_key=f"need_{k + 1}",
                provider_side_flow=True,
                flow="provider-neutral flow",
                interruption="app killed mid flow: resume by status",
                pending_timeout="pending for up to 15 minutes, then fail",
                events=["succeeded", "failed"],
            ),
        )

    # screens
    n_scr = 0
    per_module: dict[str, list[str]] = {m: [] for m in mod_ids}
    for a, app_id in enumerate(app_ids):
        for s in range(sp.screens_per_app):
            n_scr += 1
            sid = f"SCR-{n_scr:03d}"
            eid = ent_ids[(a + s) % len(ent_ids)]
            ent = g.entities[eid]
            mod = mod_ids[(a + s) % sp.modules]
            stype = "auth" if s == 0 else _TYPES[(s - 1) % len(_TYPES)]
            checkout = stype == "checkout"
            reg: list[Region] = []
            n_reg = 3 + rnd.randint(0, 3)
            used = ent.fields[1 : 1 + n_reg]
            for r, f in enumerate(sorted(used, key=lambda x: x.key)):
                shape, imp = _SHAPES[(s + r) % len(_SHAPES)]
                reg.append(
                    Region(
                        key=f"{f.key}_{r}",
                        what=f"what the user needs to know about {f.key}",
                        shape=shape,
                        bind=[f"{eid}.{f.key}"],
                        importance=imp,  # type: ignore[arg-type]
                        cardinality="few",
                        typical=3,
                        maximum=20,
                    )
                )
            reg.sort(key=lambda x: x.key)
            ops = custom if checkout else op_for[eid][:1]
            acts = [
                Action(
                    key=f"act_{i}",
                    importance="primary" if i == 0 else "secondary",
                    intent="pay" if checkout else ("confirm" if i == 0 else "navigate"),
                    what="do the main thing" if i == 0 else "go elsewhere",
                    effect=[
                        Step(verb="call", target=ops[i % len(ops)]),
                        Step(
                            verb="go",
                            target=f"SCR-{(n_scr % (sp.screens_per_app * sp.apps)) + 1:03d}",
                        ),
                    ],
                    errors=["network"],
                    flags=["idempotent"] if checkout else [],
                )
                for i in range(2 + (len(ops) if checkout else 0))
            ]
            ckeys = [
                namespaced_copy_key(sid, k)
                for k in ("title", "purpose", "empty", *[a.key for a in acts])
            ]
            lib = state_library_for(stype, sp.profile)
            for loc in sp.locales:
                for t in lib.states:
                    g.locale_copy.setdefault(loc, {})[t.copy_key] = t.copy_text
                for k in ckeys:
                    g.locale_copy.setdefault(loc, {})[k] = f"{k.split('.')[1]} text ({loc})"
            g.add(
                "screens",
                Screen(
                    id=sid,
                    name=f"{stype.title()} of {ent.name}",
                    app_id=app_id,
                    module_id=mod,
                    screen_type=stype,
                    surface=g.apps[app_id].surface,
                    role_ids=sorted(
                        {
                            g.apps[app_id].role_ids[s % len(g.apps[app_id].role_ids)],
                            g.apps[app_id].role_ids[(s + 1) % len(g.apps[app_id].role_ids)],
                        }
                    ),
                    purpose=f"The user handles a {ent.name.lower()} so the job gets done.",
                    route=Route(
                        name=sid.lower(),
                        path=f"/{stype}/{eid.lower()}/:id",
                        params=[RouteParam(name="id", type="id", source=f"{eid}.id")],
                    ),
                    regions=reg,
                    actions=acts,
                    states=[t.to_state_spec() for t in lib.states],
                    copy_keys=ckeys + [t.copy_key for t in lib.states],
                    acceptance=[
                        Acceptance(
                            given="a signed in user",
                            when="the user acts",
                            then="the result is shown",
                        )
                        for _ in range(
                            3 if sp.profile == "compact" else 5 if sp.profile == "standard" else 8
                        )
                    ],
                    leads_to=[f"SCR-{(n_scr % (sp.screens_per_app * sp.apps)) + 1:03d}"],
                    stories=["US-001"],
                    provenance=[Prov(kind="library", ref="synth")],
                ),
            )
            per_module[mod].append(sid)
    for mid in mod_ids:
        g.add(
            "modules",
            Module(
                id=mid,
                name=f"Module {mid[-3:]}",
                app_ids=app_ids,
                screen_ids=per_module[mid],
                entity_ids=[e for e in ent_ids if g.entities[e].owner_module == mid],
            ),
        )

    g.add(
        "flows",
        Flow(
            id="FLW-001",
            name="Main journey",
            role_ids=role_ids[:1],
            app_ids=app_ids[:1],
            primary=True,
            stages=[
                Stage(n=1, goal="start", screen_ids=["SCR-001"]),
                Stage(n=2, goal="finish", screen_ids=["SCR-002"], emotion="anxious"),
            ],
            moments_that_matter=[2],
            unhappy_paths=["it fails: try again"],
        ),
    )
    g.add(
        "stories",
        Story(
            id="US-001",
            role_id=role_ids[0],
            want="do the main thing",
            so_that="the job is done",
            feature_ids=[],
            policy_id="policy:synth@2026-11-14",
            screen_ids=["SCR-001"],
        ),
    )
    for e in g.entities.values():
        for t in e.lifecycle.transitions:
            t.roles = [role_ids[0]]
        e.access = [AccessRow(role_id=role_ids[0], actions=["manage"])] + [
            AccessRow(role_id=r, actions=["view"]) for r in role_ids[1:3]
        ]
    g.glossary = {"order": "order"}
    g.meta.facts = sorted(
        {"accounts", "always"} | ({"provider_side_flow", "payments"} if sp.integrations else set())
    )
    instantiate_behaviors(g, set(g.meta.facts))
    have = {b.category for b in g.behaviors.values()}
    for cat in behavior_categories_for(
        sp.profile, set(g.meta.facts)
    ):  # app-specific ones the model would write
        if cat.name not in have:
            bid = f"BHV-{len(g.behaviors) + 1:03d}"
            g.behaviors[bid] = Behavior(
                id=bid,
                category=cat.name,
                text=f"synthetic {cat.name} behaviour",
                provenance=[Prov(kind="library", ref="synth")],
            )
    return g
