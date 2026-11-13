import pytest

from mde_spec_core.lint_pack import pack_from_files, read_pack_dir, write_pack_dir
from mde_spec_core.pack import PackOptions, assemble
from mde_spec_core.profiles import load_profiles
from mde_spec_core.render import TokenCounter
from mde_spec_core.simulate import (
    simulate_all,
    simulate_one,
    simulate_parallel,
    task_tokens,
)
from mde_spec_core.synth import make_graph

PRESETS = ["small", "medium", "big", "program"]


@pytest.fixture(scope="module")
def packs():
    return {n: assemble(make_graph(n)) for n in PRESETS}


@pytest.mark.parametrize("name", PRESETS)
def test_simulated_task_totals_equal_the_packagers(packs, name):
    p = packs[name]
    for t in p.tasks:
        got = task_tokens(p, t)
        assert got["total"] == t["read_tokens"]["total"], t["id"]
        for part in ("prefix", "shards", "module", "slices", "front_end"):
            if part in t["read_tokens"]:
                assert got[part] == t["read_tokens"][part], (t["id"], part)


def test_totals_also_equal_after_a_round_trip_through_a_folder(tmp_path, packs):
    write_pack_dir(packs["medium"], tmp_path)
    p = pack_from_files(read_pack_dir(tmp_path))
    assert all(task_tokens(p, t)["total"] == t["read_tokens"]["total"] for t in p.tasks)


def test_totals_equal_for_another_consumer_budget():
    g = make_graph("big")
    p = assemble(g, PackOptions(chars_per_token=2.4, task_read_budget=40000, consumer_id="roomy"))
    assert all(task_tokens(p, t)["total"] == t["read_tokens"]["total"] for t in p.tasks)
    assert simulate_parallel(p, 3)["timeline"][0]["budget"] == 40000


@pytest.mark.parametrize("name", PRESETS)
def test_all_at_once_is_refused_with_a_plain_reason_when_the_pack_is_big(packs, name):
    r = simulate_all(packs[name], load_profiles().weakest())
    assert r["allowed"] is False and r["files"] == []
    assert "one read holds" in r["reason"] and str(r["total_tokens"]) in r["reason"]
    assert r["consumer"] == "weak-default"


def test_all_at_once_is_allowed_when_it_fits():
    p = assemble(make_graph("tiny"), PackOptions(task_read_budget=400000))
    r = simulate_all(p)
    assert (
        r["allowed"]
        and r["total_tokens"] <= r["whole_pack_tokens"]
        and "prefix.bundle.md" in r["files"]
    )
    assert "MANIFEST.json" not in r["files"] and r["bytes"] > 0


def test_one_by_one_lists_prefix_and_screen_with_bytes(packs):
    p = packs["medium"]
    c = TokenCounter(p.manifest["tokenizer_chars_per_token"])
    r = simulate_one(p, "SCR-002", c)
    assert r["files"] == ["prefix.bundle.md", "screens/SCR-002.md"]
    assert r["bytes"] == sum(len(p.files[f].encode()) for f in r["files"])
    assert r["token_total"] == c.count(p.files["prefix.bundle.md"]) + c.count(
        p.files["screens/SCR-002.md"]
    )
    assert r["fits"] and r["budget"] == p.manifest["task_read_budget"]
    assert simulate_one(p, "SCR-999", c)["found"] is False


def test_one_by_one_equals_a_one_screen_tasks_slice(packs):
    p = packs["big"]
    t = next(t for t in p.tasks if len(t["reads"]["screens"]) == 1 and t["wave"] == "W2")
    sid = t["reads"]["screens"][0].removeprefix("screens/").removesuffix(".md")
    r = simulate_one(p, sid, TokenCounter(p.manifest["tokenizer_chars_per_token"]))
    assert (
        r["tokens"]["prefix"] == t["read_tokens"]["prefix"]
        and r["tokens"]["screen"] == t["read_tokens"]["slices"]
    )


@pytest.mark.parametrize("name", PRESETS)
def test_parallel_timeline_respects_waves_slots_and_totals(packs, name):
    p = packs[name]
    r = simulate_parallel(p, 3)
    assert "error" not in r and len(r["timeline"]) == len(p.tasks)
    end = {x["task"]: x["end"] for x in r["timeline"]}
    start = {x["task"]: x["start"] for x in r["timeline"]}
    for t in p.tasks:
        assert all(start[t["id"]] >= end[d] for d in t["depends_on"])
    by_slot = {}
    for x in sorted(r["timeline"], key=lambda x: x["start"]):
        assert x["start"] >= by_slot.get(x["slot"], 0)  # a slot never runs two tasks at once
        by_slot[x["slot"]] = x["end"]
    assert max(by_slot) < 3
    assert {x["task"]: x["read_tokens"] for x in r["timeline"]} == {
        t["id"]: t["read_tokens"]["total"] for t in p.tasks
    }
    assert set(r["waves"]) == {t["wave"] for t in p.tasks}


def test_more_workers_never_take_longer_and_one_slot_is_serial(packs):
    p = packs["medium"]
    spans = [simulate_parallel(p, k)["makespan"] for k in (1, 2, 4, 8)]
    assert spans == sorted(spans, reverse=True)
    assert simulate_parallel(p, 1)["makespan"] >= len(p.tasks)
    assert simulate_parallel(p, None)["k"] >= 1
    with pytest.raises(ValueError):
        simulate_parallel(p, -1)
