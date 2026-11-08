"""The OpenAPI document: every route is documented, typed, and the committed export is current."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from mde_context_api.main import create_app
from mde_contracts.events import INBOUND_TYPES, OUTBOUND_TYPES

ROOT = Path(__file__).resolve().parents[3]
EXPORT = ROOT / "docs" / "api" / "openapi.json"

ROUTES = {
    ("post", "/v1/sessions"),
    ("get", "/v1/sessions/{session_id}"),
    ("post", "/v1/sessions/{session_id}/events"),
    ("get", "/v1/sessions/{session_id}/stream"),
    ("get", "/v1/sessions/{session_id}/brief"),
    ("get", "/v1/sessions/{session_id}/plan"),
    ("get", "/v1/sessions/{session_id}/spec"),
    ("get", "/v1/sessions/{session_id}/versions"),
    ("get", "/healthz"),
    ("get", "/readyz"),
    ("get", "/v1/budget"),
}


@pytest.fixture(scope="module")
def doc() -> dict[str, Any]:
    return create_app().openapi()


def test_exactly_the_documented_routes_exist(doc: dict[str, Any]) -> None:
    found = {(m, p) for p, ops in doc["paths"].items() for m in ops}
    assert found == ROUTES


def test_every_operation_has_a_summary_and_typed_error_responses(doc: dict[str, Any]) -> None:
    error_ref = {"$ref": "#/components/schemas/ErrorBody"}
    for path, ops in doc["paths"].items():
        for method, op in ops.items():
            assert op.get("summary"), (method, path)
            if path in ("/healthz", "/readyz"):
                continue
            for status in ("401", "422"):  # never FastAPI's default validation-error shape
                body = op["responses"][status]["content"]["application/json"]["schema"]
                assert body == error_ref, (method, path, status)
    sessions = doc["paths"]["/v1/sessions/{session_id}"]["get"]["responses"]
    assert {"401", "403", "404"} <= set(sessions)
    events = doc["paths"]["/v1/sessions/{session_id}/events"]["post"]["responses"]
    assert {"401", "403", "404", "409", "413", "415", "422", "429"} <= set(events)


def test_the_error_body_is_code_and_message(doc: dict[str, Any]) -> None:
    schema = doc["components"]["schemas"]["ErrorBody"]
    assert set(schema["properties"]) == {"code", "message", "request_id", "details"}
    assert schema["required"] == ["code", "message"]


def test_the_inbound_event_is_a_discriminated_union_of_the_five_types(
    doc: dict[str, Any],
) -> None:
    op = doc["paths"]["/v1/sessions/{session_id}/events"]["post"]
    body = op["requestBody"]["content"]["application/json"]["schema"]
    assert set(body["discriminator"]["mapping"]) == set(INBOUND_TYPES)
    ok = op["responses"]
    assert (
        ok["200"]["content"]["application/json"]["schema"]
        == ok["202"]["content"]["application/json"]["schema"]
    )


def test_the_event_response_carries_the_outbound_union(doc: dict[str, Any]) -> None:
    schema = doc["components"]["schemas"]["EventResponse"]
    items = schema["properties"]["events"]["items"]
    assert set(items["discriminator"]["mapping"]) == set(OUTBOUND_TYPES)
    assert set(schema["required"]) >= {"session_id", "events", "state", "last_seq"}


def test_the_stream_and_the_spec_document_their_media_types(doc: dict[str, Any]) -> None:
    stream = doc["paths"]["/v1/sessions/{session_id}/stream"]["get"]
    assert "text/event-stream" in stream["responses"]["200"]["content"]
    names = {p["name"] for p in stream["parameters"]}
    assert {"Last-Event-ID", "after", "follow", "limit", "timeout"} <= names
    spec = doc["paths"]["/v1/sessions/{session_id}/spec"]["get"]
    assert "text/markdown" in spec["responses"]["200"]["content"]


def test_authentication_is_documented(doc: dict[str, Any]) -> None:
    assert "HTTPBearer" in doc["components"]["securitySchemes"]
    op = doc["paths"]["/v1/sessions"]["post"]
    assert {"HTTPBearer": []} in op["security"]
    names = {p["name"] for p in op["parameters"]}
    assert {"X-Tenant-Id", "X-User-Id", "X-Roles"} <= names
    for path in ("/healthz", "/readyz"):
        assert "security" not in doc["paths"][path]["get"]


def test_the_readiness_503_is_typed(doc: dict[str, Any]) -> None:
    op = doc["paths"]["/readyz"]["get"]
    assert "503" in op["responses"]


def test_the_committed_export_is_current() -> None:
    """``uv run python scripts/export_schemas.py`` rewrites docs/api/openapi.json."""
    fresh = json.dumps(create_app().openapi(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    assert EXPORT.read_text(encoding="utf-8") == fresh, (
        "docs/api/openapi.json is stale: run `uv run python scripts/export_schemas.py`"
    )
