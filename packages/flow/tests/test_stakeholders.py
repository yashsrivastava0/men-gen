"""GAP-3, the pure part: who wins when two stakeholders answer the same slot differently.

``settle_writes`` compares the slots before and after one Discover result and decides, per slot:
a first answer or a change of mind is recorded; a different person's different answer is resolved
by role precedence or flagged (``disputed``) for the decision owner; a disputed slot is settled by
the owner and never by anyone else."""

from __future__ import annotations

import pytest

from mde_contracts.slots import SlotData, SlotRecord
from mde_flow import stakeholders
from mde_flow.stakeholders import Actor, Conflict, Stake, settle_writes

OWNER = "u-owner"
PRECEDENCE = ["compliance_officer", "product_owner", "requester"]


def rec(
    value: SlotData,
    *,
    state: str = "confirmed",
    source: str = "text",
    slot: str = "design.direction",
) -> SlotRecord:
    return SlotRecord.model_validate(
        {"slot_id": slot, "value": value, "state": state, "source": source}
    )


def actor(user: str, *roles: str) -> Actor:
    return Actor(user=user, roles=list(roles))


def stake(user: str, roles: list[str], value: SlotData) -> Stake:
    return Stake(user=user, roles=roles, value=value)


def settle(
    before: dict[str, SlotRecord],
    after: dict[str, SlotRecord],
    who: Actor,
    *,
    precedence: list[str] | None = None,
    actors: dict[str, Stake] | None = None,
    conflicts: dict[str, Conflict] | None = None,
    choices: dict[str, SlotData] | None = None,
) -> stakeholders.Settlement:
    return settle_writes(
        before=before,
        after=after,
        actor=who,
        owner_id=OWNER,
        precedence=PRECEDENCE if precedence is None else precedence,
        actors=actors or {},
        conflicts=conflicts or {},
        owner_choices=choices,
    )


LOOK = "design.direction"
OWNERS_STAKE = stake(OWNER, ["requester"], "bold-dark")


# ----------------------------------------------------------------------------- precedence


@pytest.mark.parametrize(
    ("new", "old", "expected"),
    [
        (["product_owner"], ["requester"], "new"),
        (["requester"], ["product_owner"], "old"),
        (["compliance_officer"], ["product_owner"], "new"),
        (["product_owner"], ["product_owner"], None),  # the same place: precedence cannot decide
        (["intern"], ["janitor"], None),  # neither is listed
        (["intern"], ["requester"], "old"),  # an unlisted role ranks below every listed one
        (["requester"], ["intern"], "new"),
        (["intern", "product_owner"], ["requester"], "new"),  # a person's best role counts
        ([], ["requester"], "old"),
        ([], [], None),
    ],
)
def test_winner(new: list[str], old: list[str], expected: str | None) -> None:
    assert stakeholders.winner(PRECEDENCE, new, old) == expected


def test_an_empty_precedence_never_decides() -> None:
    assert stakeholders.winner([], ["product_owner"], ["requester"]) is None
    assert stakeholders.rank([], ["requester"]) is None


# ----------------------------------------------------------------------------- recording answers


def test_a_first_answer_is_recorded_without_a_conflict() -> None:
    done = settle({}, {LOOK: rec("bold-dark")}, actor(OWNER, "requester"))
    assert done.findings == [] and done.conflicts == {}
    assert done.actors[LOOK] == OWNERS_STAKE
    assert done.slots[LOOK].value == "bold-dark" and not done.reverted


def test_the_same_person_changing_their_mind_is_not_a_disagreement() -> None:
    done = settle(
        {LOOK: rec("bold-dark")},
        {LOOK: rec("warm-friendly")},
        actor(OWNER, "requester"),
        actors={LOOK: OWNERS_STAKE},
    )
    assert done.findings == [] and done.conflicts == {}
    assert done.actors[LOOK].value == "warm-friendly"


@pytest.mark.parametrize(
    ("state", "source"),
    [
        ("assumed", "default"),
        ("volunteered", "upload"),
        ("confirmed", "research"),
        ("assumed", "text"),
    ],
)
def test_only_an_answer_a_person_gave_counts(state: str, source: str) -> None:
    done = settle(
        {LOOK: rec("bold-dark")},
        {LOOK: rec("warm-friendly", state=state, source=source)},
        actor("u-pat", "product_owner"),
        actors={LOOK: OWNERS_STAKE},
    )
    assert done.findings == [] and done.actors[LOOK] == OWNERS_STAKE
    assert done.slots[LOOK].value == "warm-friendly"  # not an answer: the engine's value stands


