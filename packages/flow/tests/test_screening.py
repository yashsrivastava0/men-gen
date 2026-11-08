"""N9: the id-like fields of an inbound event are screened for secrets before anything is stored.

The contract holds ``card_instance_id``, ``option_ids``, ``upload_id``, ``media_type``, ``data_ref``,
``approver_id``, ``role``, ``target``, ``selected_screen_id`` and ``event_id`` to an id's character
set, but a key fits that set. So the flow screens them like text, and what reaches the store is the
screened event. (The first part is the pure function; the second replays the attack through the
flow with a spy on exactly what is handed to ``record_inbound``.)
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any

import pytest

from mde_contracts.events import (
    GateDecision,
    InboundEvent,
    OptionSelected,
    UiAction,
    UploadAdded,
    UserMessage,
)
from mde_contracts.ports import Store, UnitOfWork
from mde_flow import messages
from mde_flow.screening import redacted_event_id, screen_event, screen_text
from mde_flow.testing.kit import FlowKit, StoreFactory, make_flow_kit

KEY = "sk-proj-AbCdEfGhIjKlMnOpQrStUvWxYz0123456789"  # secret-scan: allow
FRAGMENTS = ("AbCdEfGhIj", "KlMnOpQrSt", "0123456789")
DIGEST = "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08"  # hex passes untouched
# the same key, split by a zero-width space (built, not typed, so no hidden character sits in the
# source) and in full-width letters
SPLIT_KEY = KEY[:16] + chr(0x200B) + KEY[16:]
FULLWIDTH_KEY = "".join(chr(ord(c) + 0xFEE0) if c in "sk" else c for c in KEY)


def dump(event: InboundEvent) -> str:
    return event.model_dump_json()


def clean_of_the_key(event: InboundEvent) -> None:
    text = dump(event)
    for fragment in FRAGMENTS:
        assert fragment not in text, fragment


# --------------------------------------------------------------------------- the pure function


@pytest.mark.parametrize(
    "key", [KEY, SPLIT_KEY, FULLWIDTH_KEY], ids=["plain", "split", "fullwidth"]
)
def test_a_key_in_a_card_or_option_id_is_redacted(key: str) -> None:
    event = OptionSelected.model_validate(
        {
            "event_id": "evt_1",
            "card_instance_id": KEY,
            "option_ids": ["A", KEY, "B"],
        }
    )
    event = event.model_copy(update={"card_instance_id": key, "option_ids": ["A", key, "B"]})
    out, found = screen_event(event)
    assert found
    assert isinstance(out, OptionSelected)
    assert out.card_instance_id == "[REDACTED]"
    assert out.option_ids == ["A", "[REDACTED]", "B"]  # the innocent ids are untouched, in order
    clean_of_the_key(out)


def test_a_key_in_any_upload_field_is_redacted() -> None:
    event = UploadAdded.model_validate(
        {
            "event_id": "evt_1",
            "upload_id": KEY,
            "filename": "notes.md",
            "media_type": f"text/plain; x={KEY}",
            "size_bytes": 5,
            "data_ref": f"blob/{KEY}",
        }
    )
    out, found = screen_event(event)
    assert found and isinstance(out, UploadAdded)
    assert out.upload_id == "[REDACTED]"
    assert out.media_type == "text/plain; x=[REDACTED]"  # still shaped like what it was
    assert out.data_ref == "blob/[REDACTED]"
    clean_of_the_key(out)


def test_a_key_in_a_role_or_approver_id_is_redacted_and_a_digest_is_left_alone() -> None:
    event = GateDecision.model_validate(
        {
            "event_id": "evt_1",
            "gate": "plan",
            "decision": "approve",
            "version": 2,
            "hash": DIGEST,
            "role": KEY,
            "approver_id": KEY,
        }
    )
    out, found = screen_event(event)
    assert found and isinstance(out, GateDecision)
    assert (out.role, out.approver_id) == ("[REDACTED]", "[REDACTED]")
    assert out.hash == DIGEST  # a proper digest is never scanned or changed
    clean_of_the_key(out)


def test_a_hash_that_is_not_a_digest_is_screened_too() -> None:
    """The contract refuses it, but the flow does not rely on that (a caller can hand it any model,
    for instance ``model_construct``)."""
    forged = GateDecision.model_construct(
        event_id="evt_1",
        type="gate_decision",
        gate="plan",
        decision="approve",
        version=1,
        hash=KEY,
        comment=None,
        approver_id=None,
        role=None,
        at=None,
    )
    out, found = screen_event(forged)
    assert found and isinstance(out, GateDecision) and out.hash == "[REDACTED]"
    clean_of_the_key(out)


def test_a_key_in_a_ui_target_or_screen_id_is_redacted() -> None:
    event = UiAction.model_validate(
        {
            "event_id": "evt_1",
            "action": "toggle_feature",
            "target": KEY,
            "selected_screen_id": KEY,
        }
    )
    out, found = screen_event(event)
    assert found and isinstance(out, UiAction)
    assert (out.target, out.selected_screen_id) == ("[REDACTED]", "[REDACTED]")
    clean_of_the_key(out)


def test_a_key_used_as_the_event_id_becomes_a_stable_one_way_stand_in() -> None:
    event = UserMessage(event_id=KEY, text="hi")
    out, found = screen_event(event)
    assert found
    assert out.event_id == redacted_event_id(KEY) and out.event_id.startswith("evt_redacted_")
    clean_of_the_key(out)
    again, _ = screen_event(UserMessage(event_id=KEY, text="hi again"))
    assert again.event_id == out.event_id  # a resend keeps its idempotency key
    other, _ = screen_event(UserMessage(event_id=KEY[:-1] + "X", text="hi"))
    assert other.event_id != out.event_id  # a different event does not collide with it
    assert len(out.event_id) <= 128  # still a valid event id


def test_ordinary_ids_hashes_and_media_types_pass_through_untouched() -> None:
    ids = OptionSelected.model_validate(
        {
            "event_id": "evt_01M6WXF6VW2HD0MHDGXZPYY1NP",
            "card_instance_id": "crd_01M6WXF6VW2HD0MHDGXZPYY1NR",
            "option_ids": ["opt_a", "Q-look-01", "RA-001"],
        }
    )
    up = UploadAdded.model_validate(
        {
            "event_id": "evt_2",
            "upload_id": "up_1",
            "filename": "a.png",
            "media_type": "text/markdown; charset=utf-8",
            "size_bytes": 1,
            "data_ref": "uploads/2026/11/a.png",
        }
    )
    gate = GateDecision.model_validate(
        {
            "event_id": "evt_3",
            "gate": "brief",
            "decision": "approve",
            "version": 1,
            "hash": DIGEST,
            "role": "client_product_owner",
            "approver_id": "alice@example.com",
        }
    )
    ui = UiAction.model_validate(
        {"event_id": "evt_4", "action": "toggle_feature", "target": "brand.name"}
    )
    for event in (ids, up, gate, ui):
        out, found = screen_event(event)
        assert not found and out == event


def test_a_hidden_character_in_an_id_is_dropped() -> None:
    event = OptionSelected.model_construct(
        event_id="evt_1",
        type="option_selected",
        card_instance_id="crd" + chr(0x200B) + "_1",
        option_ids=[],
        other_text=None,
        you_decide=False,
        skipped=False,
        at=None,
    )
    out, found = screen_event(event)
    assert isinstance(out, OptionSelected) and out.card_instance_id == "crd_1" and not found


def test_screen_text_still_screens_free_text_exactly_as_before() -> None:
    assert screen_text("hello") == ("hello", False)
    text, hit = screen_text(f"my key is {KEY} ok")
    assert hit and text == "my key is [REDACTED] ok"


# --------------------------------------------------------------------------- through the flow


class _SpyEvents:
    def __init__(self, inner: Any, sink: list[InboundEvent]) -> None:
        self._inner = inner
        self._sink = sink

    async def record_inbound(self, session_id: str, event: InboundEvent) -> bool:
        self._sink.append(event)
        return bool(await self._inner.record_inbound(session_id, event))

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


class _SpyUow:
    def __init__(self, inner: UnitOfWork, sink: list[InboundEvent]) -> None:
        self._inner = inner
        self.events = _SpyEvents(inner.events, sink)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


class SpyStore:
    """Keeps exactly what the flow hands ``record_inbound`` (what is persisted)."""

    def __init__(self, inner: Store) -> None:
        self.inner = inner
        self.inbound: list[InboundEvent] = []

    def begin(self, tenant_id: str | None) -> AbstractAsyncContextManager[UnitOfWork]:
        return self._begin(tenant_id)

    @asynccontextmanager
    async def _begin(self, tenant_id: str | None) -> AsyncGenerator[UnitOfWork]:
        async with self.inner.begin(tenant_id) as uow:
            yield _SpyUow(uow, self.inbound)  # type: ignore[misc]

    def assert_clean(self) -> None:
        for event in self.inbound:
            clean_of_the_key(event)


def spy_kit(flow_store: StoreFactory) -> tuple[FlowKit, SpyStore]:
    spies: list[SpyStore] = []

    def factory(clock: Any, ids: Any) -> Store:
        spy = SpyStore(flow_store(clock, ids))
        spies.append(spy)
        return spy

    return make_flow_kit(store_factory=factory), spies[0]


@pytest.mark.parametrize(
    "build",
    [
        pytest.param(
            lambda d, card: OptionSelected(
                event_id=d.eid(), card_instance_id=KEY, option_ids=[KEY], other_text=None
            ),
            id="option_selected",
        ),
        pytest.param(
            lambda d, card: OptionSelected(
                event_id=d.eid(), card_instance_id=card, option_ids=[KEY, "A"]
            ),
            id="option-id-on-a-real-card",
        ),
        pytest.param(
            lambda d, card: UploadAdded(
                event_id=d.eid(),
                upload_id=KEY,
                filename="notes.md",
                media_type=f"text/plain; x={KEY}",
                size_bytes=5,
                text="an app for plants",
                data_ref=KEY,
            ),
            id="upload_added",
        ),
        pytest.param(
            lambda d, card: GateDecision(
                event_id=d.eid(),
                gate="brief",
                decision="approve",
                version=1,
                hash=DIGEST,
                role=KEY,
                approver_id=KEY,
            ),
            id="gate_decision",
        ),
        pytest.param(
            lambda d, card: UiAction(
                event_id=d.eid(), action="toggle_feature", target=KEY, selected_screen_id=KEY
            ),
            id="ui_action",
        ),
        pytest.param(
            lambda d, card: UserMessage(event_id=KEY, text="hello"),
            id="event_id",
        ),
    ],
)
async def test_a_key_in_an_id_field_never_reaches_the_store(
    flow_store: StoreFactory, build: Any
) -> None:
    kit, spy = spy_kit(flow_store)
    d = await kit.driver()
    await d.say("a fitness tracking app")
    card = (await d.snapshot()).pending_card
    assert card is not None
    event = build(d, card.instance_id)
    try:
        out = await d.send(event)
    except Exception:  # an unknown card or role may be refused: the point is what was stored
        out = []
    assert spy.inbound  # the turn did record events ...
    spy.assert_clean()  # ... and none of them ever held a fragment of the key
    for reply in out:
        for fragment in FRAGMENTS:
            assert fragment not in reply.model_dump_json()


async def test_the_user_is_told_when_an_id_field_held_a_secret(flow_store: StoreFactory) -> None:
    kit, spy = spy_kit(flow_store)
    d = await kit.driver()
    await d.say("a fitness tracking app")
    card = (await d.snapshot()).pending_card
    assert card is not None
    out = await d.send(
        OptionSelected(event_id=d.eid(), card_instance_id=card.instance_id, option_ids=["A", KEY])
    )
    assert messages.N_INPUT_CLEANED in {getattr(e, "code", None) for e in out}
    spy.assert_clean()
