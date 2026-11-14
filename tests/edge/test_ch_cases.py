"""SPC-CH (doc 15 12.6): change and versioning. The pack is a pure function of the graph, so a change is a graph edit plus a
re-assemble and the proof is the file-level diff (`views.diff_packs`, by content hash)."""

from __future__ import annotations

import pytest
from edge_support import case
from edge_support.eb_run import Rig, findings_of, load_input, pack_of, source_mentions

from mde_spec_agent.views import diff_packs
from mde_spec_core.graph import Feature, Screen, SpecGraph
from mde_spec_core.ids import IdRegistry, format_id, parse_id
from mde_spec_core.synth import make_graph
from mde_spec_core.tokens import build_tokens

BOOKKEEPING = {"MANIFEST.json", "index.json", "build/build-plan.json"}


def changed(a, b) -> set[str]:
    """Paths whose bytes differ between two packs (added, removed or changed)."""
    return {f["path"] for f in diff_packs(b, a)["files"] if f["status"] != "same"}


# --------------------------------------------------------------------------------------------------- CH-01
@case("spc-ch-01")
def test_ch_01_a_token_only_change_recompiles_the_tokens_and_nothing_else():
    g = make_graph("small")
    g.tokens = {
        "DIR-S001": build_tokens({"id": "DIR-S001", "seed_hue": 40})
    }  # code, zero model calls
    before = pack_of(g)
    after_graph = g.model_copy(deep=True)
    after_graph.tokens = {"DIR-S001": build_tokens({"id": "DIR-S001", "seed_hue": 200})}
    after = pack_of(after_graph)
    diff = changed(before, after)
    assert diff - BOOKKEEPING == {"global/tokens.dtcg.json"}
    # the prefix bundle (what every worker reads first) is untouched here: at most its tail could ever change
    assert before.files["prefix.bundle.md"] == after.files["prefix.bundle.md"]
    assert before.content_hash != after.content_hash  # but the approved content did change
    assert not [
        p for p in diff if p.startswith(("screens/", "contract/", "data-model/", "modules/"))
    ]


# --------------------------------------------------------------------------------------------------- CH-02
@case("spc-ch-02")
def test_ch_02_a_copy_change_touches_the_copy_shard_and_only_the_screens_that_show_the_key():
    g = make_graph("small")
    before = pack_of(g)
    sid = "SCR-005"
    key = next(k for k in g.screens[sid].copy_keys if not k.startswith("lib."))
    g2 = g.model_copy(deep=True)
    g2.locale_copy["en"][key] = "A brand new sentence for this one key"
    after = pack_of(g2)
    users = {s.id for s in g.screens.values() if key in s.copy_keys}
    assert users == {sid}
    assert changed(before, after) - BOOKKEEPING == {"global/copy/en.json", f"screens/{sid}.md"}
    old = before.files["global/copy/en.json"]
    new = after.files["global/copy/en.json"]
    assert [a for a, b in zip(old.split(","), new.split(","), strict=False) if a != b] == [
        a for a in old.split(",") if key in a
    ]  # a per-key patch: only that key's entry differs


# --------------------------------------------------------------------------------------------------- CH-03
def _with_new_feature(si):
    mods = list(si.modules)
    mods[1] = mods[1].model_copy(update={"features": (*mods[1].features, "FTR-099")})
    new = si.features[0].model_copy(
        update={
            "id": "FTR-099",
            "name": "Gift cards",
            "intent": "Buyers give a gift card",
            "priority": "nice",
        }
    )
    return si.model_copy(update={"features": (*si.features, new), "modules": tuple(mods)})


@case("spc-ch-03")
def test_ch_03_adding_a_feature_changes_only_the_nodes_it_touches_and_is_additive(tmp_path):
    si = load_input("standard")
    a = Rig(tmp_path, "standard", spec_input=si, tag="a")
    assert a.full_from_input().status == "awaiting_g2"
    b = Rig(tmp_path, "standard", spec_input=_with_new_feature(si), tag="b")
    assert b.full_from_input().status == "awaiting_g2"
    diff = diff_packs(b.ctx.pack, a.ctx.pack)
    assert diff["counts"]["removed"] == 0  # additive: nothing the first pack had is gone
    paths = changed(a.ctx.pack, b.ctx.pack)
    assert "global/scope-ledger.json" in paths  # the new feature is in the ledger
    assert not [
        p for p in paths if p.startswith(("contract/", "data-model/", "modules/", "global/copy/"))
    ]
    other_modules = [
        p
        for p in paths
        if p.startswith("screens/")
        and a.ctx.graph.screens.get(p[8:-3]) is not None
        and a.ctx.graph.screens[p[8:-3]].module_id not in (None, "MOD-002")
        and not a.ctx.graph.screens[p[8:-3]].system
    ]
    assert other_modules == []  # no other module's own screens moved
    assert diff["counts"]["same"] > 0.9 * sum(diff["counts"].values())


