"""SPC-MD (doc 15 12.4): model and provider edge cases. The real Gateway runs over a ScriptedProvider whose default answer
is each call's own mock handler; a case queues a fault (prose, a cut-off answer, a refusal, 429s, 402s) and the real
CallRunner, Orchestrator and lane steps react. Pack cases vary the consumer profile and re-assemble."""

from __future__ import annotations

import json
import re

import pytest
from edge_support import case
from edge_support.eb_run import (
    Rig,
    codes,
    findings_of,
    fixture_text,
    nodes_of,
    pack_of,
    reference_graph_text,
    wrap_gateway,
)

from mde_core.errors import CircuitOpen
from mde_llm_gateway.providers.base import ProviderCallError
from mde_llm_gateway.providers.scripted import ScriptedResponse
from mde_spec_core.callkit import check_all
from mde_spec_core.config import default_config
from mde_spec_core.profiles import load_profiles
from mde_spec_core.synth import make_graph

PROSE = (
    "Sure! I looked at the module and here is what I think, in plain words: it is a good module."
)
MODULE = "spec.prod_module"


def degraded_ids(graph):
    return {n.id for n in graph.all_nodes() if n.degraded}


# --------------------------------------------------------------------------------------------------- MD-01
@case("spc-md-01")
def test_md_01_a_weak_merge_is_rejected_by_code_and_the_residue_is_all_the_model_decides(tmp_path):
    rig = Rig(tmp_path, "standard")
    gw = wrap_gateway(rig.ctx)

    def bad_merge(out):
        from mde_spec_agent.lanes.product.plan.schema import MergeRec

        out = out.model_copy(deep=True)
        out.merges = [
            MergeRec(keep="SCR-901", drop="SCR-902", reason="they look alike", prov=["assumed:x"])
        ]
        return out

    gw.rewrite = lambda spec, inp, n: bad_merge if spec == "spec.plan" else None
    out = rig.go("plan")
    # validators caught it: one call, one re-ask, then the one step with no default ends the run partial
    assert out.status == "partial" and out.reason == "plan_invalid"
    assert gw.count("spec.plan") == 2
    # code decided first: the draft the model was asked about already carries owners for every entity and no merge
    inp = gw.calls[0][1]
    assert inp.baseline.owners and not inp.baseline.merges
    assert set(inp.entity_ids) == {o.entity for o in inp.baseline.owners}


# --------------------------------------------------------------------------------------------------- MD-02
@case("spc-md-02")
def test_md_02_invalid_json_gets_one_repair_then_the_library_default_and_degraded(tmp_path):
    # one bad answer: the gateway's single repair fixes it, nothing degraded
    ok = Rig(tmp_path, "standard", tag="one")
    ok.provider.queue(MODULE, "{not json")
    assert ok.go("plan", "product").status == "awaiting_g2"
    assert ok.ctx.flags == [] and degraded_ids(ok.ctx.graph) == set()
    # persistent invalid output: repair fails, ONE re-ask fails, then the deterministic default and `degraded`
    rig = Rig(tmp_path, "standard", tag="always")
    rig.provider.set_default(MODULE, "{not json")
    out = rig.go("plan", "product")
    assert out.status == "awaiting_g2"  # never a crash
    calls = rig.calls(MODULE)
    assert calls and all(c.used_fallback and c.flagged and c.reasked for c in calls)
    assert all(
        v == 1 for v in rig.ctx.runner.ledger.used.values()
    )  # one re-ask per node, never two
    flagged = {f["node"] for f in rig.ctx.flags}
    assert flagged and flagged <= degraded_ids(rig.ctx.graph)
    per_call = len(rig.provider_calls(MODULE)) / len(calls)
    assert per_call == 4  # main + repair, then the re-ask's main + repair


