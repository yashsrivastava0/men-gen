"""G2-SUMMARY input and output (portable subset: one string)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from mde_spec_agent.calls.base import CallInput


class W(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NumIn(W):
    name: str
    value: int


class G2Out(W):
    paragraph: str  # ONE paragraph, plain words, no list, no heading, only facts and numbers given in the call


class G2In(CallInput):
    language: str
    numbers: list[NumIn]
    max_words: int = 110  # the paragraph limit, stated in the prompt
    lines: list[
        str
    ]  # what differs from the Brief, the top items to confirm, what is assumed or degraded
