"""The typed back-edge (docs 11 §4): a later stage reopens ONE slot with a single targeted question.

Never a new interview: at most ``max_back_edge_loops`` such questions per session. The card shows what
surprised us (the reason, in the researcher's words), the current value, up to three alternatives from the
variant maker, and "Keep what I said". It lives in the change-variants frame so its answer is applied by the
same path as every other variant pick. Pure functions; the service hook is ``FlowHooks.reopen_slot``.
"""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import JsonValue

from mde_compass.catalog.loader import Catalog, SlotDef
from mde_compass.domain.cards import (
    Card,
    CardOption,
    CardProgress,
    CustomInput,
    SkipSpec,
    YouDecide,
)
from mde_compass.engine.flows import data
from mde_compass.engine.flows.locked_edit import value_text

REASON = "back_edge"
KEEP_ID = "K"


def loops_used(why_records: Sequence[dict[str, JsonValue]]) -> int:
    """How many back-edge questions this session already asked."""
    return sum(1 for why in why_records if why.get("reason") == REASON)


def build_card(
    slot: SlotDef,
    *,
    catalog: Catalog,
    card_id: str,
    reason: str,
    current: JsonValue,
    variants: Sequence[tuple[str, JsonValue, str]],
    language: str,
    frame: str,
) -> Card:
    """The single question. ``variants`` are (label, value, why) alternatives for the slot."""
    t = catalog.t
    options = [
        CardOption(
            id=chr(65 + i), label=label, recommended=i == 0, reason=why, sets={slot.id: value}
        )
        for i, (label, value, why) in enumerate(variants[: data.policy().backedge_max_variants])
    ]
    options.append(CardOption(id=KEEP_ID, label=t("flows.reopen.keep", language)))
    question = t(
        "flows.reopen.question", language,
        reason=reason.strip()[:200], label=slot.label.get(language), current=value_text(current)[:80],
    )  # fmt: skip
    return Card(
        id=card_id, frame=frame, slots=[slot.id], type="choose_one", question=question,
        why_we_ask=t("flows.reopen.why", language), options=options, custom_input=CustomInput(enabled=True),
        you_decide=YouDecide(enabled=False), skip=SkipSpec(enabled=False),
        progress=CardProgress(n=0, of_about=0), language=language, must_confirm=True,
    )  # fmt: skip
