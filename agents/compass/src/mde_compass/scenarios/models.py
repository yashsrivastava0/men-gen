"""Typed shape of a scenario YAML file (``SB-<ID>``): steps of user actions with optional expectations."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ACTIONS: tuple[str, ...] = (
    "say",  # typed message
    "say_voice",  # dictated message (input_mode=voice)
    "tap",  # option id (choose_one) or list of ids (pick_many / priority) on the last card
    "tap_label",  # option whose label contains this text (case-insensitive)
    "tap_recommended",
    "answer",  # raw card value (form dict, ordered ids, ...)
    "answer_custom",  # the "Something else" free-text answer to the last card
    "tap_and_custom",  # {option: id | recommended: true, custom: text}: a tap plus typed words in one answer
    "tap_all",  # every option id of the last card (pick_many / priority)
    "tap_first",  # the first n option ids of the last card
    "back_edge",  # {slot, reason}: a later stage reopens one slot (typed back-edge hook: Service.reopen_slot)
    "you_decide",
    "skip",
    "back",
    "undo",
    "edit",  # {path, value}
    "feedback",  # {target, reason, text}
    "decide",  # {asm, decision, value}
    "accept_all",  # accept every proposed assumption (a decide per ASM)
    "fill_required",  # workaround for BUG-1: edit every empty required slot the gate lists (see runner)
    "lookup_add",  # {text} or text
    "lookup_remove",  # {request_id} or {text} (first request whose topic contains it) or omitted (first)
    "approve",
    "approve_stale",
    "command",  # {name} or name
    "switch",  # contributor id
    "pause",
    "resume",
    "drive",  # {mode: tap_recommended|you_decide|skip, until: STATE, max: n}
)


class SlotExpect(BaseModel):
    """Checks on one Brief JSON path (``idea.category``, ``scope``, ``meta.probes``, ...)."""

    model_config = ConfigDict(extra="forbid")

    contains: list[str] | str | None = None  # case-insensitive, over the item's value
    not_contains: list[str] | str | None = None
    contains_any: list[str] | None = None
    # also accepted on the planner path only (it normalizes the user's words to the registry's value, for example
    # "we earn from ads" -> "advertising"); the legacy expectation above stays as it was
    planner_contains_any: list[str] | None = None
    equals: Any = None
    filled: bool | None = None
    status: list[str] | str | None = None
    tier: list[str] | str | None = None
    not_tier: list[str] | str | None = None
    history_min: int | None = None
    provenance: list[str] | str | None = None  # at least one provenance entry of this type
    count_min: int | None = None  # list sections: at least this many entries
    count_max: int | None = None
    match: str | None = (
        None  # with match_min / match_max: how many resolved nodes contain this text
    )
    match_min: int | None = None
    match_max: int | None = None

    @field_validator("contains", "not_contains", "status", "tier", "not_tier", "provenance")
    @classmethod
    def _listify(cls, v: Any) -> Any:
        return [v] if isinstance(v, str) else v


class Expect(BaseModel):
    """What must hold after a step. Texts are the assistant's messages and notices of that step."""

    model_config = ConfigDict(extra="forbid")

    state: list[str] | str | None = None
    card_frame: list[str] | str | None = None  # frame of the last card shown in this step
    card_type: list[str] | str | None = None
    card_language: list[str] | str | None = None
    no_card: bool | None = None
    text_contains: list[str] | str | None = None
    text_not_contains: list[str] | str | None = None
    text_any: list[str] | None = None  # at least one of these appears
    text_not_matches: list[str] = Field(
        default_factory=list
    )  # regexes that must not match (case-insensitive)
    voided_includes: list[str] = Field(
        default_factory=list
    )  # brief_patch.voided_sections must include these
    voided_excludes: list[str] = Field(default_factory=list)  # ... and must not include these
    card_text_contains: list[str] | str | None = (
        None  # in the last card's question and option labels
    )
    card_text_not_contains: list[str] | str | None = None
    event_types: list[str] | None = None  # must all be present among this step's outbound events
    no_event_types: list[str] | None = None
    notice_code: list[str] | str | None = None  # all of these notice codes appear
    notice_code_any: list[str] | None = None
    no_notice_code: list[str] | str | None = None
    max_cards: int | None = None  # question cards shown in this step
    questions_max: int | None = None  # question cards shown in the whole session so far
    approvable: bool | None = None  # the last brief_ready of this step says approvable (or not)
    probes_passed: bool | None = (
        None  # the latest probe_result of research, blueprint and design all passed
    )
    approved: bool | None = None  # a brief_approved event in this step / state APPROVED
    version_increased: bool | None = None  # the Brief version went up during this step
    version_unchanged: bool | None = None
    no_error: bool | None = None  # default True for every step: no `error` events
    slots: dict[str, SlotExpect] = Field(default_factory=dict)

    @field_validator(
        "state",
        "card_frame",
        "card_type",
        "card_language",
        "text_contains",
        "text_not_contains",
        "card_text_contains",
        "card_text_not_contains",
        "notice_code",
        "no_notice_code",
    )
    @classmethod
    def _listify(cls, v: Any) -> Any:
        return [v] if isinstance(v, str) else v


