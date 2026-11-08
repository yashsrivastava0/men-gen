"""Inbound events carry hard length limits (security hardening H3): oversize input is rejected at
the boundary, at the limit it is accepted."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import TypeAdapter, ValidationError

from mde_contracts.events import (
    MAX_COMMENT_CHARS,
    MAX_FILENAME_CHARS,
    MAX_OTHER_TEXT_CHARS,
    MAX_TARGET_CHARS,
    MAX_UPLOAD_TEXT_CHARS,
    MAX_USER_MESSAGE_CHARS,
    InboundEvent,
)

_BASE: dict[str, Any] = {"event_id": "evt_X"}
_GATE: dict[str, Any] = {
    "type": "gate_decision",
    "gate": "brief",
    "decision": "approve",
    "version": 1,
    "hash": "a" * 64,
}
_UPLOAD: dict[str, Any] = {
    "type": "upload_added",
    "upload_id": "up_1",
    "media_type": "text/plain",
    "size_bytes": 1,
}

# (event fields, field name, limit)
CASES: list[tuple[dict[str, Any], str, int]] = [
    ({"type": "user_message"}, "text", MAX_USER_MESSAGE_CHARS),
    ({**_UPLOAD, "filename": "a.txt"}, "text", MAX_UPLOAD_TEXT_CHARS),
    ({**_UPLOAD}, "filename", MAX_FILENAME_CHARS),
    ({**_GATE}, "comment", MAX_COMMENT_CHARS),
    ({"type": "option_selected", "card_instance_id": "c1"}, "other_text", MAX_OTHER_TEXT_CHARS),
    ({"type": "ui_action", "action": "toggle_feature"}, "target", MAX_TARGET_CHARS),
]


def test_documented_limits() -> None:
    assert MAX_USER_MESSAGE_CHARS == 50_000
    assert MAX_UPLOAD_TEXT_CHARS == 200_000
    assert MAX_FILENAME_CHARS == 255
    assert MAX_COMMENT_CHARS == 5_000
    assert MAX_OTHER_TEXT_CHARS == 5_000
    assert MAX_TARGET_CHARS == 200


@pytest.mark.parametrize(("fields", "name", "limit"), CASES, ids=[c[1] for c in CASES])
def test_event_limits_reject_oversize_input(fields: dict[str, Any], name: str, limit: int) -> None:
    adapter = TypeAdapter(InboundEvent)
    base = {**_BASE, **fields}
    ok = adapter.validate_python({**base, name: "x" * limit})
    assert getattr(ok, name) == "x" * limit
    with pytest.raises(ValidationError) as err:
        adapter.validate_python({**base, name: "x" * (limit + 1)})
    assert name in str(err.value)
