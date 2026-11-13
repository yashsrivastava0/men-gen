"""RSCH-1b: the query writer. Writes 2 or 3 search variants for ONE topic.

The model sees only its topic, anchors and place (no page text, so no untrusted web content and no tools). Whatever
it writes is checked by CODE against the anatomy gate (anchor + place + intent word, short, no URL or key, never a
repeat); variants that fail are dropped, and when none survives the deterministic builder below runs. That builder is
also the mock handler and the fallback.

Official-first: on a topic flagged ``official_first`` in the job data, ``official_domains`` lists the authority domains
the registry holds for the topic (chosen by code) and ONE variant is of style ``official``: written in ``language`` (the
language the country's authorities write in) with that language's intent words. It REPLACES a descriptive variant, so the
number of searches does not change. The model never chooses the domains; the worker attaches them to the search request.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator

from mde_contracts.gateway import CallSpec
from mde_research_agent.calls.base import ResearchCallSpec
from mde_research_agent.core.anatomy import is_repeat

CALL_ID = "rsch.write_queries"
MAX_WORDS = 12


class W(BaseModel):
    model_config = ConfigDict(extra="forbid")


class QueryIn(W):
    job: str
    question: str
    anchors: list[str]
    context: str  # the kind of app, in the user's words
    place: str  # "" for a global topic
    languages: list[str]  # non-English search languages (at most one is used)
    local_terms: list[str]  # words the Brief used in that language (synonyms)
    local_intent: list[str]  # intent words in that language
    intent: list[str]
    entity: str | None = None
    earlier: list[str]  # queries already sent for this topic: never repeat
    want: int
    round: int
    focus_terms: list[str]  # what is still missing (follow-up rounds)
    seed_names: list[str]  # names from model memory: discovery queries only
    # the language the topic's authorities write in (rules and official pages), and the intent words in it
    language: str = "en"
    official_intent: list[str] = []
    # authority domains the registry holds for this topic (round one of an official_first topic only), set by code
    official_domains: list[str] = []
    text: str = ""  # the topic as ONE untrusted block for the prompt (built from the fields above)

    @model_validator(mode="after")
    def _build_text(self) -> QueryIn:
        self.text = "\n".join(
            [
                f"Question: {self.question}",
                f"Anchors: {'; '.join(self.anchors)}",
                f"Kind of app: {self.context}",
                f"Place: {self.place}",
                f"Words the Brief used in the other language: {', '.join(self.local_terms)}",
                f"Named entity: {self.entity or 'none'}",
                f"Focus terms (still missing): {', '.join(self.focus_terms)}",
                f"Seed names from memory: {', '.join(self.seed_names)}",
                f"Official domains asked for first (written by code): {', '.join(self.official_domains[:8])}",
                "Earlier queries, do not repeat:",
                *[f"- {q}" for q in self.earlier],
            ]
        )
        return self


class Variant(W):
    text: str
    style: Literal["literal", "descriptive", "local", "official"]
    language: str
    exact: bool
    seed: bool


class QueryOut(W):
    variants: list[Variant]


def _clip(words: list[str], n: int = MAX_WORDS) -> str:
    seen: dict[str, None] = {}
    for w in " ".join(words).split():
        seen.setdefault(w, None)
    return " ".join(list(seen)[:n])


def deterministic_variants(inp: QueryIn) -> QueryOut:
    """Plain-code variants from the topic: descriptive, literal (exact entity), local-language, seed discovery."""
    anchor = inp.anchors[0] if inp.anchors else ""
    ctx = inp.context if inp.context and inp.context.casefold() not in anchor.casefold() else ""
    place = inp.place
    out: list[Variant] = []

    def add(
        text: str,
        style: Literal["literal", "descriptive", "local", "official"],
        lang: str,
        exact: bool = False,
        seed: bool = False,
    ) -> None:
        text = _clip(text.split())
        if text and not is_repeat(text, [*inp.earlier, *(v.text for v in out)]):
            out.append(Variant(text=text, style=style, language=lang, exact=exact, seed=seed))

    if inp.round > 1 and inp.focus_terms:
        add(f"{anchor} {ctx} {' '.join(inp.focus_terms[:3])} {place}", "descriptive", "en")
        add(
            f"{anchor} {' '.join(inp.focus_terms[3:6]) or ' '.join(inp.intent[2:4])} {place}",
            "descriptive",
            "en",
        )
    else:
        add(f"{anchor} {ctx} {' '.join(inp.intent[:2])} {place}", "descriptive", "en")
        if inp.entity:
            add(f'"{inp.entity}" {" ".join(inp.intent[:2])} {place}', "literal", "en", exact=True)
        else:
            add(
                f"{anchor} {' '.join(inp.intent[2:4])} {' '.join(inp.focus_terms[:2])} {place}",
                "descriptive",
                "en",
            )
    if inp.languages and (inp.local_terms or inp.local_intent):
        local = inp.local_terms[0] if inp.local_terms else anchor
        add(f"{local} {' '.join(inp.local_intent[:2])} {place}", "local", inp.languages[0])
    if inp.round == 1:
        for name in inp.seed_names[:2]:
            add(
                f'"{name}" {" ".join(inp.intent[:2])} {place}',
                "literal",
                "en",
                exact=True,
                seed=True,
            )
    if inp.official_domains and inp.round == 1:
        official = official_variant(inp, [v.text for v in out])
        if official is not None:
            # it REPLACES the second variant (or comes second when only one was written): the search count never grows
            keep = [v for v in out if not v.seed]
            seeds = [v for v in out if v.seed]
            out = [*keep[:1], official, *keep[2:], *seeds]
    plain = [v for v in out if not v.seed][: max(1, inp.want)]
    seeds = [v for v in out if v.seed][
        :1
    ]  # discovery from a model-memory name: one extra variant at most
    return QueryOut(variants=[*plain, *seeds])


def official_variant(inp: QueryIn, earlier: list[str]) -> Variant | None:
    """The deterministic ``official`` variant: the topic's anchor, the intent words of the authority's language, the word
    "official" and the place. English when ``language`` is English or no words of that language are known."""
    anchor = inp.anchors[0] if inp.anchors else ""
    words = (inp.official_intent or inp.intent[2:4] or inp.intent[:2])[:2]
    lead = "official " if inp.language == "en" or not inp.official_intent else ""
    text = _clip(f"{lead}{anchor} {' '.join(words)} {inp.place}".split())
    if not text or is_repeat(text, [*inp.earlier, *earlier]):
        return None
    return Variant(text=text, style="official", language=inp.language, exact=False, seed=False)


def handle(inp: QueryIn) -> QueryOut:
    return deterministic_variants(inp)


def validate(inp: QueryIn, out: QueryOut) -> list[str]:
    if not out.variants:
        return ["no variants"]
    return []


SPEC: ResearchCallSpec[QueryIn, QueryOut] = ResearchCallSpec(
    spec=CallSpec(
        id=CALL_ID,
        version=2,
        input_model=QueryIn,
        output_model=QueryOut,
        system_template="queries.system.jinja",
        user_template="queries.user.jinja",
        template_root=Path(__file__).parent / "templates",
        route="fast",
        reasoning_effort="low",
        max_output_tokens=1500,
        mock_handler="mde_research_agent.calls.queries:handle",
        untrusted_inputs=("text",),
        cacheable=True,
        repair=True,
        fallback="research.write_queries",
    ),
    timeout_s=45.0,
    fallback=deterministic_variants,
    validators=(validate,),
)
