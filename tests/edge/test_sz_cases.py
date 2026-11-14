"""SPC-SZ-01..06: scale and mobile fit (doc 17 section 11.5): shared blocks, pilot tasks, V-37, web exemption, view-only screens."""

from __future__ import annotations

import json
from functools import cache

from edge_support.ea_support import cached_flow, raw, validate
from edge_support.registry import case

from mde_spec_core.graph import Action, Region
from mde_spec_core.pack import assemble
from mde_spec_core.simulate import counter_for, screen_blocks, task_tokens
from mde_spec_core.synth import make_graph


@cache
def big_with_blocks():
    """The 60-screen preset with eleven recurring blocks, each shared by the screens of one task (so the task reads it once)."""
    g = make_graph("big").model_copy(deep=True)
    groups = [
        [p.split("/")[-1][:-3] for p in t["reads"]["screens"]]
        for t in assemble(g).tasks
        if len(t["reads"].get("screens", [])) >= 2
    ][:12]
    fields = [(e.id, e.fields[0].key) for e in g.entities.values() if e.fields]
    assert len(groups) >= 10  # the preset has eleven multi-screen tasks
    for k, members in enumerate(groups):
        ent, key = fields[k % len(fields)]
        for sid in members:
            s = g.screens[sid]
            s.role_ids = list(g.screens[members[0]].role_ids)
            s.regions.append(
                Region(
                    key=f"shared_{k}",
                    what="a recurring piece of information",
                    shape="key_values",
                    bind=[f"{ent}.{key}", f"{ent}.status"],
                )
            )
    return g


def _phone_screen(g, **over):
    s = next(x for x in g.screens.values() if x.surface == "phone" and not x.system)
    for k, v in over.items():
        setattr(s, k, v)
    return s


def _regions(n: int, importance: str = "supporting", start: int = 0):
    return [
        Region(key=f"r{start + i}", what="information", shape="value", importance=importance)  # type: ignore[arg-type]
        for i in range(n)
    ]


@case("spc-sz-01")
def test_block_bodies_are_counted_once_per_task_and_blocks_are_shared_by_construction():
    g = big_with_blocks()
    assert len(g.screens) == 60
    pack = assemble(g)
    assert len(pack.blocks) >= 10
    blocks_md = pack.files["global/blocks.md"]
    counter = counter_for(pack)
    deduped_tasks = 0
    for bid, body in pack.blocks.items():
        assert blocks_md.count(body) == 1  # the body is written once in the shared file
        users = [
            p for p, t in pack.files.items() if p.startswith("screens/") and bid in screen_blocks(t)
        ]
        assert len(users) >= 3  # three or more places: the same block, the same bytes
    for t in pack.tasks:
        paths = t["reads"].get("screens", [])
        if len(paths) < 2:
            continue
        naive = sum(counter.count(pack.files[p]) for p in paths)
        seen: set[str] = set()
        repeats = 0
        for p in paths:
            for bid in screen_blocks(pack.files[p]):
                repeats += counter.count(pack.blocks[bid]) if bid in seen else 0
                seen.add(bid)
        assert t["read_tokens"]["slices"] == naive - repeats  # each block body once per task
        deduped_tasks += repeats > 0
        assert task_tokens(pack, t)["total"] == t["read_tokens"]["total"]  # the simulator agrees
    assert deduped_tasks >= 10
    assert pack.budget_report["tasks_over_budget"] == []


@case("spc-sz-02")
def test_a_pilot_of_five_screens_is_five_tasks_each_inside_the_budget():
    pack = assemble(make_graph("big"))
    pilots = pack.pilots["APP-001"]
    assert len(pilots) == 5
    by_screen = {}
    for t in pack.tasks:
        for sid in (p.split("/")[-1][:-3] for p in t["reads"].get("screens", [])):
            by_screen.setdefault(sid, []).append(t)
    pilot_tasks = [by_screen[s][0] for s in pilots]
    assert len({t["id"] for t in pilot_tasks}) == 5  # one task each
    assert all(t["wave"] == "W2" and len(t["reads"]["screens"]) == 1 for t in pilot_tasks)
    assert all(t["read_tokens"]["total"] <= t["read_tokens"]["budget"] for t in pilot_tasks)
    # every other screen travels after the pilot (a later wave)
    assert all(t["wave"] != "W2" for t in pack.tasks if t not in pilot_tasks and t["wave"] != "W0")


