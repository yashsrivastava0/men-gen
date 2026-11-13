from mde_spec_core.callkit import (
    FailedItem,
    ReaskLedger,
    build_batch_payload,
    build_reask_payload,
    plan_reasks,
)
from mde_spec_core.config import default_config

CFG = default_config()


def fi(node, kind="cards", n=0):
    return FailedItem(
        node, kind, {"id": f"{node}-{n}"}, f"bad {node}-{n}", {"type": "object"}, ("a", "b")
    )


def test_batched_per_kind_in_few_calls():
    items = [fi(f"n{i}", "cards") for i in range(5)] + [fi(f"m{i}", "strings") for i in range(3)]
    plan = plan_reasks(items, ReaskLedger(), CFG)
    assert [b.call_kind for b in plan.batches] == ["cards", "strings"]
    assert plan.call_count == 2 and not plan.flagged


def test_item_limit_makes_as_few_calls_as_possible():
    items = [fi(f"n{i}") for i in range(9)]
    plan = plan_reasks(items, ReaskLedger(), CFG, item_limits={"cards": 4})
    assert [len(b.items) for b in plan.batches] == [4, 4, 1]
    plan = plan_reasks(items, ReaskLedger(), CFG, default_item_limit=5)
    assert [len(b.items) for b in plan.batches] == [5, 4]


def test_items_of_one_node_stay_together():
    items = [fi("a", n=0), fi("a", n=1), fi("b"), fi("c")]
    plan = plan_reasks(items, ReaskLedger(), CFG, default_item_limit=3)
    assert [i.node_id for i in plan.batches[0].items] == ["a", "a", "b"]


def test_ledger_enforces_one_reask_per_node_across_waves():
    ledger = ReaskLedger()
    wave1 = plan_reasks([fi("a"), fi("b")], ledger, CFG)
    assert wave1.call_count == 1
    ledger.record(wave1)
    # gate 3 wants to ask again about a, plus a new node c
    wave2 = plan_reasks([fi("a"), fi("c")], ledger, CFG)
    assert [i.node_id for b in wave2.batches for i in b.items] == ["c"]
    assert [i.node_id for i in wave2.flagged] == ["a"]
    ledger.record(wave2)
    assert ledger.has_used("a", CFG) and ledger.reasked_nodes == {"a", "b", "c"}
    assert plan_reasks([fi("a"), fi("b"), fi("c")], ledger, CFG).call_count == 0


def test_planning_alone_does_not_spend_the_reask():
    ledger = ReaskLedger()
    plan_reasks([fi("a")], ledger, CFG)
    assert not ledger.has_used("a")


def test_one_node_with_two_items_costs_one_reask():
    ledger = ReaskLedger()
    ledger.record(plan_reasks([fi("a", n=0), fi("a", n=1)], ledger, CFG))
    assert ledger.count("a") == 1


def test_ledger_roundtrip():
    ledger = ReaskLedger()
    ledger.mark("x")
    assert ReaskLedger.from_dict(ledger.to_dict()).has_used("x")


def test_payload_is_word_for_word():
    msg = "V-24: value 'Bttn' is not allowed; \"quoted\" {braces} stay as written\n second line"
    text = build_reask_payload({"id": "A", "kind": "Bttn"}, msg, {"enum": ["a", "b"]}, ["a", "b"])
    assert msg in text
    assert '{"id":"A","kind":"Bttn"}' in text
    assert '{"enum":["a","b"]}' in text
    assert "- a\n- b" in text
    assert text.endswith("Return only these items. Do not change anything else.")
    assert "return only these items" in text.lower()


def test_payload_without_allowed_values_and_batch_payload():
    assert "ALLOWED VALUES" not in build_reask_payload("x", "m", "frag", None)
    plan = plan_reasks([fi("a"), fi("b")], ReaskLedger(), CFG)
    payload = build_batch_payload(plan.batches[0])
    assert "bad a-0" in payload and "bad b-0" in payload