@case("spc-ch-03")
@pytest.mark.xfail(
    strict=True,
    reason="GAP-CH-1: a node's input hash is the hash of its whole call input, and every call input carries the closed id "
    "lists of the whole Brief; adding one feature changes the hash of every entity of every module, so an "
    "input-hash diff cannot select the affected module and a changed Brief re-asks every call",
)
def test_ch_03_only_the_affected_modules_nodes_get_a_new_input_hash(tmp_path):
    si = load_input("standard")
    a = Rig(tmp_path, "standard", spec_input=si, tag="a")
    a.full_from_input()
    b = Rig(tmp_path, "standard", spec_input=_with_new_feature(si), tag="b")
    b.full_from_input()
    ga, gb = a.ctx.graph, b.ctx.graph
    moved = {k for k, e in gb.entities.items() if e.input_hash != ga.entities[k].input_hash}
    owners = {gb.entities[k].owner_module for k in moved}
    assert owners == {"MOD-002"}


# --------------------------------------------------------------------------------------------------- CH-04
@case("spc-ch-04")
def test_ch_04_a_new_program_version_is_a_new_run_that_reopens_g2_and_leaves_the_approved_one_alone(
    tmp_path, monkeypatch
):
    from edge_support.eb_run import api_client, wait_for
    from fastapi.testclient import TestClient

    monkeypatch.setenv("MDE_ENV_FILE", "")
    with api_client(tmp_path, monkeypatch) as client:
        first = client.post("/api/runs", json={"compass_id": "synth:tiny", "offline": True}).json()[
            "run_id"
        ]
        snap1 = wait_for(client, first, ("awaiting_g2",))
        ok = client.post(
            f"/api/runs/{first}/g2",
            json={"decision": "approve", "content_hash": snap1["content_hash"]},
        )
        assert ok.status_code == 200
        approved = client.get(f"/api/runs/{first}").json()
        # L5: another consumer or an added app is another input: a NEW program version, a new run
        res = client.post(
            "/api/runs",
            json={
                "compass_id": "synth:tiny",
                "offline": True,
                "consumer_profile": "standard-default",
            },
        )
        assert res.status_code == 202
        second = res.json()["run_id"]
        assert second != first
        snap2 = wait_for(client, second, ("awaiting_g2",))
        assert snap2["gates"]["g2"] == "open"  # G2 reopens for the new version
        wrong = client.post(
            f"/api/runs/{second}/g2", json={"decision": "approve", "content_hash": "0" * 64}
        )
        assert wrong.status_code == 409  # the first approval binds nothing here
        after = client.get(f"/api/runs/{first}").json()
        assert after["status"] == "approved" and after["approved_hash"] == approved["approved_hash"]
        assert isinstance(client, TestClient)


# --------------------------------------------------------------------------------------------------- CH-05
@case("spc-ch-05")
def test_ch_05_a_renamed_screen_keeps_its_old_id_as_an_alias_that_still_resolves():
    g = make_graph("small")
    sid = "SCR-005"
    s = g.screens[sid]
    s.name = "A better name"
    s.aliases = ["SCR-777"]  # the id the old stamps and links use
    link = g.screens["SCR-001"]
    link.leads_to = [*link.leads_to, "SCR-777"]
    assert not [f for f in findings_of(g, only={"V-02", "V-32"}) if f.severity == "blocker"]
    assert not [
        f for f in findings_of(g, only={"V-32"}) if "SCR-777" in f.why
    ]  # it resolves through the alias
    reg = IdRegistry(used=set(g.screens))
    reg.alias("SCR-777", sid)
    assert reg.resolve("SCR-777") == sid and reg.resolve(sid) == sid
    link.leads_to = [*link.leads_to, "SCR-778"]  # a link to an id that was never an alias
    assert [f for f in findings_of(g, only={"V-32"}) if "SCR-778" in f.why]
    s.aliases = ["SCR-001"]  # an alias is never a live id
    assert [f for f in findings_of(g, only={"V-02"}) if f.severity == "blocker"]


