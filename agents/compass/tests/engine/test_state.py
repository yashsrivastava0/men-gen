from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from mde_compass.domain.session import SESSION_STATES
from mde_compass.engine.state import (
    ORIGIN,
    SIDE_STATES,
    TRANSITIONS,
    TRIGGERS,
    can,
    next_state,
    reachable_states,
)
from mde_core.errors import InvalidTransition

HAPPY = [
    ("WELCOME", "start", "INTAKE"),
    ("INTAKE", "idea_received", "UNDERSTANDING"),
    ("UNDERSTANDING", "understood", "INTERVIEWING"),
    ("INTERVIEWING", "playback_shown", "PLAYBACK"),
    ("PLAYBACK", "playback_confirmed", "INTERVIEWING"),
    ("INTERVIEWING", "queue_empty", "SEEDING"),
    ("SEEDING", "research_seeded", "PROBING"),
    ("PROBING", "probes_done", "BRIEF_REVIEW"),
    ("BRIEF_REVIEW", "gate_approved", "APPROVED"),
]


def test_table_covers_every_state_and_happy_path() -> None:
    assert set(TRANSITIONS) == set(SESSION_STATES)
    for state, trigger, target in HAPPY:
        assert next_state(state, trigger) == target  # type: ignore[arg-type]


def test_every_state_is_reachable() -> None:
    assert reachable_states("WELCOME") == set(SESSION_STATES)


def test_invalid_transitions_raise() -> None:
    with pytest.raises(InvalidTransition):
        next_state("WELCOME", "gate_approved")
    with pytest.raises(InvalidTransition):
        next_state("PROBING", "gate_approved")
    with pytest.raises(InvalidTransition):
        next_state("PAUSED", "resume")  # needs the origin
    assert not can("INTAKE", "no_such_trigger")


def test_approved_only_via_gate_approved() -> None:
    entering = {
        (s, t)
        for s, edges in TRANSITIONS.items()
        for t, target in edges.items()
        if target == "APPROVED"
    }
    assert entering == {("BRIEF_REVIEW", "gate_approved")}


def test_changing_and_paused_return_to_origin() -> None:
    for origin in SESSION_STATES:
        if can(origin, "change_requested"):
            assert next_state(origin, "change_requested") == "CHANGING"  # type: ignore[arg-type]
            back = next_state("CHANGING", "change_cancelled", origin=origin)  # type: ignore[arg-type]
            assert back == origin
            applied = next_state("CHANGING", "change_applied", origin=origin)  # type: ignore[arg-type]
            assert applied == ("BRIEF_REVIEW" if origin == "APPROVED" else origin)
        if can(origin, "pause"):
            assert next_state(origin, "pause") == "PAUSED"  # type: ignore[arg-type]
            assert next_state("PAUSED", "resume", origin=origin) == origin  # type: ignore[arg-type]


def test_changing_needs_a_brief_draft() -> None:
    for early in ("WELCOME", "INTAKE", "UNDERSTANDING"):
        assert not can(early, "change_requested")


def test_archived_is_terminal_and_start_over_archives_everything_else() -> None:
    assert TRANSITIONS["ARCHIVED"] == {}
    for state in SESSION_STATES:
        if state != "ARCHIVED":
            assert next_state(state, "start_over") == "ARCHIVED"  # type: ignore[arg-type]


def test_reopen_from_approved_goes_to_review() -> None:
    assert next_state("APPROVED", "reopen") == "BRIEF_REVIEW"


@settings(max_examples=200, deadline=None)
@given(st.lists(st.sampled_from(TRIGGERS), max_size=40))
def test_random_walk_invariants(triggers: list[str]) -> None:
    state, origin = "WELCOME", None
    archived = False
    for trig in triggers:
        if not can(state, trig, origin=origin):
            continue
        before = state
        if state in SIDE_STATES:
            new = next_state(state, trig, origin=origin)  # type: ignore[arg-type]
            if new == "APPROVED":
                assert origin == "APPROVED"
            origin = None
        else:
            new = next_state(state, trig)  # type: ignore[arg-type]
            if new in SIDE_STATES:
                origin = state
            if new == "APPROVED":
                assert (before, trig) == ("BRIEF_REVIEW", "gate_approved")
        state = new
        archived = archived or state == "ARCHIVED"
        if archived:
            assert state == "ARCHIVED"
    assert state in SESSION_STATES


def test_marker_only_on_side_states() -> None:
    for state, edges in TRANSITIONS.items():
        for target in edges.values():
            assert target != ORIGIN or state in SIDE_STATES
