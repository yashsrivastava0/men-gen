"""Wire models of ``cmp.compile_brief`` (strict-schema-safe: every field required, no open objects, flat).

Input: the whole active fact ledger plus the slots the call may fill (``targets``). Output: Brief values with the
fact ids that support them, the facts that fit no target (``placements``, they become ``domain_facts`` rows), the
facts that need nothing (``not_used``, each with a reason), a one-line summary and a short pitch.

Free text that carries the user's words travels as a *list of pieces* (``ledger``, ``needs_table``, ``asked_list``,
``qa_compact``, ``draft``): the gateway caps every untrusted input at 20,000 characters, and the ledger must never
be truncated, so ``ledger.chunk_text`` cuts it into pieces that are each wrapped and capped on their own. Slot
values travel as ``value_json`` strings that code validates against the slot's ``value_schema``.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from mde_compass.catalog.loader import Consumer


class Wire(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ------------------------------------------------------------------------------------------------ input
class NeedWire(Wire):
    """What a sufficient value for one target contains (the catalog's ``answer_spec`` text, or "")."""

    need_id: str
    label: str
    sufficient: str


class TargetWire(Wire):
    """One catalog slot the call may fill, and the shape its value must have."""

    path: str  # the slot id, for example ``audience.segments``
    label: str
    value_kind: (
        str  # text | enum | enum_list | text_list | object_list | object | bool | geo | locales
    )
    options: list[str]  # the allowed values (enum kinds, locales), else []
    fields: list[str]  # the allowed keys of an object or an object in a list, else []


class CompileInput(Wire):
    language: str  # "en" | "hi" | "hi-Latn"
    ledger: list[str]  # the ENTIRE active ledger, in pieces; join with a newline. Never truncated.
    needs_table: list[str]  # the engine's status table of the needs, in pieces
    needs: list[NeedWire]  # what a sufficient value of each target contains
    assumptions: list[str]
    asked_list: list[str]
    qa_compact: list[str]
    sections: list[str]  # the Brief sections to build; [] = the whole Brief
    draft: list[str]  # the assembled draft to reconcile (consistency pass), in pieces; [] = none
    targets: list[TargetWire]


# ----------------------------------------------------------------------------------------------- output
class ItemWire(Wire):
    path: str
    value_json: str  # JSON text of the value, in the shape of the target's value_kind
    fact_ids: list[str]  # the facts that support it (FCT-###), at least one
    confidence: float  # 0 to 1


class PlacementWire(Wire):
    """A fact that fits no target: it is kept as a typed detail for the agents that need it."""

    fact_id: str
    topic: str
    consumers: list[Consumer]


class NotUsedWire(Wire):
    fact_id: str
    reason: str


class CompileOutput(Wire):
    items: list[ItemWire]
    placements: list[PlacementWire]
    not_used: list[NotUsedWire]
    summary: str  # one line
    pitch: str  # two or three sentences
