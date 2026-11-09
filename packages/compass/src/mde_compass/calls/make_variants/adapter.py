"""``MakeVariantsAdapter``: implements the ``VariantMaker`` port over the ``cmp.make_variants`` call.

Maps the domain to the wire input (slot hints, neutral catalog choices, context slots, family hints), runs the
call through ``CallRunner`` (validators and fallback inside) and returns a ``VariantSet``. When fewer than three
valid alternatives exist (the deterministic fallback on a free-text slot), a "Something else" escape with
``value=None`` is appended, so the card always lets the user type their own.
"""

from __future__ import annotations

import json

from pydantic import JsonValue

from mde_compass.calls.answer import text as tx
from mde_compass.calls.answer.adapter import compact_value
from mde_compass.calls.base import CallRunner
from mde_compass.calls.interpret_change.validate import NAME_FIELD, parse_path, value_ok
from mde_compass.calls.make_variants.fallback import fallback
from mde_compass.calls.make_variants.schema import (
    VARIANT_COUNT,
    Choice,
    ContextSlot,
    FamilyHint,
    VariantsIn,
    VariantsOut,
)
from mde_compass.calls.make_variants.spec import SPEC
from mde_compass.calls.make_variants.validate import check_variants
from mde_compass.catalog.loader import Catalog, SlotDef
from mde_compass.engine.ports import TurnContext, Variant, VariantSet

MAX_CONTEXT = 12
MAX_FAMILIES = 2
_LIST_KINDS = ("enum_list", "text_list", "locales")


def slot_for_path(catalog: Catalog, path: str) -> SlotDef | None:
    """The slot a path stands for: by slot id, else by ``brief_path``."""
    if path in catalog.slots:
        return catalog.slots[path]
    return next((s for s in catalog.slots.values() if s.brief_path == path), None)


def _choice_value(slot: SlotDef, raw: str) -> JsonValue | None:
    kind = slot.value_schema.kind
    value: JsonValue = [raw] if kind in _LIST_KINDS else raw
    if kind not in ("text", "enum", *_LIST_KINDS):
        return None
    return value if value_ok(kind, slot.value_schema.options, value) is None else None


def choices_for(slot: SlotDef | None, lang: str) -> list[Choice]:
    """The slot's neutral catalog options whose values are valid for its kind."""
    if slot is None:
        return []
    out: list[Choice] = []
    for opt in slot.fallback_card.options:
        value = _choice_value(slot, opt.value or opt.label.en)
        if value is not None:
            out.append(
                Choice(label=opt.label.get(lang), value_json=json.dumps(value, ensure_ascii=False))
            )
    return out


def family_kind(path: str) -> str:
    """Which family list suits a path: features, roles, integrations, business_models or none."""
    pp = parse_path(path)
    if path == "idea.business_model":
        return "business_models"
    section = pp.section if pp else ""
    return {"scope": "features", "roles": "roles", "integrations": "integrations"}.get(section, "")


def family_hints(catalog: Catalog, path: str, text: str, lang: str) -> list[FamilyHint]:
    """Up to two families whose keywords occur in the context text, with their typical items for ``path``."""
    kind = family_kind(path)
    if not kind:
        return []
    scored: list[tuple[int, FamilyHint]] = []
    for fam in catalog.families.values():
        hits = len(tx.hits(text, tx.norm_all(fam.keywords)))
        if not hits:
            continue
        if kind == "features":
            items = [f.name.get(lang) for f in fam.typical_features]
        elif kind == "roles":
            items = list(fam.typical_roles)
        elif kind == "integrations":
            items = list(fam.typical_integrations)
        else:
            items = list(fam.typical_business_models)
        scored.append((hits, FamilyHint(family=fam.id, kind=kind, items=items)))  # type: ignore[arg-type]
    scored.sort(key=lambda pair: -pair[0])
    return [hint for _, hint in scored[:MAX_FAMILIES]]


def build_input(
    ctx: TurnContext, path: str, current: JsonValue, reason: str, catalog: Catalog
) -> VariantsIn:
    """The wire input for one disliked item."""
    lang = "hi" if ctx.language == "hi" else "en"
    slot = slot_for_path(catalog, path)
    pp = parse_path(path)
    is_record = bool(pp and pp.record and not pp.field and pp.section in NAME_FIELD)
    context: list[ContextSlot] = []
    for view in ctx.slots:
        text = compact_value(view.value)
        if view.state == "empty" or not text or len(context) >= MAX_CONTEXT:
            continue
        known = catalog.slots.get(view.slot_id)
        context.append(
            ContextSlot(
                slot_id=view.slot_id,
                label=known.label.get(lang) if known else view.slot_id,
                value=text,
            )
        )
    current_json = json.dumps(current, ensure_ascii=False)
    hint_text = " ".join([reason, current_json, *(c.value for c in context)])
    return VariantsIn(
        path=path,
        current_value_json=current_json,
        reason=reason,
        language=ctx.language,
        slot_label=slot.label.get(lang) if slot else "",
        value_kind=slot.value_schema.kind if slot else ("record" if is_record else ""),
        options=list(slot.value_schema.options) if slot else [],
        name_field=NAME_FIELD[pp.section] if is_record and pp else "",
        choices=choices_for(slot, lang),
        context=context,
        families=family_hints(catalog, path, hint_text, lang),
    )


class MakeVariantsAdapter:
    """``VariantMaker`` over ``cmp.make_variants``."""

    def __init__(self, runner: CallRunner, catalog: Catalog) -> None:
        self._runner = runner
        self._catalog = catalog

    async def variants(
        self, ctx: TurnContext, *, path: str, current: JsonValue, reason: str
    ) -> VariantSet:
        wire = build_input(ctx, path, current, reason, self._catalog)
        result = await self._runner.run(
            SPEC, wire, session_id=ctx.session_id, tenant_id=ctx.tenant_id
        )
        out = result.output
        if not result.used_fallback and check_variants(wire, out):
            out = fallback(wire)  # the runner already validated; stay safe for other gateways
        return self._to_domain(out, wire, "hi" if ctx.language == "hi" else "en")

    def _to_domain(self, out: VariantsOut, wire: VariantsIn, lang: str) -> VariantSet:
        variants = [
            Variant(
                label=v.label.strip(),
                value=json.loads(v.value_json),
                reason=v.reason.strip(),
                basis=v.basis,
            )
            for v in out.variants[:VARIANT_COUNT]
        ]
        if len(variants) < VARIANT_COUNT:  # the card always lets the user type their own
            variants.append(
                Variant(
                    label=self._catalog.t("ui.something_else", lang),
                    value=None,
                    reason="",
                    basis="suggestion",
                )
            )
        return VariantSet(variants=variants)
