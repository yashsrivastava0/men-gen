"""RSCH-4 (v3): the checker. A second model reads, for EACH item of a topic, the claim, the typed data and the page
passage around the quote, and says whether the passage supports them.

Every verified item is checked (not only high-impact ones), inside its topic and before the coverage audit, so a
rejected item reopens its slot and a follow-up can run. The checker sees the claim, the data as compact JSON, the
passage window (about 150 words around the quote, from the stored page), the page title and domain: no URL, no tools.
It returns a verdict (``supported`` / ``partial`` / ``not_supported``), ``unsupported_fields`` (the paths in ``data`` that
the passage does not state) and, v3, ``unsupported_sentences`` (the numbers of the claim's sentences the passage does not
state). CODE then nulls those fields, may drop those sentences and ask once more (D-159: it removes words, it never adds
any, and the same checker must then say ``supported``), drops an item that lost its minimum, flags a
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
# A path starts with a field of the data. The model sometimes appends prose to it ("pricing_model.free trial"): that still
# names the field, and the engine nulls the WHOLE field (never less than was meant), so the call is not thrown away.
_PATH = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\[\d+\])?(\..+)?$")


class W(BaseModel):
    model_config = ConfigDict(extra="forbid")


_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[\"'(\[]?[A-Z])")
# A full stop after one of these does not end a sentence ("Dr. Rao", "Rs. 500", "9 a.m. daily").
_ABBREVIATIONS = frozenset(
    {
        "dr.",
        "mr.",
        "mrs.",
        "ms.",
        "st.",
        "no.",
        "inc.",
        "ltd.",
        "vs.",
        "rs.",
        "e.g.",
        "i.e.",
        "a.m.",
        "p.m.",
        "approx.",
    }
)


def claim_sentences(claim: str) -> list[str]:
    """The claim's sentences, in order. The checker is shown them numbered from 1 and answers with those numbers, and
    the engine drops by the same split, so both must use this one function."""
    out: list[str] = []
    for piece in (x.strip() for x in _SENTENCE_END.split(claim.strip())):
        if not piece:
            continue
        if out and out[-1].split()[-1].lower() in _ABBREVIATIONS:
            out[-1] = f"{out[-1]} {piece}"
        else:
            out.append(piece)
    return out


class CheckItem(W):
    id: str
    claim: str
    quote: str
    data: str = "{}"  # the typed data as compact JSON
    passage: str = ""  # about 150 words of the stored page around the quote
    page_title: str = ""
    page_domain: str = ""
    about_region: str = ""  # the country the item says it is about ("" when not given)
    about_entity: str = ""  # the company, product or authority the item says it is about


class CheckIn(W):
    items: list[CheckItem]
    legacy: bool = (
        False  # v2 layout (no numbered sentences, no ABOUT line): the baseline of the experiments
    )
    text: str = ""  # everything the model reads about the items as ONE untrusted block

    @model_validator(mode="after")
    def _build_text(self) -> CheckIn:
        self.text = "\n\n".join(self._block(i) for i in self.items)
        return self

    def _block(self, i: CheckItem) -> str:
        head = f"[{i.id}] page: {i.page_title} ({i.page_domain})\n"
        tail = f"QUOTE: {i.quote}\nDATA: {i.data}\nPASSAGE: {i.passage or i.quote}"
        if self.legacy:
            return f"{head}CLAIM: {i.claim}\n{tail}"
        sentences = claim_sentences(i.claim)
        claim = (
            " ".join(f"[{n}] {s}" for n, s in enumerate(sentences, 1))
            if len(sentences) > 1
            else i.claim
        )
        about = ", ".join(
            x
            for x in (
                i.about_region and f"country {i.about_region}",
                i.about_entity and f"about {i.about_entity}",
            )
            if x
        )
        return f"{head}CLAIM: {claim}\n{tail}" + (f"\nABOUT: {about}" if about else "")


class CheckVerdict(W):
    id: str
    verdict: Verdict
    unsupported_fields: list[str] = Field(
        default_factory=list
    )  # paths in DATA the passage does not state
    unsupported_sentences: list[int] = Field(
        default_factory=list
    )  # numbers (from 1) of the claim's sentences the passage does not state; only for "partial"


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


def _spec(version: int, system: str, user: str) -> ResearchCallSpec[CheckIn, CheckOut]:
    return ResearchCallSpec(
        spec=CallSpec(
            id=CALL_ID,
            version=version,
            input_model=CheckIn,
            output_model=CheckOut,
            system_template=system,
            user_template=user,
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


SPEC: ResearchCallSpec[CheckIn, CheckOut] = _spec(3, "check.system.jinja", "check.user.jinja")
# The v2 checker, kept only so an experiment can measure the new one against it (``CheckIn(legacy=True)``).
SPEC_V2: ResearchCallSpec[CheckIn, CheckOut] = _spec(
    2, "check_v2.system.jinja", "check_v2.user.jinja"
)
