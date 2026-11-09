"""Run a scenario script against the real ``Service`` (mock mode, in-memory store) and check its expectations.

Failures name the scenario, the step, the expectation and the last events, so a red scenario can be read
without a debugger. A scenario marked ``known_issue: BUG-n`` is a strict xfail in the test module: the product
behaviour is wrong, the scenario says what should happen, and it flips to a failure when the bug is fixed.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, cast

import yaml

from mde_compass.domain import Card, StoredEvent
from mde_compass.domain.events import (
    AnswerEdit,
    AssumptionDecision,
    BriefEdit,
    BriefFeedback,
    CardAnswer,
    CardBack,
    CardSkip,
    CardYouDecide,
    Command,
    ContributorSwitch,
    GateDecision,
    LookupEdit,
    UserMessage,
)
from mde_compass.domain.session import Contributor
from mde_compass.scenarios.models import Expect, Scenario, SlotExpect, Step
from mde_compass.testing import Conversation

NON_QUESTION_FRAMES: frozenset[str] = frozenset({"welcome", "change_variants"})
TYPED_ANSWER = (
    "We handle about 200 orders a day for regular customers and our own staff take care of them"
)
TYPED_BY_NEED: dict[str, str] = {
    "context.geo": "In Pune, India",
    "audience.segments": "Regular customers who order from their phone every day",
    "locales": "English and Hindi",
    "look.tone_words": "Friendly, simple and warm",
    "scope.features": "Ordering, online payments and order tracking",
    "scope.priority": "Ordering and payments first, the rest can wait",
    "roles.list": "Customers, shop staff who prepare orders, and an admin",
    "apps.list": "One app for customers and a small admin panel",
    "idea.business_model": "We charge a small fee on every order",
}
"""What a person types when a planned question has no options and the script just says "accept what is offered"
(``tap_recommended``, ``drive``). A planner question is often free text; tapping "ok" on it would teach nothing.
The text is about the question's own need (never another need's words), so it cannot overwrite what the person
said elsewhere."""
TERMINAL_STATES: frozenset[str] = frozenset({"BRIEF_REVIEW", "APPROVED", "ARCHIVED"})


def frame_slots(frames: Sequence[str]) -> frozenset[str]:
    """The needs (slot ids) that the legacy frames ask about; a planned question has no frame, only needs."""
    from mde_compass.catalog import load_catalog

    catalog = load_catalog()
    return frozenset(s for f in frames if f in catalog.frames for s in catalog.frames[f].slots)


def in_frame(card: Card, frames: Sequence[str], engine: str) -> bool:
    """Whether a card belongs to one of the legacy frames. On the planner path a card has the frame ``planned``,
    so the frame expectation is translated: the card's needs include a slot of that frame (D-planner-frames)."""
    if card.frame in frames:
        return True
    return engine == "planner" and bool(set(card.need_ids) & frame_slots(frames))


class ScenarioFailure(AssertionError):
    """A step expectation did not hold (or the step could not run)."""


@dataclass
class StepRecord:
    index: int
    action: str
    kind: str  # tap | custom | you_decide | skip | text | other
    events: list[StoredEvent]
    state_before: str
    state_after: str
    version_before: int
    version_after: int
    answers: list[tuple[str, Card]] = field(
        default_factory=list
    )  # (tap | custom | you_decide | skip, card)


@dataclass
class RunResult:
    scenario: Scenario
    conv: Conversation
    records: list[StepRecord] = field(default_factory=list)
    failure: str | None = None
    failed_step: int | None = None

    @property
    def ok(self) -> bool:
        return self.failure is None

    def raise_if_failed(self) -> None:
        if self.failure is not None:
            raise ScenarioFailure(self.failure)


Engine = Literal["legacy", "planner"]
ENGINES: tuple[Engine, ...] = ("legacy", "planner")


def engine_config(engine: Engine, policy: dict[str, Any] | None = None) -> Any:
    """The default configuration with the question engine set (the planner path is selected by this one field).
    ``policy`` overrides fields of ``InterviewPolicy`` for one scenario (``policy:`` in its YAML)."""
    import dataclasses

    from mde_compass.config import DEFAULT_CONFIG

    interview = dataclasses.replace(DEFAULT_CONFIG.interview, engine=engine, **(policy or {}))
    return dataclasses.replace(DEFAULT_CONFIG, interview=interview)


def new_scripted_service(
    engine: Engine, script: Any, policy: dict[str, Any] | None = None
) -> tuple[Any, Any]:
    """A planner service whose model calls come from a provider fixture; returns the service and the gateway
    (``HybridGateway``: its ``trace`` lists the calls, ``assert_consumed`` checks the script was used up)."""
    from mde_compass.compose import build_service
    from mde_compass.scenarios.providers import hybrid_for
    from mde_core.clock import FakeClock

    clock = FakeClock()
    gateway = hybrid_for(script, clock)
    service = build_service(
        memory=True,
        clock=clock,
        compass_config=engine_config(engine, policy),
        gateway=cast(Any, gateway),
    )
    return service, gateway


def new_service(engine: Engine = "legacy", *, gateway: Any | None = None) -> Any:
    """A fresh real Service: mock mode, in-memory store, a fake clock (the gateway rate limiter then never waits).

    ``engine`` picks the question engine (``legacy`` frame queue or the AI ``planner``); ``gateway`` replaces the
    mock gateway (a ``ScriptedGateway`` with hand-written model outputs)."""
    from mde_compass.compose import build_service
    from mde_core.clock import FakeClock

    return build_service(
        memory=True, clock=FakeClock(), compass_config=engine_config(engine), gateway=gateway
    )


# ---------------------------------------------------------------------------------------- loading
def load_scenario(path: Path | str) -> Scenario:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    scn = Scenario.model_validate(raw)
    return scn.model_copy(update={"source_dir": str(Path(path).resolve().parent)})


def load_dir(directory: Path | str, pattern: str = "*.yaml") -> list[tuple[Path, Scenario]]:
    return [(p, load_scenario(p)) for p in sorted(Path(directory).glob(pattern))]


# ------------------------------------------------------------------------------------- event views
def question_cards(events: Sequence[StoredEvent]) -> list[Card]:
    """Distinct question cards (re-shown cards counted once; welcome and variant cards are not questions)."""
    seen: dict[str, Card] = {}
    for ev in events:
        if ev.type != "card":
            continue
        card = Card.model_validate(ev.payload["card"])
        if card.frame in NON_QUESTION_FRAMES:
            continue
        seen.setdefault(card.id, card)
    return list(seen.values())


def _texts(events: Sequence[StoredEvent]) -> list[str]:
    out: list[str] = []
    for ev in events:
        if ev.type in ("assistant_message", "notice"):
            out.append(str(ev.payload.get("text", "")))
    return out


def _card_text(card: Card) -> str:
    return " | ".join([card.question, *(o.label for o in card.options)])


def _last_card_in(events: Sequence[StoredEvent]) -> Card | None:
    for ev in reversed(events):
        if ev.type == "card":
            return Card.model_validate(ev.payload["card"])
    return None


