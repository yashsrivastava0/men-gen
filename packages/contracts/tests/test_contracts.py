from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, TypeAdapter, ValidationError

from mde_contracts import calls
from mde_contracts.brief import Brief, BriefRow
from mde_contracts.cards import Card, CardOption
from mde_contracts.events import InboundEvent, OutboundEvent
from mde_contracts.gateway import CallContext, CallSpec, WebSearchTool
from mde_contracts.registry import CALL_IO, call_folder, exported_models, llm_output_models
from mde_contracts.research import ResearchFact, ResearchNote, SourceRef
from mde_contracts.slots import SLOT_IDS, SlotRecord
from mde_contracts.spec import (
    AcceptanceBlock,
    ApiBlock,
    AppBlock,
    AppSpec,
    AssumptionsBlock,
    BehaviorBlock,
    BuildSequence,
    BuildStep,
    DataBlock,
    DesignBlock,
    DesignTokens,
    FlowBlock,
    IntegrationsBlock,
    NavigationBlock,
    NavNode,
    RevisionBlock,
    RolesBlock,
    ScreenBlock,
    SpecHeader,
    TypeStyle,
)

# ------------------------------------------------------------------ registry / schemas


def test_every_exported_model_has_a_json_schema() -> None:
    models = exported_models()
    assert len(models) > 60
    for name, model in models.items():
        schema = model.model_json_schema()
        assert schema, name


def test_call_catalog_is_complete() -> None:
    expected = {
        "C1", "C2", "C3", "C5", "C6", "S1", "C7", "C8", "C9a", "C9b", "C9c",
        "C10", "C11", "C12", "C13", "C14",
    }  # fmt: skip
    assert {cid.split(".")[0] for cid in CALL_IO} == expected
    assert call_folder("C9a.screens_nav") == "c9a_screens_nav"
    assert "S1.web_search" not in llm_output_models()  # free text


def _depth(schema: dict[str, Any], defs: dict[str, Any], seen: tuple[str, ...] = ()) -> int:
    """Nesting depth of objects/arrays (the OpenAI strict limit is 5 levels)."""
    if "$ref" in schema:
        name = schema["$ref"].rsplit("/", 1)[-1]
        if name in seen:  # recursion: not allowed in strict mode either
            return 99
        return _depth(defs[name], defs, (*seen, name))
    kind = schema.get("type")
    if "anyOf" in schema:
        return max(_depth(s, defs, seen) for s in schema["anyOf"])
    if kind == "object" or "properties" in schema:
        if schema.get("additionalProperties") not in (None, False):
            return 99  # open object (dict) is not strict-safe
        props = schema.get("properties", {})
        return 1 + max((_depth(p, defs, seen) for p in props.values()), default=0)
    if kind == "array":
        return _depth(schema.get("items", {}), defs, seen)
    return 0


@pytest.mark.parametrize("call_id", sorted(llm_output_models()))
def test_llm_outputs_are_strict_schema_shaped(call_id: str) -> None:
    model = llm_output_models()[call_id]
    schema = model.model_json_schema()
    depth = _depth(schema, schema.get("$defs", {}))
    assert depth <= 5, f"{call_id} nests {depth} levels (limit 5, 99 = open object/recursion)"


# ------------------------------------------------------------------ events


def test_inbound_union_parses_by_type() -> None:
    ta = TypeAdapter(InboundEvent)
    ev = ta.validate_python({"type": "user_message", "event_id": "evt_X", "text": "hi"})
    assert ev.type == "user_message"
    gd = ta.validate_python(
        {
            "type": "gate_decision",
            "event_id": "e",
            "gate": "brief",
            "decision": "approve",
            "version": 1,
            "hash": "a" * 64,
        }  # fmt: skip
    )
    assert gd.type == "gate_decision"
    with pytest.raises(ValidationError):
        ta.validate_python({"type": "nope", "event_id": "e"})


def test_outbound_union_and_extra_forbidden() -> None:
    ta = TypeAdapter(OutboundEvent)
    ev = ta.validate_python(
        {"type": "assistant_message", "event_id": "evt_Y", "session_id": "ses_X", "text": "Hello"}
    )
    assert ev.seq == 0
    with pytest.raises(ValidationError):
        ta.validate_python(
            {
                "type": "plan_ready",
                "event_id": "e",
                "session_id": "s",
                "version": 1,
                "hash": "h",
                "surprise": 1,
            }  # fmt: skip
        )


# ------------------------------------------------------------------ hashing


def _brief() -> Brief:
    row = BriefRow(key="building", title="What you are building", text="A bakery app",
                   slot_ids=["idea.summary"])  # fmt: skip
    return Brief(version=1, app_summary="A bakery app", rows=[row])


def test_brief_hash_ignores_status_and_timestamps() -> None:
    a = _brief().with_hash()
    b = a.model_copy(update={"status": "approved", "created_at": datetime.now(UTC)})
    assert a.hash and a.hash == b.compute_hash()
    c = a.model_copy(update={"app_summary": "A cafe app"})
    assert c.compute_hash() != a.hash


