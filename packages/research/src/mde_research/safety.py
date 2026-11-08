"""Safety rules for web content (spec 4.4): web text is data, never instructions.

Layers, each independent of the model:

1. ``clean_web_text`` strips hidden and zero-width characters, scripts, styles, comments and tags,
   turns inline links into numbered source markers (``[n]``), drops every other url and caps size.
2. ``detect_injection`` looks for instruction look-alikes in the text (links to known sources
   already replaced by markers) and again in the cleaned text; a result that trips it is dropped
   before any model reads it.
3. C7 runs without tools and sees the text only inside the gateway's untrusted block.
4. ``clean_statement`` / ``check_statement`` validate every fact C7 returns (no urls, no markup,
   no instruction look-alikes, at most 240 characters) and the engine only accepts source urls that
   came from the search result.

The detector is deliberately a tripwire, not a guarantee (OWASP: foolproof prevention does not
exist): the structural layers 3 and 4 hold even when a payload gets past it.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass

from mde_contracts.gateway import Citation
from mde_contracts.policy import WebPolicy
from mde_core.redact import redact_text, strip_hidden_chars
from mde_research.domains import (
    domain_allowed,
    host_of,
    registrable_domain,
    safe_http_url,
    url_key,
)

_FLAGS = re.IGNORECASE | re.MULTILINE

# Every regex here sees web text, which an attacker controls, so every one must be *linear* in the
# length of that text (a quadratic pattern freezes the shared event loop for every tenant):
#
# * whitespace gaps are bounded (``\s{0,16}``, never ``\s*`` / ``\s+``);
# * a pattern is never tried from every start of a long run (a left boundary is a lookbehind, a
#   repeat that could run on is bounded or possessive, ``^`` is only tried at line starts);
# * no ``.*?`` that can scan to the end of the text from every start: the script / comment blocks
#   are removed by :func:`_strip_markup`'s cursor scan instead (one pass, see there);
# * :func:`normalize_text` folds whitespace runs first, so the bounds are a second line of defence.
#
# ``tests/security/test_regex_linearity.py`` holds the line (hostile inputs, 4x scaling check).

# (name, pattern). Names are logged; the text that matched never is. Tuned to stay quiet on
# ordinary product and developer documentation.
INJECTION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (name, re.compile(pattern, _FLAGS))
    for name, pattern in (
        (
            "override_instructions",
            r"\b(?:ignore|disregard|forget|override)\b[^.\n]{0,50}"
            r"\b(?:previous|prior|above|earlier|preceding|all|any|your|these|those|system)\b"
            r"[^.\n]{0,40}\b(?:instruction|prompt|rule|direction|guideline|constraint)s?\b",
        ),
        (
            "forget_everything",
            r"\b(?:ignore|forget|disregard)\s{1,16}(?:everything|anything|all)\s{1,16}"
            r"(?:above|before|else|you (?:were|have been) told)\b",
        ),
        (
            "system_prompt",
            r"\b(?:system|developer)\s{1,16}(?:prompt|instruction)s?\b"
            r"|\bhidden\s{1,16}(?:prompt|instruction)s?\b",
        ),
        (
            "exfiltrate",
            r"\b(?:reveal|print|show|repeat|output|leak|disclose|expose|tell me)\b[^.\n]{0,30}"
            r"\b(?:your|this|its|all)\s{1,16}"
            r"(?:(?:system|hidden|initial|original)\s{1,16})?"
            r"(?:prompt|instructions|api[ _-]?key|secrets?|credentials)\b"
            r"|\b(?:reveal|print|show|repeat|output|leak|disclose|expose|tell me)\b[^.\n]{0,30}"
            r"\bthe\s{1,16}(?:system|hidden|initial|original)\s{1,16}(?:prompt|instructions)\b"
            r"|\b(?:reveal|print|show|repeat|output|leak|disclose|expose|tell me)\b[^.\n]{0,30}"
            r"\b(?:api[ _-]?key|secret key|password|credentials?)\b[^.\n]{0,30}\byou\b",
        ),
        (
            "fetch_url",
            r"\b(?:visit|fetch|open|download|browse|navigate|go to|click|curl|wget)\b"
            r"[^.\n]{0,80}(?:https?://|www\.)",
        ),
        (
            "send_to_url",
            r"\b(?:send|post|upload|forward|submit)\b[^.\n]{0,60}\bto\b[^.\n]{0,30}"
            r"(?:https?://|www\.)",
        ),
        ("role_marker", r"(?:^|[.!?]\s{1,16})(?:system|assistant|developer|tool)\s{0,16}[:>\]]"),
        (
            "chat_template",
            r"<\|[a-z_]{2,30}\|>|\[/?(?:inst|sys)\]|<</?sys>>"
            r"|^[^\S\n]{0,16}#{2,}+\s{0,16}(?:system|instruction)",
        ),
        ("untrusted_wrapper", r"</?\s{0,16}untrusted_content"),
        (
            "role_change",
            r"\b(?:you are now|from now on[, ]{1,16}you|pretend (?:to be|you)|new instructions?)\b",
        ),
        ("new_task", r"\byour (?:new |real |actual )?(?:task|goal|job|purpose) is\b"),
        (
            "ai_address",
            r"\b(?:as an? (?:ai|language model|assistant)"
            r"|dear (?:ai|assistant|model|llm)|attention (?:ai|assistant|model|llm)"
            r"|note to (?:the )?(?:ai|assistant|model|llm))\b",
        ),
        (
            "hide_from_user",
            r"\bdo not (?:tell|mention|inform|show)\b[^.\n]{0,30}\b(?:user|human|operator)\b",
        ),
        (
            "output_override",
            r"\b(?:respond|reply|answer|output)\s{1,16}(?:only\s{1,16})?with\s{1,16}"
            r"(?:exactly|the following|this exact|[\"'])",
        ),
        (
            "json_payload",
            r"[\"'](?:statement|source_url|injection_suspected|facts|suggestions|fact_ids)[\"']"
            r"\s{0,16}:",
        ),
        (
            "script_markup",
            r"<\s{0,16}(?:script|iframe|object|embed|svg|form|meta|link)\b|javascript\s{0,16}:"
            r"|on(?:error|load|click)\s{0,16}=",
        ),
    )
)

# Opening tags of the blocks whose *content* is dropped (the closing tag is looked for by a
# separate search, see ``_strip_markup``). ``[^<>]`` keeps a start from running into the next tag.
_BLOCK_OPEN = re.compile(r"<\s{0,16}(script|style)\b[^<>]{0,500}+>", re.IGNORECASE)
_BLOCK_CLOSE = {
    tag: re.compile(rf"<\s{{0,16}}/\s{{0,16}}{tag}\s{{0,16}}>", re.IGNORECASE)
    for tag in ("script", "style")
}
_TAG = re.compile(r"</?[A-Za-z][^<>]{0,500}>")
# ``[^\[...]`` stops a start at the next ``[``: a run of ``[x](`` costs O(1) per start, not O(500)
_MD_LINK = re.compile(
    r"!?\[([^\[\]\n]{0,300})\]\(\s{0,16}<?\s{0,16}([^\s)<>\[]{1,2048}+)[^)\n\[]{0,200}+\)"
)
_BARE_URL = re.compile(r"(?:https?://|www\.)[^\s<>\"')\]]+", re.IGNORECASE)
_PAREN_MARKERS = re.compile(r"\(\s{0,16}((?:\[\d{1,2}\][\s,;]{0,16}+)++)\)")
_EMPTY_PARENS = re.compile(r"\(\s{0,16}\)|\[\s{0,16}\]")
_REPEAT_MARKER = re.compile(r"(\[\d{1,2}\])(?:\s{0,16}\1)+")
_ADJACENT_MARKERS = re.compile(r"(\[\d{1,2}\])(?=\[\d)")
_CODE_FENCE = re.compile(r"```")
# a left boundary (lookbehind) so a run of word characters is scanned once, not from every start
_EMAIL = re.compile(r"(?<![\w.+-])[\w.+-]++@[\w-]++\.[\w.-]++")
_PHONE = re.compile(r"(?<!\d)(?:\+?\d[\d\s().-]{7,}\d)(?!\d)")
_JSON_KEY = re.compile(r"[{}]\s{0,16}[\"']?\w++[\"']?\s{0,16}:")
_SPACE_BEFORE_PUNCT = re.compile(r"(?<!\s)\s++([.,;:!?])")  # starts once per whitespace run
_HSPACE = re.compile(r"[^\S\n]+")  # a run of horizontal whitespace
_VSPACE = re.compile(r"\n(?: ?\n)+")  # a run of line breaks (with at most one space between)
RAW_TEXT_FACTOR = 4  # raw answers longer than this many times the size cap are cut before cleaning
MAX_TITLE_SCAN = 2_000  # a source title is shown at 100 characters; more is never looked at
MAX_MODEL_TEXT_CHARS = 20_000  # a fact statement or query longer than this is not a fact or a query


@dataclass(frozen=True)
class SourceInfo:
    """One usable source of a search result (position + 1 is its marker number)."""

    url: str
    title: str | None
    domain: str  # registrable domain


@dataclass(frozen=True)
class PreparedResult:
    text: str
    sources: tuple[SourceInfo, ...]
    injection: tuple[str, ...]  # pattern names that matched (text, cleaned text or a title)
    dropped_sources: int


def normalize_text(text: str) -> str:
    """Hidden characters out, compatibility forms folded (full-width letters and the like) and
    whitespace runs folded: a run of spaces, tabs or no-break spaces becomes one space and a run
    of line breaks one line break (a hostile page cannot make a pattern chew on a long run).
    """
    folded = unicodedata.normalize("NFKC", strip_hidden_chars(text))
    return _VSPACE.sub("\n", _HSPACE.sub(" ", folded))


def detect_injection(text: str) -> list[str]:
    """Names of the injection look-alike patterns found in ``text`` (empty when none)."""
    folded = normalize_text(text)
    return [name for name, pattern in INJECTION_PATTERNS if pattern.search(folded)]


def _title(raw: str | None) -> str | None:
    if not raw:
        return None
    text = _TAG.sub(" ", normalize_text(raw[:MAX_TITLE_SCAN]))
    text = re.sub(r"[\[\]()<>`]", " ", text)
    text = " ".join(text.split())[:100].strip()
    if not text or detect_injection(text) or _BARE_URL.search(text):
        return None
    return text


def select_sources(
    citations: Sequence[Citation], web: WebPolicy, *, limit: int
) -> tuple[list[SourceInfo], int]:
    """Usable sources in citation order: valid public http(s) urls that pass the domain policy.

    Returns the sources and the number of citations that were dropped."""
    sources: list[SourceInfo] = []
    seen: set[str] = set()
    dropped = 0
    for citation in citations:
        url = safe_http_url(citation.url)
        host = host_of(url) if url else None
        if url is None or host is None:
            dropped += 1
            continue
        if not domain_allowed(host, web.allowed_domains, web.blocked_domains):
            dropped += 1
            continue
        key = url_key(url)
        if key in seen:
            continue
        if len(sources) >= limit:
            dropped += 1
            continue
        seen.add(key)
        sources.append(
            SourceInfo(url=url, title=_title(citation.title), domain=registrable_domain(host))
        )
    return sources, dropped


def _marker_for(url: str, index: dict[str, int]) -> int | None:
    return index.get(url_key(url.rstrip(".,;:")))


def _link_known_sources(text: str, index: dict[str, int]) -> str:
    """Markdown links to a known source become ``[n]``; every other link is left for the checks."""

    def link(match: re.Match[str]) -> str:
        n = _marker_for(match.group(2), index)
        return f" [{n}] " if n else match.group(0)

    return _MD_LINK.sub(link, text)


def _markers_for_links(text: str, index: dict[str, int]) -> str:
    """Remaining urls: a known source becomes ``[n]``, anything else is removed."""

    def link(match: re.Match[str]) -> str:
        n = _marker_for(match.group(2), index)
        return f" [{n}] " if n else " "

    def bare(match: re.Match[str]) -> str:
        found = match.group(0)
        tail = found[len(found.rstrip(".,;:")) :]  # sentence punctuation is not part of the url
        n = _marker_for(found, index)
        return (f" [{n}] " if n else " ") + tail

    return _BARE_URL.sub(bare, _MD_LINK.sub(link, text))


def _cap(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    cut = text[:max_chars]
    end = max(cut.rfind(". "), cut.rfind(".\n"))
    return cut[: end + 1] if end > max_chars // 2 else cut.rsplit(" ", 1)[0]


def _drop_blocks(text: str) -> str:
    """``<script>..</script>`` and ``<style>..</style>`` blocks become one space (linear).

    A lazy ``.*?`` closing search from *every* opening tag is quadratic on a page of repeated
    ``<script>``: here the closing tag is searched once per opening tag, and when a tag has no
    closing tag left in the text no later opening tag of that kind can have one either, so it is
    remembered and the search is never repeated.
    """
    out: list[str] = []
    last = scan = 0
    exhausted: set[str] = set()
    while (opening := _BLOCK_OPEN.search(text, scan)) is not None:
        tag = opening.group(1).lower()
        scan = opening.start() + 1  # an unclosed opening tag does not hide one nested in it
        if tag in exhausted:
            continue
        closing = _BLOCK_CLOSE[tag].search(text, opening.end())
        if closing is None:
            exhausted.add(tag)
            continue
        out.append(text[last : opening.start()])
        out.append(" ")
        last = scan = closing.end()
    out.append(text[last:])
    return "".join(out)


def _drop_comments(text: str) -> str:
    """``<!-- .. -->`` becomes one space; an unterminated comment is left (linear: ``str.find``)."""
    out: list[str] = []
    pos = 0
    while (start := text.find("<!--", pos)) != -1:
        end = text.find("-->", start + 4)
        if end == -1:  # no later comment can end either
            break
        out.append(text[pos:start])
        out.append(" ")
        pos = end + 3
    out.append(text[pos:])
    return "".join(out)


def _strip_markup(text: str) -> str:
    out = _drop_comments(_drop_blocks(text))
    out = _TAG.sub(" ", out)
    return _CODE_FENCE.sub(" ", out)


def clean_web_text(text: str, sources: Sequence[SourceInfo], *, max_chars: int) -> str:
    """Strip markup and hidden text, number the inline links, cap the size."""
    index = {url_key(s.url): i + 1 for i, s in enumerate(sources)}
    out = _strip_markup(normalize_text(text))
    out = _markers_for_links(out, index)
    out = _PAREN_MARKERS.sub(lambda m: f" {m.group(1).strip()} ", out)
    out = _EMPTY_PARENS.sub(" ", out)
    lines = [" ".join(line.split()) for line in out.splitlines()]
    out = "\n".join(line for line in lines if line)
    out = _REPEAT_MARKER.sub(r"\1", _ADJACENT_MARKERS.sub(r"\1 ", out))
    out = _SPACE_BEFORE_PUNCT.sub(r"\1", out)
    return _cap(out.strip(), max_chars)


def prepare_search_result(
    text: str | None,
    citations: Sequence[Citation],
    web: WebPolicy,
    *,
    max_chars: int,
    max_sources: int,
) -> PreparedResult:
    """Everything between the S1 answer and the C7 call: sources, cleaned text, injection check."""
    raw = (text or "")[: RAW_TEXT_FACTOR * max_chars]  # bounds the regex work on hostile input
    sources, dropped = select_sources(citations, web, limit=max_sources)
    index = {url_key(s.url): i + 1 for i, s in enumerate(sources)}
    reasons = set(detect_injection(_link_known_sources(normalize_text(raw), index)))
    cleaned = clean_web_text(raw, sources, max_chars=max_chars)
    reasons.update(detect_injection(cleaned))
    for citation in citations:  # a poisoned title is an injection attempt too
        if citation.title and detect_injection(citation.title[:MAX_TITLE_SCAN]):
            reasons.add("poisoned_title")
    return PreparedResult(
        text=cleaned,
        sources=tuple(sources),
        injection=tuple(sorted(reasons)),
        dropped_sources=dropped,
    )


# ----------------------------------------------------------------------------- statements


def clean_statement(text: str, limit: int) -> str:
    """One fact statement: plain text on one line, at most ``limit`` characters."""
    out = normalize_text(text[:MAX_MODEL_TEXT_CHARS])
    out = _TAG.sub(" ", out)
    out = _BARE_URL.sub(" ", out)
    out = re.sub(r"[`*_>|]+", " ", out)
    out = re.sub(r"(?:^|\s)#{1,6}\s+", " ", out)  # markdown headings; hex colors like #1A4FD6 stay
    out = " ".join(out.split()).strip(" -•\"'“”")
    if len(out) > limit:
        head = out[: limit - 1]
        stop = max(head.rfind(". "), head.rfind("; "))
        if stop >= limit // 2:
            out = head[: stop + 1]
        else:
            out = head.rsplit(" ", 1)[0].rstrip(" ,;:") + "…"
    return out


def check_statement(clean: str, original: str, *, min_chars: int = 12) -> str | None:
    """Why a statement must be dropped (a short reason code), or ``None`` when it is acceptable."""
    if len(clean) < min_chars:
        return "too_short"
    if len(original) > MAX_MODEL_TEXT_CHARS:  # not a fact; and never scanned at that length
        return "too_long"
    if _CODE_FENCE.search(original) or _JSON_KEY.search(original):
        return "looks_like_json"
    if detect_injection(original) or detect_injection(clean):
        return "instruction_like"
    if _BARE_URL.search(original) or "://" in original:
        return "contains_url"
    return None


def has_private_data(text: str) -> bool:
    """Emails, phone numbers and secret-looking strings (checked on every outgoing query)."""
    if _EMAIL.search(text) or _PHONE.search(text):
        return True
    return bool(redact_text(text)[1])


def clean_query(raw: str, limit: int) -> str | None:
    """A search query as plain words, or ``None`` when it is unusable or unsafe."""
    text = normalize_text(raw[:MAX_MODEL_TEXT_CHARS])
    text = _TAG.sub(" ", text)
    text = _BARE_URL.sub(" ", text)
    text = re.sub(r"[\"'`<>{}\[\]\\|]+", " ", text)
    text = " ".join(text.split())
    if len(text) > limit:
        text = text[:limit].rsplit(" ", 1)[0]
    if len(text.split()) < 2 or detect_injection(text) or has_private_data(text):
        return None
    return text