def describe_event(ev: StoredEvent) -> str:
    p = ev.payload
    if ev.type == "assistant_message":
        return f"assistant_message: {str(p.get('text', ''))[:140]!r}"
    if ev.type == "card":
        c: dict[str, Any] = cast(dict[str, Any], p.get("card") or {})
        opts = ", ".join(
            f"{o.get('id')}={str(o.get('label'))[:24]}"
            for o in cast(list[dict[str, Any]], c.get("options", []))
        )
        return (
            f"card {c.get('id')} frame={c.get('frame')} type={c.get('type')} "
            f"fallback={c.get('fallback')}: {str(c.get('question'))[:80]!r} [{opts}]"
        )
    if ev.type == "notice":
        return f"notice[{p.get('code')}]: {str(p.get('text', ''))[:120]!r}"
    if ev.type == "brief_ready":
        return f"brief_ready v{p.get('version')} approvable={p.get('approvable')} blockers={p.get('blockers')}"
    if ev.type == "brief_patch":
        return f"brief_patch v{p.get('from_version')}->{p.get('to_version')}: {p.get('summary')}"
    if ev.type == "error":
        return f"ERROR {p.get('code')}: {p.get('message')}"
    return f"{ev.type}: {json.dumps(p, ensure_ascii=False, default=str)[:140]}"


# ------------------------------------------------------------------------------------------ invariants
def locked_without_evidence(node: Any, path: str = "") -> list[str]:
    """Every Brief item whose tier is Locked but whose provenance holds no evidence entry (must be none)."""
    from mde_compass.domain.common import EVIDENCE_TYPES

    found: list[str] = []
    if isinstance(node, dict):
        if node.get("tier") == "Locked":
            prov = node.get("provenance") or []
            if not any(
                isinstance(p, dict)
                and p.get("type") in EVIDENCE_TYPES
                and str(p.get("ref", "")).strip()
                for p in prov
            ):
                found.append(path or "<root>")
        for key, value in node.items():
            if isinstance(key, str) and key.startswith("_"):
                continue
            found.extend(locked_without_evidence(value, f"{path}.{key}" if path else str(key)))
    elif isinstance(node, list):
        for i, item in enumerate(node):
            found.extend(locked_without_evidence(item, f"{path}[{i}]"))
    return found


# ----------------------------------------------------------------------------------------- slot paths
def _resolve(node: Any, parts: list[str]) -> list[Any]:
    if not parts:
        return [node]
    head, rest = parts[0], parts[1:]
    if head == "*":
        if isinstance(node, list):
            return [r for item in node for r in _resolve(item, rest)]
        if isinstance(node, dict):
            return [r for item in node.values() for r in _resolve(item, rest)]
        return []
    if isinstance(node, dict):
        return _resolve(node[head], rest) if head in node else []
    if isinstance(node, list):
        if head.isdigit():
            idx = int(head)
            return _resolve(node[idx], rest) if idx < len(node) else []
        return [r for item in node for r in _resolve(item, parts)]
    return []


def _value_of(node: Any) -> Any:
    if isinstance(node, dict) and "value" in node and "tier" in node:
        return node["value"]
    return node


def _text_of(node: Any) -> str:
    return json.dumps(_value_of(node), ensure_ascii=False, default=str).lower()


def _filled(node: Any) -> bool:
    v = _value_of(node)
    return v not in (None, "", [], {}, False)


def _as_list(v: Any) -> list[Any]:
    return [] if v is None else (v if isinstance(v, list) else [v])


def check_slot(
    brief: dict[str, Any], path: str, exp: SlotExpect, engine: str = "legacy"
) -> list[str]:
    nodes = _resolve(brief, path.split("."))
    errs: list[str] = []
    if not nodes:
        positive = (
            exp.filled is True or exp.contains or exp.contains_any or exp.equals is not None
            or exp.status or exp.tier or exp.history_min or exp.provenance or exp.count_min
            or exp.match_min
        )  # fmt: skip
        return [f"slot {path}: path not found in the Brief (nothing there yet)"] if positive else []
    texts = [_text_of(n) for n in nodes]
    if exp.filled is not None and any(_filled(n) for n in nodes) != exp.filled:
        errs.append(f"slot {path}: filled should be {exp.filled}")
    for needle in _as_list(exp.contains):
        if not any(str(needle).lower() in t for t in texts):
            errs.append(f"slot {path}: value should contain {needle!r}; got {texts[0][:200]}")
    accepted = list(exp.contains_any or [])
    if engine == "planner":  # the planner path normalizes the user's words to the registry's value
        accepted += list(exp.planner_contains_any or [])
    if accepted and not any(str(n).lower() in t for n in accepted for t in texts):
        errs.append(f"slot {path}: value should contain any of {accepted}; got {texts[0][:200]}")
    for needle in _as_list(exp.not_contains):
        if any(str(needle).lower() in t for t in texts):
            errs.append(f"slot {path}: value should NOT contain {needle!r}; got {texts[0][:200]}")
    if exp.equals is not None and not any(_value_of(n) == exp.equals for n in nodes):
        errs.append(f"slot {path}: value should equal {exp.equals!r}; got {_value_of(nodes[0])!r}")
    metas = [n for n in nodes if isinstance(n, dict)]
    if exp.status and not any(m.get("status") in _as_list(exp.status) for m in metas):
        errs.append(
            f"slot {path}: status should be {exp.status}; got {[m.get('status') for m in metas]}"
        )
    if exp.tier and not any(m.get("tier") in _as_list(exp.tier) for m in metas):
        errs.append(f"slot {path}: tier should be {exp.tier}; got {[m.get('tier') for m in metas]}")
    if exp.not_tier and any(m.get("tier") in _as_list(exp.not_tier) for m in metas):
        errs.append(
            f"slot {path}: tier must not be {exp.not_tier}; got {[m.get('tier') for m in metas]}"
        )
    if exp.history_min is not None and not any(
        len(m.get("history", [])) >= exp.history_min for m in metas
    ):
        errs.append(f"slot {path}: history should keep >= {exp.history_min} earlier values")
    if exp.provenance and not any(
        p.get("type") in _as_list(exp.provenance) for m in metas for p in m.get("provenance", [])
    ):
        errs.append(f"slot {path}: provenance should include {exp.provenance}")
    if exp.count_min is not None or exp.count_max is not None:
        n = len(nodes[0]) if len(nodes) == 1 and isinstance(nodes[0], list) else len(nodes)
        if exp.count_min is not None and n < exp.count_min:
            errs.append(f"slot {path}: expected at least {exp.count_min} entries, got {n}")
        if exp.count_max is not None and n > exp.count_max:
            errs.append(f"slot {path}: expected at most {exp.count_max} entries, got {n}")
    if exp.match is not None:
        n_match = sum(1 for t in texts if exp.match.lower() in t)
        if exp.match_min is not None and n_match < exp.match_min:
            errs.append(
                f"slot {path}: at least {exp.match_min} entries should contain {exp.match!r}; got {n_match}"
            )
        if exp.match_max is not None and n_match > exp.match_max:
            errs.append(
                f"slot {path}: at most {exp.match_max} entries should contain {exp.match!r}; got {n_match}: {texts}"
            )
    return errs