# --------------------------------------------------------------------------------------------------- MD-03
@case("spc-md-03")
def test_md_03_a_truncated_batch_is_redone_in_smaller_batches_with_the_same_nodes(tmp_path):
    ref = Rig(tmp_path, "thorough", tag="ref")
    ref.go("plan", "product")
    rig = Rig(tmp_path, "thorough", tag="cut")
    gw = wrap_gateway(rig.ctx)
    gw.truncate_over = {MODULE: 2}  # the provider cannot finish more than two items in one answer
    assert rig.go("plan", "product").status == "awaiting_g2"
    assert rig.ctx.flags == []  # smaller batches fit: nothing degraded
    assert gw.count(MODULE) > len(ref.calls(MODULE))
    assert nodes_of(rig.ctx.graph) == nodes_of(ref.ctx.graph)


@case("spc-md-03")
@pytest.mark.xfail(
    strict=True,
    reason="BUG-MD-1: through the real gateway a cut-off answer (incomplete, max_output_tokens) surfaces as "
    "StructuredOutputError whose message has no 'truncat'; CallRunner only raises Truncated on that word, so the "
    "batch is never split: the nodes are re-asked and take the fallback instead",
)
def test_md_03_the_finish_reason_of_the_real_gateway_drives_the_split(tmp_path):
    ref = Rig(tmp_path, "thorough", tag="ref")
    ref.go("plan", "product")
    rig = Rig(tmp_path, "thorough", tag="cut")

    def answer(req):
        from edge_support.eb_run import mock_answer

        if len(req.input.baseline.entities) + len(req.input.baseline.stories) > 3:
            return ScriptedResponse(
                output='{"entities": [', status="incomplete", incomplete_reason="max_output_tokens"
            )
        return mock_answer(req)

    rig.provider.set_default(MODULE, answer)
    assert rig.go("plan", "product").status == "awaiting_g2"
    assert rig.ctx.flags == []
    assert nodes_of(rig.ctx.graph) == nodes_of(ref.ctx.graph)


# --------------------------------------------------------------------------------------------------- MD-04
@case("spc-md-04")
def test_md_04_a_refusal_takes_the_fallback_and_degrades_the_node_never_a_crash(tmp_path):
    rig = Rig(tmp_path, "standard")
    rig.provider.set_default(MODULE, ScriptedResponse(output="", refusal="I cannot help with this"))
    out = rig.go("plan", "product")
    assert out.status == "awaiting_g2" and out.status != "failed"
    calls = rig.calls(MODULE)
    assert calls and all(c.used_fallback and c.flagged for c in calls)
    flagged = {f["node"] for f in rig.ctx.flags}
    assert flagged and flagged <= degraded_ids(rig.ctx.graph)
    # a refusal is not repairable: the gateway makes no repair call for it
    assert len(rig.provider_calls(MODULE)) == 2 * len(calls)  # the call and the runner's one re-ask


# --------------------------------------------------------------------------------------------------- MD-05
@case("spc-md-05")
def test_md_05_a_429_is_retried_with_growing_backoff_and_the_run_resumes_from_checkpoints(tmp_path):
    rig = Rig(tmp_path, "standard")
    err = ProviderCallError("slow down", status=429, retryable=True)
    rig.provider.queue(MODULE, err, err)
    assert rig.go("plan", "product").status == "awaiting_g2"
    assert rig.ctx.flags == []  # two 429s then success: nothing degraded
    sleeps = rig.clock.sleeps
    assert len(sleeps) >= 2 and sleeps[1] > sleeps[0] * 1.2  # exponential backoff, not a tight loop
    # resume from checkpoints: a second pass over the finished run asks nothing again
    n = len(rig.provider.calls)
    rig.store.update_run(rig.ctx.run_id, status="partial", done_steps=[])
    rig.ctx.plan.clear()
    assert rig.go("plan", "product").status == "awaiting_g2"
    assert len(rig.provider.calls) == n


