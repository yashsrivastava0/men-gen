"""Live batches E, F and G of the AI-planned interview (build prompt 02, phase 7), and their offline twin.

    uv run python -m mde_compass.live_batches --batch E --dry-run      # the plan and the expected cost, no calls
    uv run python -m mde_compass.live_batches --batch E                # the real batch (needs LLM_MODE=live)
    uv run python -m mde_compass.live_batches --batch F --mock         # the same code, offline and free

The batches drive the REAL Compass service (``compose.build_service``, planner engine forced, in-memory store, so
no database is needed) through scripted sessions and write a JSON report to ``agents/compass/evals/reports/``
(gitignored). Scripts, answer pools and thresholds are data (``live_data/*.yaml``); this module holds the logic:

* **Preflight** (``preflight``, a pure function): refuses, with the reasons in plain words, unless
  ``LLM_MODE=live`` is in the operator's environment, the gateway ledger exists, and Compass's spend plus the allowance
  fits under its ceiling (95% stop line, the reserve kept unspent).
* **Plan**: what runs, how many calls, the expected cost and the worst-case ceiling, printed before any call.
* **Allowance stop**: after every turn the gateway ledger delta (not an estimate) is read; at the allowance the run
  stops cleanly and still writes the report (``stopped: allowance``).
* **Answer policy** (``AnswerPolicy``): the model plans the questions, so answers cannot be fixed per question; the
  policy answers by card type from the data pools, mixes in skips and "you decide", and plants a needle, a
  contradiction and a pivot on the listed turns.
* **Checks**: computed in code from what was stored (questions, facts, the Brief), each with a value, a threshold
  and pass, fail or "report only".

Nothing here switches live mode on: the mode comes from the process environment
(``docs/runbooks/live-llm-budget.md``). ``--mock`` forces mock settings and cannot make a live call.
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import itertools
import json
import os
import sys
import textwrap
import time
from collections import Counter
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import ROUND_CEILING, Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Literal, cast

import yaml
from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError, model_validator

from mde_compass.catalog import Catalog
from mde_compass.compose import build_service
from mde_compass.config import (
    CALL_PROFILES,
    DEFAULT_CONFIG,
    MODEL_ID,
    BudgetPolicy,
    CompassConfig,
    load_budget_policy,
)
from mde_compass.domain import Brief, Card, StoredEvent
from mde_compass.domain.common import EVIDENCE_TYPES
from mde_compass.domain.interview import BatchRecord, Fact, NeedStatus, QaRecord
from mde_compass.engine import needs as needs_mod
from mde_compass.engine import quotes
from mde_compass.engine.brief_builder import slots_from_brief
from mde_compass.engine.interview_support import counts_by_need
from mde_compass.engine.question_validators import (
    DOMAIN_PREFIX,
    cross_idea_overlap_pairs,
    is_universal,
    untagged,
)
from mde_compass.engine.service import Service
from mde_compass.engine.slots import SlotStore
from mde_compass.engine.terms import contains_project_term, extract_terms, jaccard, project_terms
from mde_compass.ledger_spend import compass_spent_micros
from mde_compass.store.ports import CallLogEntry
from mde_compass.testing import Conversation
from mde_core.clock import Clock
from mde_core.money import micros_to_usd, token_cost_micros, usd_to_micros
from mde_core.settings import load_settings
from mde_llm_gateway.pricing import ModelPrice, Pricing

PKG_DIR = Path(__file__).resolve().parent
DATA_DIR = PKG_DIR / "live_data"
REPORT_DIR = PKG_DIR.parents[1] / "evals" / "reports"  # agents/compass/evals/reports (gitignored)
BEFORE_EVIDENCE = (
    PKG_DIR.parents[3] / "docs" / "compass" / "evidence" / "before-first-questions.json"
)

BATCH_IDS: tuple[str, ...] = ("E", "F", "G")
OPEN_STATES: frozenset[str] = frozenset({"INTERVIEWING", "PLAYBACK", "UNDERSTANDING", "INTAKE"})
REVIEW_STATES: frozenset[str] = frozenset({"BRIEF_REVIEW", "APPROVED"})
TEXT_CARD_TYPES: frozenset[str] = frozenset({"open_prompt", "quick_form", "upload_or_link"})
TRUTHY: frozenset[str] = frozenset({"1", "true", "yes", "on", "t", "y"})
INIT_COMMAND = "uv run python -m mde_llm_gateway.budget_cli init"
REPO_TRIPWIRE_FRACTION = Decimal("0.80")  # the gateway's must-run tripwire: stay below it
DEFAULT_REPO_CAP_MICROS = 6_000_000

SpendReader = Callable[[], int]
ServiceFactory = Callable[[], Service]


# ===================================================================================================== data
class _Data(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PoolLines(_Data):
    specific: tuple[str, ...] = ()
    vague: tuple[str, ...] = ()


class Pools(_Data):
    by_need: dict[str, PoolLines] = Field(default_factory=dict)
    generic: PoolLines = Field(default_factory=PoolLines)


class Needle(_Data):
    turn: int = Field(ge=1)
    text: str = Field(min_length=1)
    probe_terms: tuple[str, ...] = Field(min_length=1)
    bonus_terms: tuple[str, ...] = ()


class Contradiction(_Data):
    anchor_turn: int = Field(ge=1)
    anchor_text: str = Field(min_length=1)
    anchor_terms: tuple[str, ...] = Field(min_length=1)
    correction_turn: int = Field(ge=1)
    correction_text: str = Field(min_length=1)
    correction_terms: tuple[str, ...] = Field(min_length=1)


class Pivot(_Data):
    turn: int = Field(ge=1)
    text: str = Field(min_length=1)
    terms: tuple[str, ...] = Field(min_length=1)


class Document(_Data):
    filename: str = Field(min_length=1)
    text: str = Field(min_length=1)


class SessionDef(_Data):
    """One scripted session: an idea (or a document), how to answer, and what is planted."""

    id: str
    kind: Literal["first_batch", "interview"]
    label: str
    idea: str = ""
    budget: Literal["quick", "standard", "thorough"] | None = None
    start_locale: str = "en"
    expect_language: tuple[str, ...] = ()
    max_actions: int = Field(default=40, ge=1)
    max_answers: int | None = Field(default=None, ge=1)
    # "Ask me more" presses when the engine ends the interview before the planted turns are reached
    ask_more_max: int = Field(default=0, ge=0)
    tail: Literal["stop", "you_decide"] = (
        "stop"  # after max_answers: stop, or tire out (the fatigue rule)
    )
    qa_range: tuple[int, int] | None = None
    text_on_choice_every: int = Field(default=0, ge=0)
    answer_cycle: tuple[Literal["specific", "vague"], ...] = ("specific",)
    skip_turns: tuple[int, ...] = ()
    you_decide_turns: tuple[int, ...] = ()
    needle: Needle | None = None
    contradiction: Contradiction | None = None
    pivot: Pivot | None = None
    document: Document | None = None
    pools: Pools = Field(default_factory=Pools)
    expected_calls: dict[str, int] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _consistent(self) -> SessionDef:
        if self.kind == "first_batch" and not self.idea.strip():
            raise ValueError(f"{self.id}: a first_batch session needs an idea")
        if self.kind == "interview":
            if not (self.idea.strip() or self.document):
                raise ValueError(f"{self.id}: an interview needs an idea or a document")
            if not self.pools.generic.specific or not self.pools.generic.vague:
                raise ValueError(f"{self.id}: the generic pool needs specific and vague lines")
            if not self.answer_cycle:
                raise ValueError(f"{self.id}: answer_cycle must not be empty")
        planted: list[int] = []
        if self.needle:
            planted.append(self.needle.turn)
        if self.contradiction:
            planted += [self.contradiction.anchor_turn, self.contradiction.correction_turn]
        if len(set(planted)) != len(planted):
            raise ValueError(f"{self.id}: planted turns must be different from each other")
        quiet = sorted({*self.skip_turns, *self.you_decide_turns})
        if any(b - a < 2 for a, b in itertools.pairwise(quiet)):
            raise ValueError(f"{self.id}: skips and you-decide turns must not be consecutive")
        return self


class BatchData(_Data):
    id: str
    title: str
    allowance_usd: str
    thresholds: dict[str, JsonValue] = Field(default_factory=dict)
    sessions: tuple[SessionDef, ...]


class CallTokens(_Data):
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)


class CostModel(_Data):
    version: int = 1
    ceiling_call_factor: Decimal
    ceiling_input_factor: Decimal
    calls: dict[str, CallTokens]


def _load_yaml(path: Path) -> dict[str, Any]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"{path.name}: expected a mapping at the top level")
    return raw


def load_batch(batch_id: str) -> BatchData:
    """The batch script from ``live_data/batch_<id>.yaml`` (validated; a mistake names the file)."""
    key = batch_id.strip().upper()
    if key not in BATCH_IDS:
        raise ValueError(f"unknown batch {batch_id!r}: choose one of {', '.join(BATCH_IDS)}")
    path = DATA_DIR / f"batch_{key.lower()}.yaml"
    try:
        data = BatchData.model_validate(_load_yaml(path))
    except ValidationError as exc:
        raise ValueError(f"{path.name}: {exc}") from exc
    if data.id != key:
        raise ValueError(f"{path.name}: id is {data.id!r}, expected {key!r}")
    return data


def load_costs() -> CostModel:
    path = DATA_DIR / "cost_model.yaml"
    try:
        return CostModel.model_validate(_load_yaml(path))
    except ValidationError as exc:
        raise ValueError(f"{path.name}: {exc}") from exc


def threshold(data: BatchData, key: str, default: JsonValue = None) -> JsonValue:
    return data.thresholds.get(key, default)


# ============================================================================================== preflight
def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in TRUTHY


def _usd(micros: int) -> str:
    return f"{micros_to_usd(micros):.4f}"


def _repo_cap_micros(env: Mapping[str, str]) -> int:
    """The repository-wide cap from ``LLM_BUDGET_USD`` (it can only lower USD 6.00, as in the gateway)."""
    raw = env.get("LLM_BUDGET_USD")
    if raw is None:
        return DEFAULT_REPO_CAP_MICROS
    try:
        parsed = usd_to_micros(Decimal(raw.strip()))
    except (InvalidOperation, ValueError):
        return DEFAULT_REPO_CAP_MICROS
    return min(parsed, DEFAULT_REPO_CAP_MICROS) if parsed > 0 else DEFAULT_REPO_CAP_MICROS


def preflight(
    env: Mapping[str, str],
    ledger_path: Path | str,
    allowance_micros: int,
    cap_micros: int,
    *,
    dry_run: bool = False,
    reserve_micros: int | None = None,
    stop_fraction: Decimal | None = None,
    api_key_present: bool | None = None,
) -> list[str]:
    """Why this batch must NOT run now, in plain words (an empty list means it may).

    * Not for ``dry_run``: the live mode (mode live, the forbid flag unset), the engine
      override, the key's presence (``api_key_present``; ``None`` means unknown) and the existence of the gateway
      ledger (an absent ledger blocks live calls on purpose; create it with ``budget_cli init``).
    * Always: the allowance must be positive and fit. Compass's spend so far (from the ledger, ``cmp.*`` calls)
      plus the allowance must stay under ``min(stop line, cap - reserve)``: the stop line is ``stop_fraction``
      (95%) of Compass's ceiling ``cap_micros`` and the reserve (USD 0.30) stays unspent for manual sessions.
      The whole ledger must also stay under the gateway's 80% must-run tripwire.

    Never mentions a key value: only names and amounts.
    """
    defaults = BudgetPolicy()
    reserve = int(defaults.reserve_usd * 1_000_000) if reserve_micros is None else reserve_micros
    fraction = defaults.stop_fraction if stop_fraction is None else stop_fraction
    ledger = Path(ledger_path)
    reasons: list[str] = []
    if not dry_run:
        if env.get("LLM_MODE", "").strip().lower() != "live":
            reasons.append(
                "LLM_MODE is not live in the outer environment: this batch calls the real model "
                "(use --mock for an offline run of the same code)"
            )
        if _truthy(env.get("MDE_FORBID_LIVE")) or _truthy(env.get("FORBID_LIVE")):
            reasons.append("MDE_FORBID_LIVE is set: live calls are forbidden in this process")
        if env.get("COMPASS_INTERVIEW_ENGINE", "").strip().lower() == "legacy":
            reasons.append(
                "COMPASS_INTERVIEW_ENGINE=legacy would override the planner engine this batch measures"
            )
        if api_key_present is False:
            reasons.append(
                "OPENAI_API_KEY is not set (only its presence is checked, never its value)"
            )
        if not ledger.is_file():
            reasons.append(
                f"the gateway ledger {ledger} does not exist, and an absent ledger blocks live calls on "
                f"purpose; the operator creates it with: {INIT_COMMAND}"
            )
    if allowance_micros <= 0:
        reasons.append("the allowance must be a positive amount")
        return reasons
    stop_line = int(Decimal(cap_micros) * fraction)
    reserve_line = cap_micros - reserve
    limit = min(stop_line, reserve_line)
    spent = compass_spent_micros(ledger)
    if spent + allowance_micros > limit:
        reasons.append(
            f"Compass has spent USD {_usd(spent)} and this allowance of USD {_usd(allowance_micros)} would "
            f"reach USD {_usd(spent + allowance_micros)}, above the limit of USD {_usd(limit)} "
            f"(the stop line is {fraction:.0%} of the USD {_usd(cap_micros)} ceiling = USD "
            f"{_usd(stop_line)}; the USD {_usd(reserve)} reserve stays unspent, leaving USD "
            f"{_usd(reserve_line)})"
        )
    repo_cap = _repo_cap_micros(env)
    repo_limit = int(Decimal(repo_cap) * REPO_TRIPWIRE_FRACTION)
    everything = compass_spent_micros(ledger, prefix="")
    if everything + allowance_micros > repo_limit:
        reasons.append(
            f"the whole ledger holds USD {_usd(everything)}; adding USD {_usd(allowance_micros)} would pass "
            f"USD {_usd(repo_limit)}, the 80% tripwire of the USD {_usd(repo_cap)} repository cap"
        )
    return reasons


# ===================================================================================================== plan
@dataclass(frozen=True)
class SessionPlan:
    id: str
    label: str
    kind: str
    calls: dict[str, int]
    expected_micros: int
    ceiling_micros: int


@dataclass(frozen=True)
class Plan:
    batch_id: str
    title: str
    sessions: tuple[SessionPlan, ...]
    expected_micros: int
    ceiling_micros: int
    allowance_micros: int
    spent_micros: int
    cap_micros: int
    stop_micros: int
    reserve_micros: int

    @property
    def limit_micros(self) -> int:
        return min(self.stop_micros, self.cap_micros - self.reserve_micros)

    @property
    def calls_total(self) -> int:
        return sum(sum(s.calls.values()) for s in self.sessions)


def _ceil_micros(value: Decimal) -> int:
    return int(value.to_integral_value(rounding=ROUND_CEILING))


def estimate_session(
    calls: Mapping[str, int], costs: CostModel, price: ModelPrice
) -> tuple[int, int]:
    """(expected, ceiling) micro-dollars of a session's planned calls. The ceiling puts every call at its own
    output cap, the prompt at ``ceiling_input_factor`` times its size and the call count at ``ceiling_call_factor``."""
    expected = 0
    ceiling = Decimal(0)
    for spec_id, count in calls.items():
        tokens = costs.calls.get(spec_id)
        if tokens is None:
            raise ValueError(f"cost_model.yaml has no tokens for {spec_id}")
        expected += count * (
            token_cost_micros(tokens.input_tokens, price.input_per_m)
            + token_cost_micros(tokens.output_tokens, price.output_per_m)
        )
        profile = CALL_PROFILES.get(spec_id)
        out_cap = profile.max_output_tokens if profile else tokens.output_tokens
        worst_in = _ceil_micros(Decimal(tokens.input_tokens) * costs.ceiling_input_factor)
        one = token_cost_micros(worst_in, price.input_per_m) + token_cost_micros(
            out_cap, price.output_per_m
        )
        ceiling += Decimal(count * one)
    return expected, _ceil_micros(ceiling * costs.ceiling_call_factor)


def build_plan(
    data: BatchData,
    costs: CostModel,
    *,
    allowance_micros: int,
    spent_micros: int,
    policy: BudgetPolicy,
    price: ModelPrice,
) -> Plan:
    sessions = []
    for s in data.sessions:
        expected, ceiling = estimate_session(s.expected_calls, costs, price)
        sessions.append(
            SessionPlan(s.id, s.label, s.kind, dict(s.expected_calls), expected, ceiling)
        )
    return Plan(
        batch_id=data.id,
        title=data.title,
        sessions=tuple(sessions),
        expected_micros=sum(s.expected_micros for s in sessions),
        ceiling_micros=sum(s.ceiling_micros for s in sessions),
        allowance_micros=allowance_micros,
        spent_micros=spent_micros,
        cap_micros=policy.cap_micros,
        stop_micros=policy.effective_stop_micros,
        reserve_micros=int(policy.reserve_usd * 1_000_000),
    )


def format_plan(plan: Plan, *, mode: str) -> str:
    """The plan as plain text: what runs, the number of calls, the expected cost, the allowance, the budget left."""
    lines = [
        f"Batch {plan.batch_id}: {plan.title}",
        f"  mode: {mode}    sessions: {len(plan.sessions)}    planned calls: {plan.calls_total}",
    ]
    for i, s in enumerate(plan.sessions, 1):
        mix = ", ".join(f"{k.removeprefix('cmp.')} x{v}" for k, v in s.calls.items())
        lines.append(f"  {i}. {s.id} [{s.kind}] {s.label}")
        lines.append(
            f"       {mix}; about USD {_usd(s.expected_micros)} (worst case USD {_usd(s.ceiling_micros)})"
        )
    remaining = max(0, plan.limit_micros - plan.spent_micros)
    lines += [
        f"  expected cost: USD {_usd(plan.expected_micros)}   worst-case ceiling: USD "
        f"{_usd(plan.ceiling_micros)}   (estimates from live_data/cost_model.yaml and the gateway price list)",
        f"  allowance (the run stops when the ledger delta reaches it): USD {_usd(plan.allowance_micros)}",
        f"  Compass spent so far: USD {_usd(plan.spent_micros)}; limit USD {_usd(plan.limit_micros)} "
        f"(ceiling USD {_usd(plan.cap_micros)}, stop line USD {_usd(plan.stop_micros)}, reserve USD "
        f"{_usd(plan.reserve_micros)} unspent); remaining USD {_usd(remaining)}",
    ]
    return "\n".join(lines)


# ======================================================================================== answer policy
ActionKind = Literal["tap", "text", "skip", "you_decide"]
Specificity = Literal["specific", "vague"]


@dataclass(frozen=True)
class Action:
    """One thing the scripted user does to the pending card."""

    turn: int
    card_id: str
    kind: ActionKind
    value: JsonValue = None
    text: str | None = None
    tag: str = "normal"  # normal | needle | anchor | correction | followup
    specificity: Specificity | None = None
    pool: str = ""  # the need id whose pool gave the line, "generic", or the planted tag


def tap_value(card: Card) -> JsonValue:
    """The tap on a card: the recommended option (or the first); ticked or recommended for a checklist; all in
    their order for a ranking."""
    options = card.options
    if not options:
        return None
    recommended = card.recommended_option() or options[0]
    if card.type == "pick_many":
        ticked = [o.id for o in options if o.ticked]
        return list(ticked or [recommended.id])
    if card.type == "priority":
        return [o.id for o in options]
    return recommended.id


class AnswerPolicy:
    """Deterministic answers for a planner-led interview, driven by a ``SessionDef`` (no randomness)."""

    def __init__(self, sdef: SessionDef) -> None:
        self.sdef = sdef
        self._cursor: Counter[str] = Counter()
        self._typed = 0
        self._pivot_sent = False
        # Skips and "you decide" are owed from their turn on: a card that cannot take one (it must be confirmed,
        # or the button is off) hands it to the next card, and two are never put back to back (that is fatigue).
        skips: list[tuple[int, ActionKind]] = [(t, "skip") for t in sdef.skip_turns]
        decides: list[tuple[int, ActionKind]] = [(t, "you_decide") for t in sdef.you_decide_turns]
        self._owed: list[tuple[int, ActionKind]] = sorted(skips + decides)
        self._last_quiet = -10

    # ------------------------------------------------------------------------------------- planted
    def pivot_before(self, turn: int) -> str | None:
        """The pivot message to send before answering question ``turn`` (once)."""
        pivot = self.sdef.pivot
        if pivot is None or self._pivot_sent or turn < pivot.turn:
            return None
        self._pivot_sent = True
        return pivot.text

    def last_planted_turn(self) -> int:
        """The latest turn on which something is planted (a needle, an anchor, a correction or the pivot)."""
        s = self.sdef
        turns = [0]
        if s.needle:
            turns.append(s.needle.turn)
        if s.contradiction:
            turns += [s.contradiction.anchor_turn, s.contradiction.correction_turn]
        if s.pivot:
            turns.append(s.pivot.turn)
        return max(turns)

    def _planted(self, turn: int) -> tuple[str, str] | None:
        s = self.sdef
        if s.needle and s.needle.turn == turn:
            return "needle", s.needle.text
        if s.contradiction:
            if s.contradiction.anchor_turn == turn:
                return "anchor", s.contradiction.anchor_text
            if s.contradiction.correction_turn == turn:
                return "correction", s.contradiction.correction_text
        return None

    # ---------------------------------------------------------------------------------------- lines
    def _need_pool(self, card: Card) -> str | None:
        return next((n for n in card.need_ids if n in self.sdef.pools.by_need), None)

    def _line(self, card: Card, spec: Specificity) -> tuple[str, str]:
        pools = self.sdef.pools
        need = self._need_pool(card)
        other: Specificity = "vague" if spec == "specific" else "specific"
        candidates: list[tuple[str, tuple[str, ...]]] = []
        if need is not None:
            candidates += [(need, getattr(pools.by_need[need], spec))]
        candidates += [("generic", getattr(pools.generic, spec))]
        if need is not None:
            candidates += [(need, getattr(pools.by_need[need], other))]
        candidates += [("generic", getattr(pools.generic, other))]
        for key, lines in candidates:
            if lines:
                slot = f"{key}:{spec}"
                index = self._cursor[slot]
                self._cursor[slot] += 1
                return key, lines[index % len(lines)]
        raise ValueError(f"{self.sdef.id}: no answer lines for {card.id}")  # validated away at load

    def _wants_text(self, card: Card, turn: int) -> bool:
        if not card.options or card.type in TEXT_CARD_TYPES:
            return True
        every = self.sdef.text_on_choice_every
        return bool(
            every
            and turn % every == 0
            and card.custom_input.enabled
            and self._need_pool(card) is not None
        )

    def _quiet(self, card: Card, turn: int, *, can_skip: bool, can_decide: bool) -> Action | None:
        if turn - self._last_quiet < 2:
            return None
        for i, (due, kind) in enumerate(self._owed):
            if due > turn:
                break
            if (kind == "skip" and can_skip) or (kind == "you_decide" and can_decide):
                del self._owed[i]
                self._last_quiet = turn
                return Action(turn, card.id, kind)
        return None

    # ----------------------------------------------------------------------------------------- decide
    def decide(self, card: Card, turn: int) -> Action:
        s = self.sdef
        planted = self._planted(turn)
        if planted is not None:
            tag, text = planted
            return Action(
                turn, card.id, "text", text=text, tag=tag, specificity="specific", pool=tag
            )
        if card.origin == "followup":
            pool, text = self._line(card, "specific")
            return Action(
                turn, card.id, "text", text=text, tag="followup", specificity="specific", pool=pool
            )
        can_skip = card.skip.enabled and not card.must_confirm
        can_decide = card.you_decide.enabled and not card.must_confirm
        if s.max_answers is not None and turn > s.max_answers and s.tail == "you_decide":
            if can_decide:  # the person is tired: "you decide" to the rest (the fatigue rule ends the interview)
                return Action(turn, card.id, "you_decide", tag="tail")
            return Action(
                turn, card.id, "tap", value=tap_value(card), tag="tail", specificity="specific"
            )
        quiet = self._quiet(card, turn, can_skip=can_skip, can_decide=can_decide)
        if quiet is not None:
            return quiet
        if self._wants_text(card, turn):
            spec = s.answer_cycle[self._typed % len(s.answer_cycle)]
            self._typed += 1
            pool, text = self._line(card, spec)
            return Action(turn, card.id, "text", text=text, specificity=spec, pool=pool)
        return Action(turn, card.id, "tap", value=tap_value(card), specificity="specific")


# ================================================================================== run state and traces
class AllowanceReached(Exception):
    """The ledger delta reached the batch allowance: stop cleanly."""


@dataclass
class RunContext:
    allowance_micros: int
    spend: SpendReader
    start_spent: int
    service_factory: ServiceFactory
    mode: str
    sessions: list[SessionData] = field(
        default_factory=list
    )  # filled as sessions start, so a report can be partial

    def delta(self) -> int:
        return self.spend() - self.start_spent

    def check_allowance(self) -> None:
        if self.delta() >= self.allowance_micros:
            raise AllowanceReached


@dataclass
class ShownCard:
    """A question as it was put to the person, with the state of every need at that moment."""

    card: Card
    turn: int
    needs: dict[str, str]


@dataclass
class ActionRecord:
    action: Action
    state_after: str
    spend_micros: int  # the batch's ledger delta right after this turn
    wall_ms: int


@dataclass
class SessionData:
    sdef: SessionDef
    session_id: str = ""
    catalog: Catalog | None = None
    state: str = ""
    locale: str = ""
    brief: Brief | None = None
    qa: list[QaRecord] = field(default_factory=list)
    qa_all: list[QaRecord] = field(default_factory=list)
    facts: list[Fact] = field(default_factory=list)
    batches: list[BatchRecord] = field(default_factory=list)
    call_log: list[CallLogEntry] = field(default_factory=list)
    claims: list[Any] = field(default_factory=list)
    needs: dict[str, NeedStatus] = field(default_factory=dict)
    events: list[StoredEvent] = field(default_factory=list)
    shown: list[ShownCard] = field(default_factory=list)
    actions: list[ActionRecord] = field(default_factory=list)
    messages: list[dict[str, Any]] = field(default_factory=list)
    texts_sent: list[str] = field(default_factory=list)
    closed_by_document: list[str] = field(default_factory=list)
    pivot_snapshot: dict[str, Any] | None = None
    calls: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    stopped: str | None = None
    error: str | None = None
    first_batch_latency_ms: int | None = None
    first_message_wall_ms: int | None = None
    wall_ms: int = 0
    spend_start: int = 0
    spend_end: int = 0

    @property
    def spend_micros(self) -> int:
        return self.spend_end - self.spend_start

    def cards_shown(self) -> list[Card]:
        """Questions that reached the person (a planned-but-never-shown question does not count)."""
        return [
            q.card
            for q in self.qa
            if q.status != "dropped" and not (q.status == "open" and q.asked_at is None)
        ]


def first_batch_latency_ms(events: Sequence[StoredEvent]) -> int | None:
    """Milliseconds from the playback card being committed to the first planned batch (or card) being committed."""
    playback = next((e for e in events if e.type == "playback" and e.at is not None), None)
    if playback is None or playback.at is None:
        return None

    def planned(e: StoredEvent) -> bool:
        if e.type == "question_batch":
            return True
        card = e.payload.get("card")
        return e.type == "card" and isinstance(card, dict) and card.get("frame") == "planned"

    first = next(
        (e for e in events if e.idx > playback.idx and e.at is not None and planned(e)), None
    )
    if first is None or first.at is None:
        return None
    return max(0, round((first.at - playback.at).total_seconds() * 1000))


async def needs_snapshot(svc: Service, conv: Conversation) -> dict[str, NeedStatus]:
    """The status of every need right now, computed by the engine's own function (no model call)."""
    async with svc.store.begin(conv.caller.tenant_id) as uow:
        session = await uow.sessions.get(conv.session_id)
        rec = await uow.briefs.get(conv.session_id)
        facts = await uow.facts.list(conv.session_id)
        qa = await uow.qa.list(conv.session_id)
    slots = SlotStore(svc.catalog, clock=svc.clock)
    slots.load(slots_from_brief(rec.brief, svc.catalog, svc.clock))
    return needs_mod.compute(
        svc.catalog,
        [f for f in facts if f.status == "active"],
        slots,
        assumptions=rec.brief.assumptions,
        asked=counts_by_need(qa),
        followups=counts_by_need(qa, followups_only=True),
        mode=session.mode,
    )