async def session_budget(conv: Conversation) -> tuple[str, int]:
    """The interview tier of a planner session (name and its HARD cap: the most questions it may ever ask)."""
    raw = await conv.brief()
    meta = cast(dict[str, Any], raw.get("meta") or {})
    block = cast(dict[str, Any], meta.get("interview") or {})
    plan = cast(dict[str, Any], meta.get("plan") or {})
    name = str(plan.get("interview_budget") or block.get("budget") or "standard")
    if name not in ("quick", "standard", "thorough"):
        name = "standard"
    policy = conv.service.config.interview
    return name, int(policy.hard_cap(cast(Any, name)))


# ----------------------------------------------------------------------------------------- expectations
async def check_expect(
    exp: Expect | None,
    *,
    conv: Conversation,
    events: Sequence[StoredEvent],
    state_after: str,
    version_before: int,
    version_after: int,
    card_on_screen: Card | None = None,
    engine: str = "legacy",
    gateway: Any | None = None,
) -> list[str]:
    exp = exp or Expect()
    errs: list[str] = []
    types = [e.type for e in events]
    if exp.no_error is not False and "error" in types:
        err = next(e for e in events if e.type == "error")
        errs.append(
            f"an error event was emitted: {err.payload.get('code')} {err.payload.get('message')}"
        )
    if exp.state and state_after not in _as_list(exp.state):
        errs.append(f"state should be one of {exp.state}; got {state_after}")
    texts_joined = "\n".join(_texts(events)).lower()
    for needle in _as_list(exp.text_contains):
        if needle.lower() not in texts_joined:
            errs.append(f"assistant text should contain {needle!r}")
    for needle in _as_list(exp.text_not_contains):
        if needle.lower() in texts_joined:
            errs.append(f"assistant text should NOT contain {needle!r}")
    if exp.text_any and not any(n.lower() in texts_joined for n in exp.text_any):
        errs.append(f"assistant text should contain any of {exp.text_any}")
    for pattern in exp.text_not_matches:
        if re.search(pattern, texts_joined, flags=re.IGNORECASE):
            errs.append(f"assistant text should NOT match /{pattern}/: {texts_joined[:200]!r}")
    if exp.voided_includes or exp.voided_excludes:
        voided = {
            str(s)
            for e in events
            if e.type == "brief_patch"
            for s in e.payload.get("voided_sections", [])  # type: ignore[union-attr]
        }
        for sec in exp.voided_includes:
            if sec not in voided:
                errs.append(f"voided sections should include {sec!r}; got {sorted(voided)}")
        for sec in exp.voided_excludes:
            if sec in voided:
                errs.append(f"voided sections should NOT include {sec!r}; got {sorted(voided)}")
    last = _last_card_in(events) or card_on_screen
    if exp.no_card and last is not None:
        errs.append(f"no card expected, but card {last.id} ({last.frame}) was shown")
    if exp.card_frame and (last is None or not in_frame(last, _as_list(exp.card_frame), engine)):
        errs.append(
            f"last card frame should be one of {exp.card_frame}; got {last.frame if last else None}"
        )
    # the planner chooses the card type per question (choose_one only for three real alternatives), so a type named
    # next to a legacy frame describes that frame's template and is not checked on the planner path
    type_applies = not (
        engine == "planner" and exp.card_frame and last is not None and last.frame == "planned"
    )
    if (
        exp.card_type
        and type_applies
        and (last is None or last.type not in _as_list(exp.card_type))
    ):
        errs.append(
            f"last card type should be one of {exp.card_type}; got {last.type if last else None}"
        )
    if exp.card_language and (last is None or last.language not in _as_list(exp.card_language)):
        errs.append(
            f"last card language should be one of {exp.card_language}; got {last.language if last else None}"
        )
    if exp.card_text_contains or exp.card_text_not_contains:
        ctext = _card_text(last).lower() if last else ""
        for needle in _as_list(exp.card_text_contains):
            if needle.lower() not in ctext:
                errs.append(f"last card should contain {needle!r}: {ctext[:200]!r}")
        for needle in _as_list(exp.card_text_not_contains):
            if needle.lower() in ctext:
                errs.append(f"last card should NOT contain {needle!r}: {ctext[:200]!r}")
    for t in exp.event_types or []:
        if t not in types:
            errs.append(f"event type {t!r} should be present; got {sorted(set(types))}")
    for t in exp.no_event_types or []:
        if t in types:
            errs.append(f"event type {t!r} should NOT be present")
    codes = [str(e.payload.get("code")) for e in events if e.type == "notice"]
    for c in _as_list(exp.notice_code):
        if c not in codes:
            errs.append(f"notice code {c!r} should be present; got {codes}")
    if exp.notice_code_any and not any(c in codes for c in exp.notice_code_any):
        errs.append(f"one notice code of {exp.notice_code_any} should be present; got {codes}")
    for c in _as_list(exp.no_notice_code):
        if c in codes:
            errs.append(f"notice code {c!r} should NOT be present")
    if exp.max_cards is not None and len(question_cards(events)) > exp.max_cards:
        errs.append(
            f"at most {exp.max_cards} question cards expected in this step; got {len(question_cards(events))}"
        )
    if exp.questions_max is not None:
        total = len(question_cards(conv.events))
        limit, why = exp.questions_max, ""
        if (
            engine == "planner"
        ):  # the planner path is governed by the interview budget, not a frame count
            name, limit = await session_budget(conv)
            why = f" (the {name} hard cap)"
        if total > limit:
            errs.append(f"at most {limit} questions in the whole session{why}; got {total}")
    if exp.approvable is not None:
        readies = [e for e in events if e.type == "brief_ready"]
        if not readies or bool(readies[-1].payload.get("approvable")) != exp.approvable:
            got = readies[-1].payload.get("blockers") if readies else "no brief_ready event"
            errs.append(f"brief_ready.approvable should be {exp.approvable}; blockers: {got}")
    if exp.probes_passed is not None:
        latest: dict[str, bool] = {}
        for e in conv.events:
            if e.type == "probe_result":
                latest[str(e.payload.get("kind"))] = bool(e.payload.get("passed"))
        ok = {"research", "blueprint", "design"} <= set(latest) and all(latest.values())
        if ok != exp.probes_passed:
            errs.append(
                f"the latest probe results should {'all pass' if exp.probes_passed else 'not all pass'}; got {latest}"
            )
    if exp.approved is not None:
        approved_now = "brief_approved" in types or state_after == "APPROVED"
        if exp.approved and not approved_now:
            errs.append("the Brief should be approved")
        if exp.approved is False and "brief_approved" in types:
            errs.append("the Brief must NOT be approved")
    if exp.version_increased is True and version_after <= version_before:
        errs.append(f"Brief version should increase (still {version_after})")
    if exp.version_unchanged is True and version_after != version_before:
        errs.append(f"Brief version should not change ({version_before} -> {version_after})")
    if exp.slots:
        brief = await conv.brief()
        for path, sx in exp.slots.items():
            errs.extend(check_slot(brief, path, sx, engine))
    errs.extend(await check_memory(exp, conv=conv, events=events, gateway=gateway))
    return errs


# ---------------------------------------------------------------------------- the interview memory
def _has(texts: Sequence[str], needle: str) -> bool:
    return any(needle.lower() in t.lower() for t in texts)


