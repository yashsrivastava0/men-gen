"""Canned, no-LLM copy (greeting, nudge, stop/pause, decline templates, ...) with fallback."""

from __future__ import annotations

import re

from mde_content.questions import load_question_banks, resolve_text

_PLACEHOLDER = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


def copy_ids() -> list[str]:
    """Every copy id in the bank."""
    return load_question_banks().ui_copy_ids()


def copy(copy_id: str, lang: str | None = "en", /, **values: object) -> str:
    """The text for ``copy_id`` in ``lang`` (falls back to English), with ``{name}`` filled in.

    A placeholder with no value is left as written, so a missing value never raises and never
    hides the rest of the sentence. Only ``{name}`` placeholders are substituted: no format
    specs, no attribute access. ``KeyError`` for an unknown ``copy_id``.
    """
    template = resolve_text(load_question_banks().ui_copy(copy_id).text, lang)

    def fill(match: re.Match[str]) -> str:
        name = match.group(1)
        return str(values[name]) if name in values else match.group(0)

    return _PLACEHOLDER.sub(fill, template)