async def _document_closed_needs(svc: Service, conv: Conversation) -> list[str]:
    """Needs the uploaded document already answered: satisfied by the engine's count, or a slot whose value
    came from the document."""
    statuses = await needs_snapshot(svc, conv)
    closed = {n for n, s in statuses.items() if s.state == "satisfied"}
    async with svc.store.begin(conv.caller.tenant_id) as uow:
        rec = await uow.briefs.get(conv.session_id)
    for slot in slots_from_brief(rec.brief, svc.catalog, svc.clock):
        has_value = slot.status != "empty" and slot.value not in (None, "", [], {})
        if has_value and any(p.type == "document" for p in slot.provenance):
            closed.add(slot.slot_id)
    return sorted(closed)


async def _fact_ids(svc: Service, conv: Conversation) -> dict[str, list[str]]:
    async with svc.store.begin(conv.caller.tenant_id) as uow:
        facts = await uow.facts.list(conv.session_id)
    return {
        "all": [f.id for f in facts],
        "active": [f.id for f in facts if f.status == "active"],
    }


async def _perform(conv: Conversation, card: Card, action: Action) -> None:
    if action.kind == "tap":
        await conv.answer(action.value, card=card)
    elif action.kind == "text":
        await conv.answer(None, custom=action.text, card=card)
    elif action.kind == "skip":
        await conv.skip()
    else:
        await conv.you_decide()