def _fact_texts(facts: Sequence[Any]) -> list[str]:
    return [f"{f.statement} | {f.quote}" for f in facts]


def check_facts(exp: Any, data: Any) -> list[str]:
    from mde_compass.scenarios import ledger

    errs: list[str] = []
    facts = data.facts
    active = [f for f in facts if f.status == "active"]
    if exp.count_min is not None and len(facts) < exp.count_min:
        errs.append(f"facts: at least {exp.count_min} expected, got {len(facts)}")
    if exp.count_max is not None and len(facts) > exp.count_max:
        errs.append(f"facts: at most {exp.count_max} expected, got {len(facts)}")
    if exp.active_min is not None and len(active) < exp.active_min:
        errs.append(f"facts: at least {exp.active_min} active expected, got {len(active)}")
    for needle in exp.active_contains:
        if not _has(_fact_texts(active), needle):
            errs.append(f"facts: no ACTIVE fact contains {needle!r}: {_fact_texts(active)[:6]}")
    for needle in exp.active_not_contains:
        if _has(_fact_texts(active), needle):
            errs.append(f"facts: an active fact still contains {needle!r}")
    superseded = [f for f in facts if f.status == "superseded"]
    for needle in exp.superseded_contains:
        if not _has(_fact_texts(superseded), needle):
            errs.append(
                f"facts: no SUPERSEDED fact contains {needle!r}: {_fact_texts(superseded)[:6]}"
            )
    if exp.superseded_min is not None and len(superseded) < exp.superseded_min:
        errs.append(
            f"facts: at least {exp.superseded_min} superseded expected, got {len(superseded)}"
        )
    disputed = [f for f in facts if f.status == "disputed"]
    if exp.disputed_min is not None and len(disputed) < exp.disputed_min:
        errs.append(f"facts: at least {exp.disputed_min} disputed expected, got {len(disputed)}")
    if exp.ids_gapless:
        want = [f"FCT-{i:03d}" for i in range(1, len(facts) + 1)]
        got = [f.id for f in facts]
        if got != want:
            errs.append(
                f"facts: ids should run FCT-001..{len(facts):03d} without gaps or reuse; got {got}"
            )
    bad = [f.id for f in facts if f.unverified_quote]
    if exp.unverified_max is not None and len(bad) > exp.unverified_max:
        errs.append(f"facts: at most {exp.unverified_max} unverified quotes expected, got {bad}")
    if exp.unverified_min is not None and len(bad) < exp.unverified_min:
        errs.append(f"facts: at least {exp.unverified_min} unverified quotes expected, got {bad}")
    if exp.coverage is not None:
        cov = ledger.coverage(data)
        if cov.ratio < exp.coverage:
            errs.append(
                f"facts: coverage {cov.ratio:.2f} < {exp.coverage}; unreferenced {cov.unreferenced}"
            )
    return errs


def check_qa(exp: Any, data: Any, catalog: Any) -> list[str]:
    from mde_compass.scenarios import ledger

    errs: list[str] = []
    shown = data.shown_cards()
    n = len(shown)
    if exp.shown_min is not None and n < exp.shown_min:
        errs.append(f"qa: at least {exp.shown_min} questions shown expected, got {n}")
    if exp.shown_max is not None and n > exp.shown_max:
        errs.append(
            f"qa: at most {exp.shown_max} questions shown expected, got {n}: {[c.id for c in shown]}"
        )
    follow = sum(1 for c in shown if c.origin == "followup")
    if exp.followups is not None and follow != exp.followups:
        errs.append(f"qa: exactly {exp.followups} follow-ups expected, got {follow}")
    if exp.followups_max is not None and follow > exp.followups_max:
        errs.append(f"qa: at most {exp.followups_max} follow-ups expected, got {follow}")
    if exp.no_repeats:
        repeats = ledger.repeated_questions(shown)
        if repeats:
            errs.append(f"qa: repeated questions {repeats}")
    by_id = {q.card_id: q for q in data.qa}
    for cid, status in exp.statuses.items():
        got = by_id[cid].status if cid in by_id else "(no such question)"
        if got != status:
            errs.append(f"qa: {cid} should be {status}; got {got}")
    for cid, count in exp.versions.items():
        got_n = sum(1 for q in data.qa_versions if q.card_id == cid)
        if got_n != count:
            errs.append(f"qa: {cid} should keep {count} versions; got {got_n}")
    dropped = sum(1 for q in data.qa if q.status == "dropped")
    if exp.dropped_min is not None and dropped < exp.dropped_min:
        errs.append(f"qa: at least {exp.dropped_min} questions dropped expected, got {dropped}")
    texts = [f"{c.question} | {' | '.join(o.label for o in c.options)}" for c in shown]
    for needle in exp.shown_contains:
        if not _has(texts, needle):
            errs.append(f"qa: no shown question contains {needle!r}")
    for needle in exp.shown_not_contains:
        if _has(texts, needle):
            errs.append(f"qa: a shown question contains {needle!r}")
    first = shown[0] if shown else None
    for needle in exp.first_contains:
        if first is None or needle.lower() not in first.question.lower():
            errs.append(
                f"qa: the first question should contain {needle!r}; got {first.question if first else None!r}"
            )
    if exp.first_serves and (first is None or not set(first.need_ids) & set(exp.first_serves)):
        errs.append(
            f"qa: the first question should serve one of {exp.first_serves}; got {first.need_ids if first else None}"
        )
    served = {need for c in shown for need in c.need_ids}
    for need in exp.asked_needs:
        if need not in served:
            errs.append(f"qa: no shown question serves {need}; they serve {sorted(served)}")
    for need in exp.not_asked_needs:
        if need in served:
            errs.append(f"qa: a question serving {need} was shown, but the facts already answer it")
    origins = {c.origin for c in shown}
    for origin in exp.origins_include:
        if origin not in origins:
            errs.append(f"qa: no {origin} question was shown; origins: {sorted(origins)}")
    if exp.project_term_share_min is not None:
        with_term, scored, missing = ledger.project_term_report(
            data.planned_cards(), ledger.terms_of(data), catalog
        )
        share = with_term / scored if scored else 1.0
        if share < exp.project_term_share_min:
            errs.append(
                f"qa: project-term share {share:.2f} < {exp.project_term_share_min}; without a term: {missing}"
            )
    return errs


