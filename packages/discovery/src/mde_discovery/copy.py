"""Canned copy: the engine's own ``data/copy.yaml`` first, then the content bank's ui_copy.

Every line has English, Hindi and Hinglish text; a missing language falls back to English through
the content language chain. Placeholders are ``{name}``; a missing value is left as written.
"""

from __future__ import annotations

import re
from functools import lru_cache
from importlib import resources

import yaml

from mde_content import ui_copy
from mde_content.questions import resolve_text

_PLACEHOLDER = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


@lru_cache(maxsize=1)
def _own() -> dict[str, dict[str, str]]:
    text = resources.files("mde_discovery").joinpath("data/copy.yaml").read_text(encoding="utf-8")
    data: dict[str, dict[str, str]] = yaml.safe_load(text)
    return data


def own_ids() -> list[str]:
    return list(_own())


def say(copy_id: str, lang: str | None = "en", /, **values: object) -> str:
    """The text for ``copy_id`` in ``lang`` with ``{placeholders}`` filled."""
    table = _own().get(copy_id)
    if table is None:
        return ui_copy.copy(copy_id, lang, **values)
    template = resolve_text(table, lang)

    def fill(match: re.Match[str]) -> str:
        name = match.group(1)
        return str(values[name]) if name in values else match.group(0)

    return _PLACEHOLDER.sub(fill, template)


# --------------------------------------------------------------------------- assumption reasons

REASON_PREFIX = "reason."


def reason(slot_id: str, lang: str | None = "en", default: str = "") -> str:
    """The one-line reason a slot is assumed ("We assumed ..."), in ``lang``.

    The texts are content-bank copy (``reason.<slot id>``); ``default`` (the catalog's English
    ``SlotDef.default_reason``) is used for a slot the bank has no line for.
    """
    try:
        return ui_copy.copy(f"{REASON_PREFIX}{slot_id}", lang)
    except KeyError:
        return default


@lru_cache(maxsize=1)
def _reason_index() -> dict[str, str]:
    """Every known reason text, in every language, mapped to its slot id."""
    from mde_content.questions import load_question_banks

    banks = load_question_banks()
    index: dict[str, str] = {}
    for copy_id in banks.ui_copy_ids():
        if copy_id.startswith(REASON_PREFIX):
            for text in banks.ui_copy(copy_id).text.values():
                index[text] = copy_id.removeprefix(REASON_PREFIX)
    return index


EVIDENCE_SUFFIX = "_evidence"  # engine copy ids that are stored as a slot's evidence


@lru_cache(maxsize=1)
def _evidence_index() -> dict[str, str]:
    """Every text of the engine's own evidence lines, in every language, mapped to its copy id."""
    return {
        text: copy_id
        for copy_id, table in _own().items()
        if copy_id.endswith(EVIDENCE_SUFFIX)
        for text in table.values()
    }


def reason_in(evidence: str, lang: str | None, default: str = "") -> str:
    """``evidence`` (a stored reason, possibly written in another language) in ``lang``.

    A reason written earlier in the session, or by an older version, is found among the known
    texts (the bank's ``reason.<slot id>`` lines and the engine's own evidence lines such as
    "From your organization profile") and said again in the user's current language; any other
    evidence is returned as is.
    """
    own = _evidence_index().get(evidence)
    if own is not None:
        return say(own, lang)
    slot_id = _reason_index().get(evidence)
    if slot_id is None:
        return evidence or default
    return reason(slot_id, lang, evidence)
