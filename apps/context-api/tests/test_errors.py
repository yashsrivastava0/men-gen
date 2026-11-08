"""The error table: every failure is ``{code, message}`` with a stable status; an unexpected error is
a 500 with a request id and no stack trace."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from mde_context_api import errors
from mde_context_api.composition import AppState
from mde_context_api.errors import (
    PayloadTooLarge,
    Unauthorized,
    UnsupportedMedia,
    describe,
)
from mde_context_api.testing import Api, headers
from mde_core.errors import (
    BudgetExceeded,
    CallCancelled,
    CircuitOpen,
    ConfigError,
    Conflict,
    GateError,
    HashMismatch,
    InvalidTransition,
    LiveCallForbidden,
    MdeError,
    NotFound,
    ProviderError,
    StructuredOutputError,
    TenantViolation,
    ValidationFailed,
)
from mde_flow.errors import Forbidden, HandoffFailed

TABLE: list[tuple[BaseException, int, str]] = [
    (NotFound("session 'x' not found"), 404, "not_found"),
    (TenantViolation("record of another tenant"), 404, "not_found"),
    (Forbidden("you cannot read this session"), 403, "forbidden"),
    (Conflict("the session already has an active run"), 409, "conflict"),
    (HashMismatch("stale"), 409, "hash_mismatch"),
    (GateError("not allowed"), 409, "gate_error"),
    (InvalidTransition("PLANNING -> HANDED_OFF"), 409, "invalid_transition"),
    (CallCancelled("stopped"), 409, "cancelled"),
    (ValidationFailed("policy 'x' is unknown"), 422, "validation_failed"),
    (BudgetExceeded("cap"), 429, "budget_exceeded"),
    (StructuredOutputError("bad output"), 502, "gateway_error"),
    (CircuitOpen("open"), 502, "gateway_error"),
    (ProviderError("boom", status=500), 502, "gateway_error"),
    (HandoffFailed("git said no"), 503, "handoff_failed"),
    (ConfigError("DATABASE_URL"), 500, "config_error"),
    (LiveCallForbidden("live"), 500, "config_error"),
    (MdeError("something else"), 500, "internal_error"),
    (RuntimeError("kaboom"), 500, "internal_error"),
    (Unauthorized(), 401, "unauthorized"),
    (PayloadTooLarge("too big"), 413, "payload_too_large"),
    (UnsupportedMedia("no pdf"), 415, "unsupported_media_type"),
]


@pytest.mark.parametrize(
    ("exc", "status", "code"), TABLE, ids=[f"{t[1]}-{type(t[0]).__name__}" for t in TABLE]
)
def test_describe_maps_every_error_to_a_stable_status_and_code(
    exc: BaseException, status: int, code: str
) -> None:
    info = describe(exc)
    assert (info.status, info.code) == (status, code)
    assert info.message and "Traceback" not in info.message


def test_developer_aimed_messages_are_replaced_by_friendly_ones() -> None:
    assert describe(HashMismatch("hash abc != def")).message == errors.HASH_MISMATCH_TEXT
    assert describe(GateError("approver_id must be the caller")).message == errors.GATE_ERROR_TEXT
    assert describe(BudgetExceeded("cap 6.00")).message == errors.BUDGET_TEXT
    assert describe(ProviderError("api key sk-123 invalid")).message == errors.GATEWAY_TEXT
    assert describe(ConfigError("DATABASE_URL=postgres://u:p@h")).message == errors.CONFIG_TEXT
    assert describe(TenantViolation("tenant b row")).message == errors.NOT_FOUND_TEXT
    assert describe(HandoffFailed("x")).headers == {"Retry-After": "2"}
    assert describe(Unauthorized()).headers == {"WWW-Authenticate": "Bearer"}


def test_a_long_message_is_clipped() -> None:
    assert len(describe(Conflict("x" * 5000)).message) <= 300


ROUTE_TABLE = [row for row in TABLE if not isinstance(row[0], errors.ApiError)]


@pytest.mark.parametrize(
    ("exc", "status", "code"),
    ROUTE_TABLE,
    ids=[f"{t[1]}-{type(t[0]).__name__}" for t in ROUTE_TABLE],
)
async def test_a_route_turns_a_raised_error_into_the_documented_response(
    mem_api: Api, mem_state: AppState, exc: BaseException, status: int, code: str
) -> None:
    await mem_api.create()

    async def boom(*args: object, **kwargs: object) -> Any:
        raise exc

    mem_state.flow.get_snapshot = boom  # type: ignore[method-assign]
    resp = await mem_api.get()
    assert resp.status_code == status
    body = resp.json()
    assert body["code"] == code
    assert set(body) <= {"code", "message", "request_id", "details"}
    assert resp.headers["x-request-id"].startswith("req_")
    if status >= 500:
        assert body["request_id"] == resp.headers["x-request-id"]
        for leak in ("kaboom", "Traceback", "RuntimeError", 'File "', "something else"):
            assert leak not in resp.text
    else:
        assert "request_id" not in body
    if isinstance(exc, HandoffFailed):
        assert resp.headers["retry-after"] == "2"


async def test_errors_in_a_post_route_have_the_same_shape(
    mem_api: Api, mem_state: AppState
) -> None:
    await mem_api.create()

    async def boom(*args: object, **kwargs: object) -> Any:
        raise BudgetExceeded("cap")

    mem_state.flow.handle_event = boom  # type: ignore[method-assign]
    resp = await mem_api.post({"type": "user_message", "event_id": "evt-1", "text": "hi"})
    assert resp.status_code == 429
    assert resp.json() == {"code": "budget_exceeded", "message": errors.BUDGET_TEXT}


async def test_an_unexpected_error_is_a_500_with_a_request_id_and_no_trace(
    mem_api: Api, mem_state: AppState
) -> None:
    await mem_api.create()

    async def boom(*args: object, **kwargs: object) -> Any:
        raise RuntimeError("secret internals: /srv/app/db.py line 42 password=hunter2")

    mem_state.flow.get_snapshot = boom  # type: ignore[method-assign]
    resp = await mem_api.get(headers={**mem_api.headers, "X-Request-Id": "client-req-0001"})
    assert resp.status_code == 500
    body = resp.json()
    assert body == {
        "code": "internal_error",
        "message": errors.INTERNAL_TEXT,
        "request_id": "client-req-0001",
    }
    assert resp.headers["x-request-id"] == "client-req-0001"
    assert "hunter2" not in resp.text and "db.py" not in resp.text


async def test_a_client_request_id_is_kept_only_when_well_formed(
    mem_client: httpx.AsyncClient,
) -> None:
    kept = await mem_client.get("/healthz", headers={"X-Request-Id": "trace-abc-12345"})
    assert kept.headers["x-request-id"] == "trace-abc-12345"
    for bad in ("short", "has space in it!", "x" * 80, "<script>alert(1)</script>"):
        replaced = await mem_client.get("/healthz", headers={"X-Request-Id": bad})
        assert replaced.headers["x-request-id"].startswith("req_")
    fresh = await mem_client.get("/healthz")
    assert fresh.headers["x-request-id"].startswith("req_")


async def test_request_validation_errors_name_the_field_but_never_echo_the_value(
    mem_client: httpx.AsyncClient,
) -> None:
    resp = await mem_client.post(
        "/v1/sessions", json={"mode": "SECRET-sk-abc123", "extra": 1}, headers=headers()
    )
    assert resp.status_code == 422
    body = resp.json()
    assert body["code"] == "validation_failed"
    fields = {d["field"] for d in body["details"]}
    assert {"mode", "extra"} <= fields
    assert "SECRET-sk-abc123" not in resp.text


async def test_an_event_that_is_not_one_of_the_five_types_is_422(mem_api: Api) -> None:
    await mem_api.create()
    for event in (
        {"type": "teleport", "event_id": "evt-1"},
        {"type": "user_message", "event_id": "evt-2"},  # no text
        {"event_id": "evt-3", "text": "hi"},  # no type
    ):
        resp = await mem_api.post(event)
        assert resp.status_code == 422 and resp.json()["code"] == "validation_failed"
    garbled = await mem_api.client.post(
        mem_api.url("/events"),
        content=b"{not json",
        headers={**mem_api.headers, "Content-Type": "application/json"},
    )
    assert garbled.status_code == 422 and garbled.json()["code"] == "validation_failed"


async def test_unknown_paths_and_wrong_methods_use_the_same_body(
    mem_client: httpx.AsyncClient,
) -> None:
    missing = await mem_client.get("/v1/nothing-here", headers=headers())
    assert missing.status_code == 404 and missing.json()["code"] == "not_found"
    wrong = await mem_client.delete("/v1/sessions", headers=headers())
    assert wrong.status_code == 405 and wrong.json()["code"] == "method_not_allowed"
    assert set(wrong.json()) == {"code", "message"}


async def test_a_session_id_that_is_too_long_is_a_validation_error_not_a_crash(
    mem_client: httpx.AsyncClient,
) -> None:
    resp = await mem_client.get(f"/v1/sessions/{'s' * 200}", headers=headers())
    assert resp.status_code == 422 and resp.json()["code"] == "validation_failed"


async def test_a_version_below_one_is_a_validation_error(mem_api: Api) -> None:
    await mem_api.create()
    resp = await mem_api.get("/brief", params={"version": 0})
    assert resp.status_code == 422


async def test_missing_versions_are_404(mem_api: Api) -> None:
    await mem_api.create()
    await mem_api.to_gate1()
    for tail in ("/brief", "/plan", "/spec"):
        resp = await mem_api.get(tail, params={"version": 99})
        assert resp.status_code == 404 and resp.json()["code"] == "not_found"
