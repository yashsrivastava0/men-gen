import pytest

from mde_spec_core.graph import Feature
from mde_spec_core.pack import PackBreach, PackOptions, assemble
from mde_spec_core.synth import PRESETS, make_graph

NAMES = list(PRESETS)


@pytest.mark.parametrize("name", NAMES)
def test_every_preset_packs_inside_its_budgets(name):
    p = assemble(make_graph(name))
    assert p.budget_report["tasks_over_budget"] == []
    assert p.budget_report["max_task_read"] <= p.manifest["task_read_budget"]
    assert all(t["read_tokens"]["total"] <= p.manifest["task_read_budget"] for t in p.tasks)
    assert "prefix.bundle.md" in p.files and "MANIFEST.json" in p.files


@pytest.mark.parametrize("name", ["small", "big"])
def test_two_runs_give_identical_bytes_and_hashes(name):
    a, b = assemble(make_graph(name)), assemble(make_graph(name))
    assert a.files == b.files
    assert (a.content_hash, a.package_hash) == (b.content_hash, b.package_hash)


def test_repackaging_for_another_consumer_changes_only_sharding_and_task_cards():
    g = make_graph("big")
    a = assemble(g)
    h = assemble(
        g,
        PackOptions(
            chars_per_token=2.4, task_read_budget=40000, consumer_id="large-context-default"
        ),
    )
    assert a.content_hash == h.content_hash  # an approval is never voided by re-packaging
    assert a.package_hash != h.package_hash
    for path in a.files:  # content files are byte-identical
        if path.startswith(("screens/", "global/")) and path != "global/needs.json":
            assert a.files[path] == h.files[path]
    assert len(h.tasks) <= len(a.tasks)  # a bigger window means fewer, bigger tasks


def test_prefix_is_one_identical_bundle_for_every_task():
    p = assemble(make_graph("big"))
    assert all(t["reads"]["prefix"] == "prefix.bundle.md" for t in p.tasks)
    assert (
        len({t["read_tokens"].get("prefix") for t in p.tasks if "prefix" in t["read_tokens"]}) == 1
    )


def test_bodies_hold_no_volatile_data():
    p = assemble(make_graph("small"))
    for path, text in p.files.items():
        if path.endswith(".md"):
            assert p.content_hash not in text and "\r" not in text and text.endswith("\n")
            assert "sha256" not in text and "2026-" not in text


def test_no_layout_words_in_a_card_key_set():
    p = assemble(make_graph("small"))
    card = p.files["screens/SCR-001.md"]
    for bad in ("layout", "position", "grid", "pixel"):
        assert bad not in card.lower().split("regions:")[0]


def test_later_features_have_no_screen_nodes_in_the_pack():
    g = make_graph("small")
    sid = "SCR-004"
    g.screens[sid].status = "later"
    g.screens[sid].later_reason = "capacity"
    g.add(
        "features",
        Feature(
            id="FTR-001",
            name="x",
            status="later",
            later_reason="capacity",
            outline="outline kept in the ledger",
        ),
    )
    p = assemble(g)
    assert f"screens/{sid}.md" not in p.files
    assert "FTR-001" in p.files["global/scope-ledger.json"]


def test_needs_and_blocks_are_exact_and_deterministic():
    g = make_graph("big")
    a, b = assemble(g), assemble(g)
    assert a.needs == b.needs and a.blocks == b.blocks
    assert len(a.needs) < 80  # bucketed, not one per region


def test_pilots_cover_types_per_app_and_become_w2_tasks():
    p = assemble(make_graph("big"))
    assert len(p.pilots["APP-001"]) == 5
    assert sum(1 for t in p.tasks if t["wave"] == "W2") == 5


def test_a_slice_over_the_cap_fails_the_build_loudly():
    g = make_graph("small")
    s = g.screens["SCR-001"]
    s.purpose = "word " * 9000
    with pytest.raises(PackBreach) as e:
        assemble(g)
    assert e.value.error["code"] in {"CARD_OVER_HARD_CAP", "SLICE_OVER_CAP"}