@case("spc-sz-03")
def test_a_phone_screen_above_the_mobile_fit_limits_warns_and_a_gross_breach_is_repaired_or_waived():
    g = make_graph("small").model_copy(deep=True)
    s = _phone_screen(g)
    s.regions = _regions(4, "primary")  # limit 3: a warning
    mild = [f for f in validate(g, only={"V-37"}) if f.where == s.id]
    assert mild and {f.severity for f in mild} == {"warning"} and "primary regions" in mild[0].why
    s.regions = _regions(7, "primary")  # 2x the limit: a gross breach
    gross = [f for f in validate(g, only={"V-37"}) if f.where == s.id and f.severity == "blocker"]
    assert gross and "split the screen" in gross[0].fix and "waiver" in gross[0].fix
    waived = validate(g, only={"V-37"}, waivers={s.id})  # recorded waiver: back to a warning
    assert not [f for f in waived if f.where == s.id and f.severity == "blocker"]
    assert [f for f in waived if f.where == s.id and f.severity == "warning"]
    # the same breach on a hover intent is an error whatever the count
    s.regions = _regions(2)
    s.actions = [Action(key="peek", intent="hover", what="shows more")]
    assert [f for f in validate(g, only={"V-37"}) if f.severity == "blocker" and "hover" in f.why]


@case("spc-sz-04")
def test_a_web_portal_table_is_exempt_from_the_phone_column_limit(tmp_path_factory):
    g = make_graph("small").model_copy(deep=True)
    s = _phone_screen(g)
    table = Region(
        key="ledger",
        what="the table of bookings",
        shape="collection",
        attrs=["columns=9"],
        cardinality="many",
        bind=[],
    )
    s.regions = [table]
    phone = [f for f in validate(g, only={"V-37"}) if f.where == s.id]
    assert phone and "9 columns on a phone" in phone[0].why
    s.surface = "web"
    assert not [f for f in validate(g, only={"V-37"}) if f.where == s.id]  # exempt on a web portal
    f = cached_flow(tmp_path_factory, "in-thorough", lambda: raw("thorough"))
    web = [x for x in f.graph.screens.values() if x.surface == "web"]
    assert web and not [
        x for x in validate(f.graph, only={"V-37"}) if x.where in {w.id for w in web}
    ]


@case("spc-sz-04")
def test_the_rest_of_the_mobile_fit_rule_still_applies_to_a_web_portal():
    g = make_graph("small").model_copy(deep=True)
    s = _phone_screen(g, surface="web")
    s.regions = _regions(8, "primary")
    s.actions = [Action(key="peek", intent="hover", what="shows more")]
    assert [f for f in validate(g, only={"V-37"}) if f.where.startswith(s.id)]


@case("spc-sz-05")
def test_the_pack_asks_workers_to_follow_the_pilot_and_keep_a_design_language_record(
    tmp_path_factory,
):
    f = cached_flow(tmp_path_factory, "in-standard", lambda: raw("standard"))
    agents = f.pack_files["AGENTS.md"]
    assert "Visual consistency across parallel workers is NOT guaranteed by the pack" in agents
    assert "pilot" in agents and "design-language record" in agents
    plan = json.loads(f.pack_files["build/build-plan.json"])
    assert plan["waves"]["W2"]  # the pilot slice is its own wave, before the fan-out
    first_fanout = min(
        i for i, w in enumerate(sorted(plan["waves"])) if plan["waves"][w] and w > "W2"
    )
    assert first_fanout >= 0
    assert any(
        "polish" in str(x) for x in plan["waves"]["W6"]
    )  # the front end's own critic and polish come after
    assert f.pack_files["MANIFEST.json"]


@case("spc-sz-06")
def test_a_view_only_screen_has_no_primary_action_and_an_approve_reject_pair_is_one_decision(
    tmp_path_factory,
):
    f = cached_flow(tmp_path_factory, "in-standard", lambda: raw("standard"))
    findings = [x for x in validate(f.graph, only={"V-37"}) if x.severity in ("warning", "blocker")]
    assert not [x for x in findings if "view-only" in x.why or "primary actions" in x.why]
    g = make_graph("small").model_copy(deep=True)
    s = _phone_screen(
        g, actions=[Action(key="open", importance="primary", intent="view", what="opens it")]
    )
    assert [x for x in validate(g, only={"V-37"}) if x.where == s.id and "view-only" in x.why]


@case("spc-sz-06")
def test_an_approve_and_reject_pair_is_not_a_v37_breach():
    g = make_graph("small").model_copy(deep=True)
    s = _phone_screen(g)
    s.actions = [
        Action(key="approve", importance="primary", intent="confirm", what="approves the request"),
        Action(
            key="reject", importance="primary", intent="act_on_item", what="rejects the request"
        ),
    ]
    assert not [
        x for x in validate(g, only={"V-37"}) if x.where == s.id and "primary actions" in x.why
    ]
