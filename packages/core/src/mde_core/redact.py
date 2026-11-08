"""Secret detection and redaction; hidden-character stripping for untrusted text.

Rule 1 (brief 13): no key value ever reaches a log, a cassette, a doc or the database.

Design rules for this module (a regex bug here is a security bug):

* **Every pattern is linear.** No unbounded quantifier is ever tried from every start position of a
  run (the old ``connection_string`` scheme ``[a-z0-9+.-]*`` was quadratic on ``"a." * N``). Left
  boundaries are lookbehinds, so a pattern is attempted once per run; the connection-string and
  JWT parts use possessive quantifiers; whitespace gaps are bounded. ``private_key_block`` is a
  header pattern plus a cursor-based scan for the matching END marker (one pass over the text).
* **Text is normalised before it is scanned** (:func:`normalize_for_scan`): zero-width, bidi,
  soft-hyphen and other invisible characters are dropped, fullwidth and compatibility forms are
  folded by NFKC and every dash-like character becomes ``-``. ``redact_text`` therefore returns the
  *normalised* text with the secret spans replaced (for ASCII text without control characters that
  is the identical string), and ``find_secrets`` judges the normalised text.
* **Separator-split keys are caught** by a second, bounded pass: a key-like prefix (``sk-``,
  ``AKIA``, ``ghp_``...) whose body is broken by whitespace (a pasted, line-wrapped key) is joined
  and re-checked. To keep prose from being flagged the joined body must contain a digit and a letter.
* **Very long inputs** are scanned in overlapping windows (:data:`WINDOW_CHARS` with
  :data:`OVERLAP_CHARS` of overlap, so a secret shorter than the overlap cannot straddle a window
  unseen; a match that ends exactly at a window edge is extended through the rest of its run).
"""

from __future__ import annotations

import re
import unicodedata

REDACTED = "[REDACTED]"

WINDOW_CHARS = 262_144
OVERLAP_CHARS = 32_768
_MAX_LOOSE_HITS = 20_000  # bound on the separator-split pass (per call)

# Left boundary: an underscore before a key (``my_sk-...``, ``DB_PASSWORD``) must not hide it, but
# a letter or digit must (``task-based-...`` is not a key).
_LB = r"(?<![A-Za-z0-9])"

# (name, pattern). Patterns are deliberately broad on the prefix and strict on the length.
SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("openai_key", re.compile(_LB + r"sk-(?:proj-|svcacct-|admin-)?[A-Za-z0-9_\-]{20,}")),
    ("anthropic_key", re.compile(_LB + r"sk-ant-[A-Za-z0-9_\-]{20,}")),
    ("aws_access_key", re.compile(_LB + r"(?:AKIA|ASIA)[0-9A-Z]{16}(?![A-Za-z0-9])")),
    ("github_token", re.compile(_LB + r"gh[pousr]_[A-Za-z0-9]{30,}")),
    ("slack_token", re.compile(_LB + r"xox[abprs]-[A-Za-z0-9\-]{10,}")),
    ("google_api_key", re.compile(_LB + r"AIza[0-9A-Za-z_\-]{35,}")),
    ("stripe_key", re.compile(_LB + r"(?:sk|rk|pk)_(?:live|test)_[0-9A-Za-z]{16,}")),
    ("bearer_token", re.compile(r"(?i)" + _LB + r"bearer\s{1,16}+[A-Za-z0-9._\-]{24,}")),
    # Header only: the body is found by ``_pem_spans`` (linear scan for the END marker).
    ("private_key_block", re.compile(r"-----BEGIN [A-Z ]{0,40}PRIVATE KEY-----")),
    (
        "jwt",
        re.compile(_LB + r"eyJ[A-Za-z0-9_\-]{8,}+\.[A-Za-z0-9_\-]{8,}+\.[A-Za-z0-9_\-]{8,}+"),
    ),
    (
        "assignment",
        re.compile(
            r"(?i)" + _LB + r"(?:api[_-]?key|secret|token|passwd|password|pwd)(?![A-Za-z0-9])"
            r"\s{0,16}[:=]\s{0,16}['\"]?(?!\[REDACTED\])([^\s'\"]{8,})"
        ),
    ),
    (
        "connection_string",
        re.compile(
            # ``scheme://user:password@host``. The scheme run is matched once, from its first
            # character (lookbehind), with a possessive quantifier: no per-position rescans.
            r"(?i)(?<![a-z0-9+.\-])[a-z0-9+.\-]*+://[^\s:/@]++:[^\s@/]{3,}+@[^\s]++"
        ),
    ),
)

_PEM_END = re.compile(r"-----END [A-Z ]{0,40}PRIVATE KEY-----")
_RUN = re.compile(r"[^\s'\"]*+")  # the rest of a token-like run (extends a window-edge match)