def check_interview(exp: Any, data: Any) -> list[str]:
    from mde_compass.scenarios import ledger

    errs: list[str] = []
    block = data.brief.meta.interview
    if exp.fallback_count is not None:
        got = block.fallback_count if block is not None else 0
        if got != exp.fallback_count:
            errs.append(f"interview: fallback_count should be {exp.fallback_count}; got {got}")
    if exp.stop_reason and (
        block is None or not any(r in block.stop_reason for r in exp.stop_reason)
    ):
        errs.append(
            f"interview: stop_reason should contain one of {exp.stop_reason}; got {block.stop_reason if block else None!r}"
        )
    if exp.budget and (block is None or block.budget != exp.budget):
        errs.append(
            f"interview: budget should be {exp.budget}; got {block.budget if block else None}"
        )
    nb = len(data.batches)
    if exp.batches_min is not None and nb < exp.batches_min:
        errs.append(f"interview: at least {exp.batches_min} planner batches expected, got {nb}")
    if exp.batches_max is not None and nb > exp.batches_max:
        errs.append(f"interview: at most {exp.batches_max} planner batches expected, got {nb}")
    rejected = [r for b in data.batches for r in b.rejected]
    if exp.rejected_min is not None and len(rejected) < exp.rejected_min:
        errs.append(
            f"interview: at least {exp.rejected_min} rejected questions expected, got {len(rejected)}"
        )
    reasons = [x for r in rejected for x in r.reasons]
    for code in exp.rejected_codes:
        if not _has(reasons, code):
            errs.append(f"interview: no rejection mentions {code!r}; reasons: {reasons[:8]}")
    audited = [r for r in rejected if r.source == "auditor"]
    if exp.auditor_rejected_min is not None and len(audited) < exp.auditor_rejected_min:
        errs.append(
            f"interview: at least {exp.auditor_rejected_min} auditor rejections expected, got {len(audited)}"
        )
    if exp.repaired is not None and any(b.repaired for b in data.batches) != exp.repaired:
        errs.append(f"interview: repaired should be {exp.repaired}")
    fb = sum(1 for b in data.batches if b.used_fallback)
    if exp.fallback_batches is not None and fb != exp.fallback_batches:
        errs.append(f"interview: {exp.fallback_batches} fallback batches expected, got {fb}")
    if exp.backlog_min is not None:
        backlog = sum(len(b.output.get("backlog") or []) for b in data.batches)  # type: ignore[arg-type]
        if backlog < exp.backlog_min:
            errs.append(
                f"interview: at least {exp.backlog_min} backlog items expected, got {backlog}"
            )
    if exp.batch_sizes_max is not None:
        for b in data.batches:
            if len(b.accepted) > exp.batch_sizes_max:
                errs.append(
                    f"interview: batch {b.id} holds {len(b.accepted)} questions, more than {exp.batch_sizes_max}"
                )
    _ = ledger
    return errs


def check_batch(exp: Any, events: Sequence[StoredEvent]) -> list[str]:
    batches = [e for e in events if e.type == "question_batch"]
    if not batches:
        return ["batch: no question_batch event in this step"]
    payload = batches[-1].payload
    groups = cast(list[dict[str, Any]], payload.get("groups") or [])
    cards = [c for g in groups for c in cast(list[dict[str, Any]], g.get("cards") or [])]
    errs: list[str] = []
    if exp.mode and payload.get("mode") != exp.mode:
        errs.append(f"batch: mode should be {exp.mode}; got {payload.get('mode')}")
    if exp.ui_mode and payload.get("ui_mode") != exp.ui_mode:
        errs.append(f"batch: ui_mode should be {exp.ui_mode}; got {payload.get('ui_mode')}")
    if exp.total_min is not None and len(cards) < exp.total_min:
        errs.append(f"batch: at least {exp.total_min} questions expected, got {len(cards)}")
    if exp.total_max is not None and len(cards) > exp.total_max:
        errs.append(f"batch: at most {exp.total_max} questions expected, got {len(cards)}")
    if exp.groups_min is not None and len(groups) < exp.groups_min:
        errs.append(f"batch: at least {exp.groups_min} groups expected, got {len(groups)}")
    if exp.groups_max is not None and len(groups) > exp.groups_max:
        errs.append(f"batch: at most {exp.groups_max} groups expected, got {len(groups)}")
    if exp.group_size_max is not None:
        for g in groups:
            if len(g.get("cards") or []) > exp.group_size_max:
                errs.append(
                    f"batch: group {g.get('id')} holds more than {exp.group_size_max} questions"
                )
    if exp.titles_filled and any(not str(g.get("title", "")).strip() for g in groups):
        errs.append("batch: every group needs a title")
    first = cards[0] if cards else None
    if exp.first_serves and (
        first is None or not set(first.get("need_ids", [])) & set(exp.first_serves)
    ):
        errs.append(
            f"batch: the first question should serve one of {exp.first_serves}; got {first.get('need_ids') if first else None}"
        )
    if exp.first_contains and (
        first is None
        or not any(w.lower() in str(first.get("question", "")).lower() for w in exp.first_contains)
    ):
        errs.append(
            f"batch: the first question should contain one of {exp.first_contains}; got {first.get('question') if first else None!r}"
        )
    lead = cards[: exp.lead]
    for c in lead:
        if c.get("type") in exp.lead_types_not:
            errs.append(
                f"batch: a lead question has type {c.get('type')}, which is too detailed to open with"
            )
        if exp.lead_serves_only and not set(c.get("need_ids", [])) <= set(exp.lead_serves_only):
            errs.append(
                f"batch: lead question {c.get('id')} serves {c.get('need_ids')}, not only {exp.lead_serves_only}"
            )
    texts = [str(c.get("question", "")) for c in cards]
    for needle in exp.texts_contain:
        if not _has(texts, needle):
            errs.append(f"batch: no question contains {needle!r}")
    for needle in exp.texts_not_contain:
        if _has(texts, needle):
            errs.append(f"batch: a question contains {needle!r}, which should have been rejected")
    origins = {str(c.get("origin")) for c in cards}
    for origin in exp.origins:
        if origin not in origins:
            errs.append(f"batch: no {origin} question; origins {sorted(origins)}")
    types = {c.get("type") for c in cards}
    for t in exp.types_include:
        if t not in types:
            errs.append(
                f"batch: no {t} question in the batch; types {sorted(str(x) for x in types)}"
            )
    return errs


def check_calls(exp: Any, gateway: Any) -> list[str]:
    errs: list[str] = []
    if gateway is None:
        return ["calls: this scenario has no traced gateway (give it a provider fixture)"]
    for spec_id, n in exp.counts.items():
        if gateway.called(spec_id) != n:
            errs.append(
                f"calls: {spec_id} should be called {n} times; got {gateway.called(spec_id)}"
            )
    for spec_id, n in exp.at_least.items():
        if gateway.called(spec_id) < n:
            errs.append(
                f"calls: {spec_id} should be called at least {n} times; got {gateway.called(spec_id)}"
            )
    for spec_id, n in exp.at_most.items():
        if gateway.called(spec_id) > n:
            errs.append(
                f"calls: {spec_id} should be called at most {n} times; got {gateway.called(spec_id)}"
            )
    for spec_id, modes in exp.modes.items():
        seen = [getattr(i, "mode", None) for s, i in gateway.inputs if s == spec_id]
        if seen != modes:
            errs.append(f"calls: {spec_id} modes should be {modes}; got {seen}")
    for spec_id, needles in exp.input_contains.items():
        last = gateway.last_input(spec_id)
        text = last.model_dump_json() if last is not None else ""
        for needle in needles:
            if needle.lower() not in text.lower():
                errs.append(f"calls: the last {spec_id} input should contain {needle!r}")
    return errs


