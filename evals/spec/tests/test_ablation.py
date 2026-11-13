"""PS-10 ablation arms: sections are stripped by code at the card format."""

from __future__ import annotations

from evals.spec import ablation as ab
from evals.spec import packs
from evals.spec.synthetic import attach_could
from mde_spec_core.graph import Usually


def _pack_with_everything(standard):
    g = attach_could(standard.graph, per_screen=1, screens=36)
    sid = next(x for x in sorted(g.screens) if g.screens[x].could)
    g.screens[sid].usually = [
        Usually(note="comparable apps confirm the booking before payment", evidence="corroborated")
    ]
    g.screens[sid].mock_profiles = ["typical"]
    g.screens[sid].stories = sorted(g.stories)[:1]
    return packs.from_graph("full", g), sid


def _reads(p, sid, arm):
    r = ab.arm_reads(p, sid, arm)
    assert r is not None
    return r


def test_sections_are_stripped_at_the_card_format(standard) -> None:
    p, sid = _pack_with_everything(standard)
    text = p.files[f"screens/{sid}.md"]
    assert "\nusually:\n" in text and "\nmock:" in text and "\nstories:" in text
    must = ab.strip_screen(text, set())
    assert "usually:" not in must and "\nmock:" not in must and "\nstories:" not in must
    assert (
        "regions:" in must and "acceptance:" in must and "## closure" in must
    )  # MUST and the closure stay
    assert ab.strip_screen(text, {"usually"}).count("usually:") == 1
    assert "\nmock:" in ab.strip_screen(text, set(ab.BUNDLES))


def test_arms_are_cumulative_and_add_the_bundle_files(standard) -> None:
    p, sid = _pack_with_everything(standard)
    sizes = {a: sum(packs.count(t) for t in _reads(p, sid, a).values()) for a in ab.ARMS}
    assert list(sizes) == ["must", "+usually", "+stories", "+emotion", "+could", "+mock_assets"]
    assert (
        sizes["must"]
        < sizes["+usually"]
        <= sizes["+stories"]
        <= sizes["+emotion"]
        <= sizes["+could"]
        <= sizes["+mock_assets"]
    )
    assert f"screens/{sid}.could.md" in _reads(p, sid, "+could")
    assert f"screens/{sid}.could.md" not in _reads(p, sid, "+emotion")
    assert not any(k.endswith(".could.md") for k in _reads(p, sid, "must"))


def test_bundle_inventory_says_what_the_pack_has(quick) -> None:
    inv = ab.bundle_inventory(quick, packs.pick_samples(quick, "quick"))
    assert inv["could"]["content"] is False and inv["mock_assets"]["content"] is False
    assert (
        inv["stories"]["source"] == "graph"
    )  # the pack has ids only: the text comes from the graph, said so


async def test_run_reports_which_bundle_earns_its_place(runner, standard) -> None:
    p, sid = _pack_with_everything(standard)
    rep = await ab.run_ablation(runner, p, samples=[sid, *packs.pick_samples(p, "quick")[:1]])
    d = rep.as_dict()
    assert set(d["arms"]) == set(ab.ARMS) and set(d["bundles"]) == set(ab.BUNDLES)
    for b in d["bundles"].values():
        assert b["decision"] in ("earns_place", "no_lift", "no_content")
        assert b["recommendation"]
    # the mock reader only checks ids: no bundle can lower its questions, so every bundle with content is "no lift"
    assert all(v["decision"] == "no_lift" for v in d["bundles"].values() if v["content"])
    assert d["cut_or_optional"] and d["verdict"] == "not_measured" and "OFFLINE STUB" in d["note"]


def test_decide_reads_the_deltas() -> None:
    prev = {
        "blocking_per_screen": 1.0,
        "assumptions_per_screen": 1.0,
        "acceptance_satisfiable_share": 0.8,
    }
    better = {
        "blocking_per_screen": 0.5,
        "assumptions_per_screen": 1.0,
        "acceptance_satisfiable_share": 0.8,
    }
    same = dict(prev)
    assert ab.decide(prev, better, True)["decision"] == "earns_place"
    assert ab.decide(prev, same, True)["decision"] == "no_lift"
    assert ab.decide(prev, better, False)["decision"] == "no_content"