# Patterns that identify a key by a literal prefix: the separator-split pass tries these.
_LOOSE_NAMES = (
    "openai_key",
    "anthropic_key",
    "aws_access_key",
    "github_token",
    "slack_token",
    "google_api_key",
    "stripe_key",
)
_PATTERN_BY_NAME = dict(SECRET_PATTERNS)
_PEM_BEGIN = _PATTERN_BY_NAME["private_key_block"]
_LOOSE_PATTERNS = tuple((n, _PATTERN_BY_NAME[n]) for n in _LOOSE_NAMES)
# prefix, a first chunk of at least 8 key characters, then up to nine whitespace-separated chunks
# (bounded and possessive: linear per start; a short first chunk is prose, not a split key)
_LOOSE = re.compile(
    _LB + r"(?:sk-|sk_(?:live|test)_|[rp]k_(?:live|test)_|AKIA|ASIA|gh[pousr]_|xox[abprs]-|AIza)"
    r"[A-Za-z0-9_\-]{8,256}+[ \t\r\n]{1,8}+"
    r"(?:[A-Za-z0-9_\-]{1,256}+[ \t\r\n]{1,8}+){0,8}[A-Za-z0-9_\-]{1,256}+[A-Za-z0-9_\-]*+"
)
_GAP = re.compile(r"[ \t\r\n]+")

_ZERO_WIDTH = {
    "\u200b", "\u200c", "\u200d", "\u2060", "\ufeff", "\u180e",
    "\u202a", "\u202b", "\u202c", "\u202d", "\u202e",
    "\u2066", "\u2067", "\u2068", "\u2069",
}  # fmt: skip

# Invisible characters outside category Cf that are still used to split a key: combining grapheme
# joiner, Hangul / Khmer fillers and the variation selectors (dropped from the *scan* form only).
_SCAN_INVISIBLE = {
    0x034F, 0x115F, 0x1160, 0x17B4, 0x17B5, 0x3164, 0xFFA0,
    *range(0xFE00, 0xFE10), *range(0xE0100, 0xE01F0),
}  # fmt: skip
# Dash-like characters that NFKC leaves alone (the fullwidth hyphen-minus is folded by NFKC itself).
_DASHES = {
    ord(c): "-"
    for c in "\u2010\u2011\u2012\u2013\u2014\u2015\u2212\u2043\u058a\u05be\ufe58\ufe63\uff0d"
}
_SCAN_TRIGGER = re.compile(r"[^\x20-\x7e\t\n\r]")


class _HiddenTable(dict[int, str | None]):
    """``str.translate`` table that drops hidden characters (computed lazily, bounded cache)."""

    def __missing__(self, key: int) -> str | None:
        ch = chr(key)
        cat = unicodedata.category(ch)
        drop = cat == "Cf" or ch in _ZERO_WIDTH or (cat == "Cc" and ch not in "\n\t\r")
        value = None if drop else ch
        if len(self) < 50_000:
            self[key] = value
        return value


_HIDDEN = _HiddenTable()
_SCAN_HIDDEN = _HiddenTable()
_SCAN_HIDDEN.update(dict.fromkeys(_SCAN_INVISIBLE))


def strip_hidden_chars(text: str) -> str:
    """Drop zero-width and bidi-control characters and other non-printing controls."""
    return text.translate(_HIDDEN)


def normalize_for_scan(text: str) -> str:
    """The form of ``text`` that secret patterns are matched against.

    Drops invisible characters (zero-width, bidi, soft hyphen, variation selectors, controls),
    applies NFKC (fullwidth letters, digits and hyphens fold to ASCII) and maps every dash-like
    character to ``-``. Plain printable ASCII is returned unchanged (and without copying).
    """
    if not _SCAN_TRIGGER.search(text):
        return text
    folded = unicodedata.normalize("NFKC", text.translate(_SCAN_HIDDEN))
    return folded.translate(_DASHES)


# --------------------------------------------------------------------------- matching machinery

# A hit is (start, end, keep): ``text[start:start+keep]`` survives, the rest becomes REDACTED.
_Hit = tuple[int, int, int]


def _hit(pat: re.Pattern[str], m: re.Match[str]) -> _Hit:
    keep = (m.start(1) - m.start()) if pat.groups else 0
    return m.start(), m.end(), keep


def _merge(hits: list[_Hit]) -> list[_Hit]:
    """Union of overlapping hits (a longer hit from an overlapping window wins)."""
    out: list[_Hit] = []
    for start, end, keep in sorted(hits):
        if out and start < out[-1][1]:
            pstart, pend, pkeep = out[-1]
            out[-1] = (pstart, max(pend, end), min(pkeep, keep) if start == pstart else pkeep)
        else:
            out.append((start, end, keep))
    return out


