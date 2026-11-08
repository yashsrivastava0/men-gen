"""SafetyScreen: secrets out before storage, injection noted never obeyed, unsafe requests declined.

Order matters: text is screened *before* it reaches a slot, the summary, the recent turns, an
event or an LLM prompt. The redacted text is the only text the rest of the engine ever sees.
"""

from __future__ import annotations

from dataclasses import dataclass

from mde_contracts.events import MAX_UPLOAD_TEXT_CHARS
from mde_core.redact import redact_text, strip_hidden_chars
from mde_discovery import copy as texts
from mde_prompts.calls.c1_understand import lexicon as lx

HIDDEN_MESSAGE = "[message hidden: it may contain a secret]"
# The event contract already refuses a longer text at the boundary (messages 50k, uploads 200k);
# this is the same bound for any caller that did not come through an event: the rest is dropped
# *before* scanning (a secret in the dropped part never reaches a slot, the summary or a prompt).
MAX_SCREEN_CHARS = MAX_UPLOAD_TEXT_CHARS


@dataclass(frozen=True)
class Screened:
    text: str  # hidden characters stripped, secrets replaced by [REDACTED]
    secret_kinds: tuple[str, ...]  # names of the patterns found (never the values)
    injection: bool  # the text tries to instruct the assistant
    truncated: bool = False  # the text was longer than MAX_SCREEN_CHARS and was cut

    @property
    def had_secret(self) -> bool:
        return bool(self.secret_kinds)


class SafetyScreen:
    """Deterministic screening plus the canned decline and notice texts."""

    @staticmethod
    def screen(text: str) -> Screened:
        truncated = len(text) > MAX_SCREEN_CHARS
        clean, kinds = redact_text(strip_hidden_chars(text[:MAX_SCREEN_CHARS]))
        return Screened(clean, tuple(kinds), lx.injection_suspected(clean), truncated)

    @staticmethod
    def decline(category: str | None, lang: str | None) -> str:
        """A brief refusal that explains and offers a safe alternative (no LLM)."""
        key = f"unsafe_reason_{category}" if category in ("abuse", "illegal", "harmful") else None
        reason = texts.say(key or "unsafe_reason_other", lang)
        return texts.say("unsafe_decline", lang, reason=reason)

    @staticmethod
    def secret_notice(lang: str | None) -> str:
        return texts.say("secret_redacted", lang)

    @staticmethod
    def injection_notice(lang: str | None) -> str:
        return texts.say("injection_notice", lang)
