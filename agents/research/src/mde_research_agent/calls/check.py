"""RSCH-4: the checker. A second model reads ONE claim and ONE quote and says whether the quote supports the claim.

Only high-impact items are checked, in batches. The checker sees nothing else: no page, no URL, no tools. Code
checks stay primary (the quote is a substring of the stored page, two independent domains, official sources
first, forum/blog/SEO cannot support a high-impact claim); this call is a second opinion. It runs on the
``standard`` route, which Research reserves for it, so a different model is one ``MODEL_ROUTES`` entry away; until
one is configured the writer's model answers and the run records ``checker_vendor: same`` (RS-22). Mock handler: a
deterministic word-overlap check; the fallback treats the batch as unchecked (the engine flags, never drops).
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator

from mde_contracts.gateway import CallSpec
from mde_research_agent.calls.base import ResearchCallSpec
from mde_research_agent.core.language import dominant_script
from mde_research_agent.core.text import stem_set

CALL_ID = "rsch.check"
Verdict = Literal["supported", "partial", "not_supported"]


class W(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CheckItem(W):
    id: str
    claim: str
    quote: str


class CheckIn(W):
    items: list[CheckItem]
    text: str = ""  # claims and quotes as ONE untrusted block

    @model_validator(mode="after")
    def _build_text(self) -> CheckIn:
        self.text = "\n".join(f"[{i.id}]\nCLAIM: {i.claim}\nQUOTE: {i.quote}\n" for i in self.items)
        return self


class CheckVerdict(W):
    id: str
    verdict: Verdict


class CheckOut(W):
    verdicts: list[CheckVerdict]


def overlap_verdict(item: CheckItem) -> Verdict:
    """Word-overlap heuristic: share of the claim's content stems that the quote also contains.

    A claim paraphrased in English from a quote in another script cannot be judged by word overlap: ``partial``.
    """
    if dominant_script(item.claim) != dominant_script(item.quote):
        return "partial"
    claim, quote = stem_set(item.claim), stem_set(item.quote)
    if not claim:
        return "not_supported"
    share = len(claim & quote) / len(claim)
    return "supported" if share >= 0.55 else "partial" if share >= 0.3 else "not_supported"


def heuristic_check(inp: CheckIn) -> CheckOut:
    return CheckOut(verdicts=[CheckVerdict(id=i.id, verdict=overlap_verdict(i)) for i in inp.items])


def handle(inp: CheckIn) -> CheckOut:
    return heuristic_check(inp)


def unchecked(inp: CheckIn) -> CheckOut:
    """The fallback: no opinion. Items stay as they are and are flagged ``unchecked`` by the engine."""
    return CheckOut(verdicts=[])


def validate(inp: CheckIn, out: CheckOut) -> list[str]:
    ids = {i.id for i in inp.items}
    stray = [v.id for v in out.verdicts if v.id not in ids]
    return [f"verdicts for unknown items: {stray}"] if stray else []


SPEC: ResearchCallSpec[CheckIn, CheckOut] = ResearchCallSpec(
    spec=CallSpec(
        id=CALL_ID,
        version=1,
        input_model=CheckIn,
        output_model=CheckOut,
        system_template="check.system.jinja",
        user_template="check.user.jinja",
        template_root=Path(__file__).parent / "templates",
        route="standard",
        reasoning_effort="low",
        max_output_tokens=3000,
        mock_handler="mde_research_agent.calls.check:handle",
        untrusted_inputs=("text",),
        cacheable=True,
        repair=True,
        fallback="research.check",
    ),
    timeout_s=60.0,
    fallback=unchecked,
    validators=(validate,),
    checker=True,
)
