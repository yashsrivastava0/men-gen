"""PS-4: the freedom probe and the schema and lint statistics of cards."""

from __future__ import annotations

import json

from evals.spec import freedom_probe as fp
from evals.spec.calls.freedom.mock import fallback, handle
from evals.spec.calls.freedom.schema import FreedomIn
from mde_spec_core.config import default_config


def test_style_seeds_differ_and_are_plain_words() -> None:
    a, b = fp.style_seeds(2)
    assert a != b and "mood" in a
    assert fp.style_seeds(2) == [a, b]  # deterministic


def test_arrangement_tokens_are_a_closed_list_without_layout_words() -> None:
    from mde_spec_core.libraries import form_word_findings

    toks = fp.arrangement_tokens()
    assert len(toks) == len(set(toks)) >= 5
    assert not [t for t in toks if form_word_findings(t.replace("-", " "))]


async def test_probe_on_quick_and_standard(runner, quick, standard, tmp_path) -> None:
    for pack, size in ((quick, "quick"), (standard, "standard")):
        rep = await fp.run_freedom_probe(runner, pack, size)
        assert len(rep.pairs) == (3 if size == "quick" else 6)
        shares = rep.shares()
        assert 0.0 <= shares["distinct_layout_share"] <= 1.0
        t = default_config().proof.freedom_probe_distinct_layout_min
        by = {c.name: c for c in rep.checks()}
        assert by["screens with a different arrangement"].threshold == t
        assert rep.stats["measured"] and rep.stats["schema_validity"] == 1.0
        assert rep.stats["tier_ceiling_violations"] == 0
        assert rep.verdict() == "not_measured"  # the mock picks from a hash of the style seed
        assert "OFFLINE STUB" in rep.as_dict()["note"]
    fp.write_report(rep, standard, tmp_path)
    cards = json.loads((tmp_path / "owner-reading" / "cards.json").read_text())["cards"]
    assert len(cards) == 5 and len({c["screen"] for c in cards}) == 5
    assert all(
        "## closure" not in c["card"] and c["owner_finding"] == "" for c in cards
    )  # the card only


def test_distinct_shares_are_pure_arithmetic() -> None:
    from evals.spec.calls.freedom.schema import FreedomOut

    same = FreedomOut(arrangement="primary-first", region_order=["a", "b"], reason="")
    other = FreedomOut(arrangement="status-led", region_order=["a", "b"], reason="")
    reorder = FreedomOut(arrangement="primary-first", region_order=["b", "a"], reason="")
    pairs = [
        fp.ScreenPair("s1", same, same),
        fp.ScreenPair("s2", same, other),
        fp.ScreenPair("s3", same, reorder),
        fp.ScreenPair("s4", same, same),
    ]
    s = fp.distinct_shares(pairs)
    assert s == {
        "distinct_layout_share": 0.5,
        "distinct_token_share": 0.25,
        "distinct_order_share": 0.25,
    }


def test_the_fallback_never_differs_between_seeds_and_the_stub_does() -> None:
    toks = fp.arrangement_tokens()

    def mk(seed: str) -> FreedomIn:
        return FreedomIn(
            screen_id="SCR-1",
            style_seed=seed,
            arrangements=toks,
            card="x",
            region_keys=["a", "b", "c"],
        )

    assert fallback(mk("one")) == fallback(mk("two"))
    assert handle(mk("one")) == handle(mk("one"))


def test_lint_stats_explain_every_warning(standard) -> None:
    from evals.spec import packs
    from mde_spec_core.graph import SpecGraph  # noqa: F401

    g = standard.graph.model_copy(deep=True)
    sid = sorted(g.screens)[1]
    g.screens[sid].purpose = "A sticky button at the top of the screen in 16px"
    p = packs.from_graph("lint", g)
    st = fp.card_stats(p)
    assert st["lint_warnings_total"] >= 1
    w = st["lint_warnings"][0]
    assert w["where"].startswith(sid) and w["why"] and w["explanation"].startswith("V-25")
    assert st["lint_cap_per_card"] == default_config().checks.freedom_lint_warnings_per_card_max


def test_card_stats_without_a_graph_say_so(standard) -> None:
    from evals.spec import packs

    bare = packs.ProofPack("bare", standard.files, standard.tasks, standard.manifest)
    assert fp.card_stats(bare)["measured"] is False