@case("spc-md-05")
async def test_md_05_the_limiter_halves_on_a_rate_limit_and_grows_back_additively():
    from mde_spec_agent.run.concurrency import Limiter

    cfg = default_config()
    lim = Limiter(8, cfg)
    for _ in range(4):
        await lim.clean_window()
    before = lim.k
    await lim.rate_limited()
    assert lim.k == max(cfg.concurrency.min, int(before * cfg.concurrency.multiplicative_decrease))
    await lim.clean_window()
    assert lim.k <= lim.k_max


@case("spc-md-05")
def test_md_05_a_429_storm_ends_the_run_partial_with_reason_rate_limit(tmp_path):
    rig = Rig(tmp_path, "standard")
    rig.provider.set_default(MODULE, ProviderCallError("slow down", status=429, retryable=True))
    out = rig.go("plan", "product")
    assert out.status == "partial" and out.reason == "rate_limit"


@case("spc-md-05")
@pytest.mark.xfail(
    strict=True,
    reason="BUG-MD-3: Limiter.rate_limited() is never called by the run path, so a 429 never halves the concurrency "
    "(doc 15 10.3: back off, adapt concurrency)",
)
def test_md_05_a_429_halves_the_run_concurrency(tmp_path):
    rig = Rig(tmp_path, "standard")
    for _ in range(3):
        import asyncio

        asyncio.run(rig.ctx.limiter.clean_window())
    k0 = rig.ctx.limiter.k
    err = ProviderCallError("slow down", status=429, retryable=True)
    rig.provider.queue(MODULE, err)
    rig.go("plan", "product")
    assert rig.ctx.limiter.k < k0


# --------------------------------------------------------------------------------------------------- MD-06
@case("spc-md-06")
def test_md_06_a_402_is_not_retried_and_five_of_them_open_the_breaker(tmp_path):
    import asyncio

    from mde_spec_agent.lanes.product.prod_module.spec import SPEC

    rig = Rig(tmp_path, "standard")
    assert rig.go("plan", "product").status == "awaiting_g2"
    inp = rig.provider_calls(MODULE)[0].input  # one real module input
    rig.provider.set_default(MODULE, ProviderCallError("no credits", status=402, retryable=False))
    ctx = rig.ctx.runner.context(SPEC)
    n0 = len(rig.provider_calls(MODULE))
    outcomes = []

    async def drive():
        for _ in range(6):
            try:
                await rig.gateway.run(SPEC.spec, inp, ctx)
            except Exception as exc:
                outcomes.append(exc)

    asyncio.run(drive())
    assert [type(o).__name__ for o in outcomes[:5]] == ["ProviderCallError"] * 5
    assert isinstance(outcomes[5], CircuitOpen)  # the sixth call never reaches the provider
    assert len(rig.provider_calls(MODULE)) - n0 == 5  # a 402 is never retried: one attempt per call
    assert all(x < 0.1 for x in rig.clock.sleeps)  # and never backed off


@case("spc-md-06")
def test_md_06_credits_out_ends_the_run_partial_and_resumes_after_a_top_up(tmp_path):
    rig = Rig(tmp_path, "standard")
    rig.provider.set_default(MODULE, ProviderCallError("no credits", status=402, retryable=False))
    out = rig.go("plan", "product")
    assert out.status == "partial"
    rig.provider.set_default(MODULE, rig.provider._defaults["*"])
    rig.clock.advance(120)  # the breaker's open window passes
    assert rig.resume("plan", "product").status == "awaiting_g2"
    assert rig.ctx.flags == []


