"""COPY-L input and output (portable subset: one flat array of key and text)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from mde_spec_agent.calls.base import CallInput


class W(BaseModel):
    model_config = ConfigDict(extra="forbid")


class StrIn(W):
    key: str
    text: str


class StrOut(W):
    key: str  # exactly a key of the call, unchanged
    text: str  # the translation, placeholders in braces kept exactly


class CopyLOut(W):
    strings: list[StrOut]


class CopyLIn(CallInput):
    language: str  # the TARGET language
    source_language: str
    glossary: str  # the terms of the app: say them the same way in every string
    facts: str
    strings: list[StrIn]
