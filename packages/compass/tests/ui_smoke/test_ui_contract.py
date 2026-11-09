"""UI contract: what app.js reads must exist in the view-model / card / event schemas it renders."""

from __future__ import annotations

import json
import re
from typing import Any, get_args

import httpx
import pytest
from pydantic import TypeAdapter

from mde_compass.api.settings import ApiSettings
from mde_compass.api.testing import ScriptedService
from mde_compass.app import STATIC_DIR, create_app
from mde_compass.catalog.loader import load_catalog
from mde_compass.domain.cards import CARD_TYPES, Card
from mde_compass.domain.events import (
    INBOUND_ADAPTER,
    OUTBOUND_ADAPTER,
    InboundEvent,
    OutboundEvent,
    StoredEvent,
)
from mde_compass.views.models import BriefViewModel

APP_JS = (STATIC_DIR / "app.js").read_text(encoding="utf-8")


def _contract() -> dict[str, Any]:
    m = re.search(r"/\*CONTRACT\s*(\{.*?\})\s*CONTRACT\*/", APP_JS, flags=re.S)
    assert m, "app.js starts with a CONTRACT block"
    return json.loads(m.group(1))  # type: ignore[no-any-return]


def _deref(node: dict[str, Any], defs: dict[str, Any]) -> dict[str, Any]:
    while True:
        if "$ref" in node:
            node = defs[node["$ref"].rsplit("/", 1)[-1]]
        elif "anyOf" in node:
            for alt in node["anyOf"]:
                if alt.get("type") != "null":
                    node = alt
                    break
        else:
            return node


def _resolve(schema: dict[str, Any], path: str) -> bool:
    defs = schema.get("$defs", {})
    node = _deref(schema, defs)
    for raw in path.split("."):
        token = raw.removesuffix("[]")
        props = node.get("properties", {})
        if token not in props:
            return False
        node = _deref(props[token], defs)
        if raw.endswith("[]"):
            if node.get("type") != "array":
                return False
            node = _deref(node["items"], defs)
    return True


def test_view_model_keys_exist_in_schema() -> None:
    schema = BriefViewModel.model_json_schema()
    keys = _contract()["view_model_keys"]
    assert len(keys) > 30
    missing = [k for k in keys if not _resolve(schema, k)]
    assert not missing, f"app.js reads keys that BriefViewModel does not have: {missing}"


def test_card_and_stored_event_keys_exist_in_schema() -> None:
    contract = _contract()
    card_schema = Card.model_json_schema()
    assert [k for k in contract["card_keys"] if not _resolve(card_schema, k)] == []
    ev_schema = StoredEvent.model_json_schema()
    assert [k for k in contract["stored_event_keys"] if not _resolve(ev_schema, k)] == []


def test_every_contract_key_is_actually_read_by_the_code() -> None:
    body = APP_JS.split("CONTRACT*/", 1)[1]
    contract = _contract()
    for key in contract["view_model_keys"] + contract["card_keys"] + contract["stored_event_keys"]:
        leaf = key.split(".")[-1].removesuffix("[]")
        assert re.search(rf"\.{leaf}\b|[\"']{leaf}[\"']", body), f"{key} is listed but never read"


def test_card_types_cover_card_types_constant() -> None:
    contract = _contract()
    assert set(contract["card_types"]) == set(CARD_TYPES)
    handled = set(
        re.findall(
            r"\b(choose_one|pick_many|priority|confirm|look_signals|quick_form|upload_or_link|open_prompt):\s*card",
            APP_JS,
        )
    )
    assert handled == set(CARD_TYPES), "a render function exists for every card type"


def _union_types(ann: Any) -> set[str]:
    members = get_args(get_args(ann)[0])
    return {get_args(m.model_fields["type"].annotation)[0] for m in members}


def test_event_types_cover_domain_events() -> None:
    contract = _contract()
    inbound = _union_types(InboundEvent)
    outbound = _union_types(OutboundEvent)
    assert set(contract["inbound_events"]) == inbound
    assert set(contract["outbound_events"]) == outbound
    for name in outbound:  # a handler for each outbound event
        assert re.search(rf"\b{name}:\s*function", APP_JS), f"no OUT handler for {name}"
    for name in inbound:  # the UI can send each inbound event
        assert re.search(rf"""type:\s*["']{name}["']""", APP_JS), f"the UI never sends {name}"
    assert INBOUND_ADAPTER and OUTBOUND_ADAPTER and TypeAdapter(InboundEvent)


def _fallback_keys() -> set[str]:
    block = APP_JS.split("var FALLBACK = {", 1)[1].split("};", 1)[0]
    return set(re.findall(r'"([a-z_]+(?:\.[a-z_]+)+)":', block))


def test_strings_used_by_the_ui_exist() -> None:
    strings = load_catalog().strings
    known = set(strings["en"]) | _fallback_keys()
    used = set(re.findall(r"\bt\(\s*\"([a-z_]+\.[a-z_]+(?:\.[a-z_]+)*)\"", APP_JS))
    assert len(used) > 40
    assert sorted(used - known) == []
    assert set(strings["hi"]) == set(strings["en"])
    # strings that need placeholders are filled with the names the catalog declares
    for key, names in {
        "ui.spent_cap": {"spent", "cap"},
        "progress.question_n_of_about": {"n", "total"},
    }.items():
        assert set(re.findall(r"\{(\w+)\}", strings["en"][key])) == names


@pytest.fixture
async def view_json() -> dict[str, Any]:
    app = create_app(ScriptedService(), settings=ApiSettings())
    h = {"Authorization": "Bearer dev-token", "X-Tenant-Id": "t1", "X-User-Id": "u1"}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t", headers=h
    ) as c:
        sid = (await c.post("/v1/compass/sessions", json={})).json()["id"]
        await c.post(
            f"/v1/compass/sessions/{sid}/events",
            json={"type": "user_message", "event_id": "e0", "text": "hi"},
        )
        for i in range(8):
            await c.post(
                f"/v1/compass/sessions/{sid}/events",
                json={"type": "card_skip", "event_id": f"s{i}", "card_id": "x"},
            )
        return (await c.get(f"/v1/compass/sessions/{sid}/brief/view")).json()  # type: ignore[no-any-return]


async def test_served_view_model_has_every_key_the_ui_reads(view_json: dict[str, Any]) -> None:
    vm = BriefViewModel.model_validate(view_json)
    assert vm.sections and vm.sections[0].rows
    for key in _contract()["view_model_keys"]:
        node: Any = [view_json]
        for raw in key.split("."):
            token = raw.removesuffix("[]")
            nxt: list[Any] = []
            for item in node:
                assert isinstance(item, dict) and token in item, f"{key}: {token} missing"
                value = item[token]
                nxt.extend(value if raw.endswith("[]") else [value])
            node = nxt
