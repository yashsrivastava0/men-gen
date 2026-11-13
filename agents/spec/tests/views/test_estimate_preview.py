"""The zero-cost estimate and the preview: integer micro-USD, monotone in screens, refused above the money left."""

from __future__ import annotations

from mde_spec_agent import views as V
from mde_spec_agent.spend import Prices
from mde_spec_agent.views._data import DATA_DIR, estimate_table
from mde_spec_agent.views.estimate import STEP_ORDER

PRICES = Prices("0.10", "0.50", 100_000, "5.0")


def counts(**kw):
    base = {
        "profile": "standard",
        "release_screens": 30,
        "modules": 5,
        "entities": 10,
        "integrations": 2,
        "flows": 7,
        "apps": 2,
        "extra_locales": 2,
        "source_strings": 150,
    }
    base.update(kw)
    return V.PlanCounts(**base)


def test_the_estimate_table_covers_every_call_kind_and_marks_itself_judgement():
    t = estimate_table()
    assert set(STEP_ORDER) == set(t["calls"])
    assert all(("in" in v) and ("out" in v or "out_per_item" in v) for v in t["calls"].values())
    text = (DATA_DIR / "estimate.yaml").read_text(encoding="utf-8")
    assert "[J]" in text


def test_estimate_is_integer_micro_usd_and_low_is_below_high(built, writer, config):
    e = V.estimate(built.derived, writer, config, V.prices_from_profile(writer))
    for k in (
        "low_micros",
        "high_micros",
        "minutes_low",
        "minutes_high",
        "tokens_in",
        "tokens_out",
    ):
        assert type(e[k]) is int, k
    assert 0 < e["low_micros"] <= e["high_micros"] and 0 < e["minutes_low"] <= e["minutes_high"]
    assert all(type(v) is int for v in e["by_step_high_micros"].values())
    assert e["total_calls"] == sum(e["calls_by_step"].values())


def test_estimate_is_monotone_in_screens(writer, config):
    prev = None
    for n in (5, 10, 20, 40, 80, 160):
        e = V.estimate(counts(release_screens=n), writer, config, PRICES)
        cur = (e["total_calls"], e["low_micros"], e["high_micros"], e["tokens_out"])
        if prev:
            assert all(c >= p for c, p in zip(cur, prev, strict=True))
        prev = cur
    small = V.estimate(counts(release_screens=5), writer, config, PRICES)
    big = V.estimate(counts(release_screens=160), writer, config, PRICES)
    assert big["high_micros"] > small["high_micros"] and big["total_calls"] > small["total_calls"]


def test_screens_per_call_follows_the_writer_limit_and_an_unset_limit_degrades(writer, config):
    capped = V.estimate(counts(release_screens=40), writer, config, PRICES)["calls_by_step"][
        "screens"
    ]
    assert capped == 10  # four cards per call
    unset = writer.model_copy(update={"max_output_tokens": None})
    worse = V.estimate(counts(release_screens=40), unset, config, PRICES)["calls_by_step"][
        "screens"
    ]
    assert worse == 40  # one card per call: conservative, never a guess
    small = writer.model_copy(update={"max_output_tokens": 8000})
    assert (
        V.estimate(counts(release_screens=40), small, config, PRICES)["calls_by_step"]["screens"]
        == 20
    )


def test_verify_calls_only_for_deep(writer, config):
    deep = V.estimate(counts(profile="deep"), writer, config, PRICES)["calls_by_step"]
    assert (
        deep["critics"] == 2
        and deep["canaries"] >= 1
        and deep["skeleton"] == 1
        and deep["design"] == 3
    )
    for p in ("compact", "standard"):
        steps = V.estimate(counts(profile=p), writer, config, PRICES)["calls_by_step"]
        assert "canaries" not in steps and "critics" not in steps
    compact = V.estimate(counts(profile="compact"), writer, config, PRICES)["calls_by_step"]
    assert not {"modules", "integrations", "journeys"} & set(compact)  # folded into the plan call


def test_locale_calls_follow_languages_and_strings(writer, config):
    none = V.estimate(counts(extra_locales=0), writer, config, PRICES)["calls_by_step"]
    assert "locales" not in none
    two = V.estimate(counts(extra_locales=2, release_screens=60), writer, config, PRICES)[
        "calls_by_step"
    ]["locales"]
    one = V.estimate(counts(extra_locales=1, release_screens=60), writer, config, PRICES)[
        "calls_by_step"
    ]["locales"]
    assert (
        two == 2 * one and one == 3
    )  # 60 screens x 12 strings = 720 strings = 3 calls of at most 300


def test_long_prompt_threshold_is_checked_and_priced(writer, config):
    ok = V.estimate(counts(), writer, config, PRICES)
    assert ok["long_prompt_ok"] and ok["max_call_input_tokens"] < 100_000
    tight = V.estimate(counts(), writer, config, Prices("0.10", "0.50", 10_000, "5.0"))
    assert not tight["long_prompt_ok"] and tight["high_micros"] > ok["high_micros"]


def test_an_unpriced_profile_estimates_calls_but_says_it_is_unpriced(writer, config):
    e = V.estimate(counts(), writer, config, None)
    assert (
        e["priced"] is False
        and e["low_micros"] == 0
        and e["high_micros"] == 0
        and e["total_calls"] > 0
    )


def test_a_graph_and_a_derived_give_the_same_estimate(quick, writer, config):
    a = V.estimate(quick.graph, writer, config, PRICES)
    b = V.estimate(quick.derived, writer, config, PRICES)
    assert a["calls_by_step"] == b["calls_by_step"]


def test_preview_is_refused_when_the_upper_bound_exceeds_the_money_left(thorough, writer, config):
    d = thorough.derived
    high = V.preview(d, d.decision, writer, config, None)["estimate"]["high_micros"]
    assert high > 0
    ok = V.preview(d, d.decision, writer, config, high)
    assert ok["refused"] is False and "refusal_message" not in ok
    refused = V.preview(d, d.decision, writer, config, high - 1)
    assert refused["refused"] is True
    msg = refused["refusal_message"]
    assert "could cost up to USD" in msg and "left of the money cap" in msg and "offline" in msg
    assert V.preview(d, d.decision, writer, config, 0)["refused"] is True
    assert V.preview(d, d.decision, writer, config, None)["refused"] is False


def test_preview_profile_says_why(thorough, quick, writer, config):
    t = V.preview(thorough.derived, thorough.derived.decision, writer, config, None)["profile"]
    assert t["id"] == "deep" and "raised" in t["why"] and "several apps" in t["why"]
    q = V.preview(quick.derived, quick.derived.decision, writer, config, None)["profile"]
    assert q["id"] == "compact" and "Nothing in the app raised it" in q["why"]
    assert not any(":" in w for w in t["why"].split("because")[1:2])  # reasons are words, not codes


def test_preview_phases_match_the_plan(thorough, writer, config):
    d = thorough.derived
    p = V.preview(d, d.decision, writer, config, None)
    assert [ph["screens"] for ph in p["phases"]] == [
        len(d.plan.release_screens),
        len(d.plan.deferred_screens),
    ]
    assert p["inventory_count"] == len(d.inventory.screens)
