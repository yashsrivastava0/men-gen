"""Wire models of ``cmp.compile_brief`` (strict-schema-safe: every field required, no open objects, flat).

Input: the facts of ONE section group (v2: the engine runs one call per group, in parallel) plus the slots the call
may fill (``targets``), the Brief items that already exist and the project card. Output: Brief values with the fact
ids that support them (``items``), single list elements to add to a list target (``additions``), the facts that fit no
target (``placements``, each with its ``kind`` and the records it ``refs``; they become ``domain_facts`` rows), the
facts that need nothing (``not_used``, each with a reason), typical details nobody asked about (``typical``, task
``typical`` only), a one-line summary and a short pitch.

Free text that carries the user's words travels as a *list of pieces* (``ledger``, ``needs_table``, ``asked_list``,
``qa_compact``, ``draft``): the gateway caps every untrusted input at 20,000 characters, and the ledger must never
be truncated, so ``ledger.chunk_text`` cuts it into pieces that are each wrapped and capped on their own. Slot
values travel as ``value_json`` strings that code validates against the slot's ``value_schema``.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

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
    filled: str = (
        ""  # "" = empty; "assumed" = only a guess so far; "user" = the person already gave a value
    )


class KindWire(Wire):
    """One fact kind (``catalog/fact_kinds.yaml``): the key the model returns and what it means."""

    key: str
    description: str


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
    # v2 (ADR 0016)
    task: Literal["fill", "typical"] = "fill"
    project: list[str] = Field(default_factory=list)  # the project card, in pieces
    existing: list[str] = Field(
        default_factory=list
    )  # ``FTR-001 | scope | Name`` lines of what the Brief holds
    kinds: list[KindWire] = Field(default_factory=list)  # the fact kinds a placement may use


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
    kind: str = ""  # one of the offered kinds (rule, number, screen, ...); code derives it when it is not valid
    refs: list[str] = Field(
        default_factory=list
    )  # ids from "already in the Brief" or exact names added by this answer


class AdditionWire(Wire):
    """One ELEMENT to add to a list target the Brief already has (a feature, role or term the person mentioned)."""

    path: str  # a list target
    value_json: str  # JSON text of ONE element: an object (offered keys only) or a string
    fact_ids: list[str]  # the facts that support it, at least one
    confidence: float  # 0 to 1


class TypicalWire(Wire):
    """A detail apps of this kind usually need, that the person did not mention (a proposal, never a fact)."""

    claim: str  # one plain sentence in the person's language
    reason: str  # why apps like this usually need it, one short sentence
    kind: str  # one of the offered kinds
    risky: bool  # true only for money, personal or health data, a legal duty or security
    what_changes: str  # one line: what the app does differently if the person says no


class NotUsedWire(Wire):
    fact_id: str
    reason: str


class CompileOutput(Wire):
    items: list[ItemWire]
    placements: list[PlacementWire]
    not_used: list[NotUsedWire]
    summary: str  # one line
    pitch: str  # two or three sentences
    additions: list[AdditionWire] = Field(default_factory=list)
    typical: list[TypicalWire] = Field(default_factory=list)  # at most 12; task ``typical`` only