async def _drive(ctx: RunContext, sd: SessionData, svc: Service, conv: Conversation) -> None:
    sdef = sd.sdef
    if sdef.budget:
        await conv.command(f"budget_{sdef.budget}")  # a command: no model call
    started = time.monotonic()
    if sdef.document is not None:
        result = await svc.ingest_upload(
            conv.caller,
            conv.session_id,
            sdef.document.filename,
            sdef.document.text.encode("utf-8"),
        )
        conv.events.extend(result.events)
        sd.texts_sent.append(sdef.document.text)
    else:
        await conv.say(sdef.idea)
        sd.texts_sent.append(sdef.idea)
    sd.first_message_wall_ms = round((time.monotonic() - started) * 1000)
    sd.first_batch_latency_ms = first_batch_latency_ms(conv.events)
    if sdef.document is not None:
        sd.closed_by_document = await _document_closed_needs(svc, conv)
    ctx.check_allowance()
    if sdef.kind == "first_batch":
        return

    policy = AnswerPolicy(sdef)
    answered: set[str] = set()
    asked_more = 0
    turn = 0
    for _ in range(sdef.max_actions):
        state = (await svc.get_session(conv.caller, conv.session_id)).state
        if state not in OPEN_STATES:
            # The engine ended the interview before everything planted was said: press "Ask me more", as a person
            # who still has something to add would (the deepen mode asks more on the thinnest areas).
            if asked_more < sdef.ask_more_max and turn < policy.last_planted_turn():
                asked_more += 1
                await conv.command("ask_more")
                sd.messages.append({"turn": turn + 1, "tag": "ask_more", "text": "", "wall_ms": 0})
                ctx.check_allowance()
                if (await svc.get_session(conv.caller, conv.session_id)).state in OPEN_STATES:
                    continue
            break
        if sdef.max_answers is not None and turn >= sdef.max_answers and sdef.tail == "stop":
            sd.notes.append(f"stopped after {turn} answers (max_answers)")
            break
        pivot_text = policy.pivot_before(turn + 1)
        if pivot_text is not None:
            sd.pivot_snapshot = {
                "event_index": len(conv.events),
                "turn": turn + 1,
                **await _fact_ids(svc, conv),
            }
            t0 = time.monotonic()
            await conv.say(pivot_text)
            sd.texts_sent.append(pivot_text)
            sd.messages.append(
                {"turn": turn + 1, "tag": "pivot", "text": pivot_text, "wall_ms": _ms(t0)}
            )
            ctx.check_allowance()
            continue
        card = conv.last_card()
        if card is None or card.frame != "planned":
            sd.notes.append("no planned question to answer: the interview ended or stalled")
            break
        if card.id in answered:
            sd.notes.append(f"stopped: {card.id} was shown again after it was answered")
            break
        statuses = await needs_snapshot(svc, conv)
        sd.shown.append(ShownCard(card, turn + 1, {n: s.state for n, s in statuses.items()}))
        action = policy.decide(card, turn + 1)
        if action.text:
            sd.texts_sent.append(action.text)
        t0 = time.monotonic()
        await _perform(conv, card, action)
        turn += 1
        answered.add(card.id)
        after = (await svc.get_session(conv.caller, conv.session_id)).state
        sd.actions.append(ActionRecord(action, after, ctx.delta(), _ms(t0)))
        ctx.check_allowance()


