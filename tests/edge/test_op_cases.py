"""SPC-OP (doc 15 12.7): operations. The run store, the single-flight lock, the checkpoints, the packs and the money record
are the real ones; a crash is an exception out of the gateway double; locale and clock cases run in a subprocess."""

from __future__ import annotations

import dataclasses
import os
import subprocess
import sys

import pytest
from edge_support import case
from edge_support.eb_run import (
    ROOT,
    Rig,
    api_client,
    findings_of,
    load_input,
    pack_of,
    source_mentions,
    wait_for,
    wrap_gateway,
)

from mde_core.errors import ProviderError
from mde_spec_core.canon import sha256_text
from mde_spec_core.synth import make_graph

MODULE = "spec.prod_module"


# --------------------------------------------------------------------------------------------------- OP-01
@case("spc-op-01")
def test_op_01_a_crash_mid_run_resumes_from_the_checkpoints_to_the_same_bytes(tmp_path):
    ref = Rig(tmp_path, "standard", tag="ref")
    assert ref.go("plan", "product").status == "awaiting_g2"
    full_calls = len(ref.provider.calls)
    rig = Rig(tmp_path, "standard", tag="kill")
    gw = wrap_gateway(rig.ctx)
    count = {"n": 0}

    def kill_at_seven(spec, inp, n):
        count["n"] += 1
        return RuntimeError("process killed") if count["n"] == 7 else None

    gw.raise_on = kill_at_seven
    out = rig.go("plan", "product")
    assert out.status == "failed" and out.stage == "product"  # the process died mid-step
    rig.store.update_run(
        rig.ctx.run_id, status="running"
    )  # what the record said when the process was killed
    assert rig.store.recover_interrupted() == [
        rig.ctx.run_id
    ]  # the restart finds it and says so, plainly
    rec = rig.store.get_run(rig.ctx.run_id)
    assert rec["status"] == "partial" and rec["partial_reason"] == "crash"
    gw.raise_on = None
    before = len(rig.provider.calls)
    assert rig.resume("plan", "product").status == "awaiting_g2"
    assert rig.ctx.graph.canonical_text() == ref.ctx.graph.canonical_text()
    assert (
        0 < len(rig.provider.calls) - before < full_calls
    )  # only the unfinished batches were asked again


# --------------------------------------------------------------------------------------------------- OP-02
@case("spc-op-02")
def test_op_02_failed_screen_batches_are_degraded_with_defaults_and_a_missing_need_never_reaches_g2(
    tmp_path,
):
    rig = Rig(tmp_path, "quick")
    pre = ("plan", "product", "design", "join", "freeze", "gloss")
    assert rig.go(*pre).status == "awaiting_g2"
    gw = wrap_gateway(rig.ctx)
    failing = {"ids": None}

    def fail_first_batch(spec, inp, n):
        if spec == "spec.scrn":
            ids = [s.id for s in inp.slots]
            failing["ids"] = failing["ids"] or ids
            if ids == failing["ids"]:
                return ProviderError("scripted provider failure")
        return None

    gw.raise_on = fail_first_batch
    assert rig.go("screens", "hero", "behav", "copyl").status == "awaiting_g2"
    g = rig.ctx.graph
    bad = {sid for sid in failing["ids"] if g.screens[sid].degraded}
    assert bad == set(failing["ids"])  # those screens only, written from the library defaults
    assert all(g.screens[s].states for s in bad)  # a degraded card is still a complete card
    ledger = rig.ctx.runner.ledger
    assert all(ledger.count(s) == 1 for s in bad)  # the ladder: one re-ask per node, then the flag
    assert {f["node"] for f in rig.ctx.flags if f.get("kind") == "degraded"} >= bad
    # a pack with a `missing` builder need never reaches G2
    g.entities.clear()
    rig.ctx.save_graph()  # a resumed run reads the saved graph
    out = rig.go("assemble")
    assert out.status == "partial" and out.reason == "missing_need"
    assert rig.store.get_run(rig.ctx.run_id)["status"] == "partial"
    assert rig.ctx.check is not None and [n for n in rig.ctx.check.needs if n.status == "missing"]


