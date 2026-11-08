"""N9: id-like inbound fields are ids, not free text. Each has a length limit and a conservative
character set; a hash is a lowercase SHA-256 digest; a media type has the shape of a media type.

(A secret pasted into an id-like field can still match an id's character set: the flow screens
these fields too, see ``packages/flow/tests/test_screening.py``.)
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import TypeAdapter, ValidationError

from mde_contracts.events import (
    MAX_DATA_REF_CHARS,
    MAX_ID_CHARS,
    MAX_MEDIA_TYPE_CHARS,
    MAX_OPTION_IDS,
    InboundEvent,
)

ADAPTER: TypeAdapter[Any] = TypeAdapter(InboundEvent)
DIGEST = "0123456789abcdef" * 4
KEY = "sk-proj-" + "Zq7Lm2Vx9Kd4Rt8Ws3Bn6Hy1Pc5Jf0Ga"

OPTION = {"type": "option_selected", "event_id": "evt_1", "card_instance_id": "crd_01"}
UPLOAD = {
    "type": "upload_added",
    "event_id": "evt_1",
    "upload_id": "up_1",
    "filename": "a.txt",
    "media_type": "text/plain",
    "size_bytes": 1,
}
GATE = {
    "type": "gate_decision",
    "event_id": "evt_1",
    "gate": "brief",
    "decision": "approve",
    "version": 1,
    "hash": DIGEST,
}
UI = {"type": "ui_action", "event_id": "evt_1", "action": "toggle_feature"}


def ok(base: dict[str, Any], **fields: Any) -> Any:
    return ADAPTER.validate_python({**base, **fields})


def bad(base: dict[str, Any], **fields: Any) -> str:
    with pytest.raises(ValidationError) as err:
        ADAPTER.validate_python({**base, **fields})
    return str(err.value)


# --------------------------------------------------------------------------- plain ids


ID_FIELDS: list[tuple[dict[str, Any], str]] = [
    (OPTION, "card_instance_id"),
    (UPLOAD, "upload_id"),
    ({**UI}, "target"),
    ({**UI}, "selected_screen_id"),
]


@pytest.mark.parametrize(("base", "field"), ID_FIELDS, ids=[f for _, f in ID_FIELDS])
def test_an_id_field_takes_a_plain_id_up_to_the_limit(base: dict[str, Any], field: str) -> None:
    for value in ("crd_01HZX", "Q-look-01", "brand.name", "RA-001", "a:b", "x" * MAX_ID_CHARS):
        assert getattr(ok(base, **{field: value}), field) == value


@pytest.mark.parametrize(("base", "field"), ID_FIELDS, ids=[f for _, f in ID_FIELDS])
@pytest.mark.parametrize(
    "value",
    [
        "x" * (MAX_ID_CHARS + 1),
        "",
        "has space",
        "line\nbreak",
        "tab\there",
        "semi;colon",
        'quote"d',
        "slash/inside",
        "back\\slash",
        "<script>",
        "non-ascii-é",
        "nul\x00",
        "$(whoami)",
        "a" * 5000,
    ],
)
def test_an_id_field_refuses_what_is_not_an_id(
    base: dict[str, Any], field: str, value: str
) -> None:
    assert field in bad(base, **{field: value})


def test_option_ids_are_ids_and_there_are_not_unboundedly_many() -> None:
    assert ok(OPTION, option_ids=["a", "b-1", "c_2"]).option_ids == ["a", "b-1", "c_2"]
    assert ok(OPTION, option_ids=["o"] * MAX_OPTION_IDS)
    assert "option_ids" in bad(OPTION, option_ids=["o"] * (MAX_OPTION_IDS + 1))
    assert "option_ids" in bad(OPTION, option_ids=["fine", "not fine"])
    assert "option_ids" in bad(OPTION, option_ids=["x" * (MAX_ID_CHARS + 1)])
    assert ok(OPTION).option_ids == []


def test_event_ids_are_short_and_plain() -> None:
    assert ok(UI, event_id="evt_01HZX.1:a-b").event_id == "evt_01HZX.1:a-b"
    assert ok(UI, event_id="e").event_id == "e"
    for value in ("", "x" * 129, "-leading", "has space", "new\nline", "é"):
        assert "event_id" in bad(UI, event_id=value)


# --------------------------------------------------------------------------- the gate decision


def test_a_gate_hash_is_a_lowercase_sha256_digest() -> None:
    assert ok(GATE).hash == DIGEST
    for value in (
        "",
        "h",
        "stale",
        DIGEST.upper(),
        DIGEST[:-1],
        DIGEST + "0",
        DIGEST[:-1] + "g",
        DIGEST + "\n",
        KEY,
        "sha256:" + DIGEST,
        " " + DIGEST[1:],
    ):
        assert "hash" in bad(GATE, hash=value)


def test_a_key_cannot_hide_in_the_hash() -> None:
    """A hash is hex: a key there is a validation error. (A role or an id is only held to a
    character set a key also fits: those are screened by the flow instead.)"""
    assert "gate_decision.hash" in bad(GATE, hash=KEY)
    assert ok(GATE, role=KEY).role == KEY  # shape-valid; ``mde_flow.screening`` redacts it


def test_role_and_approver_have_the_shape_of_a_role_and_a_user() -> None:
    assert ok(GATE, role="client_product_owner", approver_id="alice@example.com")
    assert ok(GATE, role=None, approver_id=None)
    for role in ("1abc", "has space", "x" * 65, "", "r;m"):
        assert "role" in bad(GATE, role=role)
    for user in ("has space", "x" * 129, "", "-lead", "a/b"):
        assert "approver_id" in bad(GATE, approver_id=user)


def test_a_gate_version_is_a_non_negative_int_in_range() -> None:
    assert ok(GATE, version=1).version == 1
    assert ok(GATE, version=0).version == 0  # a stale client may name a version that never existed
    assert ok(GATE, version=2_147_483_647)
    for version in (-1, 2_147_483_648, 10**30):
        assert "version" in bad(GATE, version=version)


# --------------------------------------------------------------------------- uploads


@pytest.mark.parametrize(
    "media_type",
    [
        "text/plain",
        "text/markdown",
        "TEXT/Plain",
        "image/png",
        "image/svg+xml",
        "application/vnd.ms-excel",
        "text/plain; charset=utf-8",
        "text/plain;charset=UTF-8",
        'text/plain; charset="utf-8"',
        "text/markdown; charset=utf-8; variant=GFM",
        "application/octet-stream",
        "",  # a browser reports an unknown file type as empty: the API answers it in words (415)
    ],
)
def test_a_media_type_has_the_shape_of_a_media_type(media_type: str) -> None:
    assert ok(UPLOAD, media_type=media_type).media_type == media_type


@pytest.mark.parametrize(
    "media_type",
    [
        "text",
        "/plain",
        "text/",
        "text//plain",
        "text/plain/extra",
        "text/plain;",
        "text/plain; charset",
        "text/plain; charset=",
        "text/plain\n",
        "text/plain\r\nX-Injected: 1",
        "text/plain; x=a b",
        "text/plain; x=<script>",
        "text/plain; " + "a=b; " * 6 + "c=d",
        "text/pl ain",
        "x" * (MAX_MEDIA_TYPE_CHARS + 1),
        "text/" + "p" * 200,
        "text/plain; charset=" + "u" * 65,
        'text/plain; charset="unterminated',
    ],
)
def test_a_media_type_that_is_not_one_is_refused(media_type: str) -> None:
    assert "media_type" in bad(UPLOAD, media_type=media_type)


def test_a_data_ref_is_bounded_here_and_its_character_set_is_the_apis() -> None:
    """The contract bounds the length; ``mde_context_api.inbound`` checks the character set and the
    ``data:`` URI case with a message in words (see ``apps/context-api/tests/test_limits.py``)."""
    for ref in ("blob/abc", "uploads/2026/11/a.png", "s3:bucket/key.png", "a" * MAX_DATA_REF_CHARS):
        assert ok(UPLOAD, media_type="image/png", data_ref=ref).data_ref == ref
    assert ok(UPLOAD, data_ref=None).data_ref is None
    assert "data_ref" in bad(
        UPLOAD, media_type="image/png", data_ref="a" * (MAX_DATA_REF_CHARS + 1)
    )


def test_the_patterns_are_part_of_the_published_schema() -> None:
    schema = ADAPTER.json_schema()
    defs = schema["$defs"]
    assert defs["GateDecision"]["properties"]["hash"]["pattern"] == "^[0-9a-f]{64}$"
    assert defs["OptionSelected"]["properties"]["card_instance_id"]["maxLength"] == MAX_ID_CHARS
    assert defs["UploadAdded"]["properties"]["media_type"]["maxLength"] == MAX_MEDIA_TYPE_CHARS
    assert defs["OptionSelected"]["properties"]["option_ids"]["maxItems"] == MAX_OPTION_IDS


def test_matching_is_fast_on_hostile_input() -> None:
    """The patterns are linear (Rust regex engine): a long near-miss is rejected, not stuck."""
    import time

    started = time.perf_counter()
    for payload in ("a" * 100_000, ("a=b; " * 20_000), "text/plain" + "; a=b" * 20_000):
        for base, field in (
            (OPTION, "card_instance_id"),
            (UPLOAD, "media_type"),
            (GATE, "hash"),
            (UPLOAD, "media_type"),
        ):
            with pytest.raises(ValidationError):
                ADAPTER.validate_python({**base, field: payload})
    assert time.perf_counter() - started < 2.0