def _windows(n: int, window: int, overlap: int) -> list[tuple[int, int]]:
    overlap = min(overlap, window // 2)  # always advance
    spans: list[tuple[int, int]] = []
    a = 0
    while True:
        b = min(a + window, n)
        spans.append((a, b))
        if b >= n:
            return spans
        a = b - overlap


def _hits(pat: re.Pattern[str], text: str) -> list[_Hit]:
    window, overlap = WINDOW_CHARS, OVERLAP_CHARS  # read at call time (tests shrink them)
    n = len(text)
    if n <= window:
        return [_hit(pat, m) for m in pat.finditer(text)]
    found: list[_Hit] = []
    for a, b in _windows(n, window, overlap):
        for m in pat.finditer(text, a, b):  # pos/endpos: lookbehinds still see the real text
            start, end, keep = _hit(pat, m)
            if end == b and b < n:  # cut by the window edge: take the rest of the run too
                run = _RUN.match(text, b)
                end = run.end() if run is not None else end
            found.append((start, end, keep))
    return _merge(found)


def _any_hit(pat: re.Pattern[str], text: str) -> bool:
    window, overlap = WINDOW_CHARS, OVERLAP_CHARS
    n = len(text)
    if n <= window:
        return pat.search(text) is not None
    return any(pat.search(text, a, b) is not None for a, b in _windows(n, window, overlap))


def _pem_spans(text: str) -> list[_Hit]:
    """Every ``-----BEGIN ... PRIVATE KEY-----`` through its END marker (one linear pass).

    A header with no END marker (a truncated key) is redacted to the end of the text: failing
    closed is right for a private key.
    """
    spans: list[_Hit] = []
    pos = 0
    while (m := _PEM_BEGIN.search(text, pos)) is not None:
        end_m = _PEM_END.search(text, m.end())
        end = end_m.end() if end_m is not None else len(text)
        spans.append((m.start(), end, 0))
        pos = end
    return spans


def _spans_for(name: str, pat: re.Pattern[str], text: str) -> list[_Hit]:
    return _pem_spans(text) if name == "private_key_block" else _hits(pat, text)


def _substitute(text: str, hits: list[_Hit]) -> str:
    if not hits:
        return text
    parts: list[str] = []
    cursor = 0
    for start, end, keep in hits:
        parts.append(text[cursor:start])
        parts.append(text[start : start + keep])
        parts.append(REDACTED)
        cursor = end
    parts.append(text[cursor:])
    return "".join(parts)


def _loose_hits(text: str) -> list[tuple[str, _Hit]]:
    """Keys split by whitespace (a wrapped or hand-split paste): ``(kind, hit)`` per key.

    The chunks after a key prefix are joined and must satisfy the real key pattern; the joined
    body must also contain a digit and a letter, so prose that merely follows ``sk-`` is left alone.
    """
    out: list[tuple[str, _Hit]] = []
    for count, m in enumerate(_LOOSE.finditer(text)):
        if count >= _MAX_LOOSE_HITS:
            break
        joined = _GAP.sub("", m.group(0))
        if not (any(c.isdigit() for c in joined) and any(c.isalpha() for c in joined)):
            continue
        for name, pat in _LOOSE_PATTERNS:
            if pat.match(joined):
                out.append((name, (m.start(), m.end(), 0)))
                break
    return out


# --------------------------------------------------------------------------- public API


def find_secrets(text: str) -> list[str]:
    """Return the names of the secret patterns found in ``text`` (never the values).

    ``text`` is judged in its normalised form (:func:`normalize_for_scan`).
    """
    scan = normalize_for_scan(text)
    names = {name for name, pat in SECRET_PATTERNS if _any_hit(pat, scan)}
    names.update(name for name, _ in _loose_hits(scan))
    return [name for name, _ in SECRET_PATTERNS if name in names]


def contains_secret(text: str) -> bool:
    return bool(find_secrets(text))


def redact_text(text: str) -> tuple[str, list[str]]:
    """Replace every secret-looking span with ``[REDACTED]``. Returns (clean_text, kinds_found).

    ``clean_text`` is built from the *normalised* text (:func:`normalize_for_scan`), so a secret
    hidden behind zero-width characters, a soft hyphen or fullwidth hyphens is removed too. For
    plain printable ASCII input the text outside the secrets is returned unchanged.
    """
    out = normalize_for_scan(text)
    found: list[str] = []
    for name, pat in SECRET_PATTERNS:
        new = _substitute(out, _spans_for(name, pat, out))
        if new != out:
            found.append(name)
            out = new
    loose = _loose_hits(out)
    if loose:
        out = _substitute(out, _merge([hit for _, hit in loose]))
        found.extend(dict.fromkeys(name for name, _ in loose if name not in found))
    return out, found


def redact_value(text: str, secret: str | None) -> str:
    """Remove one known secret value (for example the loaded API key) from ``text``.

    The value is also removed when it is hidden in the text behind invisible characters.
    """
    if not secret or len(secret) < 8:
        return text
    out = text.replace(secret, REDACTED)
    scan = normalize_for_scan(out)
    if scan is not out and secret in scan:
        out = scan.replace(secret, REDACTED)
    return out