def _ms(started: float) -> int:
    return round((time.monotonic() - started) * 1000)


async def _collect(sd: SessionData, svc: Service, conv: Conversation) -> None:
    tenant = conv.caller.tenant_id
    async with svc.store.begin(tenant) as uow:
        session = await uow.sessions.get(conv.session_id)
        sd.state, sd.locale = session.state, session.locale
        sd.brief = (await uow.briefs.get(conv.session_id)).brief
        sd.qa = await uow.qa.list(conv.session_id)
        sd.qa_all = await uow.qa.list(conv.session_id, latest_only=False)
        sd.facts = await uow.facts.list(conv.session_id)
        sd.batches = await uow.batches.list(conv.session_id)
        sd.call_log = await uow.call_log.list(conv.session_id, limit=5000)
        sd.claims = await uow.sources.list_claims(conv.session_id)
    sd.needs = await needs_snapshot(svc, conv)
    sd.events = list(conv.events)
    sd.catalog = svc.catalog


async def play_session(ctx: RunContext, sdef: SessionDef) -> SessionData:
    """Play one scripted session against a fresh service, never losing the report to a crash."""
    svc = ctx.service_factory()
    expected = "live" if ctx.mode == "live" else "mock"  # replay runs report as "mock" (no billing)
    if svc.mode_label != expected:
        raise RuntimeError(
            f"the service runs in {svc.mode_label} mode but this run is {ctx.mode}: refusing to go on"
        )
    if svc.config.interview.engine != "planner":
        raise RuntimeError("the planner engine is not active (COMPASS_INTERVIEW_ENGINE=legacy?)")
    conv = await Conversation.start(svc, locale=sdef.start_locale)
    sd = SessionData(sdef=sdef, session_id=conv.session_id)
    ctx.sessions.append(sd)
    sd.spend_start = ctx.delta()
    started = time.monotonic()
    interrupted: asyncio.CancelledError | None = None
    try:
        await _drive(ctx, sd, svc, conv)
    except AllowanceReached:
        sd.stopped = "allowance"
    except (
        asyncio.CancelledError
    ) as exc:  # Ctrl+C: keep what was paid for, then let the cancellation go on
        sd.stopped = "interrupted"
        interrupted = exc
    except Exception as exc:  # the report must survive whatever went wrong
        sd.stopped = "error"
        sd.error = f"{type(exc).__name__}: {exc}"[:300]
    sd.spend_end = ctx.delta()
    sd.wall_ms = _ms(started)
    await _collect(sd, svc, conv)
    if interrupted is not None:
        raise interrupted
    return sd


# ====================================================================================== brief inspection
def _norm(text: str) -> str:
    return quotes.normalize(text)


def contains_all(text: str, terms: Sequence[str]) -> bool:
    folded = _norm(text)
    return all(_norm(t) in folded for t in terms)


def iter_nodes(node: JsonValue, path: str = "") -> Iterator[tuple[str, dict[str, JsonValue]]]:
    """Every mapping inside a JSON value, with its path (``audience.segments``, ``scope[2]``)."""
    if isinstance(node, dict):
        yield path, node
        for key, value in node.items():
            yield from iter_nodes(value, f"{path}.{key}" if path else key)
    elif isinstance(node, list):
        for i, value in enumerate(node):
            yield from iter_nodes(value, f"{path}[{i}]")


def brief_content(brief: Brief) -> dict[str, JsonValue]:
    """The Brief as JSON without its ``meta`` (the audit block is not content)."""
    dump = brief.model_dump(mode="json")
    dump.pop("meta", None)
    return dump


def find_in_brief(brief: Brief, terms: Sequence[str]) -> list[str]:
    """Where all ``terms`` occur in the Brief: a ``domain_facts`` row, an item's evidence quote, or anywhere."""
    content = brief_content(brief)
    where: list[str] = []
    for row in brief.domain_facts:
        if contains_all(f"{row.statement} {row.quote}", terms):
            where.append(f"domain_facts:{row.id}")
    for path, node in iter_nodes(content):
        provenance = node.get("provenance")
        if isinstance(provenance, list):
            for p in provenance:
                if isinstance(p, dict) and contains_all(str(p.get("quote", "")), terms):
                    where.append(f"evidence:{path}")
                    break
    if not where and contains_all(json.dumps(content, ensure_ascii=False), terms):
        where.append("elsewhere_in_brief")
    return list(dict.fromkeys(where))


def locked_report(brief: Brief, facts: Sequence[Fact]) -> dict[str, JsonValue]:
    """Every Locked item must stand on a verified quote of the person's own words (never an unverified one)."""
    by_id = {f.id: f for f in facts}
    total = 0
    violations: list[JsonValue] = []
    for path, node in iter_nodes(brief_content(brief)):
        provenance = node.get("provenance")
        if node.get("tier") != "Locked" or not isinstance(provenance, list):
            continue
        total += 1
        evidence = [
            p
            for p in provenance
            if isinstance(p, dict)
            and p.get("type") in EVIDENCE_TYPES
            and str(p.get("ref", "")).strip()
        ]
        quoted = [p for p in evidence if str(p.get("quote", "")).strip()]
        if not quoted:
            violations.append({"path": path, "reason": "no evidence quote"})
            continue
        ok = False
        for p in quoted:
            ref = str(p.get("ref", ""))
            fact = by_id.get(ref)
            if ref.startswith("FCT-"):
                ok = ok or (fact is not None and fact.supports_locked())
            else:
                ok = True
        if not ok:
            violations.append(
                {"path": path, "reason": "its facts are unverified or no longer active"}
            )
    return {"locked_items": total, "violations": violations}


# ============================================================================================== checks
@dataclass(frozen=True)
class Check:
    """One measured claim. ``passed`` is None when the value is reported but not judged."""

    id: str
    description: str
    value: Any  # JSON-serializable by convention
    threshold: Any = None
    passed: bool | None = None
    note: str = ""
    session: str | None = None


def _ratio(num: int, den: int) -> float | None:
    return round(num / den, 4) if den else None


def _untag_text(text: str) -> str:
    return _norm(text.replace("[mock]", ""))


def repeated_questions(cards: Sequence[Card]) -> list[dict[str, str]]:
    """Questions that repeat an earlier one (same normalized text, or same non-empty intent). A follow-up is a
    refinement of its parent and never counts as its repeat; a card is counted once per id."""
    seen_text: dict[str, str] = {}
    seen_intent: dict[str, str] = {}
    parents: dict[str, str | None] = {}
    out: list[dict[str, str]] = []
    done: set[str] = set()

    def ancestors(card_id: str) -> set[str]:
        chain: set[str] = set()
        cur = parents.get(card_id)
        while cur and cur not in chain:
            chain.add(cur)
            cur = parents.get(cur)
        return chain

    for card in cards:
        if card.id in done:
            continue
        done.add(card.id)
        parents[card.id] = card.parent_card_id
        text, intent = _untag_text(card.question), _untag_text(card.intent)
        family = ancestors(card.id)
        twin = seen_text.get(text)
        if twin is not None and twin not in family:
            out.append({"card": card.id, "repeats": twin, "by": "text"})
        elif intent and (other := seen_intent.get(intent)) is not None and other not in family:
            out.append({"card": card.id, "repeats": other, "by": "intent"})
        seen_text.setdefault(text, card.id)
        if intent:
            seen_intent.setdefault(intent, card.id)
    return out


def unmapped_questions(cards: Sequence[Card], catalog: Catalog) -> list[str]:
    """Questions that serve no catalog need and no ``domain:<topic>``."""

    def known(need: str) -> bool:
        return need in catalog.slots or (
            need.startswith(DOMAIN_PREFIX) and bool(need[len(DOMAIN_PREFIX) :].strip())
        )

    return [c.id for c in cards if not c.need_ids or not all(known(n) for n in c.need_ids if n)]


def fallback_counts(sd: SessionData) -> dict[str, Any]:
    """Both fallback numbers. ``interview_block`` is ``meta.interview.fallback_count``, which only counts the
    planner, auditor, rephrase, summary and compile fallbacks. ``calls`` counts EVERY call-log entry with outcome
    ``fallback`` (understand_turn, situate, probe_handoff and the rest), and ``calls_by_spec`` splits it by spec."""
    block = sd.brief.meta.interview if sd.brief else None
    by_spec = Counter(e.spec_id for e in sd.call_log if e.outcome == "fallback")
    return {
        "interview_block": block.fallback_count if block else 0,
        "calls": sum(by_spec.values()),
        "calls_by_spec": dict(sorted(by_spec.items())),
        "cards": sum(1 for q in sd.qa if q.card.fallback or q.card.origin == "fallback"),
        "batches": sum(1 for b in sd.batches if b.used_fallback),
    }


def _no_fallbacks(counts: Mapping[str, Any]) -> bool:
    return all(not v for v in counts.values())


def common_checks(sd: SessionData, cards: Sequence[Card]) -> list[Check]:
    """Checks every session gets: no fallback, no repeated question, every question mapped to a need."""
    sid = sd.sdef.id
    fb = fallback_counts(sd)
    repeats = repeated_questions(cards)
    unmapped = unmapped_questions(cards, sd.catalog) if sd.catalog else []
    return [
        Check(
            "fallback_count",
            "no fallback anywhere: the interview block's count AND every call-log entry with outcome fallback "
            "(a fallback is a failure)",
            dict(fb),
            0,
            _no_fallbacks(fb),
            session=sid,
        ),
        Check(
            "repeated_questions",
            "no question repeats an earlier one (normalized text or intent)",
            len(repeats),
            0,
            not repeats,
            note=json.dumps(repeats) if repeats else "",
            session=sid,
        ),
        Check(
            "questions_map_to_a_need",
            "every question serves a catalog need or a domain: topic",
            len(unmapped),
            0,
            not unmapped,
            note=", ".join(unmapped),
            session=sid,
        ),
    ]


def active_facts(sd: SessionData) -> list[Fact]:
    return [f for f in sd.facts if f.status == "active"]


def first_batch_cards(sd: SessionData) -> list[Card]:
    """Every question of the first planned batch, shown or not."""
    event = next((e for e in sd.events if e.type == "question_batch"), None)
    if event is None:
        return []
    groups = event.payload.get("groups")
    cards: list[Card] = []
    if isinstance(groups, list):
        for group in groups:
            members = group.get("cards") if isinstance(group, dict) else None
            if isinstance(members, list):
                cards += [Card.model_validate(c) for c in members]
    return cards


def session_terms(sd: SessionData) -> frozenset[str]:
    glossary = [g.term for g in sd.brief.glossary] if sd.brief else []
    return project_terms(sd.sdef.idea, active_facts(sd), glossary)