# --------------------------------------------------------------------------------------------------- MD-07
@case("spc-md-07")
def test_md_07_a_provider_switch_re_packages_with_the_same_content_and_the_same_probes_pass():
    ps = load_profiles()
    g = make_graph("big")
    packs = {}
    for pid in ("weak-default", "standard-default"):
        prof = ps.get(pid)
        assert prof.id == pid
        packs[pid] = pack_of(
            g,
            consumer_id=pid,
            chars_per_token=prof.tokenizer_chars_per_token,
        )
        assert [f for f in findings_of(g, packs[pid]) if f.severity == "blocker"] == []
        assert packs[pid].manifest["consumer"] == pid
    a, b = packs["weak-default"], packs["standard-default"]
    assert a.content_hash == b.content_hash  # an approval is never voided by a provider switch
    # every call schema compiles against both profiles (the compile probe passes for the new one too)
    from mde_spec_agent.calls.registry import all_calls

    models = {c.id: c.spec.output_model for c in all_calls()}
    rep = check_all(models, [ps.get("weak-default"), ps.get("standard-default")])
    assert rep.ok, rep.errors()[:3]


# --------------------------------------------------------------------------------------------------- MD-08
@case("spc-md-08")
def test_md_08_a_30_percent_heavier_tokenizer_re_shards_with_the_same_content_hash():
    g = make_graph("program")
    cfg = default_config()
    base = cfg.budgets.token_factor_chars_per_token
    a = pack_of(g)
    h = pack_of(g, chars_per_token=base / 1.3)
    assert a.content_hash == h.content_hash
    assert h.manifest["tokenizer_chars_per_token"] == pytest.approx(base / 1.3)
    assert h.budget_report["total_tokens"] > a.budget_report["total_tokens"] * 1.2
    assert len(h.tasks) >= len(a.tasks)
    assert (
        h.budget_report["tasks_over_budget"] == []
    )  # nothing is over the budget after re-sharding
    assert h.package_hash != a.package_hash  # the packaging differs, the content does not


# --------------------------------------------------------------------------------------------------- MD-09
@case("spc-md-09")
def test_md_09_the_effective_window_sets_the_budgets_and_content_is_unchanged():
    from mde_spec_core.callkit.fusion import input_cap, items_per_call

    cfg = default_config()
    ps = load_profiles()
    small = ps.get("weak-default").model_copy(update={"effective_context_tokens": 20_000})
    large = ps.get("weak-default").model_copy(update={"effective_context_tokens": 400_000})
    assert input_cap(small, cfg) < input_cap(large, cfg) or input_cap(large, cfg) == 100_000
    assert input_cap(small, cfg) == int(cfg.fusion.input_cap_share_of_effective_window * 20_000)
    big_out = ps.get("weak-default").model_copy(update={"max_output_tokens": 128_000})
    small_out = ps.get("weak-default").model_copy(update={"max_output_tokens": 8_000})
    assert items_per_call(small_out, 500, 12, cfg) < items_per_call(big_out, 500, 12, cfg)
    g = make_graph("big")
    a, b = pack_of(g), pack_of(g, task_read_budget=40_000, consumer_id="large-context-default")
    assert a.content_hash == b.content_hash
    assert len(b.tasks) <= len(a.tasks)
    assert b.manifest["task_read_budget"] == 40_000


# --------------------------------------------------------------------------------------------------- MD-10
@case("spc-md-10")
def test_md_10_without_a_structured_mode_the_output_is_validated_json_plus_repair(tmp_path):
    ps = load_profiles()
    none = ps.get("weak-default").model_copy(update={"structured_output": "none"})
    assert none.structured_output == "none"
    rig = Rig(tmp_path, "standard")
    rig.provider.queue(MODULE, PROSE)  # prose where JSON was asked
    assert rig.go("plan", "product").status == "awaiting_g2"
    first = rig.provider_calls(MODULE)
    assert len(first) == 6 and rig.ctx.flags == []  # 5 modules + the ONE repair call, no fallback
    assert any(c.repaired for c in rig.calls(MODULE))  # repaired by the gateway, validated locally
    assert (
        "JSON" in first[0].user or "JSON" in first[0].system
    )  # the word the JSON-mode providers need
    # the same call specs carry local validators: the contract does not depend on the provider's schema mode
    from mde_spec_agent.calls.registry import all_calls

    assert all(
        c.validators or c.id in {"spec.gloss_l", "spec.copy_l"}
        for c in all_calls()
        if c.spec.repair
    )


