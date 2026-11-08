"""Request size limits and the upload rules: text, markdown and images only; PDFs are refused with
a friendly message; image bytes never enter an event (only a reference)."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio

from mde_context_api.composition import AppState
from mde_context_api.inbound import (
    OTHER_TYPE_TEXT,
    PDF_TEXT,
    media_type_of,
    validate_inbound,
)
from mde_context_api.settings import ApiSettings
from mde_context_api.testing import Api, build_state, headers, make_client, make_settings
from mde_contracts.events import UiAction, UploadAdded, UserMessage
from mde_core.errors import ValidationFailed

LIMIT = 4096


@pytest_asyncio.fixture
async def small(tmp_path: Path) -> AsyncIterator[tuple[Api, AppState]]:
    st = await build_state(make_settings(tmp_path / "projects", max_body_bytes=LIMIT))
    async with make_client(st) as c:
        api = Api(c)
        await api.create()
        yield api, st
    await st.aclose()


def message(text: str, event_id: str = "evt-1") -> dict[str, Any]:
    return {"type": "user_message", "event_id": event_id, "text": text}


def upload(**fields: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "type": "upload_added",
        "event_id": "evt-up-1",
        "upload_id": "upl-1",
        "filename": "notes.md",
        "media_type": "text/markdown",
        "size_bytes": 12,
        "text": "# my app\nan idea",
    }
    return {**base, **fields}


# --------------------------------------------------------------------------- body size


def test_the_default_body_limit_is_one_megabyte() -> None:
    assert ApiSettings().max_body_bytes == 1_048_576


async def test_a_body_over_the_default_limit_is_a_413(mem_api: Api) -> None:
    await mem_api.create()
    huge = json.dumps(message("x" * 1_048_577))
    resp = await mem_api.client.post(
        mem_api.url("/events"),
        content=huge,
        headers={**mem_api.headers, "Content-Type": "application/json"},
    )
    assert resp.status_code == 413
    body = resp.json()
    assert body["code"] == "payload_too_large" and "1048576" in body["message"]
    assert resp.headers["x-request-id"].startswith("req_")


async def test_a_body_just_under_the_limit_is_accepted(small: tuple[Api, AppState]) -> None:
    api, _ = small
    overhead = len(json.dumps(message("")))
    resp = await api.post(message("a" * (LIMIT - overhead - 8)))
    assert resp.status_code == 200, resp.text


async def test_a_body_over_the_limit_is_refused_by_content_length(
    small: tuple[Api, AppState],
) -> None:
    api, _ = small
    resp = await api.post(message("a" * (LIMIT + 1)))
    assert resp.status_code == 413
    assert resp.json()["code"] == "payload_too_large"


async def test_a_chunked_body_over_the_limit_is_refused_while_reading(
    small: tuple[Api, AppState],
) -> None:
    api, _ = small

    async def chunks() -> AsyncIterator[bytes]:
        for _ in range(8):  # no Content-Length: the size is only known by reading
            yield b" " * 1024

    resp = await api.client.post(
        api.url("/events"),
        content=chunks(),
        headers={**api.headers, "Content-Type": "application/json"},
    )
    assert resp.status_code == 413 and resp.json()["code"] == "payload_too_large"


async def test_a_refused_body_leaves_the_session_untouched(small: tuple[Api, AppState]) -> None:
    api, _ = small
    before = await api.refresh()
    assert (await api.post(message("a" * (LIMIT + 1)))).status_code == 413
    assert await api.refresh() == before


async def test_the_limit_also_applies_to_session_creation(small: tuple[Api, AppState]) -> None:
    api, _ = small
    resp = await api.client.post(
        "/v1/sessions",
        json={"mode": "enterprise", "org_profile": {"notes": "x" * LIMIT}},
        headers=headers(),
    )
    assert resp.status_code == 413


# --------------------------------------------------------------------------- uploads: accepted


@pytest.mark.parametrize(
    "media_type", ["text/plain", "text/markdown", "text/markdown; charset=UTF-8"]
)
async def test_text_and_markdown_uploads_are_accepted(mem_api: Api, media_type: str) -> None:
    await mem_api.create()
    resp = await mem_api.post(upload(media_type=media_type))
    assert resp.status_code == 200, resp.text


@pytest.mark.parametrize("media_type", ["image/png", "image/jpeg", "image/webp", "IMAGE/PNG"])
async def test_images_are_accepted_by_reference(mem_api: Api, media_type: str) -> None:
    await mem_api.create()
    resp = await mem_api.post(
        upload(
            media_type=media_type,
            filename="logo.png",
            size_bytes=48_000,
            text=None,
            data_ref="upl_store/2026/logo-01.png",
        )
    )
    assert resp.status_code == 200, resp.text


# --------------------------------------------------------------------------- uploads: refused


async def test_a_pdf_is_refused_with_a_friendly_message(mem_api: Api) -> None:
    await mem_api.create()
    before = await mem_api.refresh()
    resp = await mem_api.post(
        upload(media_type="application/pdf", filename="requirements.pdf", text=None, size_bytes=900)
    )
    assert resp.status_code == 415
    body = resp.json()
    assert body["code"] == "unsupported_media_type"
    assert body["message"] == PDF_TEXT
    assert "PDFs are not supported yet" in body["message"] and "paste the text" in body["message"]
    assert await mem_api.refresh() == before  # nothing recorded


async def test_a_pdf_sent_as_an_octet_stream_is_recognised_by_its_name(mem_api: Api) -> None:
    await mem_api.create()
    resp = await mem_api.post(
        upload(media_type="application/octet-stream", filename="Spec.PDF", text=None)
    )
    assert resp.status_code == 415 and resp.json()["message"] == PDF_TEXT


@pytest.mark.parametrize(
    "media_type",
    [
        "application/zip",
        "application/json",
        "application/msword",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "image/svg+xml",
        "video/mp4",
        "audio/mpeg",
        "text/html",
        "",
    ],
)
async def test_any_other_type_is_refused_and_says_what_is_supported(
    mem_api: Api, media_type: str
) -> None:
    await mem_api.create()
    resp = await mem_api.post(upload(media_type=media_type, filename="thing.bin", text=None))
    assert resp.status_code == 415
    assert resp.json()["message"] == OTHER_TYPE_TEXT
    assert "plain text, Markdown and images" in resp.json()["message"]


async def test_image_bytes_are_never_accepted_inline(mem_api: Api) -> None:
    await mem_api.create()
    as_text = await mem_api.post(
        upload(media_type="image/png", filename="a.png", text="iVBORw0KGgoAAAANSUhEUg==")
    )
    assert as_text.status_code == 422 and "reference" in as_text.json()["message"]
    data_uri = await mem_api.post(
        upload(
            media_type="image/png",
            filename="a.png",
            text=None,
            data_ref="data:image/png;base64,iVBORw0KGgo=",
        )
    )
    assert data_uri.status_code == 422 and "never the file itself" in data_uri.json()["message"]
    sneaky = await mem_api.post(
        upload(media_type="image/png", text=None, data_ref="aGVsbG8=;base64,aGVsbG8gd29ybGQ=")
    )
    assert sneaky.status_code == 422
    huge_ref = await mem_api.post(upload(media_type="image/png", text=None, data_ref="r" * 600))
    assert huge_ref.status_code == 422


async def test_an_oversized_text_upload_is_a_413(mem_api: Api, mem_state: AppState) -> None:
    await mem_api.create()
    too_big = "x" * (mem_state.settings.max_upload_text_bytes + 1)
    resp = await mem_api.post(upload(text=too_big, size_bytes=len(too_big)))
    assert resp.status_code in (413,)
    assert resp.json()["code"] == "payload_too_large"
    declared = await mem_api.post(
        upload(text=None, size_bytes=mem_state.settings.max_upload_text_bytes + 1)
    )
    assert declared.status_code == 413


async def test_an_oversized_image_is_a_413(mem_api: Api, mem_state: AppState) -> None:
    await mem_api.create()
    resp = await mem_api.post(
        upload(
            media_type="image/jpeg",
            filename="big.jpg",
            text=None,
            data_ref="ref-1",
            size_bytes=mem_state.settings.max_upload_image_bytes + 1,
        )
    )
    assert resp.status_code == 413


@pytest.mark.parametrize(
    "fields",
    [
        {"size_bytes": -1},
        {"filename": "a\x00b.md"},
        {"filename": "n" * 300},
        {"upload_id": "bad id"},
        {"event_id": "bad id with spaces"},
        {"event_id": "e" * 129},
        {"event_id": ""},
    ],
    ids=[
        "negative",
        "control-char",
        "long-name",
        "bad-upload-id",
        "space-id",
        "long-id",
        "empty-id",
    ],
)
async def test_malformed_upload_fields_are_422(mem_api: Api, fields: dict[str, Any]) -> None:
    await mem_api.create()
    resp = await mem_api.post(upload(**fields))
    assert resp.status_code == 422 and resp.json()["code"] == "validation_failed"


async def test_an_event_id_must_be_plain_for_every_event_type(mem_api: Api) -> None:
    await mem_api.create()
    for event in (
        message("hi", event_id="has space"),
        {"type": "ui_action", "event_id": "../etc", "action": "stop"},
    ):
        resp = await mem_api.post(event)
        assert resp.status_code == 422 and resp.json()["code"] == "validation_failed"


# --------------------------------------------------------------------------- unit


def test_media_types_are_normalised() -> None:
    assert media_type_of("Text/Markdown; charset=utf-8") == "text/markdown"
    assert media_type_of("  image/PNG ") == "image/png"


def test_validate_inbound_passes_plain_events(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    validate_inbound(UserMessage(event_id="evt-1", text="hi"), settings)
    validate_inbound(UiAction(event_id="evt-2", action="stop"), settings)
    with pytest.raises(ValidationFailed):
        # (the contract refuses this id at construction; the API check is a second line of
        # defence for an event built without validation)
        validate_inbound(
            UserMessage.model_construct(event_id="no spaces allowed", text="hi"), settings
        )
    ok = UploadAdded(
        event_id="evt-3",
        upload_id="u-1",
        filename="a.txt",
        media_type="text/plain",
        size_bytes=3,
        text="abc",
    )
    validate_inbound(ok, settings)
