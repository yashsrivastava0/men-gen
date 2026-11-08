"""Snippet responder: the offline brain of C3 (mock handler and engine fallback).

Questions are answered only from the knowledge snippets passed in. A question no snippet covers
gets the plain "I do not have that detail" reply, never a guess about price, time or ownership.
"""

from __future__ import annotations

import hashlib

from mde_contracts.calls import C3Input, C3Output, KnowledgeLite
from mde_prompts.calls.c1_understand import lexicon as lx


def _pick(options: list[str], seed: str) -> str:
    digest = hashlib.sha256(seed.encode("utf-8")).digest()
    return options[digest[0] % len(options)]


def best_snippet(message: str, knowledge: list[KnowledgeLite]) -> KnowledgeLite | None:
    """The snippet whose topic words appear most in the message (ties: first in the list)."""
    topics = lx.load_lexicon().knowledge_topics
    best: tuple[int, KnowledgeLite] | None = None
    for snippet in knowledge:
        topic = snippet.id.removeprefix("kb-")
        hits = lx.keyword_hits(lx.clip(message), topics.get(topic, ()))
        score = sum(len(h.split()) for h in hits)  # longer phrases are more specific
        if score and (best is None or score > best[0]):
            best = (score, snippet)
    return best[1] if best else None


def unknown_reply(lang: str | None) -> str:
    return str(lx.localized(lx.load_lexicon().raw["unknown_reply"], lang))


def idea_chips(lang: str | None) -> list[str]:
    chips: list[str] = list(lx.localized(lx.load_lexicon().raw["idea_chips"], lang))
    return chips[:3]


def steer_back_text(lang: str | None) -> str:
    return str(lx.localized(lx.load_lexicon().raw["steer_back"], lang))


def respond(inp: C3Input) -> C3Output:
    """Deterministic C3 reply for a question, chit-chat, brainstorming or help."""
    lang = inp.language
    if inp.intent == "question":
        snippet = best_snippet(inp.message, inp.knowledge)
        if snippet is not None:
            return C3Output(reply=snippet.text, unknown=False, used_snippets=[snippet.id])
        return C3Output(reply=unknown_reply(lang), unknown=True, used_snippets=[])
    replies: list[str] = list(lx.localized(lx.load_lexicon().raw["chitchat_replies"], lang))
    reply = _pick(replies, inp.message)
    steer = inp.off_topic_streak >= 2
    if steer:
        reply = f"{reply} {steer_back_text(lang)}"
    chips = idea_chips(lang) if inp.stuck else []
    return C3Output(
        reply=reply, unknown=False, used_snippets=[], suggested_chips=chips, steer_back=steer
    )