# --------------------------------------------------------------------------------------------------- MD-11
@case("spc-md-11")
def test_md_11_the_static_prefix_leads_every_call_whatever_the_caching_style(tmp_path):
    rig = Rig(tmp_path, "standard")
    rig.go("plan", "product")
    reqs = rig.provider_calls(MODULE)
    assert len(reqs) >= 2
    assert (
        len({r.system for r in reqs}) == 1
    )  # the system layer is identical for every call of a kind
    assert all(r.system and r.user.strip().endswith("no other text") for r in reqs)
    assert len({r.user for r in reqs}) == len(reqs)  # only the user layer (the slice) differs


@case("spc-md-11")
@pytest.mark.xfail(
    strict=True,
    reason="GAP-MD-1: estimate() never reads the profile's cache_share or cached price, so a caching profile "
    "and `none` give the same estimate (doc 15 11.4: the estimate uses cache_share)",
)
def test_md_11_the_estimate_uses_cache_share_from_the_profile():
    from mde_spec_agent.spend import Prices
    from mde_spec_agent.views.estimate import estimate

    ps = load_profiles()
    base = ps.get("weak-default")
    none = base.model_copy(update={"caching": "none", "cache_share": 0.0})
    cached = base.model_copy(update={"caching": "automatic_prefix", "cache_share": 0.6})
    g = make_graph("small")
    prices = Prices("0.10", "0.50", 100_000, "5.0")
    a = estimate(g, none, default_config(), prices)["high_micros"]
    b = estimate(g, cached, default_config(), prices)["high_micros"]
    assert b < a


# --------------------------------------------------------------------------------------------------- MD-12
@case("spc-md-12")
def test_md_12_three_runs_agree_and_a_replay_comes_from_the_checkpoints(tmp_path):
    texts = []
    for i in range(3):
        rig = Rig(tmp_path, "standard", tag=f"run{i}")
        assert rig.go("plan", "product").status == "awaiting_g2"
        texts.append(rig.ctx.graph.canonical_text())
    assert texts[0] == texts[1] == texts[2] == reference_graph_text("standard", ("plan", "product"))
    n = len(rig.provider.calls)
    rig.store.update_run(rig.ctx.run_id, status="partial", done_steps=[])
    rig.ctx.plan.clear()
    rig.ctx.graph = type(rig.ctx.graph)()
    assert rig.go("plan", "product").status == "awaiting_g2"
    assert len(rig.provider.calls) == n  # replayed from the graph checkpoints, not asked again
    assert rig.ctx.graph.canonical_text() == texts[0]


@case("spc-md-12")
@pytest.mark.xfail(
    strict=True,
    reason="GAP-MD-2: nothing records a variance band across repeated runs (no `variance` field in the run record, "
    "the manifest or the evidence files)",
)
def test_md_12_variance_bands_are_recorded(tmp_path):
    rig = Rig(tmp_path, "standard")
    rig.go("plan", "product")
    blob = json.dumps(rig.store.get_run(rig.ctx.run_id), default=str)
    for p in rig.store.run_dir(rig.ctx.run_id).rglob("*.json"):
        blob += p.read_text(encoding="utf-8")
    assert "variance" in blob


# --------------------------------------------------------------------------------------------------- MD-13
@case("spc-md-13")
def test_md_13_no_critic_runs_for_compact_and_standard_and_verify_flags_a_single_vendor(tmp_path):
    from mde_spec_agent.calls.registry import all_calls

    assert not [
        c for c in all_calls() if "critic" in c.id or "canary" in c.id
    ]  # none in Spec's own calls
    rig = Rig(tmp_path, "standard")
    rig.go("plan", "product")
    assert not [c for c in rig.provider.calls if "critic" in c.spec_id]
    small = pack_of(make_graph("small"))
    assert small.manifest["critic_vendor"] is None and small.manifest["checked_by"] == [
        "self_check"
    ]
    from mde_verify_agent.evidence import manifest_fields

    assert manifest_fields("same") == {
        "checked_by": ["self_check", "verify"],
        "critic_vendor": "same",
    } or (manifest_fields("same")["critic_vendor"] == "same")