def test_a_slot_nobody_touched_is_left_alone() -> None:
    same = {LOOK: rec("bold-dark")}
    done = settle(same, dict(same), actor("u-pat", "product_owner"), actors={LOOK: OWNERS_STAKE})
    assert done.findings == [] and done.slots == same


def test_an_answer_over_a_default_is_not_a_disagreement() -> None:
    done = settle(
        {LOOK: rec("clean-bright", state="assumed", source="default")},
        {LOOK: rec("bold-dark")},
        actor("u-pat", "product_owner"),
        actors={LOOK: stake("u-someone", ["requester"], "clean-bright")},
    )
    assert done.findings == [] and done.actors[LOOK].user == "u-pat"


def test_confirming_what_someone_else_said_is_not_a_disagreement() -> None:
    done = settle(
        {LOOK: rec("bold-dark", state="volunteered")},
        {LOOK: rec("bold-dark", state="confirmed")},
        actor("u-pat", "product_owner"),
        actors={LOOK: OWNERS_STAKE},
    )
    assert done.findings == [] and done.conflicts == {}


# ----------------------------------------------------------------------------- the disagreement


def test_the_higher_role_wins_and_both_answers_stay_on_record() -> None:
    done = settle(
        {LOOK: rec("bold-dark")},
        {LOOK: rec("warm-friendly")},
        actor("u-pat", "product_owner"),
        actors={LOOK: OWNERS_STAKE},
    )
    pat = stake("u-pat", ["product_owner"], "warm-friendly")
    assert [f.kind for f in done.findings] == ["resolved_new"]
    assert done.findings[0].kept == pat and done.findings[0].other == OWNERS_STAKE
    assert done.slots[LOOK].value == "warm-friendly" and not done.reverted
    assert done.actors[LOOK] == pat
    assert done.conflicts[LOOK] == Conflict(
        slot_id=LOOK, stakes=[OWNERS_STAKE, pat], status="resolved", how="precedence"
    )


def test_the_lower_role_does_not_overwrite_and_its_answer_stays_on_record() -> None:
    before = {LOOK: rec("bold-dark")}
    done = settle(
        before,
        {LOOK: rec("warm-friendly")},
        actor("u-pat", "product_owner"),
        precedence=["requester", "product_owner"],
        actors={LOOK: OWNERS_STAKE},
    )
    pat = stake("u-pat", ["product_owner"], "warm-friendly")
    assert [f.kind for f in done.findings] == ["resolved_old"]
    assert done.findings[0].kept == OWNERS_STAKE and done.findings[0].other == pat
    assert done.slots[LOOK] == before[LOOK] and done.reverted == {LOOK}  # put back, untouched
    assert done.actors[LOOK] == OWNERS_STAKE
    assert done.conflicts[LOOK].stakes == [OWNERS_STAKE, pat]
    assert done.conflicts[LOOK].status == "resolved"


def test_when_precedence_cannot_decide_the_slot_is_disputed_and_the_earlier_value_stays() -> None:
    done = settle(
        {LOOK: rec("bold-dark")},
        {LOOK: rec("warm-friendly")},
        actor("u-pat", "product_owner"),
        precedence=[],
        actors={LOOK: OWNERS_STAKE},
    )
    assert [f.kind for f in done.findings] == ["disputed"]
    assert done.slots[LOOK].value == "bold-dark" and done.slots[LOOK].state == "disputed"
    assert done.reverted == {LOOK} and stakeholders.open_disputes(done.conflicts) == [LOOK]
    assert done.conflicts[LOOK].how == "decision_owner"
    assert done.actors[LOOK] == OWNERS_STAKE


def test_two_people_in_the_same_role_are_a_dispute() -> None:
    first = stake("u-pat", ["product_owner"], "bold-dark")
    done = settle(
        {LOOK: rec("bold-dark")},
        {LOOK: rec("warm-friendly")},
        actor("u-pam", "product_owner"),
        actors={LOOK: first},
    )
    assert [f.kind for f in done.findings] == ["disputed"]


def test_a_list_slot_disagreement_is_handled_like_any_other() -> None:
    feats = "scope.features"
    mine = stake(OWNER, ["requester"], ["browse", "search"])
    done = settle(
        {feats: rec(["browse", "search"], slot=feats)},
        {feats: rec(["browse", "chat"], slot=feats)},
        actor("u-pat", "product_owner"),
        actors={feats: mine},
    )
    assert [f.kind for f in done.findings] == ["resolved_new"]
    assert done.slots[feats].value == ["browse", "chat"]


# ----------------------------------------------------------------------------- settling a dispute