# --------------------------------------------------------------------------------------------------- OP-03
@case("spc-op-03")
def test_op_03_two_starts_for_one_program_are_one_run_and_a_different_input_is_refused(
    tmp_path, monkeypatch
):
    with api_client(tmp_path, monkeypatch) as client:
        a = client.post("/api/runs", json={"compass_id": "synth:small", "offline": True})
        b = client.post(
            "/api/runs", json={"compass_id": "synth:small", "offline": True}
        )  # a double click
        assert a.status_code == 202 and b.status_code in (200, 202)
        assert b.json()["run_id"] == a.json()["run_id"]
        other = client.post(
            "/api/runs",
            json={
                "compass_id": "synth:small",
                "offline": True,
                "consumer_profile": "standard-default",
            },
        )
        assert other.status_code == 409 and other.json()["error"]["code"] == "run_active"
        assert (
            len(client.get("/api/runs").json()["runs"]) == 1
        )  # one run exists, whatever was asked
        wait_for(client, a.json()["run_id"], ("awaiting_g2",))


# --------------------------------------------------------------------------------------------------- OP-04
@case("spc-op-04")
def test_op_04_a_phone_number_and_a_key_in_the_brief_never_reach_fixtures_or_the_pack_and_the_scan_names_them(
    tmp_path,
):
    from mde_spec_core import mock
    from mde_spec_core.lint_pack import lint_pack_files

    phone = "+91 " + "98" + "76543210"
    key = "sk-" + "A1b2C3d4" * 3  # secret-scan: allow
    si = load_input("quick")
    feats = list(si.features)
    feats[0] = feats[0].model_copy(
        update={"intent": f"{feats[0].intent}. Call the owner on {phone}, api key {key}"}
    )
    rig = Rig(tmp_path, "quick", spec_input=si.model_copy(update={"features": tuple(feats)}))
    assert rig.full_from_input().status == "awaiting_g2"
    digits = phone.replace(" ", "").lstrip("+")
    assert (
        key in rig.ctx.graph.canonical_text()
    )  # the Brief's own words are in the working graph ...
    hero_text = " ".join(mock.iter_strings([r.model_dump() for r in rig.ctx.graph.hero.values()]))
    assert (
        digits not in hero_text.replace(" ", "") and key not in hero_text
    )  # ... but never in a fixture
    assert (
        mock.find_unsafe(mock.iter_strings([r.content for r in rig.ctx.graph.hero.values()])) == []
    )
    for path, text in rig.ctx.pack.files.items():  # and never in a file of the pack
        assert key not in text and digits not in text.replace(" ", ""), path
    assert not [f for f in lint_pack_files(dict(rig.ctx.pack.files)) if f.code == "V-20"]
    v20 = findings_of(rig.ctx.graph, rig.ctx.pack, only={"V-20"})
    assert [
        f for f in v20 if f.severity == "blocker" and f.where == "graph"
    ]  # the scan names the graph
    assert key not in " ".join(f.why + f.fix for f in v20)  # and never prints the value it found


# --------------------------------------------------------------------------------------------------- OP-05
@case("spc-op-05")
def test_op_05_packs_are_content_addressed_and_versions_share_their_unchanged_files():
    g = make_graph("small")
    a = pack_of(g)
    for path, info in a.manifest["files"].items():
        body = a.files[path]
        assert info["sha256"] == sha256_text(body)  # every listed file is named by its content hash
    g2 = g.model_copy(deep=True)
    g2.locale_copy["en"]["scr_005.title"] = "Version two"
    b = pack_of(g2)
    same = [
        p
        for p, i in a.manifest["files"].items()
        if b.manifest["files"].get(p, {}).get("sha256") == i["sha256"]
    ]
    assert len(same) > 0.9 * len(a.manifest["files"])  # a version stores only what changed
    assert pack_of(g).package_hash == a.package_hash  # and identical content is one object


@case("spc-op-05")
@pytest.mark.xfail(
    strict=True,
    reason="GAP-OP-1: no retention policy exists: neither the Spec settings nor the run store keep, prune or "
    "configure a number of working versions (approved baselines are never deleted, but nothing else is managed)",
)
def test_op_05_a_retention_policy_keeps_approved_baselines_and_a_number_of_working_versions():
    from mde_spec_agent.config import SpecSettings

    assert [
        f for f in SpecSettings.model_fields if "retention" in f or "keep" in f or "retain" in f
    ]