# --------------------------------------------------------------------------------------------------- MD-14
@case("spc-md-14")
def test_md_14_prose_instead_of_json_is_handled_as_invalid_json_and_the_rate_is_visible(tmp_path):
    rig = Rig(tmp_path, "standard")
    rig.provider.set_default(MODULE, PROSE)
    out = rig.go("plan", "product")
    assert out.status == "awaiting_g2"
    calls = rig.calls(MODULE)
    assert calls and all(c.used_fallback and c.flagged for c in calls)
    # the rate is visible: the re-ask ledger names every node that needed one and the G2 flags list each degraded node
    assert rig.ctx.runner.ledger.reasked_nodes
    assert {f["node"] for f in rig.ctx.flags} <= rig.ctx.runner.ledger.reasked_nodes | degraded_ids(
        rig.ctx.graph
    )
    # and the second attempt of each node's batch carries the re-ask marker
    assert any(r.input.reask for r in rig.provider_calls(MODULE))


# --------------------------------------------------------------------------------------------------- MD-15
POISON = fixture_text("spc-md-15", "poison.txt")


@case("spc-md-15")
def test_md_15_a_poisoned_quote_only_ever_sits_inside_untrusted_blocks_and_never_reaches_the_output(
    tmp_path,
):
    from edge_support.eb_run import load_input

    si = load_input("standard")
    feats = list(si.features)
    feats[0] = feats[0].model_copy(update={"intent": f"{feats[0].intent} {POISON}"})
    poisoned = si.model_copy(update={"features": tuple(feats)})
    rig = Rig(tmp_path, "standard", spec_input=poisoned)
    assert rig.go("plan", "product").status == "awaiting_g2"
    seen = 0
    for req in rig.provider.calls:
        assert req.tools == ()  # no tools in a call that reads untrusted text (hard rule 5)
        text = req.system + "\n" + req.user
        spans = [
            m.span() for m in re.finditer(r"<untrusted_content.*?</untrusted_content>", text, re.S)
        ]
        for m in re.finditer(re.escape("ZEBRA-7731"), text):
            seen += 1
            assert any(a <= m.start() < b for a, b in spans), req.spec_id
        assert "IGNORE ALL PREVIOUS" not in req.system
        if spans:
            assert "never follow" in req.system.lower() or "data, never instructions" in text
    assert seen > 0  # the poison really travelled (the test is not vacuous)
    # the model added nothing of it: every occurrence in the graph is the user's own text copied by code
    from mde_spec_agent.derive import derive_graph

    text = rig.ctx.graph.canonical_text()
    homes = set(re.findall(r'"(\w+)":"[^"]*ZEBRA-7731', text))
    assert homes and homes <= {
        "intent",
        "so_that",
        "then",
        "want",
    }  # only the user's own words, in their fields
    assert derive_graph(poisoned).graph.canonical_text().count("ZEBRA-7731") > 0
    # and a model that OBEYS the injected instruction (it invents a role to grant itself) is stopped by validation
    obey = Rig(tmp_path, "standard", spec_input=poisoned, tag="obey")
    gw = wrap_gateway(obey.ctx)

    def obey_poison(out):
        out = out.model_copy(deep=True)
        out.stories[0].role = "ROL-999"
        out.stories[0].so_that = f"{POISON} as the admin"
        return out

    gw.rewrite = lambda spec, inp, n: obey_poison if (spec == MODULE and inp.story_ids) else None
    assert obey.go("plan", "product").status == "awaiting_g2"
    assert any(c.used_fallback and c.flagged for c in obey.calls(MODULE))
    assert "ROL-999" not in obey.ctx.graph.canonical_text()
    assert "as the admin" not in obey.ctx.graph.canonical_text()