# --------------------------------------------------------------------------------------------------- CH-06
@case("spc-ch-06")
def test_ch_06_a_merge_leaves_a_tombstone_and_an_alias_and_v32_holds(tmp_path):
    from edge_support.eb_run import wrap_gateway

    from mde_spec_agent.lanes.product.plan.schema import MergeRec

    def candidates(inp):
        sysids = set(inp.system_screen_ids)
        apps = dict(x.split("=", 1) for x in inp.screen_apps)
        roles = dict(x.split("=", 1) for x in inp.screen_roles)
        free = [s for s in inp.screen_ids if s not in sysids]
        return [
            (x, y)
            for i, x in enumerate(free)
            for y in free[i + 1 :]
            if apps[x] == apps[y] and roles[x] == roles[y]
        ]

    first = Rig(tmp_path, "standard", tag="probe")
    probe = wrap_gateway(first.ctx)
    probe.rewrite = lambda spec, inp, n: None
    first.go("plan")
    pairs = candidates(probe.calls[0][1])
    assert pairs
    merged = None
    for n, (keep, drop) in enumerate(
        pairs[:12]
    ):  # the model proposes; the validators decide which pair is trivial
        rig = Rig(tmp_path, "standard", tag=f"m{n}")
        gw = wrap_gateway(rig.ctx)

        def merge(out, keep=keep, drop=drop):
            out = out.model_copy(deep=True)
            out.merges = [
                MergeRec(
                    keep=keep, drop=drop, reason="a trivial pair", prov=["assumed:trivial pair"]
                )
            ]
            return out

        gw.rewrite = lambda spec, inp, n, merge=merge: merge if spec == "spec.plan" else None
        if rig.go("plan").status == "awaiting_g2":
            merged = (rig, keep, drop)
            break
    assert merged, "at least one pair of same-role screens merges cleanly"
    rig, keep, drop = merged
    g = rig.ctx.graph
    assert rig.ctx.plan["aliases"] == {drop: keep}
    assert (
        drop not in g.screens and drop in g.screens[keep].aliases
    )  # tombstone: the id is gone, the alias remains
    assert not [f for f in findings_of(g, only={"V-02", "V-32"}) if f.severity == "blocker"]
    reg = IdRegistry(used={*g.screens, drop}, aliases={drop: keep})
    assert reg.resolve(drop) == keep
    reg.tombstone("SCR-123")  # a dropped split part resolves to nothing
    assert reg.resolve("SCR-123") is None


# --------------------------------------------------------------------------------------------------- CH-07
@case("spc-ch-07")
def test_ch_07_the_same_brief_gives_the_same_input_hashes_and_a_changed_one_names_the_nodes(
    tmp_path,
):
    si = load_input("standard")
    a = Rig(tmp_path, "standard", spec_input=si, tag="a")
    a.full_from_input()
    same = Rig(tmp_path, "standard", spec_input=si, tag="same")
    same.full_from_input()
    hashes = lambda g: {  # noqa: E731
        (t, k): n.input_hash
        for t in ("entities", "stories", "rules", "flows", "integrations", "features")
        for k, n in getattr(g, t).items()
    }
    assert hashes(a.ctx.graph) == hashes(
        same.ctx.graph
    )  # a re-approved, unchanged Brief: an empty diff
    assert a.ctx.pack.package_hash == same.ctx.pack.package_hash
    b = Rig(tmp_path, "standard", spec_input=_with_new_feature(si), tag="b")
    b.full_from_input()
    ha, hb = hashes(a.ctx.graph), hashes(b.ctx.graph)
    moved = {k for k in hb if k in ha and hb[k] != ha[k]}
    assert moved  # a changed Brief names nodes whose input changed
    assert {k for k in hb if k not in ha}  # and the nodes that are new
    assert any(
        t == "entities" and a.ctx.graph.entities[k].owner_module == "MOD-002" for t, k in moved
    )


# --------------------------------------------------------------------------------------------------- CH-08
@case("spc-ch-08")
def test_ch_08_a_previous_version_is_restored_by_its_hash_to_the_same_bytes():
    g1 = make_graph("small")
    p1 = pack_of(g1)
    snapshot = g1.canonical_text()  # what a store keeps of version 1
    g2 = g1.model_copy(deep=True)
    g2.locale_copy["en"]["scr_005.title"] = "Version two"
    g2.screens["SCR-005"].name = "Renamed in version two"
    p2 = pack_of(g2)
    assert p2.content_hash != p1.content_hash
    restored = pack_of(
        SpecGraph.model_validate_json(snapshot)
    )  # undo: the manifest of version 1 by its hash
    assert restored.content_hash == p1.content_hash and restored.package_hash == p1.package_hash
    assert (
        restored.files == p1.files
        and restored.manifest["content_hash"] == p1.manifest["content_hash"]
    )
    assert changed(p2, restored) == changed(p2, p1)
    assert p2.files != p1.files  # and version 2 was never touched by restoring version 1