def test_research_unavailable_note() -> None:
    note = ResearchNote.unavailable("h1", "budget")
    assert note.status == "unavailable" and note.hash and note.unavailable_reason == "budget"
    fact = ResearchFact(id="F-001", job="R1", statement="x",
                        sources=[SourceRef(url="https://a.example", retrieved_at=date(2026, 11, 8))])  # fmt: skip
    assert fact.verified is False


# ------------------------------------------------------------------ slots / cards / calls


def test_slot_ids_and_update_conversion() -> None:
    assert "idea.summary" in SLOT_IDS and len(SLOT_IDS) == len(set(SLOT_IDS))
    assert calls.SlotUpdate(slot_id="scope.features", value_list=["a", "b"]).to_data() == ["a", "b"]
    assert calls.SlotUpdate(slot_id="pay.model", value_text="mock").to_data() == "mock"
    assert calls.SlotUpdate(slot_id="behavior.offline", value_bool=False).to_data() is False
    with pytest.raises(ValidationError):
        calls.SlotUpdate(slot_id="not.a.slot")  # type: ignore[arg-type]
    assert SlotRecord(slot_id="locale").state == "empty"


def test_card_roundtrip() -> None:
    card = Card(id="Q-look-01", slot="design.direction", type="visual3", text="Which look?",
                options=[CardOption(id="A", label="Clean", recommended=True,
                                    sets={"design.direction": "clean-bright"})])  # fmt: skip
    assert Card.model_validate_json(card.model_dump_json()) == card


def test_callspec_rules(tmp_path: Path) -> None:
    kw: dict[str, Any] = dict(
        id="X.test", version=1, input_model=calls.C11Input, output_model=calls.C11Output,
        system_template="s.jinja", user_template="u.jinja", template_root=tmp_path,
        route="fast", reasoning_effort="low", max_output_tokens=100, mock_handler="m:f",
    )  # fmt: skip
    CallSpec(**kw)
    with pytest.raises(ValueError, match="untrusted"):
        CallSpec(**{**kw, "tools": (WebSearchTool(),), "untrusted_inputs": ("facts",)})
    with pytest.raises(ValueError, match="free-text"):
        CallSpec(**{**kw, "output_model": None})
    with pytest.raises(ValueError, match="max_output_tokens"):
        CallSpec(**{**kw, "max_output_tokens": 0})
    CallSpec(**{**kw, "output_model": None, "tools": (WebSearchTool(),)})  # S1 shape is fine


def test_call_context_scopes() -> None:
    ctx = CallContext(request_id="req_1", budget_scope="session:s", purpose="t",
                      extra_scopes=("research:r",))  # fmt: skip
    assert ctx.scopes == ("session:s", "research:r") and not ctx.cancel.cancelled


# ------------------------------------------------------------------ spec aggregate


def _minimal_spec() -> AppSpec:
    tokens = DesignTokens(
        colors_light={"background": "#FFFFFF", "text": "#111111"},
        colors_dark={"background": "#000000", "text": "#EEEEEE"},
        type_scale={"body": TypeStyle(size=16)},
        spacing=[4, 8, 16],
        radius={"md": 12},
        elevation={"card": "0 1 3"},
        motion={"fast": "120ms"},
    )
    screen = ScreenBlock(id="SCR-001", route="/", recipe="list", purpose="Home")
    return AppSpec(
        header=SpecHeader(spec_version=1, app_id="app_X", brief_hash="b"),
        summary="A tiny app.",
        app=AppBlock(name="Tiny", purpose="Demo", audience="Everyone"),
        design=DesignBlock(direction="clean-bright", tokens=tokens),
        navigation=NavigationBlock(
            root=NavNode(
                id="NAV-001",
                route="/",
                kind="stack",
                children=[NavNode(id="NAV-002", route="/", kind="screen", screen="SCR-001")],
            )  # fmt: skip
        ),
        data=DataBlock(entities=[]),
        api=ApiBlock(operations=[]),
        roles=RolesBlock(roles=[]),
        integrations=IntegrationsBlock(items=[]),
        behavior=BehaviorBlock(),
        screens=[screen],
        flows=[
            FlowBlock(
                id="FLW-001",
                name="Main",
                primary=True,
                screens=["SCR-001"],
                start="SCR-001",
                end="SCR-001",
            )
        ],  # fmt: skip
        build=BuildSequence(steps=[BuildStep(id="S0", name="Scaffold", load=["APP", "DESIGN"])]),
        acceptance=AcceptanceBlock(),
        assumptions=AssumptionsBlock(),
        revision=RevisionBlock(version=1),
    )


def test_app_spec_roundtrip_and_hash_is_stable() -> None:
    spec = _minimal_spec()
    again = AppSpec.model_validate_json(spec.model_dump_json())
    assert again == spec
    assert spec.compute_content_hash() == again.compute_content_hash()
    changed = spec.model_copy(update={"summary": "Different."})
    assert changed.compute_content_hash() != spec.compute_content_hash()


def test_id_patterns_reject_bad_ids() -> None:
    with pytest.raises(ValidationError):
        ScreenBlock(id="SCR-1", route="/", recipe="list", purpose="x")  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        BuildStep(id="S11", name="nope")  # type: ignore[arg-type]


def test_models_are_frozen() -> None:
    brief = _brief()
    with pytest.raises(ValidationError):
        brief.version = 2  # type: ignore[misc]
    assert isinstance(brief, BaseModel)
