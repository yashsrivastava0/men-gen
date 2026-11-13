"""BEHAV input and output (portable subset: flat records in one array, no nullable field)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from mde_spec_agent.calls.base import CallInput


class W(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BehOut(W):
    category: str  # exactly one of the categories asked for
    text: str  # what the app does, in plain words; no layout, no widgets
    basis: list[
        str
    ]  # ids from this call that ground it (INT-, ENT-, ROL-, RUL-, FTR-); empty when assumed
    assumed: bool  # true when nothing in the data grounds it


class BehavOut(W):
    behaviors: list[BehOut]


class CatIn(W):
    name: str
    what: str
    supplied_by: str  # app_specific | mixed
    library_text: str  # the library's default for a mixed category (extend it, keep its meaning); empty if none
    existing_id: str | None
    existing_text: str  # what code wrote so far (improve it)


class IntegIn(W):
    id: str
    name: str
    provider_side: bool
    flow: str
    interruption: str
    pending_timeout: str
    events: list[str]


class BehavIn(CallInput):
    language: str
    profile: str
    facts: str
    glossary: str
    categories: list[CatIn]
    integrations: list[IntegIn]
    roles: list[str]  # "ROL-001 Buyer"
    entities: list[str]  # "ENT-006 Order (primary)"
    rules: list[str]  # "RUL-001 subject: statement" (confirm rules first)
    languages: list[str]
