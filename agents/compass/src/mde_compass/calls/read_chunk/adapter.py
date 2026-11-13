"""Adapter: the ``ChunkReader`` port over ``cmp.read_chunk`` (domain <-> wire, post-validation)."""

from __future__ import annotations

from mde_compass.calls.base import CallRunner
from mde_compass.calls.read_chunk.rules import load_rules
from mde_compass.calls.read_chunk.schema import ReadChunkInput, WireStatement
from mde_compass.calls.read_chunk.spec import SPEC
from mde_compass.calls.read_chunk.validate import keep_valid
from mde_compass.catalog import Catalog
from mde_compass.domain.claims import Statement
from mde_compass.engine.ports import TurnContext

CONFIDENCE = {"high": 0.9, "medium": 0.6, "low": 0.3}


class ReadChunkAdapter:
    """Implements ``ChunkReader``. Statements whose quote is not in the chunk are dropped (never invents)."""

    def __init__(self, runner: CallRunner, catalog: Catalog) -> None:
        self._runner = runner
        self._catalog = catalog

    async def read(
        self, ctx: TurnContext, *, chunk_text: str, source_id: str, pointer: str
    ) -> list[Statement]:
        if not chunk_text.strip():
            return []
        inp = ReadChunkInput(
            chunk_text=chunk_text,
            pointer=pointer,
            language=ctx.language,
            idea_hint=_idea_hint(ctx),
        )
        res = await self._runner.run(SPEC, inp, session_id=ctx.session_id, tenant_id=ctx.tenant_id)
        return to_statements(chunk_text, pointer, keep_valid(chunk_text, res.output.statements))


def _short_name(w: WireStatement, limit: int) -> str:
    """The model's short name for a requirement or role, cut at a word boundary; empty for every other type."""
    if w.type not in ("requirement", "user_or_role"):
        return ""
    text = " ".join(w.name.split())
    if len(text) <= limit:
        return text
    cut = text[:limit]
    if text[limit] != " ":  # the cut falls inside a word: back up to the last space
        cut = cut.rsplit(" ", 1)[0] or cut
    return cut.rstrip()


def to_statements(chunk_text: str, pointer: str, wire: list[WireStatement]) -> list[Statement]:
    rules = load_rules()
    # ``Statement.name`` (the short name) exists once the lead adds it to the domain model; until then it is dropped
    named = "name" in Statement.model_fields
    return [
        Statement(
            type=w.type,
            text=w.text[: rules.max_text_chars],
            quote=w.quote,
            pointer=pointer,
            confidence=CONFIDENCE[w.confidence],
            **({"name": _short_name(w, rules.max_name_chars)} if named else {}),
        )
        for w in wire
        if w.quote in chunk_text
    ]


def _idea_hint(ctx: TurnContext) -> str:
    for slot in ctx.slots:
        if slot.slot_id == "idea.summary" and isinstance(slot.value, str):
            return slot.value[:200]
    return ""
