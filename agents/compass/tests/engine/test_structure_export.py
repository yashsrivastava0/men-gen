"""What a real session gives out about its structure (ADR 0017, S6): the Structure page, the outline, one slice per
module and the full export (format 2). A session without a structure exports exactly what it did before, apart from the
format number; the research view tells the research engine the app size and depth."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from mde_compass.compose import build_service
from mde_compass.config import DEFAULT_CONFIG, CompassConfig
from mde_compass.domain.events import BatchAnswer, Command
from mde_compass.engine.service import Service
from mde_compass.testing import Conversation
from mde_core.clock import FakeClock
from mde_core.errors import NotFound

IDEA = "I want an app for my tiffin delivery service in Pune. Office workers order a daily lunch."
SLICE_BUDGET_TOKENS = 1500


def config(*, enabled: bool) -> CompassConfig:
    return replace(
        DEFAULT_CONFIG, interview=replace(DEFAULT_CONFIG.interview, structure_enabled=enabled)
    )


async def review(*, tier: str | None = None, enabled: bool = True) -> tuple[Service, Conversation]:
    svc = build_service(memory=True, clock=FakeClock(), compass_config=config(enabled=enabled))
    conv = await Conversation.start(svc)
    if tier:
        await conv.send(Command(event_id=conv._id(), name=f"budget_{tier}"))  # type: ignore[arg-type]
    await conv.say(IDEA)
    batch = [e.payload for e in conv.events if e.type == "question_batch"][-1]
    await conv.send(
        BatchAnswer(
            event_id=conv._id(), batch_id=str(batch["batch_id"]), answers=[], decide_rest=True
        )
    )
    return svc, conv


@pytest.mark.parametrize(
    ("tier", "band", "depth"), [("quick", "small", "light"), ("thorough", "large", "deep")]
)
async def test_the_page_the_outline_and_the_slices_agree_with_the_stored_brief(
    tier: str, band: str, depth: str
) -> None:
    svc, conv = await review(tier=tier)
    page = await svc.structure_view(conv.caller, conv.session_id)
    assert page["drafted"] is True and page["band"]["id"] == band  # type: ignore[index]
    assert page["band"]["research_depth"] == depth  # type: ignore[index]
    outline = await svc.structure_outline(conv.caller, conv.session_id)
    counts = outline["counts"]
    assert counts == page["counts"]
    assert counts["screens"] == sum(len(m["screens"]) for m in outline["modules"])  # type: ignore[index,union-attr]
    for m in outline["modules"]:  # type: ignore[union-attr]
        sl = await svc.structure_slice(conv.caller, conv.session_id, m["id"])  # type: ignore[index]
        assert sl["module"]["id"] == m["id"]  # type: ignore[index]
        assert len(json.dumps(sl)) // 4 <= SLICE_BUDGET_TOKENS
        assert {s["id"] for s in sl["screens"]} == {s["id"] for s in m["screens"]}  # type: ignore[index]


async def test_the_page_follows_the_requested_language() -> None:
    svc, conv = await review()
    en = await svc.structure_view(conv.caller, conv.session_id, language="en")
    hi = await svc.structure_view(conv.caller, conv.session_id, language="hi")
    assert en["language"] == "en" and hi["language"] == "hi"
    assert hi["band"]["label"] != en["band"]["label"]  # type: ignore[index]


async def test_an_unknown_module_or_session_is_not_found() -> None:
    svc, conv = await review()
    with pytest.raises(NotFound):
        await svc.structure_slice(conv.caller, conv.session_id, "MOD-999")
    with pytest.raises(NotFound):
        await svc.structure_view(conv.caller, "ses_missing")


async def test_the_export_is_format_two_and_carries_the_structure_next_to_the_views() -> None:
    svc, conv = await review(tier="standard")
    data = await svc.export_full(conv.caller, conv.session_id)
    head = data["export"]
    assert head["format_version"] == 2 and "structure" in head["reading_guide"]  # type: ignore[index,operator]
    structure = data["structure"]
    assert structure["band"] == "medium" and structure["source"] == "tier"  # type: ignore[index]
    brief = data["brief"]
    assert structure["counts"] == {  # type: ignore[index]
        "modules": len(brief["modules"]),  # type: ignore[index]
        "screens": len(brief["screens"]),  # type: ignore[index]
        "flows": len(brief["flows"]),  # type: ignore[index]
        "entities": len(brief["entities"]),  # type: ignore[index]
    }
    assert [s["module"]["id"] for s in structure["slices"]] == [m["id"] for m in brief["modules"]]  # type: ignore[index]
    assert structure["research"]["depth"] == "standard"  # type: ignore[index]
    views = data["consumer_views"]
    assert views["blueprint"]["structure"]["outline"] == structure["outline"]  # type: ignore[index]
    assert views["research"]["guidance"]["size_band"] == "medium"  # type: ignore[index]
    assert views["design"]["screens"] and views["verify"]["flows"]  # type: ignore[index]
    json.dumps(data)  # one plain JSON file the owner can download


async def test_a_session_without_a_structure_exports_no_structure_and_no_guidance() -> None:
    svc, conv = await review(enabled=False)
    data = await svc.export_full(conv.caller, conv.session_id)
    assert data["export"]["format_version"] == 2  # type: ignore[index]
    assert "structure" not in data
    for key in ("modules", "screens", "flows", "entities"):
        assert key not in data["brief"]  # type: ignore[operator]
    views = data["consumer_views"]
    assert "guidance" not in views["research"]  # type: ignore[operator,index]
    assert "structure" not in views["blueprint"] and "screens" not in views["design"]  # type: ignore[operator,index]


async def test_the_structure_page_of_a_session_without_a_draft_is_empty_and_says_so() -> None:
    svc, conv = await review(enabled=False)
    page = await svc.structure_view(conv.caller, conv.session_id)
    assert page["drafted"] is False and page["enabled"] is False
    assert page["modules"] == [] and page["issues"] == []
    assert (await svc.structure_outline(conv.caller, conv.session_id))["modules"] == []
