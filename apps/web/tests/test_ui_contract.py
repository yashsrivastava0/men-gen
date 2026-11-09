"""UI contract: what compass.js reads must exist in the view-model / card / event schemas it renders.

The one place where the console is checked against the agent's own models (test-only imports); the
console code itself imports nothing and reaches the agent through its HTTP API only.
"""

from __future__ import annotations

import inspect
import json
import re
from pathlib import Path
from typing import Any, get_args

import httpx
from pydantic import TypeAdapter

from mde_compass.catalog.loader import load_catalog
from mde_compass.domain.cards import CARD_TYPES, Card
from mde_compass.domain.events import (
    INBOUND_ADAPTER,
    OUTBOUND_ADAPTER,
    CommandName,
    InboundEvent,
    OutboundEvent,
    QuestionBatch,
    StoredEvent,
)
from mde_compass.domain.interview import FactStatus
from mde_compass.engine.service import Service
from mde_compass.views.models import BriefViewModel

CONSOLE = Path(__file__).resolve().parents[1] / "console"
APP_JS = (CONSOLE / "agents" / "compass" / "compass.js").read_text(encoding="utf-8")
HELPER_JS = (CONSOLE / "agents" / "compass" / "interview.js").read_text(encoding="utf-8")
ALL_JS = APP_JS + "\n" + HELPER_JS  # the screen script and the helpers it loads first


def _contract() -> dict[str, Any]:
    m = re.search(r"/\*CONTRACT\s*(\{.*?\})\s*CONTRACT\*/", APP_JS, flags=re.S)
    assert m, "compass.js starts with a CONTRACT block"
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
    assert not missing, f"compass.js reads keys that BriefViewModel does not have: {missing}"


def test_card_and_stored_event_keys_exist_in_schema() -> None:
    contract = _contract()
    card_schema = Card.model_json_schema()
    assert [k for k in contract["card_keys"] if not _resolve(card_schema, k)] == []
    ev_schema = StoredEvent.model_json_schema()
    assert [k for k in contract["stored_event_keys"] if not _resolve(ev_schema, k)] == []


def test_batch_keys_exist_in_the_question_batch_schema() -> None:
    schema = QuestionBatch.model_json_schema()
    keys = _contract()["batch_keys"]
    assert "ui_mode" in keys and "groups[].cards" in keys
    assert [k for k in keys if not _resolve(schema, k)] == []


def test_qa_item_keys_are_the_ones_the_service_puts_in_the_list() -> None:
    """GET /qa is a plain dict (no model), so its keys are pinned to the code that builds it."""
    source = inspect.getsource(Service.qa_list)
    keys = _contract()["qa_item_keys"]
    assert "facts[].statement" in keys
    for key in keys:
        leaf = key.split(".")[-1].removesuffix("[]")
        if key == "facts[].status":
            assert '"status": fact.status' in source
        else:
            assert f'"{leaf}"' in source, f"qa_list no longer returns {key}"


def test_command_names_the_ui_sends_are_server_commands() -> None:
    names = _contract()["command_names"]
    assert set(names) <= set(get_args(CommandName))
    assert {"ask_more", "mode_answer_all", "mode_one_at_a_time"} <= set(names)
    assert {"budget_quick", "budget_standard", "budget_thorough"} <= set(names)
    for name in names:
        assert re.search(rf"""["']{name}["']""", ALL_JS.split("CONTRACT*/", 1)[1]), (
            f"the UI never sends the {name} command"
        )


def test_every_contract_key_is_actually_read_by_the_code() -> None:
    body = ALL_JS.split("CONTRACT*/", 1)[1]
    contract = _contract()
    keys = (
        contract["view_model_keys"]
        + contract["card_keys"]
        + contract["stored_event_keys"]
        + contract["batch_keys"]
        + contract["qa_item_keys"]
    )
    for key in keys:
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
    server_made = {
        "upload_added"
    }  # the upload endpoint (multipart) makes this event, the UI sends the file
    assert "/uploads" in APP_JS and "FormData" in APP_JS
    for name in inbound - server_made:  # the UI can send each other inbound event
        assert re.search(rf"""type:\s*["']{name}["']""", APP_JS), f"the UI never sends {name}"
    assert INBOUND_ADAPTER and OUTBOUND_ADAPTER and TypeAdapter(InboundEvent)


def _fallback_keys() -> set[str]:
    block = APP_JS.split("var FALLBACK = {", 1)[1].split("};", 1)[0]
    return set(re.findall(r'"([a-z_]+(?:\.[a-z0-9_]+)+)":', block))