async def check_memory(
    exp: Expect, *, conv: Conversation, events: Sequence[StoredEvent], gateway: Any | None
) -> list[str]:
    """The checks that read the interview memory (facts, Q&A, planner runs, Brief text) and the model calls."""
    errs: list[str] = []
    if exp.event_order:
        types = [e.type for e in events]
        pos = -1
        for t in exp.event_order:
            nxt = next((i for i in range(pos + 1, len(types)) if types[i] == t), None)
            if nxt is None:
                errs.append(f"event order {exp.event_order} not found in {types}")
                break
            pos = nxt
    if exp.blockers_contain:
        readies = [e for e in events if e.type == "brief_ready"]
        raw_blockers = cast(list[Any], readies[-1].payload.get("blockers", []) if readies else [])
        blockers = " ".join(str(b) for b in raw_blockers)
        for needle in exp.blockers_contain:
            if needle.lower() not in blockers.lower():
                errs.append(f"the Gate 1 blockers should mention {needle!r}; got {blockers!r}")
    if exp.calls is not None:
        errs.extend(check_calls(exp.calls, gateway))
    if exp.batch is not None:
        errs.extend(check_batch(exp.batch, events))
    wants_data = bool(
        exp.facts or exp.qa or exp.interview or exp.brief_contains or exp.brief_not_contains
        or exp.domain_facts_contains or exp.assumptions_min is not None
    )  # fmt: skip
    if not wants_data:
        return errs
    from mde_compass.catalog import load_catalog
    from mde_compass.scenarios import ledger

    data = await ledger.load_session(conv)
    if exp.facts:
        errs.extend(check_facts(exp.facts, data))
    if exp.qa:
        errs.extend(check_qa(exp.qa, data, load_catalog()))
    if exp.interview:
        errs.extend(check_interview(exp.interview, data))
    for needle in exp.brief_contains:
        if not ledger.needle_in_brief(data.brief, needle):
            errs.append(f"the Brief should contain {needle!r} somewhere")
    for needle in exp.brief_not_contains:
        if ledger.needle_in_brief(data.brief, needle):
            errs.append(f"the Brief should NOT contain {needle!r}")
    if exp.domain_facts_contains:
        rows = [f"{d.statement} | {d.quote}" for d in data.brief.domain_facts]
        for needle in exp.domain_facts_contains:
            if not _has(rows, needle):
                errs.append(f"domain_facts should contain {needle!r}; rows: {rows[:6]}")
    if exp.assumptions_min is not None and len(data.brief.assumptions) < exp.assumptions_min:
        errs.append(
            f"the Brief should hold at least {exp.assumptions_min} assumptions; has {len(data.brief.assumptions)}"
        )
    return errs


