"""GLOSS-L input and output (PORTABLE SUBSET: flat closed records in arrays, no optional fields, depth 3).

The glossary is flattened by code into short items (an id and a text each); the model returns one entry per (item,
language). Code re-assembles the glossary from the entries, so the model never sees a structure it could break."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from mde_spec_agent.calls.base import CallInput


class GlossItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str  # g001, g002, ...
    text: str  # a name, a status, a verb, a term or the voice line, as the Brief wrote it


class GlossIn(CallInput):
    source_language: str
    languages: list[str]  # the extra languages
    items: list[GlossItem]  # untrusted: the Brief's own words


class W(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GlossEntry(W):
    id: str
    language: str
    text: str


class GlossOut(W):
    entries: list[GlossEntry]
