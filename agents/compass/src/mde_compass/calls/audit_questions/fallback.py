"""The deterministic auditor: the fallback of ``cmp.audit_questions`` and, with a label, its mock handler.

A pure function of the wire input (no model, no network, no clock). It is a *strict* reviewer on purpose: it
cannot read meaning, so it only ever trusts what it can count. A question gets a high specificity score only
when it uses the project's own words; a question with none can never score 4 or 5, so the heuristic cannot
approve a generic question by accident. Its verdicts are plain facts the engine can act on (reject below 3 or
on any flag), with short English issues and a short ``fix`` hint. It never rewrites a question.

Tokens are compared after Unicode folding and a one-letter plural strip, so "Clinics" matches the term
"clinic" and Devanagari words match as whole words. Every pattern here is linear.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from difflib import SequenceMatcher

from mde_compass.calls.audit_questions.schema import (
    AuditInput,
    AuditOutput,
    QuestionWire,
    VerdictWire,
)

MOCK_TAG = "[mock]"

# Needs that any project must answer, so a question for them needs no project word (a "which languages" card
# reads the same for every app). Mirrors the catalog's ``universal`` flag for the ids that do not depend on a
# domain.
UNIVERSAL_NEEDS: frozenset[str] = frozenset(
    {"idea.summary", "context.geo", "brand.name", "brand.assets", "locales"}
)

# Wording that pushes the person towards an answer (English, Hindi, Hindi in Latin letters).
LEADING_WORDS: tuple[str, ...] = (
    "best",
    "obviously",
    "clearly",
    "you should",
    "you must",
    "just",
    "simply",
    "amazing",
    "of course",
    "definitely",
    "surely",
    "no brainer",
    "zaroor",
    "bilkul",
    "sabse accha",
    "sabse achha",
    "sabse badhiya",
    "ज़रूर",
    "बिल्कुल",
    "सबसे अच्छा",
)

# Words a non-technical person does not know. A word the person used themselves (it is among the project
# terms) is not jargon for them and is exempt.
JARGON_WORDS: tuple[str, ...] = (
    "api",
    "backend",
    "frontend",
    "database",
    "db",
    "oauth",
    "sso",
    "schema",
    "framework",
    "deploy",
    "deployment",
    "server",
    "endpoint",
    "sdk",
    "webhook",
    "json",
    "middleware",
    "latency",
    "scalability",
    "microservice",
    "crud",
    "sql",
    "cache",
    "jwt",
    "docker",
    "kubernetes",
    "tech stack",
    "ssl",
    "tls",
)

MAX_ANSWERABLE_WORDS = 40  # a longer question is not answerable in one go
DUPLICATE_RATIO = 0.8  # SequenceMatcher ratio at which two questions count as the same
MAX_COMPARE_CHARS = 400  # bounds the cost of one similarity check
MAX_REPORTED = 3  # words named in one issue
_TOKEN = re.compile(r"[\wऀ-ॿ]+")  # word characters plus the Devanagari block (marks included)


# --------------------------------------------------------------------------------------------- tokens
def tokens(text: str) -> list[str]:
    """Folded word tokens: NFKC, case-folded, a trailing plural ``s`` dropped from words longer than three."""
    folded = unicodedata.normalize("NFKC", text).casefold()
    out: list[str] = []
    for tok in _TOKEN.findall(folded):
        if len(tok) > 3 and tok.endswith("s") and not tok.endswith("ss"):
            tok = tok[:-1]
        out.append(tok)
    return out


def _has_phrase(haystack: list[str], phrase: tuple[str, ...]) -> bool:
    n = len(phrase)
    if n == 0 or n > len(haystack):
        return False
    return any(tuple(haystack[i : i + n]) == phrase for i in range(len(haystack) - n + 1))


def _phrases(words: Iterable[str]) -> list[tuple[str, tuple[str, ...]]]:
    out: list[tuple[str, tuple[str, ...]]] = []
    for w in words:
        toks = tuple(tokens(w))
        if toks:
            out.append((w, toks))
    return out


_LEADING = _phrases(LEADING_WORDS)
_JARGON = _phrases(JARGON_WORDS)


def matched_terms(text_tokens: list[str], project_terms: list[str]) -> int:
    """How many DISTINCT project terms the text uses (whole words, case-folded)."""
    seen: set[tuple[str, ...]] = set()
    for term in project_terms:
        toks = tuple(tokens(term))
        if toks and toks not in seen and _has_phrase(text_tokens, toks):
            seen.add(toks)
    return len(seen)


def leading_words(text_tokens: list[str]) -> list[str]:
    """The leading words and phrases the text contains (at most three, in list order)."""
    return [w for w, toks in _LEADING if _has_phrase(text_tokens, toks)][:MAX_REPORTED]


def jargon_words(text_tokens: list[str], project_terms: list[str]) -> list[str]:
    """The technical words the text contains, minus any the person used themselves (at most three)."""
    own = {tok for term in project_terms for tok in tokens(term)}
    return [
        w
        for w, toks in _JARGON
        if _has_phrase(text_tokens, toks) and not all(tok in own for tok in toks)
    ][:MAX_REPORTED]


def _quoted(words: list[str]) -> str:
    return ", ".join(f"'{w}'" for w in words)


# ---------------------------------------------------------------------------------------- duplicates
def _plain(text: str) -> str:
    return " ".join(tokens(text))[:MAX_COMPARE_CHARS]


def similar(a: str, b: str) -> bool:
    """Two normalized texts that are nearly identical (``SequenceMatcher`` ratio at least 0.8)."""
    if not a or not b:
        return False
    sm = SequenceMatcher(None, a, b, autojunk=False)
    if sm.real_quick_ratio() < DUPLICATE_RATIO or sm.quick_ratio() < DUPLICATE_RATIO:
        return False
    return sm.ratio() >= DUPLICATE_RATIO


def parse_asked(asked_list: str) -> list[tuple[str, str]]:
    """``(id, intent)`` for each line that starts with a ``Q-<n>`` id; a ``[status]`` right after the id and a
    ``(status)`` at the end of the line are dropped. Lines without an id (like "(nothing asked yet)") are ignored."""
    out: list[tuple[str, str]] = []
    for raw in asked_list.splitlines():
        line = raw.strip().lstrip("-* ").strip()
        head, _, rest = line.partition(" ")
        head = head.rstrip(":")
        if not (head.startswith("Q-") and head[2:].isdigit()):
            continue
        rest = rest.strip()
        if rest.startswith("[") and "]" in rest:
            rest = rest[rest.index("]") + 1 :].strip()
        if rest.endswith(")") and "(" in rest:
            rest = rest[: rest.rindex("(")].strip()
        out.append((head, rest))
    return out


def duplicate_of(q: QuestionWire, earlier: list[QuestionWire], asked: list[tuple[str, str]]) -> str:
    """The id of the asked-list line or earlier batch question that ``q`` nearly repeats, or ``""``."""
    text, intent = _plain(q.text), _plain(q.intent)
    for aid, atext in asked:
        if aid == q.card_id:
            continue
        a = _plain(atext)
        if similar(intent, a) or similar(text, a):
            return aid
    for other in earlier:
        if similar(intent, _plain(other.intent)) or similar(text, _plain(other.text)):
            return other.card_id
    return ""


# ------------------------------------------------------------------------------------------ the verdict
def _specific(q: QuestionWire, matches: int) -> int:
    if matches >= 2:
        return 4
    if matches == 1:
        return 3
    universal = bool(q.serves) and all(s in UNIVERSAL_NEEDS for s in q.serves)
    return 3 if universal else 2


def _maps(q: QuestionWire, listed: set[str]) -> bool:
    if not q.serves:
        return False
    return all(s in listed or (s.startswith("domain:") and bool(s[7:].strip())) for s in q.serves)


def verdict_for(
    inp: AuditInput,
    q: QuestionWire,
    earlier: list[QuestionWire],
    asked: list[tuple[str, str]],
    *,
    mock: bool = False,
) -> VerdictWire:
    text_tokens = tokens(q.text)
    specific = _specific(q, matched_terms(text_tokens, inp.project_terms))
    lead = leading_words(text_tokens)
    tech = jargon_words(text_tokens, inp.project_terms)
    dup = duplicate_of(q, earlier, asked)
    words = len(q.text.split())
    answerable = words <= MAX_ANSWERABLE_WORDS
    maps = _maps(q, {n.need_id for n in inp.needs})

    issues: list[str] = []
    hints: list[str] = []
    if specific < 3:
        issues.append(
            "Uses none of the project's own words; it would read the same for an unrelated app."
        )
        hints.append("Name the project's own people, things or steps.")
    if lead:
        issues.append(f"Leading wording: {_quoted(lead)}.")
        hints.append(f"Remove {_quoted(lead)} and keep the wording neutral.")
    if tech:
        issues.append(f"Technical word for a non-technical person: {_quoted(tech)}.")
        hints.append(f"Replace {_quoted(tech)} with plain words.")
    if dup:
        issues.append(f"Nearly the same as {dup}.")
        hints.append(f"Drop it or ask about something {dup} did not cover.")
    if not answerable:
        issues.append(f"Too long to answer in one go ({words} words).")
        hints.append("Cut it to one idea.")
    if not maps:
        issues.append("Serves no listed need and no domain topic.")
        hints.append("Tie it to a listed need or to a domain topic.")
    fix = " ".join(hints)
    if mock and fix:
        fix = f"{MOCK_TAG} {fix}"
    return VerdictWire(
        card_id=q.card_id,
        specific=specific,
        leading=bool(lead),
        jargon=bool(tech),
        duplicate_of=dup,
        answerable=answerable,
        maps_to_need=maps,
        issues=issues,
        fix=fix,
    )


def heuristic(inp: AuditInput, *, mock: bool = False) -> AuditOutput:
    """One verdict per question, in order. ``mock`` labels every non-empty ``fix`` with ``[mock]``."""
    asked = parse_asked(inp.asked_list)
    verdicts = [
        verdict_for(inp, q, inp.questions[:i], asked, mock=mock)
        for i, q in enumerate(inp.questions)
    ]
    return AuditOutput(verdicts=verdicts)


def wire_fallback(inp: AuditInput) -> AuditOutput:
    """What ``CompassCallSpec.fallback`` needs: the heuristic's verdicts (flagged by the adapter as a fallback)."""
    return heuristic(inp)
