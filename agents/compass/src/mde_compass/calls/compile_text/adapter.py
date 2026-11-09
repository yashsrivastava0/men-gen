"""``CompileTextAdapter``: the ``Compiler`` port over the call (domain <-> wire, validation, fallback)."""

from __future__ import annotations

from mde_compass.calls.base import CallRunner
from mde_compass.calls.compile_text.brief_text import slot_facts
from mde_compass.calls.compile_text.fallback import fallback
from mde_compass.calls.compile_text.schema import (
    MAX_ITEMS,
    CompileTextInput,
    CompileTextOutput,
    ItemRef,
    SlotText,
)
from mde_compass.calls.compile_text.spec import SPEC
from mde_compass.calls.compile_text.validators import validate
from mde_compass.catalog.loader import Catalog
from mde_compass.domain.brief import Brief
from mde_compass.engine.ports import CompiledText, ItemDescription, TurnContext


def build_input(ctx: TurnContext, brief: Brief, catalog: Catalog) -> CompileTextInput:
    """The compact wire input: slots as text, counts and at most 40 items to describe."""
    facts = slot_facts(ctx, brief, catalog)
    slots = [
        SlotText(slot_id=f.slot_id, label=f.label, value=f.text, state=f.state)
        for f in facts.values()
    ]
    live = [f for f in brief.scope if f.scope_status == "in"]
    items = (
        [ItemRef(path=f"scope.{f.id}", name=f.name, intent=f.intent) for f in live]
        + [ItemRef(path=f"apps.{a.id}", name=a.name, intent=a.purpose) for a in brief.apps]
        + [
            ItemRef(path=f"roles.{r.id}", name=r.name_in_user_words, intent=r.goal)
            for r in brief.roles
        ]
    )[:MAX_ITEMS]
    return CompileTextInput(
        language=ctx.language,
        slots=slots,
        app_count=len(brief.apps),
        role_count=len(brief.roles),
        feature_count=len(live),
        items=items,
    )


def to_domain(out: CompileTextOutput) -> CompiledText:
    return CompiledText(
        summary=" ".join(out.summary.split()),
        pitch=out.pitch.strip(),
        descriptions=[
            ItemDescription(item_id=d.item_id, text=d.text.strip()) for d in out.descriptions
        ],
    )


class CompileTextAdapter:
    """Implements ``mde_compass.engine.ports.Compiler``."""

    def __init__(self, runner: CallRunner, catalog: Catalog) -> None:
        self._runner = runner
        self._catalog = catalog

    async def compile(self, ctx: TurnContext, brief: Brief) -> CompiledText:
        inp = build_input(ctx, brief, self._catalog)
        res = await self._runner.run(SPEC, inp, session_id=ctx.session_id, tenant_id=ctx.tenant_id)
        out = res.output
        if validate(inp, out):  # belt and braces: never return text that fails the rules
            out = fallback(inp)
        return to_domain(out)