def test_strings_used_by_the_ui_exist() -> None:
    strings = load_catalog().strings
    known = set(strings["en"]) | _fallback_keys()
    used = set(re.findall(r"\bt\(\s*\"([a-z_]+\.[a-z0-9_]+(?:\.[a-z0-9_]+)*)\"", ALL_JS))
    assert len(used) > 40
    assert sorted(used - known) == []
    assert set(strings["hi"]) == set(strings["en"])
    # strings that need placeholders are filled with the names the catalog declares
    for key, names in {
        "ui.spent_cap": {"spent", "cap"},
        "progress.question_n_of_about": {"n", "total"},
    }.items():
        assert set(re.findall(r"\{(\w+)\}", strings["en"][key])) == names


def _fallback_strings() -> dict[str, str]:
    block = APP_JS.split("var FALLBACK = {", 1)[1].split("};", 1)[0]
    return dict(re.findall(r'"([a-z_]+(?:\.[a-z0-9_]+)+)":\s*"((?:[^"\\]|\\.)*)"', block))


def test_every_interview_string_exists_in_both_languages_and_the_embedded_english() -> None:
    strings = load_catalog().strings
    embedded = _fallback_strings()
    keys = {k for k in strings["en"] if k.startswith("ui.interview.")}
    assert len(keys) >= 40
    assert keys == {k for k in strings["hi"] if k.startswith("ui.interview.")}
    assert keys == {k for k in embedded if k.startswith("ui.interview.")}
    for key in keys:
        assert embedded[key] == strings["en"][key], f"{key}: the embedded copy drifted from en.json"
    # any ui.interview key the code names, even outside a t("...") call, is a real string
    named = set(re.findall(r"[\"'](ui\.interview\.[a-z_]+)[\"']", ALL_JS))
    assert sorted(named - keys) == []
    assert keys <= named | {k for k in keys if k.startswith("ui.interview.fact_")}, (
        "an interview string nothing uses: " + str(sorted(keys - named))
    )
    for status in get_args(FactStatus):  # interview.js tags the facts that no longer count
        if status != "active":
            assert f"ui.interview.fact_{status}" in keys


def test_every_v2_string_exists_in_both_languages_and_the_embedded_english() -> None:
    """Compass v2 copy (``ui.v2.*``) is new-keys-only: en, hi and the page's embedded English stay equal, and a
    key nothing uses is dead copy."""
    strings = load_catalog().strings
    embedded = _fallback_strings()
    keys = {k for k in strings["en"] if k.startswith("ui.v2.")}
    assert len(keys) >= 10
    assert keys == {k for k in strings["hi"] if k.startswith("ui.v2.")}
    assert keys == {k for k in embedded if k.startswith("ui.v2.")}
    for key in keys:
        assert embedded[key] == strings["en"][key], f"{key}: the embedded copy drifted from en.json"
        assert strings["hi"][key] != strings["en"][key], f"{key}: not translated"
        assert set(re.findall(r"\{(\w+)\}", strings["hi"][key])) == set(
            re.findall(r"\{(\w+)\}", strings["en"][key])
        ), f"{key}: placeholders differ between en and hi"
    named = set(re.findall(r"[\"'](ui\.v2\.[a-z0-9_]+)[\"']", ALL_JS))
    assert sorted(named - keys) == [], "the code names a ui.v2 string that does not exist"
    assert sorted(keys - named) == [], "a ui.v2 string nothing uses"


def test_t_calls_fill_exactly_the_placeholders_of_their_strings() -> None:
    pool = {**_fallback_strings(), **load_catalog().strings["en"]}
    call = re.compile(r"""\bt\(\s*"([a-z_]+(?:\.[a-z0-9_]+)+)"\s*,\s*\{(.*?)\}\s*\)""", re.S)
    seen = 0
    for key, inner in call.findall(ALL_JS):
        names = set(re.findall(r"(?:^|,)\s*(\w+)\s*:", inner))
        assert names == set(re.findall(r"\{(\w+)\}", pool[key])), f"t({key!r}) fills {names}"
        seen += 1
    assert seen > 15


async def _served_view(client: httpx.AsyncClient) -> dict[str, Any]:
    sid = (await client.post("/v1/compass/sessions", json={})).json()["id"]
    await client.post(
        f"/v1/compass/sessions/{sid}/events",
        json={"type": "user_message", "event_id": "e0", "text": "hi"},
    )
    for i in range(8):
        await client.post(
            f"/v1/compass/sessions/{sid}/events",
            json={"type": "card_skip", "event_id": f"s{i}", "card_id": "x"},
        )
    return (await client.get(f"/v1/compass/sessions/{sid}/brief/view")).json()  # type: ignore[no-any-return]


async def test_served_view_model_has_every_key_the_ui_reads(client: httpx.AsyncClient) -> None:
    view_json = await _served_view(client)
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