# --------------------------------------------------------------------------------------------- execution
class _Runner:
    def __init__(
        self, conv: Conversation, scenario: Scenario, engine: str = "legacy", gateway: Any = None
    ) -> None:
        self.conv = conv
        self.scn = scenario
        self.engine = engine
        self.gateway = gateway
        self._n = 0
        self.answers: list[
            tuple[str, Card]
        ] = []  # every card answer given so far (for the persona metrics)

    def eid(self) -> str:
        self._n += 1
        return f"evt_s{self._n:04d}"

    # a step may drive several inbound events; each helper returns the slice of events it caused
    async def _card(self) -> Card:
        card = self.conv.last_card()
        if card is None:
            raise ScenarioFailure("the step needs a card on screen, but none has been shown")
        return card

    async def _answer(self, value: Any, *, custom: str | None, who: str | None) -> None:
        card = await self._card()
        self.answers.append(("custom" if custom else "tap", card))
        await self.conv.send(
            CardAnswer(
                event_id=self.eid(),
                card_id=card.id,
                value=value,
                custom_text=custom,
                contributor_id=who,
            )
        )

    async def _slot_store(self) -> Any:
        from mde_compass.catalog import load_catalog
        from mde_compass.domain import Brief
        from mde_compass.engine.brief_builder import slots_from_brief
        from mde_compass.engine.slots import SlotStore

        svc = self.conv.service
        catalog = load_catalog()
        raw = await self.conv.brief()
        brief = Brief.model_validate({k: v for k, v in raw.items() if not k.startswith("_")})
        store = SlotStore(catalog, clock=svc.clock)
        store.load(slots_from_brief(brief, catalog, svc.clock))
        return store

    async def _form_value(self, card: Card) -> dict[str, Any]:
        """The answer a person who accepts what we already understood would send for a quick form: the held
        value of each slot, else the persona's ``form:`` value, else a plain neutral value of the right kind."""
        from mde_compass.catalog import load_catalog

        catalog = load_catalog()
        store = await self._slot_store()
        preset: dict[str, Any] = dict(self.scn.persona.get("form", {}))
        out: dict[str, Any] = {}
        for sid in card.slots:
            rec = store.get(sid)
            if sid in preset:
                out[sid] = preset[sid]
            elif rec is not None and rec.value not in (None, "", [], {}):
                out[sid] = rec.value
            elif sid in catalog.slots:
                kind = catalog.slots[sid].value_schema.kind
                opts = catalog.slots[sid].value_schema.options
                neutral: dict[str, Any] = {
                    "enum": opts[0] if opts else "",
                    "enum_list": opts[:1],
                    "text": "everyone who needs it",
                    "text_list": ["customers"],
                    "geo": {"city": "Pune"},
                    "locales": ["en"],
                    "bool": True,
                }
                if kind in neutral and neutral[kind] not in ("", []):
                    out[sid] = neutral[kind]
        return out

    async def _fill_required(self, who: str | None) -> None:
        """Edit every empty required slot (what the UI would do through "Change something") until none is left.

        Exists because the interview can open the review with a required, must-confirm slot never asked
        (BUG-1); scenarios that are about the gate and not about that bug use this to get a clean Brief.
        """
        from mde_compass.catalog import load_catalog
        from mde_compass.engine.validate import must_slots_missing

        catalog = load_catalog()
        for _ in range(5):
            store = await self._slot_store()
            missing = must_slots_missing(store, catalog, mode=self.scn.mode)
            if not missing:
                return
            for sid in missing:
                slot = catalog.slots[sid]
                opts = slot.value_schema.options
                value: Any = (
                    slot.default
                    if slot.default is not None
                    else (opts[0] if opts else "to be decided")
                )
                await self.conv.send(BriefEdit(event_id=self.eid(), item_id=slot.brief_path, value=value, contributor_id=who))  # fmt: skip

    def _card_ref(self, ref: Any) -> str:
        """``first`` / ``last`` question shown, or a card id (``Q-003``)."""
        planned = [
            Card.model_validate(e.payload["card"]) for e in self.conv.events if e.type == "card"
        ]
        planned = [c for c in planned if c.frame not in NON_QUESTION_FRAMES]
        if ref == "first" and planned:
            return planned[0].id
        if ref == "last" and planned:
            return planned[-1].id
        return str(ref)

    async def _feedback_target(self, arg: Any) -> str:
        """``target`` as written; on the planner path ``planner_target: slot:<slot id>`` names the assumption by its
        slot (assumption numbers depend on which questions the engine asked)."""
        target = str(arg["target"])
        alias = arg.get("planner_target") if self.engine == "planner" else None
        if not alias:
            return target
        slot_id = str(alias).removeprefix("slot:")
        brief = await self.conv.brief()
        for asm in brief.get("assumptions", []):
            if asm.get("slot_id") == slot_id:
                return str(asm["id"])
        raise ScenarioFailure(f"no assumption for slot {slot_id!r} to give feedback on")

    async def execute(self, step: Step) -> str:
        """Run one step; returns how a card was answered (tap | custom | you_decide | skip | text | other)."""
        conv, who, arg = self.conv, step.contributor, step.arg
        a = step.action
        if a in ("say", "say_voice"):
            text = arg["text"] if isinstance(arg, dict) else str(arg)
            hint = arg.get("lang_hint") if isinstance(arg, dict) else None
            await conv.send(
                UserMessage(event_id=self.eid(), text=text, contributor_id=who, lang_hint=hint,
                            input_mode="voice" if a == "say_voice" else "text")
            )  # fmt: skip
            return "text"
        if a == "tap":
            await self._answer(arg, custom=None, who=who)
            return "tap"
        if a == "tap_label":
            card = await self._card()
            needle = str(arg).lower()
            opt = next((o for o in card.options if needle in o.label.lower()), None)
            if opt is None:
                labels = [o.label for o in card.options]
                raise ScenarioFailure(
                    f"no option on card {card.id} has a label containing {arg!r}: {labels}"
                )
            await self._answer(
                [opt.id] if card.type == "pick_many" else opt.id, custom=None, who=who
            )
            return "tap"
        if a == "tap_recommended":
            card = await self._card()
            opt = card.recommended_option() or (card.options[0] if card.options else None)
            if self.engine == "planner" and not card.options and card.type != "quick_form":
                typed = str(self.scn.persona.get("typed", ""))
                typed = typed or next(
                    (TYPED_BY_NEED[n] for n in card.need_ids if n in TYPED_BY_NEED), TYPED_ANSWER
                )
                await self._answer(None, custom=typed, who=who)
                return "custom"
            if card.type == "quick_form":
                value: Any = await self._form_value(card)
            elif card.type == "priority":
                value = [o.id for o in card.options]
            elif card.type == "pick_many":
                value = [o.id for o in card.options if o.ticked] or ([opt.id] if opt else [])
            elif opt is None:
                value = "ok"
            else:
                value = opt.id
            await self._answer(value, custom=None, who=who)
            return "tap"
        if a == "answer":
            await self._answer(arg, custom=None, who=who)
            return "tap"
        if a == "answer_custom":
            await self._answer(None, custom=str(arg), who=who)
            return "custom"
        if a == "tap_and_custom":
            card = await self._card()
            spec = arg if isinstance(arg, dict) else {}
            if spec.get("recommended"):
                opt = card.recommended_option() or card.options[0]
                opt_id = opt.id
            else:
                opt_id = str(spec["option"])
            await self._answer(
                [opt_id] if card.type == "pick_many" else opt_id,
                custom=str(spec["custom"]),
                who=who,
            )
            return "custom"
        if a in ("tap_all", "tap_first"):
            card = await self._card()
            ids = [o.id for o in card.options]
            if a == "tap_first":
                ids = ids[: int(arg)]
            await self._answer(ids, custom=None, who=who)
            return "tap"
        if a == "back_edge":
            hook = getattr(self.conv.service, "reopen_slot", None)
            if hook is None:
                raise ScenarioFailure(
                    "the service has no typed back-edge hook (reopen_slot): not implemented"
                )
            spec = arg if isinstance(arg, dict) else {}
            result = await hook(
                self.conv.caller,
                self.conv.session_id,
                str(spec.get("slot")),
                str(spec.get("reason", "")),
            )
            self.conv.events.extend(
                getattr(result, "events", [])
            )  # the hook's events are this step's
            return "other"
        if a == "you_decide":
            card = await self._card()
            self.answers.append(("you_decide", card))
            await conv.send(CardYouDecide(event_id=self.eid(), card_id=card.id, contributor_id=who))
            return "you_decide"
        if a == "skip":
            card = await self._card()
            self.answers.append(("skip", card))
            await conv.send(CardSkip(event_id=self.eid(), card_id=card.id, contributor_id=who))
            return "skip"
        if a == "back":
            await conv.send(CardBack(event_id=self.eid(), contributor_id=who))
            return "other"
        if a == "undo":
            await conv.send(Command(event_id=self.eid(), name="undo", contributor_id=who))
            return "other"
        if a in ("pause", "resume"):
            await conv.send(Command(event_id=self.eid(), name=a, contributor_id=who))  # type: ignore[arg-type]
            return "other"
        if a == "command":
            name = arg["name"] if isinstance(arg, dict) else str(arg)
            await conv.send(Command(event_id=self.eid(), name=name, contributor_id=who))  # type: ignore[arg-type]
            return "other"
        if a == "edit":
            await conv.send(BriefEdit(event_id=self.eid(), item_id=str(arg["path"]), value=arg.get("value"), contributor_id=who))  # fmt: skip
            return "other"
        if a == "feedback":
            target = await self._feedback_target(arg)
            await conv.send(
                BriefFeedback(event_id=self.eid(), target=target, reason=arg.get("reason", "other"), text=str(arg.get("text", "")), contributor_id=who)  # type: ignore[arg-type]
            )  # fmt: skip
            return "other"
        if a == "decide":
            await conv.send(
                AssumptionDecision(event_id=self.eid(), id=str(arg["asm"]), decision=arg["decision"], value=arg.get("value"), contributor_id=who)  # type: ignore[arg-type]
            )  # fmt: skip
            return "other"
        if a == "accept_all":
            brief = await conv.brief()
            for asm in brief.get("assumptions", []):
                if asm.get("decision", "proposed") == "proposed":
                    await conv.send(AssumptionDecision(event_id=self.eid(), id=asm["id"], decision="accept", contributor_id=who))  # fmt: skip
            return "other"
        if a == "fill_required":
            await self._fill_required(who)
            return "other"
        if a == "lookup_add":
            text = arg["text"] if isinstance(arg, dict) else str(arg)
            await conv.send(
                LookupEdit(event_id=self.eid(), action="add", text=text, contributor_id=who)
            )
            return "other"
        if a == "lookup_remove":
            brief = await conv.brief()
            reqs = brief.get("research_requests", [])
            if isinstance(arg, dict) and arg.get("request_id"):
                rid = str(arg["request_id"])
            else:
                needle = (arg.get("text", "") if isinstance(arg, dict) else str(arg or "")).lower()
                pick = next(
                    (r for r in reqs if needle in json.dumps(r, ensure_ascii=False).lower()), None
                )
                if pick is None:
                    raise ScenarioFailure(
                        f"no research request matches {needle!r} (have {len(reqs)})"
                    )
                rid = pick["id"]
            await conv.send(
                LookupEdit(event_id=self.eid(), action="remove", request_id=rid, contributor_id=who)
            )
            return "other"
        if a in ("approve", "approve_stale"):
            brief = await conv.brief()
            stale = a == "approve_stale"
            version = int(brief["version"]) - (1 if stale else 0)
            hashed = ("0" * 64) if stale else str(brief["_hash"])
            sections = (
                dict.fromkeys(brief["_section_hashes"], "0" * 64)
                if stale
                else dict(brief["_section_hashes"])
            )
            await conv.send(
                GateDecision(
                    event_id=self.eid(),
                    contributor_id=who,
                    decision="approve",
                    version=version,
                    brief_hash=hashed,
                    section_hashes=sections,
                )  # fmt: skip
            )
            return "other"
        if a == "switch":
            await conv.send(ContributorSwitch(event_id=self.eid(), contributor_id=str(arg)))
            return "other"
        if a == "upload":
            spec = arg if isinstance(arg, dict) else {}
            res = await conv.service.ingest_upload(
                conv.caller,
                conv.session_id,
                str(spec.get("name", "notes.txt")),
                str(spec["text"]).encode(),
            )
            conv.events.extend(res.events)
            return "other"
        if a == "edit_answer":
            spec = arg if isinstance(arg, dict) else {}
            await conv.send(
                AnswerEdit(
                    event_id=self.eid(),
                    card_id=self._card_ref(spec.get("card", "first")),
                    value=spec.get("value"),
                    custom_text=spec.get("custom"),
                    contributor_id=who,
                )
            )
            return "other"
        if a == "say_many":
            for text in cast(list[Any], arg):
                await self.execute(Step(action="say", arg=text, contributor=who))
            return "text"
        if a == "answer_texts":
            for text in cast(list[Any], arg):
                await self._answer(None, custom=str(text), who=who)
            return "custom"
        if a == "drive":
            spec = arg if isinstance(arg, dict) else {}
            mode = spec.get("mode", "tap_recommended")
            until = spec.get("until", "BRIEF_REVIEW")
            limit = int(spec.get("max", 40))
            until_frame = spec.get("until_frame")
            for _ in range(limit):
                if await conv.state() == until or conv.last_card() is None:
                    break
                if until_frame and in_frame(conv.last_card(), [until_frame], self.engine):  # type: ignore[arg-type]
                    break
                card = conv.last_card()
                if card is not None and card.must_confirm and mode in ("you_decide", "skip"):
                    await self.execute(Step(action="tap_recommended", arg=True, contributor=who))
                else:
                    await self.execute(Step(action=mode, arg=True, contributor=who))
            return mode if mode in ("you_decide", "skip") else "tap"
        raise ScenarioFailure(f"unknown action {a!r}")


