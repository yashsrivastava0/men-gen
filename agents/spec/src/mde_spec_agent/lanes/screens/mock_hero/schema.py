"""MOCK-HERO input and output. Output: flat values and image briefs in arrays (portable subset, no nullable field).
A value is text for a field code listed as writable; ids, status, dates, relations and shared values are never written by
the model. Image briefs say subject, count and what to avoid; never a URL, never a real brand."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from mde_spec_agent.calls.base import CallInput


class W(BaseModel):
    model_config = ConfigDict(extra="forbid")


class HeroValueOut(W):
    record: str  # a record id from the call (never a new one)
    field: str  # a writable field of that record's entity
    value: str


class HeroBriefOut(W):
    record: str
    subject: str  # what the picture shows
    count: int  # how many pictures
    avoid: list[str]  # what must not appear (brand marks, readable text, real people)


class HeroOut(W):
    values: list[HeroValueOut]
    briefs: list[HeroBriefOut]


class FieldRuleIn(W):
    key: str
    type: str
    rule: str
    allowed: list[str]  # the allowed values of a choice or enum field, else empty


class RecIn(W):
    id: str
    state: str | None
    set_by_code: list[
        str
    ]  # "status=placed", "created_at=now-12d": shown for context, never written by you


class HeroEntityIn(W):
    id: str
    name: str
    klass: str
    statuses: list[str]
    fields: list[FieldRuleIn]  # the fields YOU write, for every record
    derived: list[str]  # filled by code from a parent record: "amount from ENT-006.amount_due"
    media: list[str]  # fields that hold a picture: write one image brief per record
    records: list[RecIn]


class HeroIn(CallInput):
    language: str
    country: str
    profile: str
    facts: str
    glossary: str
    names: list[
        str
    ]  # locale-realistic people and places drawn from the pools: use names like these
    allowed_names: list[str]  # business names the person gave: the only real names that may appear
    entities: list[HeroEntityIn]
    context: list[str]  # parent entities outside this call (read only)
