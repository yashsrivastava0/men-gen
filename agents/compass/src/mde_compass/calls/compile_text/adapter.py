"""``CompileTextAdapter``: the ``Compiler`` port over the call (domain <-> wire, validation, fallback)."""

from __future__ import annotations

import hashlib

from mde_compass.calls.base import CallRunner
from mde_compass.calls.compile_text.brief_text import slot_facts
from mde_compass.calls.compile_text.fallback import fallback
from mde_compass.calls.compile_text.schema import (
    DETAIL_CHARS,
    MAX_DETAILS,
    MAX_ITEMS,
    MAX_PITCH_CHARS,
    CompileTextInput,
    CompileTextOutput,
    ItemRef,
    SlotText,
)
from mde_compass.calls.compile_text.spec import SPEC
from mde_compass.calls.compile_text.validators import validate
from mde_compass.catalog.fact_kinds import kind_for
from mde_compass.catalog.loader import Catalog
from mde_compass.domain.brief import Brief
from mde_compass.engine.ports import CompiledText, ItemDescription, TurnContext

# the detail kinds the pitch may lean on, most important first
PITCH_KINDS = ("compliance", "rule", "number", "constraint", "integration_detail")
DIGEST_KEY = "text"


def input_digest(inp: CompileTextInput) -> str:
    """A hash of everything the text is written from: the same slots, items and details give the same text."""
    return hashlib.sha256(inp.model_dump_json().encode()).hexdigest()[:24]


def pitch_details(brief: Brief) -> list[str]:
    rows: list[tuple[int, str, str]] = []
    for f in brief.domain_facts:
        kind = f.kind or kind_for(f.type, f.topics)
        if kind in PITCH_KINDS and f.statement.strip():
            rows.append(
                (PITCH_KINDS.index(kind), f.id, " ".join(f.statement.split())[:DETAIL_CHARS])
            )
    return [text for _, _, text in sorted(rows)[:MAX_DETAILS]]


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
        details=pitch_details(brief),
    )


def _clip(text: str, limit: int) -> str:
    """Cut at a word boundary within the limit (a slightly long pitch is trimmed, not a failure)."""
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0].rstrip(" ,;:-")
    return cut if cut.endswith((".", "!", "?")) else cut + "."


def to_domain(out: CompileTextOutput) -> CompiledText:
    return CompiledText(
        summary=" ".join(out.summary.split()),
        pitch=_clip(out.pitch.strip(), MAX_PITCH_CHARS),
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
        """The summary and pitch. When the Brief was last written from exactly this input (``meta.compile_digests``,
        key ``text``) and already has both, they are returned as they are and no call is spent, so a re-run with
        unchanged slots never rewrites the text (the Brief hash does not churn). The digest is recorded on ``brief``
        when fresh text is written; the caller assigns the returned text to the same Brief."""
        inp = build_input(ctx, brief, self._catalog)
        digest = input_digest(inp)
        if (
            brief.summary.strip()
            and brief.pitch.strip()
            and brief.meta.compile_digests.get(DIGEST_KEY) == digest
        ):
            return CompiledText(summary=brief.summary, pitch=brief.pitch)
        res = await self._runner.run(SPEC, inp, session_id=ctx.session_id, tenant_id=ctx.tenant_id)
        out = res.output
        failed = bool(validate(inp, out))
        if failed:  # belt and braces: never return text that fails the rules
            out = fallback(inp)
        if not (failed or res.used_fallback):
            brief.meta.compile_digests[DIGEST_KEY] = (
                digest  # a flagged fallback is tried again next time
            )
        return to_domain(out)
