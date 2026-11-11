"""RSCH-4 (v2): the checker. A second model reads, for EACH item of a topic, the claim, the typed data and the page
passage around the quote, and says whether the passage supports them.

Every verified item is checked (not only high-impact ones), inside its topic and before the coverage audit, so a
rejected item reopens its slot and a follow-up can run. The checker sees the claim, the data as compact JSON, the
passage window (about 150 words around the quote, from the stored page), the page title and domain: no URL, no tools.
It returns a verdict (``supported`` / ``partial`` / ``not_supported``) and ``unsupported_fields``: the paths in
``data`` that the passage does not state. CODE then nulls those fields, drops an item that lost its minimum, flags a
partial one and rejects a ``not_supported`` one. Code checks stay primary (quote on the page, numbers in the quote,
region and entity fit, independence); this call is a second opinion. It runs on the ``standard`` route, which
Research reserves for it, so a different model is one ``MODEL_ROUTES`` entry away; until one is configured the
writer's model answers and the run records ``checker_vendor: same`` (RS-22). Mock handler: a deterministic
word-overlap check; the fallback gives no opinion (the engine flags ``unchecked``, never drops, never trusts).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from mde_contracts.gateway import CallSpec
from mde_research_agent.calls.base import ResearchCallSpec
from mde_research_agent.core.language import dominant_script
from mde_research_agent.core.text import stem_set

CALL_ID = "rsch.check"
Verdict = Literal["supported", "partial", "not_supported"]
_PATH = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\[\d+\])?(\.[A-Za-z_][A-Za-z0-9_]*)?$")


class W(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CheckItem(W):
    id: str
    claim: str
    quote: str
    data: str = "{}"  # the typed data as compact JSON
    passage: str = ""  # about 150 words of the stored page around the quote
    page_title: str = ""
    page_domain: str = ""


class CheckIn(W):
    items: list[CheckItem]
    text: str = ""  # everything the model reads about the items as ONE untrusted block

    @model_validator(mode="after")
    def _build_text(self) -> CheckIn:
        self.text = "\n\n".join(
            f"[{i.id}] page: {i.page_title} ({i.page_domain})\nCLAIM: {i.claim}\nQUOTE: {i.quote}\n"
            f"DATA: {i.data}\nPASSAGE: {i.passage or i.quote}"
            for i in self.items
        )
        return self


class CheckVerdict(W):
    id: str
    verdict: Verdict
    unsupported_fields: list[str] = Field(
        default_factory=list
    )  # paths in DATA the passage does not state


class CheckOut(W):
    verdicts: list[CheckVerdict]


def overlap_verdict(item: CheckItem) -> Verdict:
    """Word-overlap heuristic: share of the claim's content stems that the passage (or the quote) also contains.

    A claim paraphrased in English from a passage in another script cannot be judged by word overlap: ``partial``.
    """
    evidence = item.passage or item.quote
    if dominant_script(item.claim) != dominant_script(item.quote):
        return "partial"
    claim, ev = stem_set(item.claim), stem_set(evidence)
    if not claim:
        return "not_supported"
    share = len(claim & ev) / len(claim)
    return "supported" if share >= 0.55 else "partial" if share >= 0.3 else "not_supported"


def heuristic_check(inp: CheckIn) -> CheckOut:
    return CheckOut(verdicts=[CheckVerdict(id=i.id, verdict=overlap_verdict(i)) for i in inp.items])


def handle(inp: CheckIn) -> CheckOut:
    return heuristic_check(inp)


def unchecked(inp: CheckIn) -> CheckOut:
    """The fallback: no opinion. Items are flagged ``unchecked`` by the engine, never trusted and never dropped."""
    return CheckOut(verdicts=[])


def validate(inp: CheckIn, out: CheckOut) -> list[str]:
    ids = {i.id for i in inp.items}
    problems: list[str] = []
    stray = [v.id for v in out.verdicts if v.id not in ids]
    if stray:
        problems.append(f"verdicts for unknown items: {stray}")
    bad = [p for v in out.verdicts for p in v.unsupported_fields if not _PATH.match(p)]
    if bad:
        problems.append(f"unsupported_fields must be field paths of the data, got: {bad[:3]}")
    return problems


SPEC: ResearchCallSpec[CheckIn, CheckOut] = ResearchCallSpec(
    spec=CallSpec(
        id=CALL_ID,
        version=2,
        input_model=CheckIn,
        output_model=CheckOut,
        system_template="check.system.jinja",
        user_template="check.user.jinja",
        template_root=Path(__file__).parent / "templates",
        route="standard",
        reasoning_effort="low",
        max_output_tokens=4000,
        mock_handler="mde_research_agent.calls.check:handle",
        untrusted_inputs=("text",),
        cacheable=True,
        repair=True,
        fallback="research.check",
    ),
    timeout_s=90.0,
    fallback=unchecked,
    validators=(validate,),
    checker=True,
)
