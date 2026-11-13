"""The result of the lane assembled into a pack passes spec check (Gate 3)."""

from __future__ import annotations

from mde_spec_core.check import spec_check
from mde_spec_core.pack import assemble

OWN = {
    "V-06",
    "V-07",
    "V-08",
    "V-13",
    "V-14",
    "V-22",
    "V-24",
    "V-25",
    "V-27",
    "V-30",
    "V-31",
    "V-35",
    "V-38",
    "V-42",
    "V-43",
}


def check(run):
    g = run.graph
    pack = assemble(g)
    return pack, spec_check(g, pack, rcode_log=run.ctx.rcode, flags=run.ctx.flags)


def test_quick_pack_passes_spec_check_with_no_blockers(finished):
    pack, rep = check(finished("quick"))
    assert rep.blockers == [] and rep.ok, rep.plain_lines()[:5]
    assert any(p.startswith("screens/") and p.endswith(".could.md") for p in pack.files)


def test_standard_pack_has_no_blockers_of_this_lane(finished):
    run = finished("standard")
    pack, rep = check(run)
    # the only blockers left are a baseline screen that serves no feature (V-19, V-40 on a Compass screen): Product's, not Screens'
    assert [(f.code, f.where) for f in rep.blockers if f.code not in ("V-19", "V-40")] == []
    assert {f.where for f in rep.blockers} <= {"SCR-003"}
    assert not list(rep.missing)
    assert pack.content_hash == run.graph.content_hash()


def test_thorough_pack_has_no_blockers_of_this_lane(finished):
    _, rep = check(finished("thorough"))
    assert [f for f in rep.blockers if f.code in OWN] == []


def test_the_pack_is_byte_stable_for_the_same_graph(finished):
    g = finished("quick").graph
    a, b = assemble(g), assemble(g)
    assert a.files == b.files and a.content_hash == b.content_hash
