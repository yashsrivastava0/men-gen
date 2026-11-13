"""The shared machinery: batch id ranges that cannot collide, patches with removals, batch sizes from the fusion rule,
slices assembled by code and capped, gate 1 against what a patch made worse."""

from __future__ import annotations

from mde_spec_agent.lanes.product.kit.batch import chunked, merge_patch_list
from mde_spec_agent.lanes.product.kit.gate import (
    BATCH_SPAN,
    Gate1,
    Minter,
    apply_ext,
    highest_minted,
    registry_from_graph,
)
from mde_spec_agent.lanes.product.kit.slices import CHUNK_CHARS, chunk_lines, lines_of
from mde_spec_agent.lanes.product.prod_module.schema import ModuleOut
from mde_spec_agent.lanes.product.steps.inputs import module_batches, n_per_call
from mde_spec_agent.lanes.product.steps.plan import ensure_derived, env_of
from mde_spec_agent.run.patch import node_patch
from mde_spec_core.graph import Entity, SpecGraph


def test_batches_mint_from_disjoint_ranges_so_parallel_batches_never_collide(env):
    ctx = env.ctx("standard")
    ensure_derived(ctx)
    g = ctx.graph
    a, b = Minter.for_batch(g, 0), Minter.for_batch(g, 1)
    ids_a = {a.mint("OP") for _ in range(50)} | {a.mint("US") for _ in range(10)}
    ids_b = {b.mint("OP") for _ in range(50)} | {b.mint("US") for _ in range(10)}
    assert (
        not ids_a & ids_b
        and not (ids_a | ids_b) & set(g.operations)
        and not (ids_a | ids_b) & set(g.stories)
    )
    assert (
        min(int(i.split("-S")[1]) for i in ids_b) - max(int(i.split("-S")[1]) for i in ids_a)
        > -BATCH_SPAN
    )
    # a fork mints the same ids as the original would (validation does not move the real counter)
    c = Minter.for_batch(g, 2)
    f = c.fork()
    assert f.mint("OP") == c.mint("OP")
    # existing ids and aliases are never reused
    reg = registry_from_graph(g)
    assert set(g.operations) <= reg.used
    assert highest_minted(g.operations, "OP") == max(int(i.split("-S")[1]) for i in g.operations)


def test_a_minter_refuses_to_run_past_its_range(env):
    ctx = env.ctx("quick")
    ensure_derived(ctx)
    m = Minter.for_batch(ctx.graph, 0)
    import pytest

    def mint_too_many() -> None:
        for _ in range(BATCH_SPAN + 5):
            m.mint("RUL")

    with pytest.raises(ValueError, match="ran out"):
        mint_too_many()


def test_removals_are_applied_after_the_tables_and_unknown_tables_are_refused():
    g = SpecGraph()
    g.entities["ENT-001"] = Entity(id="ENT-001", name="A")
    g.entities["ENT-002"] = Entity(id="ENT-002", name="B")
    patch = node_patch("entities", [Entity(id="ENT-003", name="C")], replace=True)
    patch["remove"] = {"entities": ["ENT-001", "ENT-404"]}
    touched = apply_ext(g, patch)
    assert touched == ["ENT-003"] and sorted(g.entities) == ["ENT-002", "ENT-003"]
    import pytest

    with pytest.raises(ValueError):
        apply_ext(g, {"tables": {}, "remove": {"nonsense": ["x"]}})


def test_merged_sub_patches_keep_tables_flags_and_marks_in_order():
    a = {
        "tables": {"entities": {"ENT-1": {"id": "ENT-1"}}},
        "replace": True,
        "flags": [{"node": "ENT-1"}],
        "reasked": True,
    }
    b = {
        "tables": {"entities": {"ENT-2": {"id": "ENT-2"}}, "stories": {"US-1": {}}},
        "replace": True,
        "flags": [{"node": "ENT-2"}],
        "degraded_call": True,
    }
    m = merge_patch_list([a, b])
    assert sorted(m["tables"]["entities"]) == ["ENT-1", "ENT-2"] and "stories" in m["tables"]
    assert (
        [f["node"] for f in m["flags"]] == ["ENT-1", "ENT-2"]
        and m["reasked"]
        and m["degraded_call"]
    )
    assert (
        chunked([1, 2, 3, 4, 5], 2) == [[1, 2], [3, 4], [5]]
        and chunked([], 3) == []
        and chunked([1], 0) == [[1]]
    )


def test_batch_sizes_come_from_the_fusion_rule_and_degrade_when_the_limit_is_unknown(env):
    ctx = env.ctx("thorough")
    ensure_derived(ctx)
    big = n_per_call(ctx, "entity", 5)
    assert 1 <= big <= 5  # clamped to the kind's cap from spec-config
    ctx.writer = ctx.writer.model_copy(update={"max_output_tokens": None})
    assert (
        n_per_call(ctx, "entity", 5) == 1
    )  # an unknown limit degrades conservatively: one item per call
    ctx.writer = ctx.writer.model_copy(update={"max_output_tokens": 2000})
    assert n_per_call(ctx, "entity", 5) == 2  # floor(0.5 * 2000 / 450)
    e = env_of(ctx)
    small = module_batches(ctx, e)
    n_entities = sum(1 for b in small for k, _ in b.items if k == "E")
    assert n_entities == len(
        e.graph.entities
    )  # every entity of every module is in exactly one batch
    assert len({b.batch_id for b in small}) == len(small)
    assert len(small) > len(
        {b.module_id for b in small}
    )  # modules with many items are split into parts
    ids = [i for b in small for k, i in b.items if k == "S"]
    assert len(ids) == len(set(ids)) == len(e.graph.stories)


def test_every_story_and_rule_of_the_graph_belongs_to_exactly_one_module_batch(env, key):
    ctx = env.ctx(key)
    ensure_derived(ctx)
    e = env_of(ctx)
    items = [x for b in module_batches(ctx, e) for x in b.items]
    for kind, table in (("S", e.graph.stories), ("R", e.graph.rules), ("E", e.graph.entities)):
        got = sorted(i for k, i in items if k == kind)
        assert got == sorted(table), kind  # total and exclusive: no node is written by two batches


def test_slices_are_chunked_below_the_untrusted_cap_without_cutting_a_line():
    lines = [f"LINE {i} " + "x" * 200 for i in range(500)]
    chunks = chunk_lines(lines)
    assert all(len(c) <= CHUNK_CHARS for c in chunks) and len(chunks) > 1
    assert lines_of(chunks) == lines


def test_gate_1_reports_only_what_the_patch_made_worse(env):
    ctx = env.ctx("standard")
    ensure_derived(ctx)
    e = env_of(ctx)
    g = Gate1(e.graph)
    assert g.errors({"tables": {}, "replace": True}) == []
    ent = e.graph.entities["ENT-003"].model_copy(deep=True)
    ent.owner_module = "MOD-999"  # V-03: an edge to a module that does not exist
    patch = node_patch("entities", [ent], replace=True)
    errs = g.errors(patch)
    assert errs and "V-03" in errs[0] and "MOD-999" in errs[0]
    assert e.graph.entities["ENT-003"].owner_module != "MOD-999"  # the snapshot was never touched
    assert isinstance(ModuleOut().model_dump(), dict)
