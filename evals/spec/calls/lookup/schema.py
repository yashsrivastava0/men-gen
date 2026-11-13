"""Input and output of the library-lookup call (portable subset)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from mde_spec_agent.calls.base import CallInput


class LookupIn(CallInput):
    library: str  # the catalogue's name
    catalogue: list[
        str
    ]  # one line per component: "Name: what it is for" (public documentation, DATA)
    items: list[str]  # one line per need: "ID: description" (pack text, DATA)


class W(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MatchOut(W):
    item: str  # the item id, word for word
    candidates: list[
        str
    ]  # up to three component names from the catalogue, best first; empty when it is a gap
    gap: bool  # true when NO component of this catalogue can show it
    gap_note: str  # what is missing, or an empty string


class LookupOut(W):
    matches: list[MatchOut]  # exactly one per item
