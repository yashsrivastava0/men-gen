"""Deterministic half of ``cmp.answer``: classify the message with phrase tables and answer from the
catalog or from the data already in the input. Never invents a price, a time or a capability."""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from mde_compass.calls.answer import text as tx
from mde_compass.calls.answer.schema import AnswerInput, AnswerOutput, Topic
from mde_compass.catalog.loader import load_catalog

MAX_RECAP_SLOTS = 5


@lru_cache(maxsize=1)
def rules() -> dict[str, Any]:
    return tx.load_rules("mde_compass.calls.answer")


def classify(question: str) -> Topic:
    """Topic hint from the words of the message (unsafe first, then price/time, then technology)."""
    r = rules()
    text = tx.squash_spaces(tx.clip(question))
    if tx.has_phrase(text, r["unsafe"]):
        return "unsafe"
    if tx.has_phrase(text, r["price_time"]):
        return "price_or_time"
    if tx.has_phrase(text, r["tech_choice"]):
        return "tech_choice"
    if tx.has_phrase(text, r["why"]):
        return "help"
    return "question"


def catalog_lang(language: str) -> str:
    """The catalog has English and Hindi strings; Hinglish falls back to English."""
    return "hi" if language == "hi" else "en"


def ensure_pointer(text: str, inp: AnswerInput) -> str:
    """Make sure the reply ends by pointing back to the next step (never leaves the user stuck).

    A reply that already ends with a question mark, or already repeats the pending question, is left as is.
    """
    r = rules()
    body = text.strip()
    if body.endswith(("?", "？")):
        return body
    if inp.pending_question:
        head = " ".join(tx.words(inp.pending_question)[:5])
        if head and head in tx.normalize(body):
            return body
        question = inp.pending_question.strip()
        if len(question) > 90:
            question = question[:89].rstrip() + "…"
        return f"{body} {tx.pick(r['pointer_prefix'], inp.language)} {question}"
    return f"{body} {tx.pick(r['pointer_generic'], inp.language)}"


def _recap(inp: AnswerInput) -> AnswerOutput:
    r = rules()
    if not inp.slots:
        text = tx.pick(r["recap_empty"], inp.language)
        return AnswerOutput(text=text, language=inp.language, grounded_in=[], declined=False)
    shown = inp.slots[:MAX_RECAP_SLOTS]
    parts = "; ".join(f"{s.label}: {s.value}" for s in shown)
    text = f"{tx.pick(r['recap_intro'], inp.language)}: {parts}."
    return AnswerOutput(
        text=text[: 480 - 1],
        language=inp.language,
        grounded_in=[s.slot_id for s in shown],
        declined=False,
    )


def fallback(inp: AnswerInput) -> AnswerOutput:
    """Pure ``input -> output``: price/time, technology and unsafe messages get the catalog's fixed
    replies; "why do you ask" is answered from the pending card; "what do you know" from the slots."""
    cat = load_catalog()
    lang = catalog_lang(inp.language)
    hint = inp.topic if inp.topic in ("price_or_time", "tech_choice", "unsafe") else None
    topic: Topic = hint or classify(inp.question)
    out_lang = inp.language

    def reply(key: str, *, declined: bool) -> AnswerOutput:
        text = ensure_pointer(cat.t(key, lang), inp)
        return AnswerOutput(text=text, language=out_lang, grounded_in=[key], declined=declined)

    if topic == "unsafe":
        return reply("fallback.unsafe_decline", declined=True)
    if topic == "price_or_time":
        return reply("fallback.out_of_scope_price_time", declined=True)
    if topic == "tech_choice":
        return reply("fallback.tech_choice_fixed_stack", declined=True)
    r = rules()
    if tx.has_phrase(tx.squash_spaces(tx.clip(inp.question)), r["recap"]):
        out = _recap(inp)
        return out.model_copy(update={"text": ensure_pointer(out.text, inp)})
    if inp.pending_why and (topic == "help" or inp.topic == "help"):
        text = ensure_pointer(inp.pending_why.strip(), inp)
        return AnswerOutput(
            text=text, language=out_lang, grounded_in=["pending_card"], declined=False
        )
    return reply("fallback.not_sure_rephrase", declined=False)
