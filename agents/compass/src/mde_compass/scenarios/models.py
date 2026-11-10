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
    "upload",  # {name, text}: a document added mid-interview (planner path)
    "edit_answer",  # {card: first|last|Q-003, value, custom}: edit an earlier answer (planner path)
    "say_many",  # [text, ...]: several typed messages, one turn each
    "answer_texts",  # [text, ...]: typed answers to the cards on screen, one per card, in order
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


class FactsExpect(BaseModel):
    """Checks on the fact ledger (planner path). Texts match statement or quote, case-insensitively."""

    model_config = ConfigDict(extra="forbid")

    count_min: int | None = None  # all facts, whatever their status
    count_max: int | None = None
    active_min: int | None = None
    active_contains: list[str] = Field(default_factory=list)
    active_not_contains: list[str] = Field(default_factory=list)
    superseded_contains: list[str] = Field(default_factory=list)
    superseded_min: int | None = None
    disputed_min: int | None = None
    ids_gapless: bool | None = None  # FCT-001..N with no gap and no reuse
    unverified_max: int | None = None  # facts whose quote was not found in its source
    unverified_min: int | None = None
    none_deleted: bool | None = (
        None  # a fact seen in an earlier step is still there (history is kept)
    )
    coverage: float | None = None  # share of active facts used by a Brief item or marked not_used


class QaExpect(BaseModel):
    """Checks on the question and answer ledger (planner path)."""

    model_config = ConfigDict(extra="forbid")

    shown_min: int | None = None  # questions the person was shown (follow-ups included)
    shown_max: int | None = None
    followups: int | None = None  # exactly this many follow-ups were shown
    followups_max: int | None = None
    no_repeats: bool | None = None  # no two shown questions share wording or intent
    statuses: dict[str, str] = Field(default_factory=dict)  # card id -> status (newest version)
    versions: dict[str, int] = Field(default_factory=dict)  # card id -> how many versions are kept
    dropped_min: int | None = None
    shown_contains: list[str] = Field(default_factory=list)  # some shown question contains the text
    shown_not_contains: list[str] = Field(default_factory=list)
    first_contains: list[str] = Field(default_factory=list)  # the first question shown contains it
    asked_needs: list[str] = Field(default_factory=list)  # some shown question serves this need
    not_asked_needs: list[str] = Field(default_factory=list)  # no shown question serves this need
    first_serves: list[str] = Field(
        default_factory=list
    )  # the first question serves one of these needs
    origins_include: list[str] = Field(default_factory=list)  # planner | followup | fallback
    options_language: str | None = (
        None  # every option label of the last card is in this script (en | hi)
    )
    project_term_share_min: float | None = None  # of the non-universal questions shown


class InterviewExpect(BaseModel):
    """Checks on the audit block ``meta.interview`` and the planner runs (planner path)."""

    model_config = ConfigDict(extra="forbid")

    fallback_count: int | None = None
    stop_reason: list[str] | str | None = None  # contains any of these
    batches_min: int | None = None
    batches_max: int | None = None
    budget: str | None = None
    rejected_min: int | None = None  # questions rejected by code or auditor across all runs
    rejected_codes: list[str] = Field(default_factory=list)  # each appears in some rejection reason
    auditor_rejected_min: int | None = None
    repaired: bool | None = None  # some run was repaired
    fallback_batches: int | None = None  # batches that used the flagged fallback
    backlog_min: int | None = None  # items moved to the backlog
    batch_sizes_max: int | None = None  # no accepted batch is larger than this

    @field_validator("stop_reason")
    @classmethod
    def _listify(cls, v: Any) -> Any:
        return [v] if isinstance(v, str) else v


