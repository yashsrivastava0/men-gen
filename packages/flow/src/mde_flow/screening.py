"""Secrets never reach the database: inbound text is screened before anything stores it.

The Discover engine screens text too, but only after the flow has already recorded the inbound
event (idempotency) and, for a gate decision, the approval with its comment (shown to the other
approvers). So the flow screens every free-text field of an inbound event **first**: the message,
the "something else" text, an upload's extracted text *and its file name*, a gate comment. What the
engines receive, and what is stored, is the screened event.

The **id-like fields** are screened as well (``event_id``, ``card_instance_id``, ``option_ids``,
``upload_id``, ``media_type``, ``data_ref``, ``approver_id``, ``role``, ``target``,
``selected_screen_id``, and a ``hash`` that is not a digest): the contract holds them to an id's
character set, but a key fits that set, so a key pasted into one would otherwise be stored raw. A
field that held a secret becomes ``[REDACTED]`` (it then matches no card, option or role, which is
what a made-up id would do anyway); an ``event_id`` becomes a stable id derived from the original,
so the idempotency key still tells two different events apart. A proper SHA-256 digest is never
scanned (it cannot carry a key, and a scanner could mistake one for a token).

Hidden characters are dropped and the text is folded (NFKC) for *detection*, so a key split with
zero-width characters or typed in full-width letters is still found; text with no secret in it is
kept as typed (apart from the hidden characters). ``mde_core.redact`` does the redacting.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from typing import Final

from mde_contracts.events import (
    GateDecision,
    InboundEvent,
    OptionSelected,
    UploadAdded,
    UserMessage,
)
from mde_core.redact import redact_text, strip_hidden_chars

_DIGEST: Final = re.compile(r"^[0-9a-f]{64}$")


def screen_text(text: str) -> tuple[str, bool]:
    """``(clean, found_a_secret)``: hidden characters removed, secret-looking spans replaced."""
    visible = strip_hidden_chars(text)
    redacted, kinds = redact_text(unicodedata.normalize("NFKC", visible))
    if kinds:
        return redacted, True
    return visible, False


def redacted_event_id(original: str) -> str:
    """A stable stand-in for an event id that held a secret: the same input gives the same id (a
    resend is still a resend) and two different ones give different ids. One-way."""
    return "evt_redacted_" + hashlib.sha256(original.encode("utf-8")).hexdigest()[:24]


def screen_event(event: InboundEvent) -> tuple[InboundEvent, bool]:
    """The event with every free-text and id-like field screened, and whether any secret was
    removed."""
    found = False

    def clean(value: str | None) -> str | None:
        nonlocal found
        if value is None:
            return None
        text, hit = screen_text(value)
        found = found or hit
        return text

    def clean_event_id(value: str) -> str:
        nonlocal found
        text, hit = screen_text(value)
        if hit:
            found = True
            return redacted_event_id(value)
        return text

    def clean_digest(value: str) -> str:
        return value if _DIGEST.match(value) else clean(value) or ""

    def clean_ids(values: list[str]) -> list[str]:
        return [clean(v) or "" for v in values]

    # (literal ``update`` dicts on purpose: ``test_no_state_writes_outside_machine`` reads them)
    event_id = clean_event_id(event.event_id)
    if isinstance(event, UserMessage):
        return event.model_copy(update={"event_id": event_id, "text": clean(event.text)}), found
    if isinstance(event, OptionSelected):
        screened = event.model_copy(
            update={
                "event_id": event_id,
                "other_text": clean(event.other_text),
                "card_instance_id": clean(event.card_instance_id),
                "option_ids": clean_ids(event.option_ids),
            }
        )
        return screened, found
    if isinstance(event, UploadAdded):
        upload = event.model_copy(
            update={
                "event_id": event_id,
                "text": clean(event.text),
                "filename": clean(event.filename),
                "upload_id": clean(event.upload_id),
                "media_type": clean(event.media_type),
                "data_ref": clean(event.data_ref),
            }
        )
        return upload, found
    if isinstance(event, GateDecision):
        decision = event.model_copy(
            update={
                "event_id": event_id,
                "comment": clean(event.comment),
                "approver_id": clean(event.approver_id),
                "role": clean(event.role),
                "hash": clean_digest(event.hash),
            }
        )
        return decision, found
    action = event.model_copy(
        update={
            "event_id": event_id,
            "target": clean(event.target),
            "selected_screen_id": clean(event.selected_screen_id),
        }
    )
    return action, found
