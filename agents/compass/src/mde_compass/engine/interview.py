"""The interview controller of the AI-planned interview (build prompt 02; ADR 0015; doc 11 section 17).

The model proposes questions and reads answers; THIS code decides what is shown, what is stored and when to stop.
No branch of the flow is chosen by a model. A turn is: load, think (these methods, with model calls but no open
transaction), commit (``Service._commit`` writes ``turn.memory`` in one unit of work).

Round 0 is two turns, chained inside ``Service.handle_event``: the first reads the idea and shows the playback
card; an internal ``prepare_questions`` command then plans the first batch, so the person reads the playback while
the planner works. After that every answer goes through ``on_answers``: record the Q&A, interpret it into facts,
project the facts onto slots, recompute the needs, then decide (follow-up, next question, replan or stop).
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, cast

from pydantic import JsonValue

from mde_compass.domain.cards import Card, CardProgress
from mde_compass.domain.events import CardEvent, Command, Notice, Progress, QuestionBatch
from mde_compass.domain.interview import (
    BatchGroup,
    BatchRecord,
    Fact,
    FactType,
    InterviewBlock,
    InterviewSummary,
    NeedStatus,
    PlannedBatch,
    PlannerMode,
    QaRecord,
)
from mde_compass.engine import cards as cards_mod
from mde_compass.engine import fact_projector, interview_policy
from mde_compass.engine import facts as facts_mod
from mde_compass.engine import needs as needs_mod
from mde_compass.engine import terms as terms_mod
from mde_compass.engine.context_pack import build_context, estimate_tokens, full_ledger, ledger_line
from mde_compass.engine.interview_support import (
    build_fact,
    counts_by_need,
    derived_facts,
    inputs_hash,
    need_briefs,
    next_card_number,
    project_card,
    refresh_block,
    slots_of,
    style_notes,
    text_of,
)
from mde_compass.engine.ports import (
    AnswerFocus,
    ExtractedFact,
    PlanContext,
    PlanRequest,
    RephraseRequest,
    SlotUpdate,
    SummaryRequest,
    TurnUnderstanding,
)
from mde_compass.engine.question_pipeline import PipelineResult, plan_validated
from mde_compass.engine.question_validators import ValidationContext

if TYPE_CHECKING:  # pragma: no cover
    from mde_compass.engine.service import Service, _Turn

Action = Literal["answer", "skip", "you_decide"]
BudgetName = Literal["quick", "standard", "thorough"]
PLAN_KINDS = ("initial", "followup", "replan", "deepen", "simplify")


FINISHED_BY_PERSON = "the person asked to finish with defaults"


@dataclass
class AnswerItem:
    card: Card
    action: Action = "answer"
    value: JsonValue = None
    custom_text: str | None = None
    input_mode: Literal["tap", "text", "voice"] = "tap"


def _untag(text: str) -> str:
    return text.replace("[mock]", "").strip()


def internal_event_id(parent: str, name: str) -> str:
    """A stable id for a chained internal event (retries find it again; it fits the event id pattern)."""
    return f"int_{hashlib.sha1(f'{parent}:{name}'.encode()).hexdigest()[:24]}"  # noqa: S324 - not a secret


class InterviewController:
    def __init__(self, svc: Service) -> None:
        self.svc = svc

    # ------------------------------------------------------------------------------------ shortcuts
    @property
    def policy(self) -> Any:
        return self.svc.config.interview

    @property
    def active(self) -> bool:
        return bool(self.policy.engine == "planner")

    @property
    def catalog(self) -> Any:
        return self.svc.catalog

    def _now(self) -> Any:
        return self.svc.clock.now()

    # -------------------------------------------------------------------------------- budget and mode
    def budget_name(self, turn: _Turn) -> BudgetName:
        plan = self.svc._plan(turn)
        if plan.interview_budget:
            return plan.interview_budget
        profile = turn.brief.meta.situation_profile
        if profile is not None and profile.urgency == "just_build":
            return "quick"
        if plan.depth == "large":
            return "thorough"
        return "standard"

    def budget_max(self, turn: _Turn) -> int:
        return int(self.policy.budget_max(self.budget_name(turn)))

    def ui_mode(self, turn: _Turn) -> Literal["one_at_a_time", "answer_all"]:
        block = turn.brief.meta.interview
        if block is not None and block.ui_mode_set:
            return block.ui_mode
        profile = turn.brief.meta.situation_profile
        if profile is not None and profile.expertise in ("technical", "product"):
            return "answer_all"
        return "one_at_a_time"

    def block(self, turn: _Turn) -> InterviewBlock:
        if turn.brief.meta.interview is None:
            turn.brief.meta.interview = InterviewBlock(
                budget=self.budget_name(turn), ui_mode=self.ui_mode(turn)
            )
        return turn.brief.meta.interview

    def touch(self, turn: _Turn) -> None:
        """Refresh the audit block from the ledgers."""
        block = self.block(turn)
        mem = turn.memory
        turn.brief.meta.interview = refresh_block(
            block.model_copy(update={"budget": self.budget_name(turn)}),
            mem.qa,
            mem.facts,
            len(mem.batches),
        )

    # --------------------------------------------------------------------------------------- needs
    def statuses(self, turn: _Turn) -> dict[str, NeedStatus]:
        mem = turn.memory
        return needs_mod.compute(
            self.catalog,
            mem.active_facts(),
            turn.slots,
            assumptions=turn.brief.assumptions,
            asked=counts_by_need(mem.qa),
            followups=counts_by_need(mem.qa, followups_only=True),
            mode=turn.session.mode,
        )

    def _terms(self, turn: _Turn) -> frozenset[str]:
        idea = text_of(turn.brief.idea["summary"].value) if "summary" in turn.brief.idea else ""
        glossary = [g.term for g in turn.brief.glossary]
        active = turn.memory.active_facts()
        said = " ".join(
            f.quote for f in active if f.quote
        )  # verbatim words, not our statement labels
        entities = [e for f in active for e in f.entities]
        return terms_mod.project_terms(
            f"{idea} {turn.original_text} {said}", (), glossary, extra=entities
        )

    def _context(self, turn: _Turn, statuses: dict[str, NeedStatus]) -> PlanContext:
        mem = turn.memory
        answered = [q for q in mem.qa if q.status in ("answered", "skipped", "you_decide")]
        pack = build_context(
            project_card=project_card(turn.brief, turn.lang, turn.session.mode),
            facts=mem.active_facts(),
            needs_table=needs_mod.needs_table(statuses, self.catalog),
            asked=mem.qa,
            last_qa=answered[-self.svc.config.context.last_qa_count :],
            summary=mem.summary,
            budgets=self.svc.config.context,
        )
        return PlanContext(
            project_card=pack.project_card.text,
            fact_digest=pack.fact_digest.text,
            needs_table=pack.needs_table.text,
            asked_list=pack.asked_list.text,
            last_qa=pack.last_qa.text,
        )

    def _request(
        self,
        turn: _Turn,
        mode: PlannerMode,
        statuses: dict[str, NeedStatus],
        *,
        focus: AnswerFocus | None = None,
        focus_needs: list[str] | None = None,
        missing_aspects: list[str] | None = None,
        pivot_from: str | None = None,
        depth: int = 1,
        want: tuple[int, int] | None = None,
    ) -> PlanRequest:
        asked = turn.memory.asked_count()
        budget = self.budget_name(turn)
        remaining = max(0, self.budget_max(turn) - asked)
        if want is not None:  # an explicit size (a handoff gap) may go one question past the budget
            lo, hi = want
        else:
            lo, hi = interview_policy.batch_size_range(budget, asked, self.policy)
            if mode in ("followup", "simplify"):
                lo = hi = 1
            hi = (
                max(0, min(hi, remaining)) if mode != "deepen" else max(1, min(hi, remaining or hi))
            )
            lo = min(lo, hi)
        briefs = need_briefs(statuses, self.catalog, turn.lang)
        involved = [*(focus_needs or []), *(focus.need_ids if focus else [])] or [
            b.need_id for b in briefs if b.state in ("missing", "partial")
        ]
        notes = style_notes(self.catalog, involved)
        for prior in turn.memory.batches[-2:]:
            backlog = prior.output.get("backlog")
            for item in backlog if isinstance(backlog, list) else []:
                if isinstance(item, dict) and item.get("topic"):
                    notes.append(f"Backlog topic worth asking: {item['topic']}")
        return PlanRequest(
            mode=mode,
            language=turn.lang,
            context=self._context(turn, statuses),
            needs=briefs,
            project_terms=sorted(self._terms(turn)),
            want_min=lo,
            want_max=hi,
            group_min=self.policy.groups_per_batch[0],
            group_max=self.policy.groups_per_batch[1],
            remaining_budget=remaining,
            focus_needs=list(focus_needs or []),
            focus=focus,
            missing_aspects=list(missing_aspects or []),
            pivot_from=pivot_from,
            followup_depth=depth,
            style_notes=notes[:8],
            next_card_n=next_card_number(turn.asked_cards(), turn.memory.qa),
        )

    def _grounding(self, turn: _Turn) -> tuple[list[str], list[str]]:
        """What an option may rest on: ``evidence`` (the person's recent words and document quotes, for
        ``your_document`` and ``your_words``) and ``hints`` (typical labels from the hypothesis and the families,
        for ``typical``)."""
        evidence = [line.text for line in turn.transcript if line.role == "user"][-3:]
        evidence += [
            f.quote for f in turn.memory.active_facts() if f.origin == "document" and f.quote
        ]
        hyp = turn.brief.meta.hypothesis
        hints: list[str] = []
        family_ids: list[str] = []
        if hyp is not None:
            hints += [f.name for f in hyp.likely_features if not f.custom]
            hints += list(hyp.likely_roles) + list(hyp.business_model_candidates)
            family_ids = list(hyp.likely_families)
        category = turn.slots.get("idea.category")
        if (
            category is not None
            and isinstance(category.value, str)
            and category.value in self.catalog.families
        ):
            family_ids.insert(0, category.value)
        hints += cards_mod.family_hints(
            self.catalog,
            ["scope.features", "roles.list", "integrations.wanted", "idea.business_model"],
            family_ids[:2],
            language=turn.lang,
        )
        return evidence, list(dict.fromkeys(hints))

    def _vctx(self, turn: _Turn, request: PlanRequest) -> ValidationContext:
        evidence, hints = self._grounding(turn)
        return ValidationContext.build(
            self.catalog,
            request.project_terms,
            language=turn.lang,
            ledger_cards=[q.card for q in turn.memory.qa],
            config=self.svc.config,
            user_text=self.svc._corpus(turn),
            evidence=evidence,
            hints=hints,
            batch_limit=max(request.want_max, 1),
        )

    # -------------------------------------------------------------------------------------- planning
    async def plan(
        self, turn: _Turn, mode: PlannerMode, statuses: dict[str, NeedStatus], **kw: Any
    ) -> tuple[PipelineResult, str | None]:
        """Plan one batch through the pipeline; returns the result and the id of the stored batch (None when a
        retried turn found its earlier answer)."""
        assert self.svc.ports is not None
        ports = self.svc.ports
        request = self._request(turn, mode, statuses, **kw)
        digest = inputs_hash(request)
        stored = turn.memory.find_batch(mode, digest)
        if stored is not None:
            batch = PlannedBatch.model_validate(stored.output)
            return PipelineResult(batch=batch), None
        started = time.monotonic()
        ctx = self.svc._ctx(turn)
        result = await plan_validated(
            planner=ports.question_planner,
            auditor=ports.question_auditor,
            ctx=ctx,
            request=request,
            vctx=self._vctx(turn, request),
            policy=self.policy,
            catalog=self.catalog,
            statuses=statuses,
            obligations=needs_mod.expert_gap_obligations(statuses, self.catalog, self.svc.config),
            needs_table=request.context.needs_table,
            asked_list=request.context.asked_list,
        )
        batch_id = turn.memory.next_batch_id()
        cards = [c.model_copy(update={"batch_id": batch_id}) for c in result.batch.cards()]
        by_id = {c.id: c for c in cards}
        groups = [
            BatchGroup(id=g.id, title=g.title, cards=[by_id[c.id] for c in g.cards])
            for g in result.batch.groups
        ]
        result.batch = result.batch.model_copy(update={"batch_id": batch_id, "groups": groups})
        record = BatchRecord(
            id=batch_id,
            mode=mode,
            inputs_hash=digest,
            output=cast(dict[str, JsonValue], result.batch.model_dump(mode="json")),
            accepted=[c.id for c in cards],
            rejected=result.rejected,
            verdicts=result.verdicts,
            used_fallback=result.used_fallback,
            repaired=result.repaired,
            latency_ms=int((time.monotonic() - started) * 1000),
            created_at=self._now(),
        )
        turn.memory.add_batch(record)
        turn.memory.fallbacks += result.fallbacks
        block = self.block(turn)
        turn.brief.meta.interview = block.model_copy(
            update={"fallback_count": block.fallback_count + result.fallbacks}
        )
        for card in cards:
            turn.memory.put_qa(
                QaRecord(
                    card_id=card.id,
                    batch_id=batch_id,
                    group_id=card.group_id,
                    mode=mode,
                    card=card,
                    status="open",
                    parent_card_id=card.parent_card_id,
                )
            )
        return result, batch_id

    # ----------------------------------------------------------------------------------- showing it
    def _progress(self, turn: _Turn) -> CardProgress:
        asked = turn.memory.asked_count()
        open_n = len(turn.memory.open_qa())
        total = min(self.budget_max(turn), asked + max(open_n, 1))
        return CardProgress(n=asked + 1, of_about=max(total, asked + 1))

    def _emit_batch(self, turn: _Turn, batch: PlannedBatch) -> None:
        cards = batch.cards()
        if not cards:
            return
        turn.out.append(
            QuestionBatch(
                batch_id=batch.batch_id,
                mode=batch.mode,
                ui_mode=self.ui_mode(turn),
                groups=batch.groups,
                total=len(cards),
                answered=0,
            )
        )

    def _show(self, turn: _Turn, qa: QaRecord, *, emit_card: bool = True) -> Card:
        progress = self._progress(turn)
        card = qa.card.model_copy(update={"progress": progress})
        turn.memory.put_qa(qa.model_copy(update={"card": card, "asked_at": self._now()}))
        turn.asked.append(
            (card, {"frame": "planned", "action": "ask", "score": 0.0, "reason": card.intent})
        )
        if emit_card:
            turn.out.append(Progress(stage="interview", n=progress.n, of_about=progress.of_about))
            turn.out.append(CardEvent(card=card))
        return card

    def show_next(self, turn: _Turn) -> bool:
        """Put the next waiting question to the person (all of the batch in answer-all mode)."""
        waiting = [q for q in turn.memory.open_qa() if q.asked_at is None]
        if not waiting:
            return False
        waiting.sort(
            key=lambda q: q.card.origin != "followup"
        )  # stable: batch order inside each kind
        if self.ui_mode(turn) == "answer_all":
            batch_id = waiting[0].batch_id
            same = [q for q in waiting if q.batch_id == batch_id]
            for i, qa in enumerate(same):
                self._show(turn, qa, emit_card=i == 0)
            return True
        self._show(turn, waiting[0])
        return True

    def pending(self, turn: _Turn) -> list[QaRecord]:
        """Questions on screen that nobody has answered yet (shown, still open), oldest first."""
        return [q for q in turn.memory.open_qa() if q.asked_at is not None]

    def reshow_pending(self, turn: _Turn) -> bool:
        """After a typed message that did not answer them, the questions still on screen come back (never
        skipped, never counted as asked again). One at a time: the latest one; answer-all: the whole form."""
        waiting = self.pending(turn)
        if not waiting:
            return False
        if self.ui_mode(turn) == "answer_all":
            self._relayout(turn, "answer_all")
        else:
            turn.out.append(CardEvent(card=waiting[-1].card))
        return True

    # ------------------------------------------------------------------------------------ round 0
    async def prepare(self, turn: _Turn) -> None:
        """Plan and show the first batch (the internal ``prepare_questions`` turn after the playback card)."""
        if not self.active or turn.memory.batches:
            return
        statuses = self.statuses(turn)
        self.block(turn)
        turn.brief.meta.plan = self.svc._plan(turn).model_copy(
            update={"interview_budget": self.budget_name(turn)}
        )
        result, _ = await self.plan(turn, "initial", statuses)
        hyp = result.batch.domain_hypothesis
        if hyp is not None:
            self.svc._merge_hypothesis(turn, hyp)
        self._emit_batch(turn, result.batch)
        self.touch(turn)
        if not self.show_next(turn):
            self.svc._go(turn, "queue_empty", "SEEDING")
            return
        turn.state = "INTERVIEWING"

    # -------------------------------------------------------------------------- the next question
    def _drop_answered(self, turn: _Turn, statuses: dict[str, NeedStatus]) -> None:
        """Questions whose needs the facts already satisfy are no longer needed (the matching cards disappear)."""
        for qa in turn.memory.open_qa():
            slot_needs = [n for n in qa.card.need_ids if n in self.catalog.slots]
            if slot_needs and all(
                statuses.get(n) is not None and statuses[n].state == "satisfied" for n in slot_needs
            ):
                turn.memory.put_qa(qa.model_copy(update={"status": "dropped"}))
                if qa.asked_at is not None:
                    turn.answered.append((qa.card_id, "answered", {"dropped": True}))  # type: ignore[arg-type]

    async def next_question(self, turn: _Turn) -> Literal["asked", "scheduled", "done"]:
        """What the interview does next. ``asked``: a question is on screen; ``scheduled``: round 0 continues in
        an internal turn; ``done``: nothing more to ask, the pipeline moves on to seeding."""
        statuses = self.statuses(turn)
        self._drop_answered(turn, statuses)
        if turn.reshow_pending and self.reshow_pending(turn):
            self.touch(turn)
            return "asked"
        if self.show_next(turn):
            self.touch(turn)
            return "asked"
        if not turn.memory.batches:
            is_prepare = isinstance(turn.event, Command) and turn.event.name == "prepare_questions"
            if not is_prepare:
                turn.internal = Command(
                    event_id=internal_event_id(turn.turn_id, "prepare_questions"),
                    name="prepare_questions",
                )
                return "scheduled"
            await self.prepare(turn)
            return "asked" if turn.state == "INTERVIEWING" else "done"
        # a batch just ended: audit the gaps, then replan or stop
        return await self._after_batch(turn, statuses)

    def _history(self, turn: _Turn) -> list[QaRecord]:
        return [q for q in turn.memory.qa if q.status in ("answered", "skipped", "you_decide")]

    async def _after_batch(
        self, turn: _Turn, statuses: dict[str, NeedStatus], *, said_enough: bool = False
    ) -> Literal["asked", "done"]:
        mem = turn.memory
        await self.refresh_summary(turn)
        new_facts = sum(1 for f in mem.facts if f.id in mem.new_fact_ids)
        decision = interview_policy.stop_decision(
            statuses=statuses,
            budget_name=self.budget_name(turn),
            asked_total=mem.asked_count(),
            last_batch_new_facts=new_facts,
            user_said_enough=said_enough,
            fatigue_flag=interview_policy.fatigue(self._history(turn), self.policy),
            policy=self.policy,
        )
        if decision.stop:
            self.finish(turn, decision.reason, decision.remaining_needs_to_assume)
            return "done"
        result, _ = await self.plan(turn, "replan", statuses)
        if not result.batch.cards():
            self.finish(turn, "the planner has nothing important left to ask", [])
            return "done"
        self._emit_batch(turn, result.batch)
        self.show_next(turn)
        self.touch(turn)
        return "asked"

    def person_finished(self, turn: _Turn) -> bool:
        """The person asked to stop and use defaults: no further question may be put to them (probe gaps are assumed)."""
        block = turn.brief.meta.interview
        return self.active and block is not None and block.stop_reason == FINISHED_BY_PERSON

    def finish(self, turn: _Turn, reason: str, remaining: Sequence[str]) -> None:
        """Stop asking: what is still open becomes visible assumptions (never silence)."""
        for qa in turn.memory.open_qa():
            turn.memory.put_qa(qa.model_copy(update={"status": "dropped"}))
        block = self.block(turn)
        turn.brief.meta.interview = block.model_copy(update={"stop_reason": reason})
        self.touch(turn)

    # ------------------------------------------------------------------------------------- answers
    def _labels_and_text(self, item: AnswerItem) -> tuple[list[str], str]:
        card, value = item.card, item.value
        by_id = {o.id: o for o in card.options}
        ids = value if isinstance(value, list) else [value]
        labels = [by_id[str(i)].label for i in ids if str(i) in by_id]
        parts = list(labels)
        if isinstance(value, dict):
            parts.append("; ".join(f"{k}: {text_of(v)}" for k, v in value.items() if text_of(v)))
        elif isinstance(value, str) and not labels and value.strip() and value != "ok":
            parts.append(value.strip())
        if item.custom_text and item.custom_text.strip():
            parts.append(item.custom_text.strip())
        return labels, " | ".join(p for p in parts if p)

    def _registry_match(self, item: AnswerItem) -> TurnUnderstanding | None:
        """A planner question about one enumerated need whose chosen option has the registry option's label: its
        value is the registry's, so no model is needed to read it."""
        card = item.card
        slots = [s for s in card.slots if s in self.catalog.slots]
        if card.origin != "planner" or len(slots) != 1 or item.custom_text:
            return None
        labels, _ = self._labels_and_text(item)
        slot = self.catalog.slots[slots[0]]
        if not labels or slot.value_schema.kind not in ("enum", "enum_list"):
            return None
        lang = card.language if card.language in ("en", "hi") else "en"
        registry = {_untag(o.label.get(lang)).casefold(): o for o in slot.fallback_card.options}
        matched = [registry.get(_untag(label).casefold()) for label in labels]
        if any(m is None or m.value is None for m in matched):
            return None
        values = [m.value for m in matched if m is not None]
        value: JsonValue = cast(
            JsonValue, values if slot.value_schema.kind == "enum_list" else values[0]
        )
        return TurnUnderstanding(
            intent="answer",
            slot_updates=[
                SlotUpdate(
                    slot_id=slot.id,
                    value=value,
                    quote=" / ".join(labels),
                    confidence=0.9,
                    basis="user_text",
                )
            ],
        )

    def _writes_understanding(self, item: AnswerItem) -> TurnUnderstanding | None:
        """A card whose options carry slot values (the registry's last-resort cards) needs no model to read."""
        card = item.card
        carries_values = any(o.sets for o in card.options)
        if not (carries_values or card.origin == "fallback"):
            return None  # a planner question: the model reads the answer
        if carries_values and item.custom_text and card.origin != "fallback":
            return None
        writes = cards_mod.answer_to_writes(card, item.value, item.custom_text, self.catalog)
        if not writes:
            return None
        labels, _ = self._labels_and_text(item)
        quote = " / ".join(labels)
        lang = card.language if card.language in ("en", "hi") else "en"
        return TurnUnderstanding(
            intent="answer",
            slot_updates=[
                SlotUpdate(
                    slot_id=w.slot_id,
                    value=self.svc._label_codes(w.slot_id, w.value, lang),
                    quote=quote,
                    confidence=0.9,
                    basis="user_text",
                )
                for w in writes
            ],
        )

    async def _interpret(
        self, turn: _Turn, item: AnswerItem, statuses: dict[str, NeedStatus]
    ) -> TurnUnderstanding:
        assert self.svc.ports is not None
        direct = self._writes_understanding(item) or self._registry_match(item)
        if direct is not None:
            return direct
        labels, text = self._labels_and_text(item)
        card = item.card
        focus = AnswerFocus(
            card_id=card.id,
            question=card.question,
            intent=card.intent,
            need_ids=list(card.need_ids),
            selected_labels=labels,
            answer_text=item.custom_text or (text if not labels else ""),
            input_mode=item.input_mode,
        )
        every = need_briefs(statuses, self.catalog, turn.lang)
        briefs = [b for b in every if b.need_id in card.need_ids]
        # the card's own needs first, then a few other open ones, so what the person also said is credited
        # (a detailed answer often covers more than the one need that was asked)
        others = [
            b for b in every if b.state in ("missing", "partial") and b.need_id not in card.need_ids
        ]
        briefs += others[:16]
        digest = facts_mod.digest(
            turn.memory.active_facts(), budget_tokens=self.svc.config.context.fact_digest // 3
        )
        mode: Literal["text", "voice"] = "voice" if item.input_mode == "voice" else "text"
        return await self.svc.ports.understander.understand(
            self.svc._ctx(turn),
            text,
            input_mode=mode,
            focus=focus,
            needs=briefs,
            fact_digest=digest,
        )

    async def understand_text(self, turn: _Turn, text: str, input_mode: str) -> TurnUnderstanding:
        """Read a free message with the open needs and the fact digest, so what it says becomes facts."""
        assert self.svc.ports is not None
        statuses = self.statuses(turn)
        briefs = [
            b
            for b in need_briefs(statuses, self.catalog, turn.lang)
            if b.state in ("missing", "partial")
        ][:12]
        digest = facts_mod.digest(
            turn.memory.active_facts(), budget_tokens=self.svc.config.context.fact_digest // 3
        )
        mode: Literal["text", "voice"] = "voice" if input_mode == "voice" else "text"
        return await self.svc.ports.understander.understand(
            self.svc._ctx(turn), text, input_mode=mode, needs=briefs, fact_digest=digest
        )

    def ingest(
        self,
        turn: _Turn,
        understood: TurnUnderstanding,
        *,
        sources: list[str],
        source_ref: str,
        card: Card | None = None,
        tap: bool = False,
        input_mode: str = "text",
    ) -> tuple[list[str], list[str]]:
        """Turn an interpretation into facts, project them onto slots, apply corrections. Returns the new fact ids
        and the chips of what was picked up."""
        mem = turn.memory
        now = self._now()
        extracted: list[ExtractedFact] = list(understood.facts)
        extracted += derived_facts(
            understood,
            slots_of(extracted),
            self.catalog,
            turn.lang,
            complete=tap,
            close_others_at=self.policy.high_confidence_closes_need,
            own_needs=frozenset(card.need_ids) if card is not None else frozenset(),
        )
        origin = "tap" if tap else "stated"
        new_ids: list[str] = []
        chips: list[str] = []
        for ef in extracted:
            fact = build_fact(
                ef,
                fact_id=mem.next_fact_id(),
                sources=sources,
                source_ref=source_ref,
                card_id=card.id if card else None,
                origin=origin,
                now=now,
                min_quote_chars=self.policy.min_quote_chars,
            )
            mem.add_fact(fact)
            new_ids.append(fact.id)
            if ef.corrects:
                old = mem.fact(ef.corrects)
                if old is not None and old.status == "active" and old.id != fact.id:
                    old2, new2 = facts_mod.supersede(old, fact, now=now)
                    mem.update_fact(old2)
                    mem.facts[[f.id for f in mem.facts].index(fact.id)] = new2
            projected = fact_projector.project(
                mem.fact(fact.id) or fact,
                turn.slots,
                self.catalog,
                turn_id=turn.turn_id,
                now=now,
                input_mode=input_mode,
            )
            for slot_id in projected.written:
                label = self.svc._label(turn, slot_id)
                rec = turn.slots.get(slot_id)
                chips.append(f"{label}: {text_of(rec.value if rec else '')}"[:80])
        for (
            old_id
        ) in understood.contradicts:  # the newest explicit statement wins; the history is kept
            old = mem.fact(old_id)
            if old is None or old.status != "active" or not new_ids:
                continue
            winner = mem.fact(new_ids[-1])
            if winner is not None and old.id != winner.id:
                old2, new2 = facts_mod.supersede(old, winner, now=now)
                mem.update_fact(old2)
                mem.update_fact(new2)
                fact_projector.reproject(
                    slots_of([old2]),
                    mem.facts,
                    turn.slots,
                    self.catalog,
                    turn_id=turn.turn_id,
                    now=now,
                )
        self.svc._rebuild(turn)
        return new_ids, chips

    async def on_answers(self, turn: _Turn, items: list[AnswerItem]) -> None:
        """Every way of answering reduces to this: record, interpret, store facts, update needs, decide."""
        mem = turn.memory
        statuses = self.statuses(turn)
        sem = asyncio.Semaphore(self.policy.understand_concurrency)

        async def read(item: AnswerItem) -> TurnUnderstanding | None:
            if item.action != "answer":
                return None
            async with sem:
                return await self._interpret(turn, item, statuses)

        readings = await asyncio.gather(*(read(i) for i in items))
        flags: dict[str, list[str]] = {}
        chips: list[str] = []
        now = self._now()
        for item, understood in zip(items, readings, strict=True):
            card = item.card
            qa = mem.qa_for(card.id) or QaRecord(card_id=card.id, card=card, status="open")
            status = {"answer": "answered", "skip": "skipped", "you_decide": "you_decide"}[
                item.action
            ]
            mem.put_qa(
                qa.model_copy(
                    update={
                        "status": status,
                        "answer": item.value,
                        "custom_text": item.custom_text,
                        "input_mode": item.input_mode,
                        "answered_at": now,
                        "asked_at": qa.asked_at or now,
                    }
                )
            )
            outcome = {"answered": "answered", "skipped": "skipped", "you_decide": "you_decide"}[
                status
            ]
            turn.answered.append(
                (card.id, outcome, {"value": item.value, "custom": item.custom_text})
            )  # type: ignore[arg-type]
            if item.action == "you_decide":
                self._assume_for(turn, card)
            if understood is None:
                continue
            labels, text = self._labels_and_text(item)
            tap = bool(labels) and not item.custom_text
            _, got = self.ingest(
                turn,
                understood,
                sources=[*labels, *([text] if text else [])],
                source_ref=turn.turn_id,
                card=card,
                tap=tap,
                input_mode="voice" if item.input_mode == "voice" else "text",
            )
            chips += got
            flags[card.id] = list(understood.flags)
        if chips:
            turn.out.append(
                Notice(
                    code="picked_up",
                    text=self.svc._t(turn, "notice.picked_up", items=", ".join(chips[:6])),
                    chips=chips[:6],
                )
            )
        statuses = self.statuses(turn)
        await self._follow_ups(turn, items, statuses, flags)
        self.touch(turn)

    def _assume_for(self, turn: _Turn, card: Card) -> None:
        """ "You decide": take the card's recommended option, else assume each of its needs visibly."""
        writes = cards_mod.you_decide_writes(card)
        if writes:
            self.svc._apply_card_writes(turn, writes, state="assumed", from_tap=True)
            picked = ", ".join(text_of(w.value) for w in writes)
            turn.notice("assumed", self.svc._t(turn, "card.assumed_collapsed", value=picked))
        else:
            for need in card.need_ids:
                self.svc._assume_need(turn, need)

    async def _follow_ups(
        self,
        turn: _Turn,
        items: list[AnswerItem],
        statuses: dict[str, NeedStatus],
        flags: dict[str, list[str]],
    ) -> None:
        """One targeted follow-up where a high-impact need is still partial or the answer was vague (the policy
        decides; a second vague answer is assumed visibly instead of nagging)."""
        mem = turn.memory
        base = mem.asked_count(origins=("planner", "fallback"))
        total = mem.asked_count(origins=("followup",))
        used = counts_by_need(mem.qa, followups_only=True)
        asked_followup = 0
        for item in items:
            if item.action != "answer":
                continue
            card = item.card
            for need in card.need_ids:
                status = statuses.get(need)
                if status is None or need not in self.catalog.slots:
                    continue
                decision = interview_policy.followup_decision(
                    status,
                    flags.get(card.id, []),
                    used.get(need, 0),
                    total + asked_followup,
                    base,
                    self.policy,
                    high_impact_at=self.svc.config.questions.high_impact_at,
                    depth=card.depth,
                )
                if decision.action == "follow_up" and asked_followup < 2:
                    labels, text = self._labels_and_text(item)
                    focus = AnswerFocus(
                        card_id=card.id,
                        question=card.question,
                        intent=card.intent,
                        need_ids=[need],
                        selected_labels=labels,
                        answer_text=text,
                    )
                    result, _ = await self.plan(
                        turn,
                        "followup",
                        statuses,
                        focus=focus,
                        focus_needs=[need],
                        missing_aspects=list(status.missing_aspects),
                        depth=card.depth + 1,
                    )
                    if result.batch.cards():
                        asked_followup += 1
                        used[need] = used.get(need, 0) + 1
                        for qa in [q for q in mem.open_qa() if q.card.parent_card_id == card.id]:
                            # a follow-up is shown first, before the rest of the batch
                            turn.memory.put_qa(qa)
                    break
                if decision.action == "assume":
                    self.svc._assume_need(turn, need)
        self.svc._rebuild(turn)

    # ----------------------------------------------------------------------------- free text, modes
    async def on_text(
        self, turn: _Turn, understood: TurnUnderstanding, text: str, input_mode: str
    ) -> list[str]:
        """A typed or spoken message becomes facts like any answer (casual chat included)."""
        new_ids, chips = self.ingest(
            turn,
            understood,
            sources=[text],
            source_ref=turn.turn_id,
            tap=False,
            input_mode=input_mode,
        )
        if "pivot" in understood.flags and turn.memory.batches:
            await self.on_pivot(turn, text, new_ids)
        self.touch(turn)
        return chips

    async def ask_more(self, turn: _Turn) -> None:
        """ "Ask me more": the planner's deepen mode on the lowest-confidence areas."""
        if not self.active:
            return
        statuses = self.statuses(turn)
        for qa in turn.memory.open_qa():
            turn.memory.put_qa(qa.model_copy(update={"status": "dropped"}))
        block = self.block(turn)
        if self.budget_name(turn) != "thorough":
            turn.brief.meta.plan = self.svc._plan(turn).model_copy(
                update={"interview_budget": "thorough"}
            )
        turn.brief.meta.interview = block.model_copy(
            update={"budget": "thorough", "stop_reason": ""}
        )
        result, _ = await self.plan(turn, "deepen", statuses)
        self._emit_batch(turn, result.batch)
        if not self.show_next(turn):
            turn.out.append(
                Notice(code="nothing_more", text=self.svc._t(turn, "notice.nothing_more_to_ask"))
            )
            return
        turn.state = "INTERVIEWING"
        self.touch(turn)

    async def on_help(self, turn: _Turn, card: Card, kind: str) -> bool:
        """ "I do not understand" or "I do not know" about the question on screen. The first time the question is
        rewritten (explain: same question in other words; don't know: a simpler one with an example). The second
        time the person is not pushed: "You decide" assumes visibly. Returns True when the question is settled
        (the interview moves on), False when a rewritten question is on screen."""
        assert self.svc.ports is not None
        mem = turn.memory
        qa = mem.qa_for(card.id)
        if qa is not None:
            card = qa.card  # the latest wording and rewrite count, not the card as first stored
        limit = 2 if kind == "explain" else 1
        if qa is None or card.rephrased >= limit:
            await self.on_answers(turn, [AnswerItem(card, "you_decide")])
            turn.notice("simplified_decided", self.svc._t(turn, "notice.simplified_decided"))
            return True
        result = await self.svc.ports.question_rephraser.rephrase(
            self.svc._ctx(turn),
            RephraseRequest(
                card=card,
                mode="simplify" if kind == "dont_know" else "rephrase",
                language=turn.lang,
                project_terms=sorted(self._terms(turn)),
            ),
        )
        if result.used_fallback:
            self.count_fallback(turn)
        new = card.model_copy(
            update={
                "question": result.question,
                "why_we_ask": result.why_we_ask or card.why_we_ask,
                "options": result.options or card.options,
                "examples": result.examples or card.examples,
                "rephrased": card.rephrased + 1,
            }
        )
        mem.put_qa(qa.model_copy(update={"card": new}))
        turn.out.append(CardEvent(card=new))
        return False

    # ------------------------------------------------------------------- editing, pivots, documents
    async def on_answer_edit(
        self, turn: _Turn, card_id: str, value: JsonValue, custom: str | None
    ) -> None:
        """Edit an earlier answer: a new answer version, the old facts superseded (never deleted), the slots they
        fed rebuilt from the facts that remain, and the questions still waiting planned afresh."""
        mem = turn.memory
        qa = mem.qa_for(card_id)
        if qa is None or qa.status not in ("answered", "skipped", "you_decide"):
            turn.notice("edit_unavailable", self.svc._t(turn, "notice.edit_unavailable"), warn=True)
            return
        now = self._now()
        old_facts = [f for f in mem.facts if f.qa_card_id == card_id and f.status == "active"]
        for fact in old_facts:
            mem.update_fact(fact.model_copy(update={"status": "superseded", "updated_at": now}))
        fact_projector.reproject(
            slots_of(old_facts), mem.facts, turn.slots, self.catalog, turn_id=turn.turn_id, now=now
        )
        typed = bool(custom) and value in (None, "", [])
        item = AnswerItem(qa.card, "answer", value, custom, "text" if typed else "tap")
        new = qa.model_copy(
            update={
                "version": qa.version + 1,
                "status": "answered",
                "answer": value,
                "custom_text": custom,
                "input_mode": item.input_mode,
                "answered_at": now,
            }
        )
        mem.supersede_qa(qa, new)
        statuses = self.statuses(turn)
        understood = await self._interpret(turn, item, statuses)
        labels, text = self._labels_and_text(item)
        self.ingest(
            turn,
            understood,
            sources=[*labels, *([text] if text else [])],
            source_ref=turn.turn_id,
            card=qa.card,
            tap=bool(labels) and not custom,
        )
        await self.replan_waiting(turn)
        self.touch(turn)

    async def replan_waiting(self, turn: _Turn) -> None:
        """Questions not yet shown are planned again from the current facts (an edit or a pivot can drop or add some)."""
        mem = turn.memory
        waiting = [q for q in mem.open_qa() if q.asked_at is None]
        if not waiting:
            return
        for qa in waiting:
            mem.put_qa(qa.model_copy(update={"status": "dropped"}))
        statuses = self.statuses(turn)
        result, _ = await self.plan(turn, "replan", statuses)
        if result.batch.cards():
            self._emit_batch(turn, result.batch)

    async def on_pivot(self, turn: _Turn, text: str, new_fact_ids: list[str]) -> None:
        """ "Actually it is for clinics": the earlier facts about the idea and its audience are superseded (history
        kept), the hypothesis is made again, and what is still waiting is planned again for the new direction."""
        assert self.svc.ports is not None
        mem = turn.memory
        now = self._now()
        category = turn.slots.get("idea.category")
        old_category = text_of(category.value) if category is not None else ""
        about_idea = {"idea", "audience"}
        feeds = {
            "idea.summary",
            "idea.category",
            "idea.subcategory",
            "idea.business_model",
            "audience.segments",
        }
        gone: list[Fact] = []
        for fact in mem.active_facts():
            if fact.id in new_fact_ids:
                continue
            if set(fact.topics) & about_idea or slots_of([fact]) & feeds:
                mem.update_fact(fact.model_copy(update={"status": "superseded", "updated_at": now}))
                gone.append(fact)
        fact_projector.reproject(
            slots_of(gone), mem.facts, turn.slots, self.catalog, turn_id=turn.turn_id, now=now
        )
        situation = await self.svc.ports.situator.situate(self.svc._ctx(turn), user_text=text)
        turn.brief.meta.hypothesis = situation.hypothesis
        turn.notice(
            "pivot",
            self.svc._t(
                turn,
                "notice.pivot",
                before=old_category or "?",
                after=situation.hypothesis.category or "?",
            ),
            chips=[situation.hypothesis.category] if situation.hypothesis.category else [],
        )
        for qa in [q for q in mem.open_qa() if q.asked_at is None]:
            mem.put_qa(qa.model_copy(update={"status": "dropped"}))
        statuses = self.statuses(turn)
        result, _ = await self.plan(turn, "replan", statuses, pivot_from=old_category or None)
        self._emit_batch(turn, result.batch)
        self.touch(turn)

    def after_ingest(self, turn: _Turn, claims: Sequence[Any], source_id: str) -> int:
        """A document's claims become facts of the ledger (origin ``document``, the quote was verified against the
        chunk by the intake). Returns how many facts were added."""
        mem = turn.memory
        now = self._now()
        kinds: dict[str, FactType] = {
            "requirement": "requirement", "constraint": "constraint", "business_context": "context",
            "goal": "goal", "user_or_role": "role", "rule_or_number": "rule", "glossary_term": "glossary",
            "example": "example", "open_issue": "context", "preference": "preference", "exclusion": "exclusion",
        }  # fmt: skip
        added = 0
        for claim in claims:
            if getattr(claim, "status", "") not in ("used", "needs_confirming"):
                continue
            st = claim.statement
            fact = Fact(
                id=mem.next_fact_id(),
                type=kinds.get(st.type, "context"),
                statement=st.text.strip()[:1000] or "(empty)",
                topics=[],
                consumers=["all"],
                confidence=st.confidence,
                origin="document",
                source_ref=f"{source_id}:{st.pointer}" if st.pointer else source_id,
                claim_id=claim.id,
                quote=st.quote.strip()[:500],
                unverified_quote=False,
                # a claim that maps to a need answers it: the interview must not ask it again (GAP-7)
                aspects=(
                    [f"{claim.slot_hint}:*"]
                    if claim.slot_hint in self.catalog.slots and claim.status == "used"
                    else []
                ),
                created_at=now,
                updated_at=now,
            )
            mem.add_fact(fact)
            added += 1
        self.touch(turn)
        return added

    async def ask_gap(self, turn: _Turn, slot_id: str) -> bool:
        """A handoff probe found a gap a consumer cannot work without: ask ONE planned question about it (at most
        two per need, so the Brief is never held hostage). Returns True when a question is on screen."""
        mem = turn.memory
        if counts_by_need(mem.qa).get(slot_id, 0) >= 2:
            return False
        statuses = self.statuses(turn)
        result, _ = await self.plan(turn, "replan", statuses, focus_needs=[slot_id], want=(1, 1))
        if not result.batch.cards():
            return False
        self._emit_batch(turn, result.batch)
        shown = self.show_next(turn)
        self.touch(turn)
        return shown

    def count_fallback(self, turn: _Turn, n: int = 1) -> None:
        """A fallback is a failure: it is counted in the audit block (live batches must show zero)."""
        block = self.block(turn)
        turn.memory.fallbacks += n
        turn.brief.meta.interview = block.model_copy(
            update={"fallback_count": block.fallback_count + n}
        )

    async def compile(self, turn: _Turn) -> None:
        """Build the Brief from the whole fact ledger (the start of Brief review)."""
        from mde_compass.engine.interview_compile import compile_ledger

        if self.active:
            await compile_ledger(self, turn)

    async def refresh_summary(self, turn: _Turn) -> None:
        """Keep the rolling summary current once the ledger outgrows half of the digest budget (it is only ever
        used beyond the budget, and it never replaces the ledger)."""
        assert self.svc.ports is not None
        mem = turn.memory
        cfg = self.svc.config.context
        if (
            estimate_tokens(full_ledger(mem.active_facts()), cfg.chars_per_token)
            < cfg.fact_digest // 2
        ):
            return
        done = mem.summary.through_fact_id if mem.summary else ""
        fresh = [f for f in mem.active_facts() if f.id > done]
        if not fresh:
            return
        result = await self.svc.ports.summary_updater.update(
            self.svc._ctx(turn),
            SummaryRequest(
                language=turn.lang,
                previous=mem.summary or InterviewSummary(),
                new_facts_digest="\n".join(ledger_line(f) for f in fresh),
                through_fact_id=fresh[-1].id,
            ),
        )
        mem.set_summary(result.summary, self._now())
        if result.used_fallback:
            self.count_fallback(turn)

    def set_ui_mode(self, turn: _Turn, mode: Literal["one_at_a_time", "answer_all"]) -> None:
        changed = self.ui_mode(turn) != mode
        block = self.block(turn)
        turn.brief.meta.interview = block.model_copy(update={"ui_mode": mode, "ui_mode_set": True})
        if changed:
            self._relayout(turn, mode)

    def _relayout(self, turn: _Turn, mode: Literal["one_at_a_time", "answer_all"]) -> None:
        """A layout switch applies at once: the unanswered questions of the batch on screen are shown again,
        as a form (answer-all) or one by one. No question is added, dropped or reworded."""
        open_qa = turn.memory.open_qa()
        shown = [q for q in open_qa if q.asked_at is not None]
        if not open_qa or not (shown or mode == "answer_all"):
            return
        lead = (shown or open_qa)[0]
        if lead.batch_id is None:
            return
        batch_id = lead.batch_id
        same = [q for q in open_qa if q.batch_id == batch_id]
        record = next((b for b in turn.memory.batches if b.id == batch_id), None)
        titles = _group_titles(record.output if record is not None else {})
        groups: list[BatchGroup] = []
        for qa in same:
            gid = qa.card.group_id or ""
            group = next((g for g in groups if g.id == gid), None)
            if group is None:
                group = BatchGroup(id=gid, title=titles.get(gid, ""), cards=[])
                groups.append(group)
            group.cards.append(qa.card)
        in_batch = [q for q in turn.memory.qa if q.batch_id == batch_id]
        turn.out.append(
            QuestionBatch(
                batch_id=batch_id,
                mode=record.mode if record is not None else (lead.mode or "initial"),
                ui_mode=mode,
                groups=groups,
                total=len(in_batch),
                answered=sum(1 for q in in_batch if q.status not in ("open", "dropped")),
            )
        )
        if mode == "answer_all":
            for i, qa in enumerate(same):
                self._show(turn, qa, emit_card=i == 0)
            return
        for qa in same[1:]:  # they wait their turn again, and no longer count as open on screen
            if qa.asked_at is not None:
                turn.memory.put_qa(qa.model_copy(update={"asked_at": None}))
                turn.answered.append((qa.card_id, "back", None))
        self._show(turn, same[0])

    def set_budget(self, turn: _Turn, name: BudgetName) -> None:
        turn.brief.meta.plan = self.svc._plan(turn).model_copy(update={"interview_budget": name})
        self.touch(turn)


def _group_titles(output: Mapping[str, object]) -> dict[str, str]:
    """Group id -> title from a stored planner output (empty when the shape is not what is expected)."""
    titles: dict[str, str] = {}
    groups = output.get("groups")
    if isinstance(groups, list):
        for g in groups:
            if (
                isinstance(g, dict)
                and isinstance(g.get("id"), str)
                and isinstance(g.get("title"), str)
            ):
                titles[g["id"]] = g["title"]
    return titles