class CallsExpect(BaseModel):
    """How often the engine called each model call (planner path with a provider fixture or a traced gateway)."""

    model_config = ConfigDict(extra="forbid")

    counts: dict[str, int] = Field(default_factory=dict)  # exact, per call id
    at_least: dict[str, int] = Field(default_factory=dict)
    at_most: dict[str, int] = Field(default_factory=dict)
    modes: dict[str, list[str]] = Field(
        default_factory=dict
    )  # call id -> modes seen, in order (cmp.plan_questions)
    input_contains: dict[str, list[str]] = Field(
        default_factory=dict
    )  # call id -> texts in the LAST input sent
    consumed: bool | None = (
        None  # every scripted answer was used (the runner always checks this at the end)
    )


class BatchExpect(BaseModel):
    """Checks on the last ``question_batch`` event of the step: the batch the person is given (planner path)."""

    model_config = ConfigDict(extra="forbid")

    mode: str | None = None  # initial | followup | replan | deepen | simplify
    total_min: int | None = None
    total_max: int | None = None
    groups_min: int | None = None
    groups_max: int | None = None
    group_size_max: int | None = None
    titles_filled: bool | None = None  # every group has a title
    first_serves: list[str] = Field(
        default_factory=list
    )  # the first question serves one of these needs
    first_contains: list[str] = Field(
        default_factory=list
    )  # ... and its text contains one of these words
    lead: int = 2  # how many of the first questions the two checks below look at
    lead_types_not: list[str] = Field(default_factory=list)  # none of them has one of these types
    lead_serves_only: list[str] = Field(default_factory=list)  # all of them serve only these needs
    types_include: list[str] = Field(
        default_factory=list
    )  # some question of the batch has each type
    texts_contain: list[str] = Field(
        default_factory=list
    )  # some question of the batch contains each text
    texts_not_contain: list[str] = Field(
        default_factory=list
    )  # no question of the batch contains the text
    origins: list[str] = Field(
        default_factory=list
    )  # origins present: planner | followup | fallback
    ui_mode: str | None = None  # one_at_a_time | answer_all


class StructureExpect(BaseModel):
    """What the structure of the app (ADR 0017) must look like after a step: size band, counts, no error from the
    checks of the band, and that the model never locked a record."""

    model_config = ConfigDict(extra="forbid")

    absent: bool | None = None  # no modules, screens, flows or entities at all
    band: str | None = None  # the size band recorded in meta.interview
    source: str | None = None  # tier | document | suggested | user
    modules_min: int | None = None
    screens_min: int | None = None
    screens_max: int | None = None
    flows_min: int | None = None
    entities_min: int | None = None
    no_error_issues: bool | None = None  # the checks of the band find no error-severity issue
    none_locked: bool | None = None  # no structure record is Locked (the model never locks)


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
    # --- the interview memory (planner path) --------------------------------------------------------------
    facts: FactsExpect | None = None
    qa: QaExpect | None = None
    interview: InterviewExpect | None = None
    calls: CallsExpect | None = None
    batch: BatchExpect | None = None
    structure: StructureExpect | None = None
    brief_contains: list[str] = Field(default_factory=list)  # anywhere in the Brief's content
    brief_not_contains: list[str] = Field(default_factory=list)
    domain_facts_contains: list[str] = Field(default_factory=list)  # in a domain_facts row
    event_order: list[str] = Field(
        default_factory=list
    )  # these event types, in this order, in this step
    assumptions_min: int | None = None  # Brief assumptions
    blockers_contain: list[str] = Field(
        default_factory=list
    )  # the last brief_ready blockers mention these

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
    policy: dict[str, Any] = Field(
        default_factory=dict
    )  # InterviewPolicy fields for this scenario only
    planner_known_issue: str | None = None
    planner_known_issue_note: str = ""
    # hand-written model outputs per call id (a file under ``providers/``, relative to the scenario directory).
    # Empty means "providers/<id>.yaml if it exists". Planner path only.
    provider: str = ""
    source_dir: str = Field(
        default="", exclude=True
    )  # set by the loader: where the YAML was read from
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