def e_checks(
    data: BatchData, sessions: Sequence[SessionData], catalog: Catalog, config: CompassConfig
) -> list[Check]:
    policy = config.interview
    checks: list[Check] = []
    by_idea: dict[str, list[Card]] = {}
    scored_total = scored_hit = 0
    per_session: dict[str, JsonValue] = {}
    for sd in sessions:
        cards = first_batch_cards(sd)
        notices = [str(e.payload.get("code")) for e in sd.events if e.type == "notice"]
        checks.append(
            Check(
                "first_batch_produced",
                "the idea led to a planned first batch (a declined or stalled idea gives none)",
                len(cards),
                ">= 1",
                bool(cards),
                note=f"state {sd.state}; notices {notices}" if not cards else "",
                session=sd.sdef.id,
            )
        )
        if not cards:
            continue
        by_idea[sd.sdef.id] = [untagged(c) for c in cards]
        terms = session_terms(sd)
        scored = [c for c in cards if not is_universal(c, catalog)]
        hit = sum(1 for c in scored if contains_project_term(untagged(c).question, terms))
        scored_total += len(scored)
        scored_hit += hit
        per_session[sd.sdef.id] = {"non_universal": len(scored), "with_project_term": hit}
        checks += common_checks(sd, cards)
    share = _ratio(scored_hit, scored_total)
    checks.append(
        Check(
            "project_term_share",
            "share of non-universal first-batch questions that contain a project term",
            share,
            policy.project_term_share_min,
            (share >= policy.project_term_share_min) if share is not None else None,
            note=json.dumps(per_session),
        )
    )
    pairs = cross_idea_overlap_pairs(by_idea, catalog)
    worst = max(pairs.values(), default=0.0)
    term_sets = {
        k: frozenset(t for c in v for t in extract_terms(c.question)) for k, v in by_idea.items()
    }
    names = sorted(term_sets)
    union_pairs = {
        f"{a}|{b}": round(jaccard(term_sets[a], term_sets[b]), 4)
        for i, a in enumerate(names)
        for b in names[i + 1 :]
    }
    checks.append(
        Check(
            "cross_idea_overlap",
            "worst pairwise overlap of question terms between two ideas' first batches",
            round(worst, 4),
            policy.cross_idea_overlap_max,
            worst < policy.cross_idea_overlap_max,
            note=(
                "uncalibrated: the threshold in config.py is marked 'calibrate' and this batch is its "
                f"calibration data; pairs {json.dumps({'|'.join(k): round(v, 4) for k, v in pairs.items()})}; "
                f"whole-batch term-set Jaccard {json.dumps(union_pairs)}"
            ),
        )
    )
    latencies = [
        sd.first_batch_latency_ms for sd in sessions if sd.first_batch_latency_ms is not None
    ]
    walls = {sd.sdef.id: sd.first_message_wall_ms for sd in sessions}
    target = threshold(data, "first_batch_latency_target_ms", 10_000)
    checks.append(
        Check(
            "first_batch_latency_ms",
            "playback committed to first planned batch committed (reported, not judged)",
            max(latencies) if latencies else None,
            target,
            None,
            note=f"idea message to first batch, wall clock per idea: {json.dumps(walls)}",
        )
    )
    return checks


def followup_stats(
    sd: SessionData, high_impact_at: int
) -> tuple[dict[str, int], dict[str, list[str]]]:
    """How often a follow-up came after the scripted answers, counted from what was asked.

    Only answers of the policy's ordinary kind are judged (not the planted needle, anchor and correction, which
    answer nothing that was asked): a vague line (a vague answer is vague for any question), a specific line of
    the pool for the card's own need ("on topic"), a specific generic-pool line (it answers nothing in
    particular, so a follow-up after it is legitimate and only counted), and a tap."""
    cards = {q.card.id: q.card for q in sd.qa_all}
    children = {
        c.parent_card_id for c in cards.values() if c.origin == "followup" and c.parent_card_id
    }
    used: set[str] = set()
    stats = Counter[str]()
    detail: dict[str, list[str]] = {"specific_fired": [], "tap_fired": [], "vague_missed": []}
    for rec in sd.actions:
        action = rec.action
        card = cards.get(action.card_id)
        if (
            card is None
            or action.kind not in ("tap", "text")
            or card.origin == "followup"
            or action.tag != "normal"
        ):
            continue
        fired = card.id in children
        if action.specificity == "vague":
            stats["vague_total"] += 1
            stats["vague_fired"] += int(fired)
            impact = max(
                (
                    sd.catalog.slots[n].impact
                    for n in card.need_ids
                    if sd.catalog and n in sd.catalog.slots
                ),
                default=0,
            )
            if card.depth == 0 and impact >= high_impact_at and not (used & set(card.need_ids)):
                stats["eligible_vague"] += 1
                stats["eligible_fired"] += int(fired)
                if not fired:
                    detail["vague_missed"].append(card.id)
        elif action.kind == "text" and action.pool != "generic":
            stats["specific_text_total"] += 1
            stats["specific_text_fired"] += int(fired)
            if fired:
                detail["specific_fired"].append(card.id)
        elif action.kind == "text":
            stats["generic_text_total"] += 1
            stats["generic_text_fired"] += int(fired)
        else:
            stats["tap_total"] += 1
            stats["tap_fired"] += int(fired)
            if fired:
                detail["tap_fired"].append(card.id)
        if fired:
            used |= set(card.need_ids)
    return dict(stats), detail


def reasked_needs(sd: SessionData, closed: Sequence[str] = ()) -> list[dict[str, JsonValue]]:
    """Questions put to the person although every need they serve was already satisfied (or closed by the
    document). A confirmation, a follow-up and a domain: topic are not counted."""
    out: list[dict[str, JsonValue]] = []
    closed_set = set(closed)
    for shown in sd.shown:
        card = shown.card
        if card.origin == "followup" or card.type == "confirm" or not sd.catalog:
            continue
        slot_needs = [n for n in card.need_ids if n in sd.catalog.slots]
        if not slot_needs:
            continue
        if all(shown.needs.get(n) == "satisfied" or n in closed_set for n in slot_needs):
            out.append({"card": card.id, "needs": list(slot_needs), "turn": shown.turn})
    return out


def _fact_text(fact: Fact) -> str:
    return f"{fact.statement} {fact.quote}"


def needle_check(sd: SessionData) -> Check:
    needle = sd.sdef.needle
    assert needle is not None
    assert sd.brief is not None
    where = find_in_brief(sd.brief, needle.probe_terms)
    in_brief = any(w.startswith(("domain_facts", "evidence")) for w in where)
    holders = [f for f in sd.facts if contains_all(_fact_text(f), needle.probe_terms)]
    bonus = [t for t in needle.bonus_terms if find_in_brief(sd.brief, [t])]
    snap = sd.pivot_snapshot
    by_pivot = [
        f.id for f in holders if snap and f.id in snap["active"] and f.status == "superseded"
    ]
    return Check(
        "needle_in_brief",
        f"the fact planted at turn {needle.turn} reaches the final Brief (a domain fact or an item's evidence)",
        where,
        list(needle.probe_terms),
        in_brief,
        note=(
            f"ledger facts holding it: {[(f.id, f.status) for f in holders]}; "
            f"bonus terms found anywhere in the Brief: {bonus}"
            + (f"; superseded by the pivot (history kept): {by_pivot}" if by_pivot else "")
        ),
        session=sd.sdef.id,
    )


def _action_card(sd: SessionData, tag: str) -> str | None:
    return next((r.action.card_id for r in sd.actions if r.action.tag == tag), None)


def contradiction_check(sd: SessionData) -> Check:
    spec = sd.sdef.contradiction
    assert spec is not None
    anchor_card, correction_card = _action_card(sd, "anchor"), _action_card(sd, "correction")
    old = [
        f
        for f in sd.facts
        if anchor_card is not None
        and f.qa_card_id == anchor_card
        and any(contains_all(_fact_text(f), [t]) for t in spec.anchor_terms)
    ]
    new = [
        f
        for f in sd.facts
        if correction_card is not None
        and f.qa_card_id == correction_card
        and any(contains_all(_fact_text(f), [t]) for t in spec.correction_terms)
    ]
    old_inactive = bool(old) and all(f.status != "active" for f in old)
    new_active = any(f.status == "active" for f in new)
    return Check(
        "contradiction_newest_wins",
        "the earlier statement is no longer active, the correction is, and the earlier fact is kept (history)",
        {"earlier": [(f.id, f.status) for f in old], "correction": [(f.id, f.status) for f in new]},
        "earlier superseded or disputed; correction active",
        old_inactive and new_active,
        note="" if old and new else "the planted answers produced no matching facts",
        session=sd.sdef.id,
    )


def pivot_check(sd: SessionData) -> Check:
    spec = sd.sdef.pivot
    assert spec is not None
    snap = sd.pivot_snapshot
    if snap is None:
        return Check(
            "pivot_handled",
            "the pivot message changed direction and the history was kept",
            "the pivot was never sent",
            passed=False,
            session=sd.sdef.id,
        )
    after = sd.events[int(snap["event_index"]) :]
    notice = any(e.type == "notice" and e.payload.get("code") == "pivot" for e in after)
    replanned = any(e.type == "question_batch" for e in after)
    ids = {f.id for f in sd.facts}
    lost = [i for i in snap["all"] if i not in ids]
    superseded = [f.id for f in sd.facts if f.id in snap["active"] and f.status == "superseded"]
    new_direction = any(
        f.status == "active" and contains_all(_fact_text(f), [t])
        for f in sd.facts
        for t in spec.terms
    )
    return Check(
        "pivot_handled",
        "pivot notice shown, no fact deleted, the new direction is an active fact, the rest was planned again",
        {
            "notice": notice,
            "replanned": replanned,
            "facts_lost": lost,
            "superseded_by_pivot": superseded,
            "new_direction_active": new_direction,
        },
        "notice, nothing lost, new direction active",
        notice and not lost and new_direction,
        session=sd.sdef.id,
    )


def handoff_checks(sd: SessionData) -> list[Check]:
    """The probes the engine ran at Brief review, and the consumer views a downstream agent would receive."""
    assert sd.brief is not None
    sid = sd.sdef.id
    in_review = sd.state in REVIEW_STATES
    meta = sd.brief.meta
    probes = dict(meta.probes)
    probes_ok = bool(probes) and all(probes.values()) and not meta.degraded
    checks = [
        Check(
            "brief_reaches_review",
            "the interview ends in Brief review",
            sd.state,
            "BRIEF_REVIEW",
            in_review,
            note=json.dumps(sd.notes),
            session=sid,
        ),
        Check(
            "handoff_probes",
            "the research, blueprint and design probes passed at Brief review",
            {"probes": probes, "degraded": meta.degraded, "rounds": meta.probe_rounds},
            "all true, not degraded",
            probes_ok if in_review else None,
            session=sid,
        ),
    ]
    if in_review:
        from mde_compass.views.consumers import build_handoff

        handoff = json.dumps(build_handoff(sd.brief), ensure_ascii=False)
        leaked = [
            f.id
            for f in active_facts(sd)
            if f.origin == "stated"
            and f.quote
            and len(f.quote) > 25
            and f.quote not in f.statement
            and f.quote in handoff
        ]
        clean = '"domain_facts"' in handoff and "evt_" not in handoff and '"quote"' not in handoff
        checks.append(
            Check(
                "consumer_views_clean",
                "consumer views carry the facts, and no transcript, event id or verbatim quote",
                {"has_domain_facts": '"domain_facts"' in handoff, "leaked_quotes": leaked},
                None,
                clean and not leaked,
                session=sid,
            )
        )
        if sd.sdef.needle:
            checks.append(
                Check(
                    "needle_in_consumer_views",
                    "the planted fact is in the consumer views",
                    contains_all(handoff, sd.sdef.needle.probe_terms),
                    list(sd.sdef.needle.probe_terms),
                    contains_all(handoff, sd.sdef.needle.probe_terms),
                    session=sid,
                )
            )
    return checks


