"""``AuditAdapter``: the engine's ``QuestionAuditor`` port over the ``cmp.audit_questions`` call."""

from __future__ import annotations

import re

from mde_compass.calls.audit_questions.schema import (
    AuditInput,
    AuditOutput,
    NeedWire,
    QuestionWire,
)
from mde_compass.calls.audit_questions.spec import SPEC
from mde_compass.calls.base import CallRunner
from mde_compass.catalog.loader import Catalog
from mde_compass.domain.cards import Card
from mde_compass.domain.interview import AuditVerdict
from mde_compass.engine.ports import AuditRequest, AuditResult, TurnContext
from mde_core.redact import REDACTED, redact_text

NO_VERDICT = "no verdict"
_NEED_ID = re.compile(
    r"[a-z][a-z0-9_]*(?:\.[a-z0-9_]+)+"
)  # linear: dots are unambiguous separators


def _lang(language: str) -> str:
    return language if language in ("en", "hi") else "en"


def _clean(text: str) -> str:
    return redact_text(text)[0]


def _need_id(token: str, catalog: Catalog) -> str:
    token = token.strip().rstrip(":")
    if token in catalog.slots or _NEED_ID.fullmatch(token) or token.startswith("domain:"):
        return token
    return ""


def parse_needs_table(table: str, catalog: Catalog, language: str = "en") -> list[NeedWire]:
    """Read the engine's compact needs text, one need per line, in either shape:

    ``- scope.features [partial, impact 4, must] Label: what a sufficient answer contains`` or
    ``scope.features | Label | what a sufficient answer contains``. Lines that do not start with a need id
    (headers, blank lines) are skipped. A missing label or answer text is filled from the catalog.
    """
    lang = _lang(language)
    needs: list[NeedWire] = []
    for raw in table.splitlines():
        line = raw.strip().lstrip("-*• ").strip()
        if not line:
            continue
        if "|" in line:
            parts = [p.strip() for p in line.split("|")] + ["", ""]
            token, label, sufficient = parts[0], parts[1], parts[2]
        else:
            token, _, rest = line.partition(" ")
            rest = rest.strip()
            if rest.startswith("[") and "]" in rest:
                rest = rest[rest.index("]") + 1 :].strip()
            label, sep, sufficient = rest.partition(":")
            if not sep:
                label, sufficient = rest, ""
        need_id = _need_id(token, catalog)
        if not need_id:
            continue
        slot = catalog.slots.get(need_id)
        label = label.strip() or (slot.label.get(lang) if slot else need_id)
        sufficient = sufficient.strip() or (
            slot.answer_spec.sufficient if slot and slot.answer_spec else ""
        )
        needs.append(NeedWire(need_id=need_id, label=label, sufficient=sufficient))
    return needs


def _needs(catalog: Catalog, request: AuditRequest) -> list[NeedWire]:
    """The listed needs: the table the engine sent, plus the real catalog slots the questions serve (a question
    for a real need is not unmapped just because the table was short). An invented id stays unlisted."""
    needs = parse_needs_table(request.needs_table, catalog, request.language)
    have = {n.need_id for n in needs}
    lang = _lang(request.language)
    for card in request.cards:
        for need_id in card.need_ids or card.slots:
            slot = catalog.slots.get(need_id)
            if slot is not None and need_id not in have:
                have.add(need_id)
                sufficient = slot.answer_spec.sufficient if slot.answer_spec else ""
                needs.append(
                    NeedWire(need_id=need_id, label=slot.label.get(lang), sufficient=sufficient)
                )
    return needs


def _question(card: Card) -> QuestionWire:
    recommended = card.recommended_option()
    return QuestionWire(
        card_id=card.id,
        text=_clean(card.question),
        intent=_clean(card.intent),
        serves=list(card.need_ids or card.slots),
        type=card.type,
        option_labels=[_clean(o.label) for o in card.options],
        recommended=_clean(recommended.label) if recommended else "",
        why_we_ask=_clean(card.why_we_ask),
    )


def build_input(catalog: Catalog, request: AuditRequest) -> AuditInput:
    """The wire input. Everything that carries the person's words is secret-redacted first."""
    terms = [_clean(t) for t in request.project_terms]
    return AuditInput(
        language=request.language,
        project_terms=[t for t in terms if t.strip() and REDACTED not in t],
        needs=_needs(catalog, request),
        asked_list=_clean(request.asked_list),
        questions=[_question(c) for c in request.cards],
    )


def to_verdicts(cards: list[Card], out: AuditOutput) -> list[AuditVerdict]:
    """One domain verdict per card, in card order. A card the model skipped is NOT approved: it gets
    ``specific=1`` and the issue "no verdict". Verdicts for unknown ids are ignored."""
    by_id: dict[str, AuditVerdict] = {}
    for w in out.verdicts:
        by_id.setdefault(
            w.card_id,
            AuditVerdict(
                card_id=w.card_id,
                specific=max(1, min(5, w.specific)),
                leading=w.leading,
                jargon=w.jargon,
                duplicate_of=w.duplicate_of.strip() or None,
                answerable=w.answerable,
                maps_to_need=w.maps_to_need,
                issues=[i for i in w.issues if i.strip()],
                fix=w.fix.strip(),
            ),
        )
    return [
        by_id.get(c.id) or AuditVerdict(card_id=c.id, specific=1, issues=[NO_VERDICT])
        for c in cards
    ]


class AuditAdapter:
    """Implements ``mde_compass.engine.ports.QuestionAuditor``."""

    def __init__(self, runner: CallRunner, catalog: Catalog) -> None:
        self._runner = runner
        self._catalog = catalog

    async def audit(self, ctx: TurnContext, request: AuditRequest) -> AuditResult:
        if not request.cards:
            return AuditResult(verdicts=[])  # nothing to review: no call, no cost
        wire = build_input(self._catalog, request)
        result = await self._runner.run(
            SPEC, wire, session_id=ctx.session_id, tenant_id=ctx.tenant_id
        )
        # on a fallback the runner's output IS the deterministic heuristic, so the verdicts are still real
        # (strict, never an approval by default); the flag tells the engine and the developer drawer
        return AuditResult(
            verdicts=to_verdicts(request.cards, result.output),
            used_fallback=result.used_fallback,
            reason=result.reason if result.used_fallback else "",
        )


__all__ = ["AuditAdapter", "build_input", "parse_needs_table", "to_verdicts"]
