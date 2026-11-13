"""SafetyScreen: secrets and card numbers out before storage, injection noted never obeyed, unsafe and
unsupported requests declined early (prompt 6.13, hard rule 5).

Order matters. ``screen`` runs BEFORE text reaches a slot, an event, the summary or a model prompt:

1. the text is cut to ``max_chars`` (a secret in the dropped tail never reaches anything);
2. hidden characters are stripped and secrets are replaced by ``[REDACTED]`` (``mde_core.redact``);
3. card-like numbers (13 to 19 digits, Luhn valid) are masked, keeping the last four;
4. injection, unsafe and declined-category detection run on the REDACTED text.

Every regex is linear: bounded gaps, no nested quantifiers; keyword matching is a substring test first and a
whole-word regex only for the few phrases present. The module has no hidden state.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml

from mde_compass.catalog.loader import CapabilityRule, Catalog, load_catalog
from mde_compass.engine.language import normalize
from mde_core.redact import redact_text, strip_hidden_chars

LEXICON_PATH = Path(__file__).resolve().parent / "lexicon.yaml"
DEFAULT_MAX_CHARS = 200_000
MESSAGE_MAX_CHARS = (
    3_000  # longer pasted text is a document: declined-category guard is for messages
)
CARD_MASK = "[CARD ****{last4}]"
_CARD_RUN = re.compile(r"(?<!\d)\d(?:[ -]?\d){12,18}(?!\d)")
_SPACES = re.compile(r"\s+")
_SPACE_BOUND = re.compile(r"\s{2,}")


@dataclass(frozen=True)
class DeclinedMatch:
    """A declined category matched in a user message, with the polite alternative."""

    id: str
    label: str
    reason: str
    alternative: str
    keyword: str = ""


@dataclass(frozen=True)
class Screened:
    text: str  # redacted; the only text the rest of the engine ever sees
    flags: list[str] = field(default_factory=list)
    injection: bool = False
    unsafe: str | None = None  # illegal | harmful | abuse
    declined: DeclinedMatch | None = None
    truncated: bool = False
    tier_b: list[str] = field(default_factory=list)  # ids of tier-B capabilities mentioned

    @property
    def had_secret(self) -> bool:
        return any(f.startswith("secret:") for f in self.flags)


@dataclass(frozen=True)
class ContentRule:
    """When a declined noun is only CONTENT of another kind of app (``declined_content`` in the lexicon)."""

    weak: frozenset[str]  # bare nouns that can be content ("game")
    introducers: tuple[str, ...]  # "with", "including"...: what introduces content
    is_the_thing: tuple[str, ...]  # "game app", "games like": the app itself is the declined thing


@dataclass(frozen=True)
class Lexicon:
    unsafe: dict[str, tuple[str, ...]]
    injection: tuple[re.Pattern[str], ...]
    declined_allow: tuple[str, ...]
    declined_companion: tuple[str, ...]
    declined_content: dict[str, ContentRule]


@lru_cache(maxsize=1)
def load_lexicon() -> Lexicon:
    """Load ``lexicon.yaml`` once (phrases normalised, injection regexes compiled)."""
    raw = yaml.safe_load(LEXICON_PATH.read_text(encoding="utf-8"))
    return Lexicon(
        unsafe={k: tuple(dict.fromkeys(normalize(str(p)) for p in v)) for k, v in raw["unsafe"].items()},
        injection=tuple(re.compile(p, re.IGNORECASE) for p in raw["injection"]),
        declined_allow=tuple(normalize(str(p)) for p in raw["declined_context_allow"]),
        declined_companion=tuple(normalize(str(p)) for p in raw["declined_companion"]),
        declined_content={
            str(cid): ContentRule(
                weak=frozenset(normalize(str(p)) for p in rule["weak"]),
                introducers=tuple(normalize(str(p)) for p in rule["introducers"]),
                is_the_thing=tuple(normalize(str(p)) for p in rule["is_the_thing"]),
            )
            for cid, rule in (raw.get("declined_content") or {}).items()
        },
    )  # fmt: skip


@lru_cache(maxsize=4096)
def _phrase_re(phrase: str) -> re.Pattern[str]:
    """Whole-word pattern for a normalised phrase; Latin phrases also match a plural ``s`` or ``es``."""
    plural = r"(?:s|es)?" if phrase[-1:].isascii() and phrase[-1:].isalpha() else ""
    return re.compile(rf"(?<!\w){re.escape(phrase)}{plural}(?!\w)")


def phrase_hits(haystack_norm: str, phrases: tuple[str, ...] | list[str]) -> list[str]:
    """Normalised ``phrases`` present in ``haystack_norm`` as whole words (substring test first)."""
    out: list[str] = []
    for phrase in phrases:
        if phrase and phrase in haystack_norm and _phrase_re(phrase).search(haystack_norm):
            out.append(phrase)
    return out


def _luhn_ok(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = ord(ch) - 48
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def mask_card_numbers(text: str) -> tuple[str, bool]:
    """Mask Luhn-valid card-like numbers (13..19 digits, optional single spaces or dashes) in ``text``."""
    found = False

    def repl(m: re.Match[str]) -> str:
        nonlocal found
        digits = "".join(ch for ch in m.group(0) if ch.isdigit())
        if 13 <= len(digits) <= 19 and _luhn_ok(digits):
            found = True
            return CARD_MASK.format(last4=digits[-4:])
        return m.group(0)

    return _CARD_RUN.sub(repl, text), found


def _kw_norm(rule: CapabilityRule) -> list[str]:
    return list(dict.fromkeys(n for k in rule.keywords if (n := normalize(k))))


_CONTENT_WINDOW = (
    80  # characters (and at most _CONTENT_WORDS words) before a hit that may hold its introducer
)
_CONTENT_WORDS = 5
_CONTENT_MAX_HITS = (
    24  # more occurrences than this are not "a mention": the message is about the thing
)


def _introduced(cleaned: str, phrase: str, introducers: tuple[str, ...]) -> bool:
    """Every whole-word occurrence of ``phrase`` in ``cleaned`` is introduced ("with short games")."""
    seen = 0
    for m in _phrase_re(phrase).finditer(cleaned):
        seen += 1
        if seen > _CONTENT_MAX_HITS:
            return False
        before = cleaned[max(0, m.start() - _CONTENT_WINDOW) : m.start()].split()
        if not phrase_hits(" ".join(before[-_CONTENT_WORDS:]), introducers):
            return False
    return seen > 0


def _content_only(cleaned: str, hits: list[str], rule: ContentRule) -> bool:
    """The declined hits are only content of another app: the weak nouns, each one introduced, and nothing
    in the message says the app itself is the declined thing."""
    if phrase_hits(cleaned, rule.is_the_thing):
        return False
    return all(h in rule.weak and _introduced(cleaned, h, rule.introducers) for h in hits)


def declined_category(
    text: str, catalog: Catalog, *, language: str = "en", user_text: str = ""
) -> DeclinedMatch | None:
    """The first declined category (games, AR/VR, editors, DRM, trading, OS utilities, medical,
    hardware) whose keyword occurs in ``text`` as a whole word, or ``None``.

    Keywords come from ``catalog.capabilities`` (kind ``declined``, en/hi/hinglish). Matching is word-aware
    ("game" does not match "game plan" or "gamification"); benign contexts listed in ``lexicon.yaml``
    ("companion app", "game plan") cancel the match, because a companion app for a game IS buildable.
    A declined noun that is only content of another app ("a learning app with short games") does not
    decline it (``declined_content`` in the lexicon). ``user_text`` is the user's own earlier words:
    a declined phrase the user already used in a message that was NOT declined is echoed content, not an
    offer of the declined thing (a card option "short games" after "a learning app with short games").
    """
    norm = normalize(text[:DEFAULT_MAX_CHARS])
    if not norm:
        return None
    lex = load_lexicon()
    if phrase_hits(norm, lex.declined_companion):
        return None
    cleaned = norm
    for ctx in phrase_hits(norm, lex.declined_allow):
        cleaned = cleaned.replace(ctx, " ")
    own = normalize(user_text[:DEFAULT_MAX_CHARS]) if user_text else ""
    own_declined = bool(own) and declined_category(own, catalog) is not None
    for rule in catalog.declined():
        hits = phrase_hits(cleaned, _kw_norm(rule))
        if hits:
            content = lex.declined_content.get(rule.id)
            if content is not None and _content_only(cleaned, hits, content):
                continue
            if own and not own_declined and all(phrase_hits(own, [h]) for h in hits):
                continue  # the user's own, already accepted words
            lang = language if language in ("en", "hi") else "en"
            return DeclinedMatch(
                id=rule.id,
                label=rule.label.get(lang),
                reason=rule.reason.get(lang) if rule.reason else "",
                alternative=rule.alternative.get(lang) if rule.alternative else "",
                keyword=hits[0],
            )
    return None


def tier_b_hits(text: str, catalog: Catalog) -> list[str]:
    """Ids of tier-B capabilities (real push, live location, biometrics...) mentioned in ``text``."""
    norm = normalize(text[:DEFAULT_MAX_CHARS])
    return [rule.id for rule in catalog.tier_b() if phrase_hits(norm, _kw_norm(rule))]


def unsafe_category(text: str) -> str | None:
    """``illegal``, ``harmful`` or ``abuse`` when the text asks for something unsafe, else ``None``."""
    norm = normalize(text[:DEFAULT_MAX_CHARS])
    lex = load_lexicon()
    for category in ("harmful", "illegal", "abuse"):
        if phrase_hits(norm, lex.unsafe.get(category, ())):
            return category
    return None


def injection_suspected(text: str) -> bool:
    """The text tries to instruct the assistant ("ignore previous instructions" style). Linear."""
    folded = _SPACE_BOUND.sub(" ", text[:DEFAULT_MAX_CHARS]).casefold()
    return any(p.search(folded) for p in load_lexicon().injection)


def screen(
    text: str, *, max_chars: int = DEFAULT_MAX_CHARS, catalog: Catalog | None = None
) -> Screened:
    """Screen one piece of user text. Returns the redacted text plus flags; never raises on hostile input."""
    truncated = len(text) > max_chars
    clean, kinds = redact_text(strip_hidden_chars(text[:max_chars]))
    clean, card = mask_card_numbers(clean)
    flags = [f"secret:{k}" for k in kinds]
    if card:
        flags.append("card_number")
    if truncated:
        flags.append("truncated")
    injection = injection_suspected(clean)
    if injection:
        flags.append("injection")
    unsafe = unsafe_category(clean)
    if unsafe:
        flags.append(f"unsafe:{unsafe}")
    declined: DeclinedMatch | None = None
    tier_b: list[str] = []
    if len(clean) <= MESSAGE_MAX_CHARS:
        cat = catalog if catalog is not None else load_catalog()
        declined = declined_category(clean, cat)
        if declined:
            flags.append(f"declined:{declined.id}")
        tier_b = tier_b_hits(clean, cat)
        flags.extend(f"tier_b:{t}" for t in tier_b)
    return Screened(clean, flags, injection, unsafe, declined, truncated, tier_b)