# --------------------------------------------------------------------------------------------------- CH-09
@case("spc-ch-09")
def test_ch_09_starting_phase_2_adds_files_and_leaves_every_phase_1_file_byte_identical():
    g = make_graph("small")
    later_ids = [
        s
        for s in sorted(g.screens)
        if g.screens[s].module_id == "MOD-003" and not g.screens[s].system
    ][:3]
    assert later_ids
    g1 = g.model_copy(deep=True)
    for sid in later_ids:
        g1.screens[sid].status, g1.screens[sid].later_reason = "later", "first_release"
    phase1 = pack_of(g1)
    assert not [
        sid for sid in later_ids if f"screens/{sid}.md" in phase1.files
    ]  # a later feature has no files
    frozen = dict(phase1.files)
    phase2 = pack_of(g)  # the backlog moves later -> in as one batch
    diff = {f["path"]: f["status"] for f in diff_packs(phase2, phase1)["files"]}
    assert {p for p, s in diff.items() if s == "added"} >= {
        f"screens/{sid}.md" for sid in later_ids
    }
    assert not [
        p for p, s in diff.items() if s == "changed" and p.startswith("screens/")
    ]  # Phase 1 cards intact
    assert phase1.files == frozen  # the approved Phase 1 baseline is immutable: nothing mutated it
    assert phase1.content_hash != phase2.content_hash  # Phase 2 gets its own


@case("spc-ch-09")
@pytest.mark.xfail(
    strict=True,
    reason="GAP-CH-2: there is no Scope Change Protocol, change controller or Phase 2 start in Spec: no source file of "
    "Spec, spec-core or Verify mentions a scope change protocol or a phase start",
)
def test_ch_09_the_backlog_moves_in_one_batch_through_a_scope_change_protocol():
    assert source_mentions("scope change protocol", "scope_change", "start_phase", "phase_start")


# --------------------------------------------------------------------------------------------------- CH-10
@case("spc-ch-10")
def test_ch_10_a_compass_id_and_a_spec_minted_id_live_in_separate_namespaces_and_never_collide():
    assert format_id("SCR", 29, spec_minted=False) == "SCR-029"
    assert format_id("SCR", 29, spec_minted=True) == "SCR-S029"
    a, b = parse_id("SCR-029"), parse_id("SCR-S029")
    assert (
        a and b and (a.spec_minted, b.spec_minted) == (False, True) and a.number == b.number == 29
    )
    reg = IdRegistry()
    minted = [reg.mint("SCR") for _ in range(29)]
    assert minted[-1] == "SCR-S029"
    assert reg.carry("SCR-029") == "SCR-029"  # Compass issues SCR-029 later: no collision
    with pytest.raises(ValueError):
        reg.carry("SCR-029")  # but a real duplicate is still an error
    g = make_graph("small")
    base = g.screens["SCR-005"]
    for sid in ("SCR-029", "SCR-S029"):
        g.screens[sid] = Screen(
            **{
                **base.model_dump(),
                "id": sid,
                "name": f"copy {sid}",
                "copy_keys": [],
                "stories": [],
            }
        )
    assert not [f for f in findings_of(g, only={"V-02", "V-32"}) if f.severity == "blocker"]
    from mde_spec_core.ids import namespaced_copy_key

    assert namespaced_copy_key("SCR-029", "title") != namespaced_copy_key("SCR-S029", "title")


# --------------------------------------------------------------------------------------------------- CH-11
@case("spc-ch-11")
def test_ch_11_a_feature_moving_to_the_backlog_needs_consent_and_the_approved_baseline_stays_immutable():
    g = make_graph("small")
    approved = pack_of(g)
    frozen = dict(approved.files)
    sid = "SCR-009"
    g2 = g.model_copy(deep=True)
    g2.screens[sid].status, g2.screens[sid].later_reason = "later", "capacity"
    g2.add(
        "features",
        Feature(
            id="FTR-950",
            name="Moved",
            status="later",
            later_reason="capacity",
            user_priority="must",
            user_evidenced=True,
            outline="kept in the ledger",
        ),
    )
    f = [x for x in findings_of(g2, only={"V-28"}) if x.where == "FTR-950"]
    assert {x.why for x in f} >= {"capacity deferral without G1.5 consent"}  # no consent: refused
    g2.features["FTR-950"].consent_id = "CON-1"
    assert not [
        x for x in findings_of(g2, only={"V-28"}) if x.where == "FTR-950"
    ]  # with consent: allowed
    moved = pack_of(g2)
    assert (
        f"screens/{sid}.md" not in moved.files
        and "FTR-950" in moved.files["global/scope-ledger.json"]
    )
    assert not [
        p for p in changed(approved, moved) if p.startswith("screens/") and p != f"screens/{sid}.md"
    ]
    assert (
        approved.files == frozen
    )  # the approved baseline is never edited: the change is a new version
    assert (
        len(moved.tasks) != len(approved.tasks) or moved.package_hash != approved.package_hash
    )  # waves re-plan


@case("spc-ch-11")
@pytest.mark.xfail(
    strict=True,
    reason="GAP-CH-3: there is no L4 change set: nothing in Spec files a change set with consent or regenerates only the "
    "affected slices after G2 (no source file mentions a change set)",
)
def test_ch_11_an_l4_change_set_is_filed_with_consent_and_regenerates_incrementally():
    assert source_mentions("change set", "change_set", "changeset")