# --------------------------------------------------------------------------------------------------- OP-06
@case("spc-op-06")
def test_op_06_a_slow_route_times_out_and_the_node_is_degraded(tmp_path):
    import asyncio

    from mde_spec_agent.calls.base import CallRunner
    from mde_spec_agent.lanes.product.prod_module.spec import SPEC

    rig = Rig(tmp_path, "standard")
    assert rig.go("plan", "product").status == "awaiting_g2"
    inp = rig.provider_calls(MODULE)[0].input

    class Slow:
        calls = 0

        async def run(self, spec, input, ctx):
            Slow.calls += 1
            await asyncio.sleep(1.0)  # far past the step's time budget
            raise AssertionError("never reached")

        async def budget_status(self):
            return None

    runner = CallRunner(Slow(), rig.ctx.guard, run_id=rig.ctx.run_id)
    quick = dataclasses.replace(SPEC, timeout_s=-4.95)  # the step's budget (+5 s backstop) is 50 ms
    res = asyncio.run(runner.run(quick, inp, node_ids=["ENT-X"], step="product"))
    assert (
        res.used_fallback and res.flagged and res.reasked
    )  # timeout means degraded, never a hang or a crash
    assert (
        Slow.calls == 2 and "TimeoutError" in res.errors[0]
    )  # the call and its one re-ask, both timed out
    assert res.output.entities  # the library default is a complete answer


# --------------------------------------------------------------------------------------------------- OP-07
@case("spc-op-07")
def test_op_07_a_policy_reference_with_an_old_date_is_not_current():
    from mde_spec_core.policy import default_policy_sheet, policy_ref, policy_ref_is_current

    sheet = default_policy_sheet()
    rule = sheet.rules[0]
    assert policy_ref_is_current(policy_ref(rule.id, sheet), sheet)
    assert not policy_ref_is_current(f"policy:{rule.id}@2020-01-01", sheet)  # cfg: an old date
    assert not policy_ref_is_current("policy:POL-DOES-NOT-EXIST@2026-11-14", sheet)


@case("spc-op-07")
def test_op_07_a_stale_policy_sheet_raises_the_policy_sheet_stale_flag_at_g2():
    from mde_spec_core.graph import Prov
    from mde_spec_core.policy import default_policy_sheet

    rule = default_policy_sheet().rules[0]
    g = make_graph("small")
    g.screens["SCR-005"].provenance = [Prov(kind="policy", ref=f"policy:{rule.id}@2020-01-01")]
    assert source_mentions("policy_sheet_stale", "sheet_stale") or any(
        f.code == "V-33" for f in findings_of(g, only={"V-33"})
    )


# --------------------------------------------------------------------------------------------------- OP-08
@case("spc-op-08")
def test_op_08_the_pack_records_the_library_version_it_was_made_with(tmp_path):
    from mde_spec_agent.compose import library_version

    rig = Rig(tmp_path, "quick")
    assert rig.full().status == "awaiting_g2"
    made = rig.ctx.pack.manifest["produced_with"]
    assert made["library_version"] == library_version() != "0"
    assert made["route_fingerprint"] and rig.ctx.pack.manifest["checked_by"] == ["self_check"]


@case("spc-op-08")
@pytest.mark.xfail(
    strict=True,
    reason="GAP-OP-2: the manifest records the library and route versions but not the validator version, so nothing can "
    "tell which packs to re-verify after a validator upgrade",
)
def test_op_08_the_pack_records_the_validator_version_for_re_verification():
    p = pack_of(make_graph("small"))
    blob = str(p.manifest["produced_with"]).lower() + str(p.manifest.get("checked_by"))
    assert "validator" in blob


# --------------------------------------------------------------------------------------------------- OP-09
SCRIPT = """
import sys, tempfile, pathlib
sys.path.insert(0, {edge!r})
from edge_support.eb_run import Rig
rig = Rig(pathlib.Path(tempfile.mkdtemp()), "quick")
out = rig.full()
assert out.status == "awaiting_g2", out
print("HASH", rig.ctx.pack.content_hash, rig.ctx.pack.package_hash)
"""