def disputed_world() -> tuple[dict[str, SlotRecord], dict[str, Stake], dict[str, Conflict]]:
    pat = stake("u-pat", ["product_owner"], "warm-friendly")
    slots = {LOOK: rec("bold-dark", state="disputed")}
    conflict = Conflict(
        slot_id=LOOK, stakes=[OWNERS_STAKE, pat], status="open", how="decision_owner"
    )
    return slots, {LOOK: OWNERS_STAKE}, {LOOK: conflict}


def test_the_decision_owner_settles_a_dispute_by_choosing() -> None:
    slots, actors, conflicts = disputed_world()
    done = settle(
        slots,
        {LOOK: rec("warm-friendly")},
        actor(OWNER, "requester"),
        precedence=[],
        actors=actors,
        conflicts=conflicts,
    )
    assert [f.kind for f in done.findings] == ["settled"]
    assert done.slots[LOOK].value == "warm-friendly" and done.slots[LOOK].state == "confirmed"
    assert stakeholders.open_disputes(done.conflicts) == []
    assert (
        done.conflicts[LOOK].status == "resolved" and done.conflicts[LOOK].how == "decision_owner"
    )
    assert done.actors[LOOK].user == OWNER


def test_the_owner_naming_the_value_the_slot_already_holds_settles_it_too() -> None:
    slots, actors, conflicts = disputed_world()
    done = settle(
        slots,
        dict(slots),  # the engine saw nothing to change
        actor(OWNER, "requester"),
        precedence=[],
        actors=actors,
        conflicts=conflicts,
        choices={LOOK: "bold-dark"},
    )
    assert [f.kind for f in done.findings] == ["settled"]
    assert done.slots[LOOK].state == "confirmed" and done.slots[LOOK].value == "bold-dark"
    assert stakeholders.open_disputes(done.conflicts) == []


def test_naming_a_different_value_without_the_engine_writing_it_settles_nothing() -> None:
    slots, actors, conflicts = disputed_world()
    done = settle(
        slots,
        dict(slots),
        actor(OWNER, "requester"),
        actors=actors,
        conflicts=conflicts,
        choices={LOOK: "something-else"},
    )
    assert done.findings == [] and done.slots[LOOK].state == "disputed"


def test_nobody_but_the_owner_can_settle_a_dispute() -> None:
    slots, actors, conflicts = disputed_world()
    done = settle(
        slots,
        {LOOK: rec("clean-bright")},
        actor("u-cleo", "compliance_officer"),  # outranks everyone, but is not the decision owner
        actors=actors,
        conflicts=conflicts,
    )
    assert [f.kind for f in done.findings] == ["still_disputed"]
    assert done.slots[LOOK].state == "disputed" and done.slots[LOOK].value == "bold-dark"
    assert done.reverted == {LOOK}
    assert stakeholders.open_disputes(done.conflicts) == [LOOK]
    assert stake("u-cleo", ["compliance_officer"], "clean-bright") in done.conflicts[LOOK].stakes


def test_the_record_of_positions_is_bounded() -> None:
    pat = stake("u-pat", ["product_owner"], "a")
    conflict = Conflict(
        slot_id=LOOK,
        stakes=[
            stake(f"u-{n}", ["product_owner"], f"v{n}") for n in range(stakeholders.MAX_STAKES)
        ],
        status="open",
        how="decision_owner",
    )
    done = settle(
        {LOOK: rec("bold-dark", state="disputed")},
        {LOOK: rec("a")},
        actor("u-pat", "product_owner"),
        actors={LOOK: OWNERS_STAKE},
        conflicts={LOOK: conflict},
    )
    assert len(done.conflicts[LOOK].stakes) == stakeholders.MAX_STAKES
    assert done.conflicts[LOOK].stakes[-1] == pat  # the oldest positions dropped first


# ----------------------------------------------------------------------------- storage


def test_the_bookkeeping_round_trips_and_survives_garbage() -> None:
    actors = {LOOK: OWNERS_STAKE}
    conflicts = {
        LOOK: Conflict(slot_id=LOOK, stakes=[OWNERS_STAKE], status="open", how="decision_owner")
    }
    assert stakeholders.load_actors(stakeholders.dump_actors(actors)) == actors
    assert stakeholders.load_conflicts(stakeholders.dump_conflicts(conflicts)) == conflicts
    assert stakeholders.dump_actors({}) is None and stakeholders.dump_conflicts({}) is None
    for bad in (None, "", "not json", '{"x": 1}', "[]"):
        assert stakeholders.load_actors(bad) == {}
        assert stakeholders.load_conflicts(bad) == {}