FOLLOWUP_CHECKS = (
    "followup_after_vague",
    "no_followup_after_specific",
)  # batch F's question, not G's


def interview_checks(data: BatchData, sd: SessionData, config: CompassConfig) -> list[Check]:
    """The long-interview checks (batch F; the shared ones for batch G sessions)."""
    sid = sd.sdef.id
    assert sd.brief is not None
    cards = sd.cards_shown()
    checks = common_checks(sd, cards)
    block = sd.brief.meta.interview
    coverage_min = float(threshold(data, "coverage_min", 1.0) or 1.0)  # type: ignore[arg-type]
    checks.append(
        Check(
            "fact_coverage",
            "every active fact is used in the Brief or marked not_used (coverage 1.0, none unreferenced)",
            {
                "coverage": block.coverage if block else None,
                "facts_unreferenced": block.facts_unreferenced if block else None,
                "facts_active": block.facts_active if block else None,
            },
            coverage_min,
            bool(block) and block.coverage >= coverage_min and block.facts_unreferenced == 0,
            session=sid,
        )
    )
    locked = locked_report(sd.brief, sd.facts)
    violations = locked["violations"]
    assert isinstance(violations, list)
    checks.append(
        Check(
            "locked_have_verified_quotes",
            "every Locked item stands on a verified quote (never an unverified one)",
            locked,
            0,
            not violations,
            session=sid,
        )
    )
    stated = [f for f in sd.facts if f.origin == "stated" and f.quote.strip()]
    verified = [f for f in stated if not f.unverified_quote]
    checks.append(
        Check(
            "quote_verification_rate",
            "stated facts whose quote was verified against the person's words",
            _ratio(len(verified), len(stated)),
            1.0,
            len(verified) == len(stated) if stated else None,
            note=f"{len(verified)} of {len(stated)}",
            session=sid,
        )
    )
    high = config.questions.high_impact_at
    stats, detail = followup_stats(sd, high)
    eligible = stats.get("eligible_vague", 0)
    rate = _ratio(stats.get("eligible_fired", 0), eligible)
    min_rate = float(threshold(data, "followup_after_vague_min", 0.9) or 0.9)  # type: ignore[arg-type]
    checks.append(
        Check(
            "followup_after_vague",
            "a follow-up follows a vague answer to a high-impact need (first time for that need)",
            rate,
            min_rate,
            (rate >= min_rate) if rate is not None else None,
            note=json.dumps({**stats, "missed": detail["vague_missed"]}),
            session=sid,
        )
    )
    max_specific = int(threshold(data, "followup_after_specific_max", 0) or 0)  # type: ignore[call-overload]
    after_specific = stats.get("specific_text_fired", 0)
    checks.append(
        Check(
            "no_followup_after_specific",
            "no follow-up after a specific typed answer that answers the question (taps are only noted)",
            after_specific,
            max_specific,
            after_specific <= max_specific,
            note=json.dumps(
                {
                    "specific_text_answers": stats.get("specific_text_total", 0),
                    "fired_after": detail["specific_fired"],
                    "taps": stats.get("tap_total", 0),
                    "taps_followed_by_followup": detail["tap_fired"],
                    "generic_text_answers": stats.get("generic_text_total", 0),
                    "generic_text_followed_by_followup": stats.get("generic_text_fired", 0),
                }
            ),
            session=sid,
        )
    )
    again = reasked_needs(sd, sd.closed_by_document)
    checks.append(
        Check(
            "answered_need_not_reasked",
            "no base question for a need that was already satisfied when it was shown",
            len(again),
            0,
            not again,
            note=json.dumps(again),
            session=sid,
        )
    )
    qa_bounds: Any = sd.sdef.qa_range or threshold(data, "qa_range")
    if isinstance(qa_bounds, (list, tuple)) and len(qa_bounds) == 2:
        qa_lo, qa_hi = int(qa_bounds[0]), int(qa_bounds[1])
        qa_count = len(sd.actions)
        checks.append(
            Check(
                "qa_count_in_range",
                "number of questions answered, skipped or decided",
                qa_count,
                [qa_lo, qa_hi],
                qa_lo <= qa_count <= qa_hi,
                session=sid,
            )
        )
    return checks


def f_session_checks(data: BatchData, sd: SessionData, config: CompassConfig) -> list[Check]:
    checks = interview_checks(data, sd, config)
    if sd.brief is None:
        return checks
    if sd.sdef.needle:
        checks.append(needle_check(sd))
    if sd.sdef.contradiction:
        checks.append(contradiction_check(sd))
    if sd.sdef.pivot:
        checks.append(pivot_check(sd))
    return checks + handoff_checks(sd)


def g_prd_checks(data: BatchData, sd: SessionData, config: CompassConfig) -> list[Check]:
    sid = sd.sdef.id
    doc = sd.sdef.document
    assert doc is not None
    assert sd.brief is not None
    checks = [c for c in interview_checks(data, sd, config) if c.id not in FOLLOWUP_CHECKS]
    from_doc = [f for f in sd.facts if f.origin == "document"]
    live = [c for c in sd.claims if getattr(c, "status", "") in ("used", "needs_confirming")]
    claim_ids = {f.claim_id for f in from_doc if f.claim_id}
    missing = [c.id for c in live if c.id not in claim_ids]
    checks.append(
        Check(
            "claims_become_facts",
            "every usable document claim became a fact of the ledger",
            {"claims": len(live), "document_facts": len(from_doc), "claims_without_fact": missing},
            0,
            not missing and bool(from_doc),
            session=sid,
        )
    )
    unverified = [
        f.id
        for f in from_doc
        if not f.quote.strip() or f.unverified_quote or not quotes.verify_quote(f.quote, doc.text)
    ]
    checks.append(
        Check(
            "document_quotes_verified",
            "every document fact has a quote found verbatim in the document",
            {"facts": len(from_doc), "unverified": unverified},
            0,
            not unverified and bool(from_doc),
            session=sid,
        )
    )
    questions = len(sd.cards_shown())
    limit = int(threshold(data, "prd_questions_max", 8) or 8)  # type: ignore[call-overload]
    checks.append(
        Check(
            "few_questions_after_a_prd",
            "questions put to the person after a rich document (few expected)",
            questions,
            limit,
            questions <= limit,
            session=sid,
        )
    )
    checks.append(
        Check(
            "needs_closed_by_document",
            "needs the document answered, listed (they must not be asked again: see answered_need_not_reasked)",
            sd.closed_by_document,
            None,
            None,
            session=sid,
        )
    )
    return checks + handoff_checks(sd)


def english_looking(sd: SessionData) -> list[str]:
    """Questions of a non-English session whose text reads as English (a heuristic: reported, not judged)."""
    from mde_compass.calls.understand_turn.lexicon import detect_language

    return [
        c.id
        for c in sd.cards_shown()
        if c.origin != "fallback" and detect_language(_untag_text(c.question), "en") == "en"
    ]


def g_hinglish_checks(data: BatchData, sd: SessionData, config: CompassConfig) -> list[Check]:
    sid = sd.sdef.id
    assert sd.brief is not None
    checks = [c for c in interview_checks(data, sd, config) if c.id not in FOLLOWUP_CHECKS]
    cards = [q.card for q in sd.qa if q.card.frame == "planned" and q.status != "dropped"]
    languages = Counter(c.language for c in cards)
    mismatched = [c.id for c in cards if c.language != sd.locale]
    expected = sd.sdef.expect_language
    language_ok = not mismatched and (not expected or sd.locale in expected) and bool(cards)
    checks.append(
        Check(
            "question_language_matches_session",
            "every question carries the session's language, and the session left English",
            {
                "session_locale": sd.locale,
                "card_languages": dict(languages),
                "mismatched": mismatched,
            },
            list(expected) or sd.locale,
            language_ok,
            session=sid,
        )
    )
    leaks = english_looking(sd)
    checks.append(
        Check(
            "english_looking_questions",
            "questions that read as English in a non-English session (heuristic, reported only)",
            {"count": len(leaks), "of": len(cards), "cards": leaks},
            None,
            None,
            note="detect_language on the question text; the questions are printed in the summary for a human read",
            session=sid,
        )
    )
    sources = [*sd.texts_sent]
    by_card = {q.card_id: q.card for q in sd.qa_all}
    typed = [f for f in sd.facts if f.origin == "stated" and f.quote.strip()]
    bad: list[str] = []
    for f in typed:
        labels = [o.label for o in by_card[f.qa_card_id].options] if f.qa_card_id in by_card else []
        if not quotes.verify_against(f.quote, [*sources, *labels]):
            bad.append(f.id)
    checks.append(
        Check(
            "facts_keep_verbatim_quotes",
            "each stated fact's quote is found verbatim in what the person typed (or the option tapped)",
            {"facts": len(typed), "not_found": bad},
            0,
            not bad if typed else None,
            session=sid,
        )
    )
    return checks + handoff_checks(sd)


# ===================================================================================== run and report
@dataclass
class BatchRun:
    data: BatchData
    mode: str
    started: datetime
    allowance_micros: int
    start_spent: int
    sessions: list[SessionData] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    end_spent: int = 0
    finished: datetime | None = None
    stopped: str | None = None
    checks: list[Check] = field(default_factory=list)
    log_warnings: list[dict[str, str]] = field(default_factory=list)


@contextmanager
def capture_gateway_logs() -> Iterator[Sequence[Mapping[str, Any]]]:
    """Collect the structured log events of the run (the gateway's ``llm_call`` lines carry reasoning tokens and
    attempts, which the call log does not). Falls back to nothing when structlog's test helper is unavailable."""
    try:
        from structlog.testing import capture_logs
    except ImportError:  # pragma: no cover - structlog ships with mde_core
        yield []
        return
    with capture_logs() as entries:
        yield entries