def _format_failure(
    scn: Scenario,
    i: int,
    step: Step,
    errors: list[str],
    events: Sequence[StoredEvent],
    all_events: Sequence[StoredEvent],
) -> str:
    shown = list(events) or list(all_events[-6:])
    lines = [
        f"{scn.id} failed at step {i + 1} ({step.action}: {json.dumps(step.arg, ensure_ascii=False, default=str)[:160]})",
        *(f"  - {e}" for e in errors),
        f"  expect: {step.expect.model_dump(exclude_none=True, exclude_defaults=True) if step.expect else {}}",
        "  last events:",
        *(f"    {describe_event(e)}" for e in shown[-10:]),
    ]
    return "\n".join(lines)


async def run_scenario(
    scn: Scenario,
    *,
    engine: Engine | None = None,
    service: Any | None = None,
    service_factory: Callable[[], Any] | None = None,
) -> RunResult:
    """Run every step in order against a fresh service; stops at the first failed expectation.

    ``engine`` picks the question engine of a service built here; for a service passed in (or made by
    ``service_factory``) the engine is read from its configuration, so the frame translation always matches."""
    svc: Any = service
    gateway: Any = None
    if svc is None:
        if service_factory is not None:
            svc = service_factory()
        elif (engine or "legacy") == "planner" and (script := _provider_for(scn)) is not None:
            svc, gateway = new_scripted_service("planner", script, scn.policy)
            svc.scripted_gateway = gateway
        else:
            svc = new_service(engine or "legacy")
    service = svc
    engine = cast(Engine, svc.config.interview.engine)
    gateway = gateway or getattr(svc, "scripted_gateway", None)
    conv = await Conversation.start(svc, mode=scn.mode, locale=scn.locale)
    result = RunResult(scenario=scn, conv=conv)
    if scn.contributors:
        await _register_contributors(service, conv, scn)
    runner = _Runner(conv, scn, engine, gateway)
    for i, step in enumerate(scn.steps):
        before_n = len(conv.events)
        state_before = await conv.state()
        version_before = int((await conv.brief())["version"])
        answers_before = len(runner.answers)
        errors: list[str] = []
        kind = "other"
        try:
            kind = await runner.execute(step)
        except ScenarioFailure as exc:
            errors.append(str(exc))
        except Exception as exc:  # the service raised: a real product or script problem
            errors.append(f"the service raised {type(exc).__name__}: {exc}")
        events = conv.events[before_n:]
        state_after = await conv.state()
        version_after = int((await conv.brief())["version"])
        result.records.append(
            StepRecord(index=i, action=step.action, kind=kind, events=list(events), state_before=state_before,
                       state_after=state_after, version_before=version_before, version_after=version_after,
                       answers=list(runner.answers[answers_before:]))
        )  # fmt: skip
        if not errors:
            errors = await check_expect(
                step.expect, conv=conv, events=events, state_after=state_after,
                version_before=version_before, version_after=version_after,
                card_on_screen=conv.last_card() if step.action == "drive" else None, engine=engine,
                gateway=gateway,
            )  # fmt: skip
        if errors:
            result.failure = _format_failure(scn, i, step, errors, events, conv.events)
            result.failed_step = i
            return result
    leaks = locked_without_evidence(await conv.brief())
    if leaks:
        result.failure = f"{scn.id}: Locked items without evidence: {leaks}"
        result.failed_step = len(scn.steps)
        return result
    if scn.final is not None:
        errors = await check_expect(
            scn.final, conv=conv, events=[], state_after=await conv.state(),
            version_before=0, version_after=int((await conv.brief())["version"]), engine=engine,
            gateway=gateway,
        )  # fmt: skip
        if errors:
            last = Step(action="final", arg=None, expect=scn.final)
            result.failure = _format_failure(scn, len(scn.steps), last, errors, [], conv.events)
            result.failed_step = len(scn.steps)
    if result.failure is None and gateway is not None and hasattr(gateway, "unconsumed"):
        left = gateway.unconsumed()
        if left:  # strict: an answer nobody asked for means the script and the engine disagree
            result.failure = f"{scn.id}: scripted model answers never used: {left}"
            result.failed_step = len(scn.steps)
    return result


def _provider_for(scn: Scenario) -> Any | None:
    """The scenario's provider fixture (``providers/<id>.yaml`` or the file named by ``provider:``), if any."""
    from mde_compass.scenarios.providers import find_provider, load_provider

    if not scn.source_dir:
        return None
    found = find_provider(scn.source_dir, scn.id, scn.provider)
    return load_provider(found) if found is not None else None


async def _register_contributors(service: Any, conv: Conversation, scn: Scenario) -> None:
    """Add the scenario's contributors to the session (enterprise mode); the creator stays the decision-owner."""
    wanted = [
        Contributor(
            id=c.id, name=c.name or c.id, role=cast(Any, c.role), decision_owner=c.decision_owner
        )
        for c in scn.contributors
    ]
    async with service.store.begin(conv.caller.tenant_id) as uow:
        rec = await uow.sessions.get(conv.session_id)
        by_id = {c.id: c for c in rec.contributors}
        for c in wanted:
            by_id[c.id] = c
            await uow.contributors.upsert(conv.session_id, c)
        rec.contributors = list(by_id.values())
        await uow.sessions.save(rec)