def _hash_under(env_extra: dict[str, str]) -> str:
    env = {k: v for k, v in os.environ.items() if k not in ("LC_ALL", "LANG", "TZ")}
    env.update({"MDE_ENV_FILE": "", **env_extra})
    code = SCRIPT.format(edge=str(ROOT / "tests" / "edge"))
    done = subprocess.run(
        [sys.executable, "-I", "-c", code],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(ROOT),
        timeout=300,
    )
    assert done.returncode == 0, done.stderr[-800:]
    return next(x for x in done.stdout.splitlines() if x.startswith("HASH ")).strip()


@case("spc-op-09")
def test_op_09_the_same_run_gives_the_same_bytes_under_two_locales_clocks_and_hash_seeds():
    a = _hash_under({"LC_ALL": "C", "LANG": "C", "TZ": "UTC", "PYTHONHASHSEED": "1"})
    b = _hash_under(
        {
            "LC_ALL": "tr_TR.UTF-8",
            "LANG": "tr_TR.UTF-8",
            "TZ": "Pacific/Auckland",
            "PYTHONHASHSEED": "777",
        }
    )
    assert a == b  # locale-independent sorting, no timestamps in bodies
    g = make_graph("small")
    p = pack_of(g)
    assert "2026-" not in "".join(t for path, t in p.files.items() if path.endswith(".md"))


# --------------------------------------------------------------------------------------------------- OP-10
@case("spc-op-10")
def test_op_10_backlog_outlines_are_ledger_entries_written_inside_the_module_calls(tmp_path):
    rig = Rig(tmp_path, "thorough")
    assert rig.go("plan", "product").status == "awaiting_g2"
    g = rig.ctx.graph
    later = [f for f in g.features.values() if f.status == "later"]
    assert len(later) >= 5 and all(
        f.outline.strip() for f in later
    )  # the outlines are in the ledger
    kinds = {c.spec_id for c in rig.provider.calls}
    assert not [k for k in kinds if "outline" in k]  # there is no call kind for them
    assert len(rig.provider_calls(MODULE)) == len(
        g.modules
    )  # one module call per module: the backlog adds none


# --------------------------------------------------------------------------------------------------- OP-11
@case("spc-op-11")
def test_op_11_a_version_bump_in_flight_never_mixes_old_and_new_checkpoints(tmp_path):
    ref = Rig(tmp_path, "standard", tag="ref")
    ref.go("plan", "product")
    full_calls = len(ref.provider.calls)
    rig = Rig(tmp_path, "standard", tag="bump")
    gw = wrap_gateway(rig.ctx)
    count = {"n": 0}

    def kill_at_seven(spec, inp, n):
        count["n"] += 1
        return RuntimeError("process killed") if count["n"] == 7 else None

    gw.raise_on = kill_at_seven
    assert rig.go("plan", "product").status == "failed"
    gw.raise_on = None
    rig.store.update_run(rig.ctx.run_id, status="partial")
    rig.ctx.versions = dataclasses.replace(
        rig.ctx.versions, library="bumped-library"
    )  # a deployment mid-run
    before = len(rig.provider.calls)
    assert rig.resume("plan", "product").status == "awaiting_g2"
    planned = len(ref.provider_calls("spec.plan"))
    assert (
        len(rig.provider.calls) - before == full_calls - planned
    )  # no old product batch was replayed
    assert (
        rig.ctx.graph.canonical_text() == ref.ctx.graph.canonical_text()
    )  # and the result is the same
    keys = [p.stem for p in (rig.store.run_dir(rig.ctx.run_id) / "checkpoints").glob("*.json")]
    assert (
        len(keys) == len(set(keys)) and len(keys) > count["n"] - 1
    )  # old and new keys sit side by side, never merged


@case("spc-op-11")
@pytest.mark.xfail(
    strict=True,
    reason="GAP-OP-4: a resumed run does NOT keep the versions it began with: the versions are read fresh from the "
    "deployed code at resume (the run record stores none), so a bump in flight re-asks every batch instead of "
    "continuing under the original versions",
)
def test_op_11_a_resumed_run_keeps_the_versions_it_began_with(tmp_path):
    rig = Rig(tmp_path, "standard")
    rig.go("plan", "product")
    rec = rig.store.get_run(rig.ctx.run_id)
    assert rec.get("versions")