def _call_rows(sd: SessionData, gateway: Mapping[str, Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for e in sd.call_log:
        g = gateway.get(e.call_id, {})
        rows.append(
            {
                "session": sd.sdef.id,
                "spec_id": e.spec_id,
                "spec_version": e.spec_version,
                "call_id": e.call_id,
                "latency_ms": e.latency_ms,
                "input_tokens": e.tokens_in,
                "output_tokens": e.tokens_out,
                "reasoning_tokens": g.get("reasoning_tokens"),
                "cached_input_tokens": g.get("cached_input_tokens"),
                "cost_micros": e.cost_micros,
                "outcome": e.outcome,
                "fallback": e.outcome == "fallback",
                "repaired": e.outcome == "repaired" or bool(g.get("repaired")),
                "attempts": g.get("attempts"),
                "reason": e.reason,
            }
        )
    return rows


def _warnings(entries: Sequence[Mapping[str, Any]]) -> list[dict[str, str]]:
    """Warning and error log events, reduced to a whitelist of fields (no prompts, no free text of the model)."""
    out: list[dict[str, str]] = []
    for e in entries:
        if str(e.get("log_level", "")) not in ("warning", "error", "critical"):
            continue
        item = {"event": str(e.get("event", ""))}
        for key in ("call", "spec_id", "reason", "status"):
            if key in e:
                item[key] = str(e[key])[:200]
        out.append(item)
    return out


def _all_checks(run: BatchRun, config: CompassConfig, catalog: Catalog) -> list[Check]:
    checks: list[Check] = []
    ran = [sd for sd in run.sessions if sd.brief is not None]
    data = run.data
    if data.id == "E":
        checks += e_checks(data, ran, catalog, config)
    for sd in ran:
        if data.id == "F":
            checks += f_session_checks(data, sd, config)
        elif data.id == "G":
            if sd.sdef.document is not None:
                checks += g_prd_checks(data, sd, config)
            else:
                checks += g_hinglish_checks(data, sd, config)
    errors = [sd.sdef.id for sd in run.sessions if sd.stopped == "error"]
    checks.append(
        Check(
            "no_session_crashed",
            "no scripted session ended in an error",
            errors,
            [],
            not errors,
            note="; ".join(f"{sd.sdef.id}: {sd.error}" for sd in run.sessions if sd.error),
        )
    )
    return checks


def _jsonable(value: Any) -> Any:
    return json.loads(json.dumps(value, default=str, ensure_ascii=False))


def _question_record(
    qa: QaRecord, titles: Mapping[tuple[str, str], str], when: Mapping[str, ShownCard]
) -> dict[str, Any]:
    c = qa.card
    shown = when.get(c.id)
    return {
        "card_id": c.id,
        "version": qa.version,
        "batch_id": qa.batch_id,
        "group_id": qa.group_id,
        "group": titles.get((qa.batch_id or "", qa.group_id or ""), qa.group_id),
        "mode": qa.mode,
        "origin": c.origin,
        "parent_card_id": c.parent_card_id,
        "depth": c.depth,
        "type": c.type,
        "question": c.question,
        "why_we_ask": c.why_we_ask,
        "intent": c.intent,
        "need_ids": list(c.need_ids),
        "unlocks": list(c.unlocks),
        "options": [
            {
                "id": o.id,
                "label": o.label,
                "description": o.description,
                "basis": o.basis,
                "recommended": o.recommended,
            }
            for o in c.options
        ],
        "language": c.language,
        "fallback": c.fallback,
        "rephrased": c.rephrased,
        "status": qa.status,
        "shown": qa.asked_at is not None,
        "answer": qa.answer,
        "custom_text": qa.custom_text,
        "input_mode": qa.input_mode,
        "needs_when_shown": shown.needs if shown else None,
    }


def _session_report(sd: SessionData) -> dict[str, Any]:
    titles: dict[tuple[str, str], str] = {}
    for e in sd.events:
        if e.type == "question_batch":
            for g in e.payload.get("groups") or []:  # type: ignore[union-attr]
                if isinstance(g, dict):
                    titles[(str(e.payload.get("batch_id", "")), str(g.get("id", "")))] = str(
                        g.get("title", "")
                    )
    when = {s.card.id: s for s in sd.shown}
    block = sd.brief.meta.interview if sd.brief else None
    brief = sd.brief
    return {
        "id": sd.sdef.id,
        "label": sd.sdef.label,
        "kind": sd.sdef.kind,
        "session_id": sd.session_id,
        "state": sd.state,
        "locale": sd.locale,
        "stopped": sd.stopped,
        "error": sd.error,
        "notes": sd.notes,
        "spend_micros": sd.spend_micros,
        "spend_usd": _usd(sd.spend_micros),
        "calllog_cost_micros": sum(c.cost_micros for c in sd.call_log),
        "wall_ms": sd.wall_ms,
        "first_batch_latency_ms": sd.first_batch_latency_ms,
        "first_message_wall_ms": sd.first_message_wall_ms,
        "questions": [_question_record(q, titles, when) for q in sd.qa],
        "answers": [
            {
                "turn": r.action.turn,
                "card_id": r.action.card_id,
                "kind": r.action.kind,
                "tag": r.action.tag,
                "specificity": r.action.specificity,
                "pool": r.action.pool,
                "text": r.action.text,
                "value": r.action.value,
                "state_after": r.state_after,
                "ledger_delta_micros": r.spend_micros,
                "wall_ms": r.wall_ms,
            }
            for r in sd.actions
        ],
        "messages": sd.messages,
        "facts": [
            {
                "id": f.id,
                "type": f.type,
                "statement": f.statement,
                "quote": f.quote,
                "quote_verified": not f.unverified_quote,
                "origin": f.origin,
                "status": f.status,
                "source_ref": f.source_ref,
                "qa_card_id": f.qa_card_id,
                "topics": list(f.topics),
                "supersedes": list(f.supersedes),
                "confidence": f.confidence,
                "supports_locked": f.supports_locked(),
            }
            for f in sd.facts
        ],
        "needs": {
            n: {
                "state": s.state,
                "impact": s.impact,
                "level": s.level,
                "covered": list(s.covered_aspects),
                "missing": list(s.missing_aspects),
                "asked": s.asked,
                "followups_used": s.followups_used,
            }
            for n, s in sd.needs.items()
        },
        "batches": [
            {
                "id": b.id,
                "mode": b.mode,
                "accepted": list(b.accepted),
                "rejected": [r.model_dump(mode="json") for r in b.rejected],
                "verdicts": len(b.verdicts),
                "used_fallback": b.used_fallback,
                "repaired": b.repaired,
                "latency_ms": b.latency_ms,
                "cost_micros": b.cost_micros,
            }
            for b in sd.batches
        ],
        "hypothesis": brief.meta.hypothesis.model_dump(mode="json")
        if brief and brief.meta.hypothesis
        else None,
        "brief": {
            "version": brief.version if brief else None,
            "domain_facts": [d.model_dump(mode="json") for d in brief.domain_facts]
            if brief
            else [],
            "meta_interview": block.model_dump(mode="json") if block else None,
            "probes": dict(brief.meta.probes) if brief else {},
            "degraded": brief.meta.degraded if brief else None,
        },
        "closed_by_document": sd.closed_by_document,
        "calls": sd.calls,
        "notices": [
            {"code": e.payload.get("code"), "text": str(e.payload.get("text", ""))[:200]}
            for e in sd.events
            if e.type == "notice"
        ],
    }


def build_report(run: BatchRun) -> dict[str, Any]:
    checks = [dataclasses.asdict(c) for c in run.checks]
    judged = [c for c in run.checks if c.passed is not None]
    return _jsonable(
        {
            "batch": run.data.id,
            "title": run.data.title,
            "mode": run.mode,
            "started_at": run.started.isoformat(),
            "finished_at": (run.finished or datetime.now(UTC)).isoformat(),
            "stopped": run.stopped,
            "allowance_micros": run.allowance_micros,
            "allowance_usd": _usd(run.allowance_micros),
            "spent_delta_micros": run.end_spent - run.start_spent,
            "spent_delta_usd": _usd(run.end_spent - run.start_spent),
            "sessions_planned": [s.id for s in run.data.sessions],
            "sessions_skipped": run.skipped,
            "sessions": [_session_report(sd) for sd in run.sessions],
            "checks": checks,
            "checks_passed": sum(1 for c in judged if c.passed),
            "checks_failed": sum(1 for c in judged if not c.passed),
            "log_warnings": run.log_warnings,
        }
    )


async def run_batch(
    data: BatchData,
    *,
    allowance_micros: int,
    spend: SpendReader,
    service_factory: ServiceFactory,
    mode: str,
    config: CompassConfig | None = None,
    out_dir: Path | None = None,
) -> dict[str, Any]:
    """Run every session of the batch in order, stop cleanly at the allowance, compute the checks, write the report."""
    config = config or planner_config()
    run = BatchRun(
        data=data,
        mode=mode,
        started=datetime.now(UTC),
        allowance_micros=allowance_micros,
        start_spent=spend(),
    )
    ctx = RunContext(allowance_micros, spend, run.start_spent, service_factory, mode)
    interrupted: asyncio.CancelledError | None = None
    with capture_gateway_logs() as entries:
        try:
            for sdef in data.sessions:
                if run.stopped == "allowance":
                    run.skipped.append(sdef.id)
                    continue
                sd = await play_session(ctx, sdef)
                if sd.stopped == "allowance":
                    run.stopped = "allowance"
        except asyncio.CancelledError as exc:
            run.stopped = "interrupted"
            interrupted = exc
    run.sessions = ctx.sessions
    catalog = next((sd.catalog for sd in run.sessions if sd.catalog is not None), None)
    gateway = {str(e.get("request_id")): e for e in entries if e.get("event") == "llm_call"}
    for sd in run.sessions:
        sd.calls = _call_rows(sd, gateway)
    run.log_warnings = _warnings(entries)
    run.end_spent = spend()
    run.finished = datetime.now(UTC)
    if catalog is not None:
        run.checks = _all_checks(run, config, catalog)
    report = build_report(run)
    if out_dir is not None:
        report["report_path"] = str(write_report(report, out_dir, data.id, mode))
    if interrupted is not None:
        raise interrupted
    return report


def write_report(report: Mapping[str, Any], out_dir: Path, batch_id: str, mode: str) -> Path:
    """The raw report as JSON (``live-<batch>.json``; a mock or replay run writes ``live-<batch>-mock.json`` or ``-replay.json`` so it
    can never overwrite a live report). The directory is gitignored."""
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"live-{batch_id}{'' if mode == 'live' else f'-{mode}'}.json"
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


# ===================================================================================== service factory
def planner_config(base: CompassConfig = DEFAULT_CONFIG) -> CompassConfig:
    """The default configuration with the AI-planned interview forced on. The batch players answer card by card,
    so they ask for the one-at-a-time layout; the live check of the batch form is a P7 item (ADR 0016)."""
    return dataclasses.replace(
        base,
        interview=dataclasses.replace(
            base.interview, engine="planner", default_ui_mode="one_at_a_time"
        ),
    )


def default_service_factory(
    *, llm_mode: Literal["mock", "replay"] | None = None, clock: Clock | None = None
) -> ServiceFactory:
    """A fresh real service per session: in-memory store, planner engine, the gateway of the process environment.
    With ``llm_mode`` the settings are forced to mock or replay (cassettes) whatever the environment says, so an
    offline run can never reach a live model."""

    def factory() -> Service:
        settings = load_settings(llm_mode=llm_mode) if llm_mode else load_settings()
        return build_service(
            memory=True, settings=settings, clock=clock, compass_config=planner_config()
        )

    return factory


def ledger_reader(ledger: Path | str) -> SpendReader:
    """Compass's committed spend on the gateway ledger, read fresh on every call (read-only)."""
    path = Path(ledger)
    return lambda: compass_spent_micros(path)


# ======================================================================================== stdout views
def say(text: str = "") -> None:
    sys.stdout.write(text + "\n")
    sys.stdout.flush()


def warn(text: str) -> None:
    sys.stderr.write(text + "\n")
    sys.stderr.flush()


def side_by_side(columns: Mapping[str, Sequence[str]], *, width: int = 38, per_row: int = 3) -> str:
    """Text columns next to each other (``per_row`` at a time); each entry is wrapped to the column width."""
    names = list(columns)
    blocks: list[str] = []
    for start in range(0, len(names), per_row):
        group = names[start : start + per_row]
        wrapped: dict[str, list[str]] = {}
        for name in group:
            lines = [name[: width - 2], "-" * (width - 2)]
            for entry in columns[name]:
                indent = "    " if entry.startswith("   ") else ""
                lines += [
                    indent + ln
                    for ln in textwrap.wrap(entry.strip(), width - 2 - len(indent)) or [""]
                ]
            wrapped[name] = lines
        height = max(len(v) for v in wrapped.values())
        for i in range(height):
            blocks.append(
                " | ".join(
                    (wrapped[n][i] if i < len(wrapped[n]) else "").ljust(width - 2) for n in group
                ).rstrip()
            )
        blocks.append("")
    return "\n".join(blocks).rstrip()


def _batch_column(q: Sequence[Mapping[str, Any]], limit: int = 8) -> list[str]:
    lines: list[str] = []
    for i, item in enumerate(q[:limit], 1):
        lines.append(f"{i}. {item['question']}")
        lines += [f"   - {o['label']}" for o in item.get("options", [])]
    return lines


def render_first_batches(report: Mapping[str, Any], before: Mapping[str, Any] | None = None) -> str:
    """Batch E: the first question batches of all ideas side by side (and the old frame-driven ones, when the
    'before' evidence exists), so a reader can judge by eye whether they are specific or generic."""
    after: dict[str, list[str]] = {}
    old: dict[str, list[str]] = {}
    for s in report["sessions"]:
        shown = [q for q in s["questions"] if q["status"] != "dropped"]
        after[s["id"]] = _batch_column(shown)
        if before and s["id"] in before:
            old[s["id"]] = _batch_column(
                [
                    {"question": c["question"], "options": c.get("options", [])}
                    for c in before[s["id"]].get("cards", [])
                ]
            )
    parts = ["FIRST BATCHES (after: AI-planned)", side_by_side(after)]
    if old:
        parts += ["", "FIRST QUESTIONS BEFORE (frame-driven, mock)", side_by_side(old)]
    return "\n".join(parts)


def render_transcript(session: Mapping[str, Any]) -> str:
    """One session as question and answer lines, so a human can read the interview."""
    answers = {a["card_id"]: a for a in session["answers"]}
    lines = [
        f"{session['id']}: {session['label']} ({session['state']}, {len(session['answers'])} answers)"
    ]
    for q in session["questions"]:
        if q["status"] in ("dropped",) or not q["shown"]:
            continue
        mark = "follow-up" if q["origin"] == "followup" else q["origin"]
        a = answers.get(q["card_id"])
        said = "-"
        if a:
            said = (
                (a["text"] or json.dumps(a["value"], ensure_ascii=False))
                if a["kind"] in ("text", "tap")
                else a["kind"]
            )
            said = f"[{a['kind']}/{a['specificity'] or '-'}/{a['tag']}] {said}"
        parent = f" of {q['parent_card_id']}" if q["parent_card_id"] else ""
        lines.append(
            f"  {q['card_id']} ({mark}{parent}; {','.join(q['need_ids'])}) {q['question']}"
        )
        lines.append(f"      -> {said[:200]}")
    return "\n".join(lines)


def render_summary(report: Mapping[str, Any]) -> str:
    lines = [
        f"== batch {report['batch']}: {report['title']} ({report['mode']}) ==",
        f"stopped: {report['stopped'] or 'no'}    spent (ledger delta): USD {report['spent_delta_usd']} "
        f"of the USD {report['allowance_usd']} allowance    sessions run: {len(report['sessions'])} of "
        f"{len(report['sessions_planned'])}",
        "",
        "session            state            Q&A  facts  fallbacks  first-batch ms  spend USD",
    ]
    for s in report["sessions"]:
        fallbacks = sum(1 for c in s["calls"] if c["fallback"])
        lines.append(
            f"{s['id'][:18]:<18} {s['state'][:15]:<15} {len(s['answers']):>4} {len(s['facts']):>6} "
            f"{fallbacks:>10} {s['first_batch_latency_ms']!s:>15} {s['spend_usd']:>10}"
        )
    by_spec: dict[str, list[int]] = {}
    for s in report["sessions"]:
        for c in s["calls"]:
            row = by_spec.setdefault(c["spec_id"], [0, 0, 0, 0, 0, 0])
            row[0] += 1
            row[1] += c["input_tokens"]
            row[2] += c["output_tokens"]
            row[3] += c["reasoning_tokens"] or 0
            row[4] += c["cost_micros"]
            row[5] += c["latency_ms"]
    if by_spec:
        lines += ["", "calls by spec        n   in-tok  out-tok  reason   cost USD  avg ms"]
        for spec, (n, i, o, r, cost, ms) in sorted(by_spec.items()):
            lines.append(f"{spec:<19}{n:>3}{i:>9}{o:>9}{r:>8}{_usd(cost):>11}{ms // max(n, 1):>8}")
    lines += ["", "checks"]
    for c in report["checks"]:
        verdict = "info" if c["passed"] is None else ("PASS" if c["passed"] else "FAIL")
        where = f" [{c['session']}]" if c.get("session") else ""
        value = json.dumps(c["value"], ensure_ascii=False)
        lines.append(
            f"  {verdict:<4} {c['id']}{where}: {value[:110]} (threshold {json.dumps(c['threshold'])})"
        )
    lines.append(f"  {report['checks_passed']} passed, {report['checks_failed']} failed")
    return "\n".join(lines)


def _before_evidence() -> Mapping[str, Any] | None:
    try:
        data = json.loads(BEFORE_EVIDENCE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def render_all(report: Mapping[str, Any]) -> str:
    parts = [render_summary(report)]
    if report["batch"] == "E":
        parts += ["", render_first_batches(report, _before_evidence())]
    else:
        parts += ["", *(render_transcript(s) for s in report["sessions"])]
    return "\n".join(parts)


# ==================================================================================================== cli
def _parse_allowance(raw: str | None, default: str) -> int:
    text = raw if raw is not None else default
    try:
        return usd_to_micros(Decimal(text))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"--allowance-usd {text!r} is not an amount") from exc


def main(
    argv: Sequence[str] | None = None,
    env: Mapping[str, str] | None = None,
    *,
    ledger_path: Path | None = None,
    service_factory: ServiceFactory | None = None,
    out_dir: Path | None = None,
) -> int:
    """Entry point. Exit codes: 0 ran (or a dry run), 2 refused or bad usage, 1 the run itself failed."""
    environ = os.environ if env is None else env
    parser = argparse.ArgumentParser(
        prog="python -m mde_compass.live_batches",
        description="Live batches E (planner quality), F (long interviews), G (documents, Hinglish).",
    )
    parser.add_argument("--batch", required=True, type=str.upper, choices=BATCH_IDS)
    parser.add_argument("--dry-run", action="store_true", help="print the plan and exit; no calls")
    offline = parser.add_mutually_exclusive_group()
    offline.add_argument(
        "--mock", action="store_true", help="run the same code offline in mock mode (free)"
    )
    offline.add_argument(
        "--replay",
        action="store_true",
        help="run the same code offline against the recorded cassettes (free; never goes live)",
    )
    parser.add_argument(
        "--allowance-usd", default=None, help="stop when the ledger delta reaches this"
    )
    parser.add_argument(
        "--only",
        default="",
        help="comma separated session ids of the batch to run (default: all)",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    data = load_batch(args.batch)
    only = {x.strip() for x in args.only.split(",") if x.strip()}
    if only:
        chosen = tuple(s for s in data.sessions if s.id in only)
        if not chosen:
            warn(f"no session of batch {args.batch} matches --only {args.only}")
            return 2
        data = data.model_copy(update={"sessions": chosen})
    try:
        allowance = _parse_allowance(args.allowance_usd, data.allowance_usd)
    except ValueError as exc:
        warn(str(exc))
        return 2
    settings = load_settings()
    ledger = ledger_path or Path(settings.llm_budget_ledger)
    policy = load_budget_policy(environ)
    spent = compass_spent_micros(ledger)
    mode = "mock" if args.mock else "replay" if args.replay else "live"
    offline_run = mode != "live"
    price = Pricing.load().price_for(MODEL_ID)
    plan = build_plan(
        data,
        load_costs(),
        allowance_micros=allowance,
        spent_micros=spent,
        policy=policy,
        price=price,
    )
    say(format_plan(plan, mode=mode if not args.dry_run else f"dry run ({mode})"))
    reasons = preflight(
        environ,
        ledger,
        allowance,
        policy.cap_micros,
        dry_run=args.dry_run or offline_run,
        reserve_micros=plan.reserve_micros,
        stop_fraction=policy.stop_fraction,
        api_key_present=None
        if (args.dry_run or offline_run)
        else settings.openai_api_key is not None,
    )
    if args.dry_run:
        for reason in reasons:
            say(f"  a live run would be refused: {reason}")
        say("dry run: nothing was called")
        return 0
    if reasons and not offline_run:
        warn(f"REFUSED: batch {data.id} will not run.")
        for reason in reasons:
            warn(f"  - {reason}")
        warn(
            "Only the lead (or the live-runner) runs live batches: see docs/runbooks/live-llm-budget.md"
        )
        return 2

    factory = service_factory or default_service_factory(
        llm_mode=cast(Literal["mock", "replay"], mode) if offline_run else None
    )
    reader: SpendReader = (lambda: 0) if offline_run else ledger_reader(ledger)
    try:
        report = asyncio.run(
            run_batch(
                data,
                allowance_micros=allowance,
                spend=reader,
                service_factory=factory,
                mode=mode,
                out_dir=out_dir or REPORT_DIR,
            )
        )
    except Exception as exc:
        warn(f"the batch failed before it could report: {type(exc).__name__}: {str(exc)[:300]}")
        return 1
    say(render_all(report))
    say("")
    say(f"report: {report.get('report_path')} (gitignored)")
    if mode == "live":
        if _truthy(environ.get("MDE_RECORD_CASSETTES")):
            say(f"cassettes recorded to {settings.cassette_dir}; run just scan-secrets")
        else:
            say(
                "cassette recording was off (MDE_RECORD_CASSETTES is not set): nothing was recorded for replay"
            )
        say("next: just budget, then write the new total into docs/implementation/PROGRESS.md")
    return 0


__all__ = [
    "Action",
    "AnswerPolicy",
    "BatchData",
    "Check",
    "SessionDef",
    "build_plan",
    "format_plan",
    "load_batch",
    "load_costs",
    "main",
    "preflight",
    "run_batch",
]


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