class Step(BaseModel):
    """One user action (exactly one key of ``ACTIONS``) plus optional ``as`` (contributor), ``expect`` and ``note``."""

    model_config = ConfigDict(extra="forbid")

    action: str
    arg: Any = None
    contributor: str | None = None
    expect: Expect | None = None
    note: str = ""
    kind: str = ""  # tap | custom | other: how a card answer was given (filled from the action)

    @classmethod
    def from_raw(cls, raw: dict[str, Any]) -> Step:
        raw = dict(raw)
        expect = raw.pop("expect", None)
        contributor = raw.pop("as", None)
        note = raw.pop("note", "")
        actions = [k for k in raw if k in ACTIONS]
        if len(actions) != 1 or len(raw) != 1:
            raise ValueError(
                f"a step has exactly one action key from {ACTIONS} (plus expect/as/note); got {sorted(raw)}"
            )
        action = actions[0]
        return cls(
            action=action,
            arg=raw[action],
            contributor=contributor,
            expect=Expect.model_validate(expect) if expect else None,
            note=str(note),
        )


class ContributorSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str = ""
    role: str = "requester"
    decision_owner: bool = False


class Scenario(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str  # SB-<ID>
    title: str = ""
    mode: str = "guided"
    locale: str = "en"
    tags: list[str] = Field(default_factory=list)
    contributors: list[ContributorSpec] = Field(default_factory=list)
    known_issue: str | None = None  # BUG-n: strict xfail while the product behaviour is wrong
    known_issue_note: str = ""
    # the question engines the script can run on. A script limited to ``legacy`` asserts the frame queue's own
    # behaviour and says why in ``engines_reason``; ``SB-QP-xx`` scripts run on the planner only.
    engines: list[str] = Field(default_factory=lambda: ["legacy", "planner"])
    engines_reason: str = ""
    # a strict xfail on the planner path only (the legacy path passes): the planner path has a product defect
    planner_known_issue: str | None = None
    planner_known_issue_note: str = ""
    # hand-written model outputs per call id (a file under ``providers/``, relative to the scenario directory).
    # Empty means "providers/<id>.yaml if it exists". Planner path only.
    provider: str = ""
    persona: dict[str, Any] = Field(default_factory=dict)  # eval metadata (kind, flags)
    steps: list[Step]
    final: Expect | None = None  # checked after the last step

    @field_validator("id")
    @classmethod
    def _id(cls, v: str) -> str:
        if not v.startswith("SB-") and not v.startswith("PERSONA-"):
            raise ValueError("scenario ids look like SB-IV-01 or PERSONA-silent")
        return v

    @field_validator("engines")
    @classmethod
    def _engines(cls, v: list[str]) -> list[str]:
        if not v or any(e not in ("legacy", "planner") for e in v):
            raise ValueError("engines is a non-empty list of legacy and planner")
        return v

    @field_validator("mode")
    @classmethod
    def _mode(cls, v: str) -> str:
        if v not in ("guided", "enterprise"):
            raise ValueError("mode is guided or enterprise")
        return v

    @model_validator(mode="before")
    @classmethod
    def _steps(cls, data: Any) -> Any:
        if isinstance(data, dict) and isinstance(data.get("steps"), list):
            data = dict(data)
            data["steps"] = [s if isinstance(s, Step) else Step.from_raw(s) for s in data["steps"]]
        return data
