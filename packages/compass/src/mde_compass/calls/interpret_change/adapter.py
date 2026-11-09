"""``InterpretChangeAdapter``: implements the ``ChangeInterpreter`` port over ``cmp.interpret_change``.

It maps the domain to the wire input (a digest of the targeted Brief items plus a history slice), runs the
call through ``CallRunner`` (validators and fallback inside), and maps the result to a ``ChangeProposal``.
It never applies anything. ``touches_locked`` is computed here from the digest tiers, whatever the model said.
"""

from __future__ import annotations

import json

from pydantic import JsonValue

from mde_compass.calls.base import CallRunner
from mde_compass.calls.interpret_change.digest import (
    add_sections_for,
    locked_paths,
    target_items,
)
from mde_compass.calls.interpret_change.fallback import fallback
from mde_compass.calls.interpret_change.schema import (
    MAX_HISTORY,
    ChangeIn,
    ChangeOut,
    DigestItem,
    HistLine,
)
from mde_compass.calls.interpret_change.spec import SPEC
from mde_compass.calls.interpret_change.validate import check_change, owner_item
from mde_compass.catalog.loader import Catalog
from mde_compass.domain.brief import Brief
from mde_compass.domain.changes import PatchOp
from mde_compass.engine.ports import ChangeInput, ChangeProposal, HistoryLine, TurnContext

MAX_LINE_CHARS = 300


def history_lines(lines: list[HistoryLine]) -> list[HistLine]:
    """The last ``MAX_HISTORY`` lines of the history slice, texts clipped, values as JSON text."""
    return [
        HistLine(
            turn_id=h.turn_id,
            role=h.role,
            text=h.text[:MAX_LINE_CHARS],
            path=h.path,
            value_json=None
            if h.value is None
            else json.dumps(h.value, ensure_ascii=False)[:MAX_LINE_CHARS],
        )
        for h in lines[-MAX_HISTORY:]
    ]


def build_input(ctx: TurnContext, change: ChangeInput, brief: Brief, catalog: Catalog) -> ChangeIn:
    """The wire input: request text, resolved target, history slice and the digest of the targeted items."""
    lang = "hi" if ctx.language == "hi" else "en"
    return ChangeIn(
        text=change.text,
        language=ctx.language,
        target_kind=change.target.kind,
        target_id=change.target.id,
        reason=change.reason,
        history=history_lines(change.history_slice),
        items=target_items(brief, change.target, catalog, lang),
        add_sections=add_sections_for(change.target, catalog),
    )


def _old_value(path: str, items: dict[str, DigestItem]) -> JsonValue:
    """The current value of ``path`` from the digest (code is authoritative, not the model)."""
    owner = owner_item(items, path)
    if owner is None:
        return None
    value: JsonValue = json.loads(owner.value_json)
    if path == owner.path:
        return value
    field = path.rsplit(".", 1)[-1]
    return value.get(field) if isinstance(value, dict) else None


def to_proposal(out: ChangeOut, wire: ChangeIn) -> ChangeProposal:
    """Map a validated wire output to the domain proposal; ``touches_locked`` comes from the digest tiers."""
    items = {i.path: i for i in wire.items}
    ops: list[PatchOp] = []
    for o in out.ops:
        value: JsonValue = None if o.op in ("remove", "clear") else json.loads(o.value_json)
        old = None if o.op == "add" else _old_value(o.path, items)
        ops.append(PatchOp(op=o.op, path=o.path, value=value, old_value=old))
    return ChangeProposal(
        understood=out.understood,
        ops=ops,
        clarification=out.clarification.strip(),
        options=[o.strip() for o in out.options],
        touches_locked=locked_paths([(o.op, o.path) for o in out.ops], wire.items),
        confidence=out.confidence,
    )


class InterpretChangeAdapter:
    """``ChangeInterpreter`` over ``cmp.interpret_change``."""

    def __init__(self, runner: CallRunner, catalog: Catalog) -> None:
        self._runner = runner
        self._catalog = catalog

    async def interpret(
        self, ctx: TurnContext, change: ChangeInput, brief: Brief
    ) -> ChangeProposal:
        wire = build_input(ctx, change, brief, self._catalog)
        result = await self._runner.run(
            SPEC, wire, session_id=ctx.session_id, tenant_id=ctx.tenant_id
        )
        out = result.output
        if check_change(wire, out):  # the runner already validated; stay safe for other gateways
            out = fallback(wire)
            if check_change(wire, out):
                out = ChangeOut(
                    understood=False,
                    ops=[],
                    clarification=self._catalog.t("fallback.not_sure_rephrase", wire.language),
                    options=[],
                    confidence=0.0,
                )
        try:
            return to_proposal(out, wire)
        except (ValueError, TypeError, KeyError):
            return to_proposal(fallback(wire), wire)
