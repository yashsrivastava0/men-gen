"""User-facing event text, built by CODE from templates (``data/labels/<lang>.yaml``) and the Brief's own words.

Never written by a model at run time. A missing key or language falls back to English; a template with a missing
placeholder renders it empty rather than failing a run.
"""

from __future__ import annotations

import re
from typing import Any

from mde_research_agent.catalog.models import Catalog, Labels

_FIELD = re.compile(r"\{(\w+)\}")


class LabelBook:
    def __init__(self, catalog: Catalog, language: str = "en") -> None:
        self._en: Labels = catalog.labels["en"]
        self._lang: Labels = catalog.labels.get(language.split("-")[0], self._en)

    @staticmethod
    def _fill(template: str, params: dict[str, Any]) -> str:
        return " ".join(_FIELD.sub(lambda m: str(params.get(m.group(1), "")), template).split())

    def event(self, event_type: str, params: dict[str, Any] | None = None) -> str:
        tpl = self._lang.events.get(event_type) or self._en.events.get(event_type, event_type)
        return self._fill(tpl, params or {})

    def warning(self, code: str) -> str:
        return self._lang.warnings.get(code) or self._en.warnings.get(code, code)

    def reason(self, code: str) -> str:
        return self._lang.reasons.get(code) or self._en.reasons.get(code, code)

    def depth_word(self, load_class: str) -> str:
        return self._lang.depth_words.get(load_class) or self._en.depth_words.get(
            load_class, load_class
        )

    def declined(self, topic: str, *, by_user: bool) -> str:
        tpl = (self._lang.declined_user if by_user else self._lang.declined) or (
            self._en.declined_user if by_user else self._en.declined
        )
        return self._fill(tpl, {"topic": topic})