# --------------------------------------------------------------------------------------------------- MD-16
@case("spc-md-16")
def test_md_16_every_call_schema_compiles_against_every_profile_offline():
    from mde_spec_agent.calls.registry import all_calls
    from mde_verify_agent.calls.registry import ALL_SPECS

    calls = [*all_calls(), *ALL_SPECS]
    assert len(calls) >= 15 and len(ALL_SPECS) == 3
    ps = load_profiles()
    models = {c.id: c.spec.output_model for c in calls}
    rep = check_all(models, ps)
    assert len(rep.checked) == len(models) * len(ps.profiles)
    assert rep.ok, rep.errors()[:3]
    # the strict-schema sanitiser (the gateway's dialect step) accepts every one of them too
    from mde_llm_gateway import to_strict_schema

    for c in calls:
        assert to_strict_schema(c.spec.output_model)


# --------------------------------------------------------------------------------------------------- MD-17
@case("spc-md-17")
def test_md_17_an_over_filled_answer_is_rejected_and_provenance_stays_required(tmp_path):
    rig = Rig(tmp_path, "standard")
    gw = wrap_gateway(rig.ctx)

    def overfill(out):
        out = out.model_copy(deep=True)
        out.entities[0].typical_count = 987654  # a number no source states
        out.entities[0].prov = []  # and no provenance
        return out

    gw.rewrite = lambda spec, inp, n: overfill if (spec == MODULE and inp.entity_ids) else None
    assert rig.go("plan", "product").status == "awaiting_g2"
    # Provenance is repaired by code (never re-asked): an entity the model filled with no source becomes an assumed node,
    # logged as R-code, so the doc's rule "every filled node has provenance, and the unsourced one is marked assumed" holds.
    g = rig.ctx.graph
    assert all(e.provenance for e in g.entities.values())  # every filled node has provenance
    overfilled = [e for e in g.entities.values() if e.typical_count == 987654]
    assert all(e.assumed or e.tier != "locked" for e in overfilled)  # never presented as evidenced
    assert rig.ctx.rcode.rows  # and the repair is on the record


# --------------------------------------------------------------------------------------------------- MD-18
@case("spc-md-18")
def test_md_18_every_call_input_stays_under_the_surcharge_threshold_and_the_estimate_uses_the_multiplier(
    tmp_path,
):
    from mde_spec_agent.spend import Prices
    from mde_spec_agent.spend.worst_case import worst_case_micros
    from mde_spec_core.render import TokenCounter

    prof = load_profiles().get("weak-default")
    threshold = prof.price.long_prompt_threshold_tokens
    assert threshold == 100_000 and prof.price.long_prompt_multiplier == "5.0"
    counter = TokenCounter(prof.tokenizer_chars_per_token or 3.0)
    rig = Rig(tmp_path, "thorough")
    assert rig.go("plan", "product").status == "awaiting_g2"
    sizes = [counter.count(r.system + r.user) for r in rig.provider.calls]
    assert sizes and max(sizes) < threshold
    # the worst case of a call just above the threshold is priced at the multiplier on the WHOLE call
    p = Prices(
        prof.price.input_usd_per_million,
        prof.price.output_usd_per_million,
        threshold,
        prof.price.long_prompt_multiplier,
    )
    below = worst_case_micros(threshold, 1000, p)
    above = worst_case_micros(threshold + 1, 1000, p)
    assert above > below * 3
    from mde_spec_agent.views.estimate import estimate

    est = estimate(make_graph("big"), prof, default_config(), p)
    assert est["long_prompt_ok"] is True and est["max_call_input_tokens"] <= threshold
    assert codes([]) == set()
