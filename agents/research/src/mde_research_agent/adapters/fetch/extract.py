"""Page body to main text: charset decoding, HTML main text (trafilatura), PDF text layer (pypdf).

Everything here is pure and synchronous (the fetcher runs it in a worker thread) and never raises:
a body that cannot be read comes back as an empty ``Extracted``.

The text is stored as extracted. The only changes made to it are the ones that cannot alter wording:
Unicode NFC, line endings as ``\\n``, control characters and lone surrogates dropped (Postgres
refuses them), outer whitespace trimmed, and PDF words split by a line-end hyphen joined again. A
verifier checks quotes against this text later, so nothing else is rewritten.

JavaScript-only pages (no server-rendered text), scanned PDFs (no text layer) and empty shells all
come back empty. No OCR and no script execution, by design.
"""

from __future__ import annotations

import codecs
import hashlib
import io
import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from typing import Final, Literal

import trafilatura
from lxml import etree  # pyright: ignore[reportAttributeAccessIssue]
from lxml import html as lxml_html
from pypdf import PasswordType, PdfReader

DEFAULT_MAX_CHARS: Final = 400_000
DEFAULT_MAX_PDF_PAGES: Final = 60

HTML_TYPES: Final = frozenset({"text/html", "application/xhtml+xml"})
TEXT_TYPES: Final = frozenset({"text/plain"})
PDF_TYPES: Final = frozenset({"application/pdf", "application/x-pdf"})
SUPPORTED_TYPES: Final = HTML_TYPES | TEXT_TYPES | PDF_TYPES

Kind = Literal["html", "text", "pdf"]
WallKind = Literal["blocked", "paywall", "not_found"]


@dataclass(frozen=True)
class Extracted:
    """What a body yielded. ``text == ""`` means nothing readable was found."""

    text: str = ""
    title: str = ""
    published: date | None = None


def content_kind(media_type: str) -> Kind | None:
    """The extractor for a media type (``text/html``), or None when the type is not supported."""
    if media_type in HTML_TYPES:
        return "html"
    if media_type in PDF_TYPES:
        return "pdf"
    if media_type in TEXT_TYPES:
        return "text"
    return None


# ---------------------------------------------------------------------------- headers and charsets

_CHARSET_PARAM = re.compile(r"""charset\s*=\s*["']?\s*([A-Za-z0-9_.:+-]+)""", re.IGNORECASE)
_META_CHARSET = re.compile(
    rb"""<meta[^>]{0,400}?charset\s*=\s*["']?\s*([A-Za-z0-9_.:+-]+)""", re.IGNORECASE
)
_XML_ENCODING = re.compile(rb"""<\?xml[^>]{0,200}?encoding\s*=\s*["']([A-Za-z0-9_.:+-]+)""")
_CHARSET_ALIASES: Final = {
    # the web treats these labels as windows-1252
    "iso-8859-1": "cp1252",
    "iso8859-1": "cp1252",
    "latin1": "cp1252",
    "latin-1": "cp1252",
    "l1": "cp1252",
    "us-ascii": "cp1252",
    "ascii": "cp1252",
}
_BOMS: Final = (
    (codecs.BOM_UTF32_LE, "utf-32"),  # before UTF-16 LE: it starts with the same two bytes
    (codecs.BOM_UTF32_BE, "utf-32"),
    (codecs.BOM_UTF8, "utf-8-sig"),
    (codecs.BOM_UTF16_LE, "utf-16"),
    (codecs.BOM_UTF16_BE, "utf-16"),
)


def split_content_type(header: str) -> tuple[str, str | None]:
    """``'text/html; charset=UTF-8'`` -> ``('text/html', 'UTF-8')``. The media type is lowercase."""
    media, _, params = header.partition(";")
    found = _CHARSET_PARAM.search(params)
    return media.strip().lower(), (found.group(1) if found else None)


def _codec_name(label: str) -> str | None:
    key = label.strip().strip("\"'").lower()
    key = _CHARSET_ALIASES.get(key, key)
    try:
        return codecs.lookup(key).name
    except LookupError:
        return None


def _try_decode(data: bytes, label: str) -> str | None:
    name = _codec_name(label)
    if name is None:
        return None
    try:
        return data.decode(name, errors="replace")
    except (LookupError, UnicodeError, ValueError):  # a codec that is not a text encoding
        return None


def _meta_charset(data: bytes) -> str | None:
    head = data[:4096]
    found = _META_CHARSET.search(head) or _XML_ENCODING.search(head)
    if found is None:
        return None
    label = found.group(1).decode("ascii", errors="ignore")
    # a page that declares UTF-16 in ASCII-looking markup is really UTF-8
    return None if label.lower().startswith(("utf-16", "utf-32")) else label


def decode_body(data: bytes, charset: str | None = None, *, html: bool = False) -> str:
    """Bytes to text: byte-order mark, then the header charset, then (HTML) the meta charset, then
    UTF-8 with replacement characters. Never raises."""
    for bom, label in _BOMS:
        if data.startswith(bom):
            decoded = _try_decode(data, label)
            if decoded is not None:
                return decoded
    labels = [charset] if charset else []
    if html and not labels:
        declared = _meta_charset(data)
        if declared:
            labels.append(declared)
    for label in labels:
        decoded = _try_decode(data, label)
        if decoded is not None:
            return decoded
    return data.decode("utf-8", errors="replace")


def sniff_media_type(prefix: bytes) -> str:
    """For a response with no (or a generic) Content-Type: ``application/pdf``, ``text/html`` or
    ``''`` (anything else is not a page we read)."""
    head = prefix[:1024]
    if b"%PDF-" in head:
        return "application/pdf"
    probe = head.lstrip(b"\xef\xbb\xbf \t\r\n").lower()
    if probe.startswith((b"<!doctype html", b"<html", b"<head", b"<body", b"<title")):
        return "text/html"
    return ""


# ---------------------------------------------------------------------------------- text hygiene

_CONTROL_CHARS = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")


def clean_text(text: str, max_chars: int = DEFAULT_MAX_CHARS) -> str:
    """The stored form of extracted text (see the module docstring), cut to ``max_chars``."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _CONTROL_CHARS.sub("", text)
    text = unicodedata.normalize("NFC", text)
    text = text.encode("utf-8", errors="replace").decode("utf-8")  # lone surrogates -> "?"
    text = text.strip()
    return text[:max_chars] if len(text) > max_chars else text


def content_hash(text: str) -> str:
    """sha256 (hex) of the NFC text; '' for empty text."""
    if not text:
        return ""
    normal = unicodedata.normalize("NFC", text)
    return hashlib.sha256(normal.encode("utf-8", errors="replace")).hexdigest()


def _clean_title(raw: object) -> str:
    if not isinstance(raw, str):
        return ""
    return unicodedata.normalize("NFC", " ".join(_CONTROL_CHARS.sub("", raw).split()))[:300]


# ------------------------------------------------------------------------------------------ HTML

_DROP_TAGS: Final = frozenset(
    {
        "script", "style", "noscript", "template", "svg", "math", "head", "iframe", "canvas",
        "object", "embed", "select", "option", "datalist", "audio", "video",
    }
)  # fmt: skip
_BLOCK_TAGS: Final = frozenset(
    {
        "p", "div", "section", "article", "main", "header", "footer", "nav", "aside", "ul", "ol",
        "li", "dl", "dt", "dd", "table", "tr", "caption", "blockquote", "pre", "figure",
        "figcaption", "form", "fieldset", "h1", "h2", "h3", "h4", "h5", "h6", "br", "hr",
        "address", "details", "summary", "body", "html",
    }
)  # fmt: skip
_INLINE_SPACE = re.compile(r"[ \t\f\v ]+")


def _parse_tree(html: str) -> lxml_html.HtmlElement | None:
    try:
        parser = lxml_html.HTMLParser(encoding="utf-8", remove_comments=True, remove_pis=True)
        root = lxml_html.fromstring(html.encode("utf-8", errors="replace"), parser=parser)
    except (etree.LxmlError, ValueError):
        return None
    return root


def _visible_text(root: lxml_html.HtmlElement) -> str:
    """All text a reader would see, block elements on their own lines (no boilerplate removal)."""
    chunks: list[str] = []

    def walk(node: lxml_html.HtmlElement) -> None:
        tag = node.tag
        if not isinstance(tag, str) or tag in _DROP_TAGS:  # comment, script, style, ...
            return
        block = tag in _BLOCK_TAGS
        if block:
            chunks.append("\n")
        if node.text:
            chunks.append(node.text)
        for child in node:
            walk(child)
            if child.tail:
                chunks.append(child.tail)
        if block:
            chunks.append("\n")

    walk(root)
    lines = (_INLINE_SPACE.sub(" ", line).strip() for line in "".join(chunks).splitlines())
    return "\n".join(line for line in lines if line)


# trafilatura drops every non-printable character (str.isprintable), and that includes the zero-width
# joiner and non-joiner. They are part of the spelling in Persian, Urdu and the Indic scripts (Kannada,
# Malayalam, Hindi conjuncts): "ನಾಸಿಕ್\u200cನಲ್ಲಿ" is not "ನಾಸಿಕ್ನಲ್ಲಿ". So both are swapped for printable
# stand-ins (segmented digits, which no page uses) before trafilatura sees the page, and swapped back in
# what it returns. Other invisible format characters (soft hyphen, bidi marks, ZWSP) are still dropped.
_ZWNJ, _ZWJ = "\u200c", "\u200d"
_ZWNJ_STAND_IN, _ZWJ_STAND_IN = "\U0001fbf0", "\U0001fbf1"
_ZWNJ_ENTITY = re.compile(r"&(?:zwnj|#0*8204|#x0*200c);", re.IGNORECASE)
_ZWJ_ENTITY = re.compile(r"&(?:zwj|#0*8205|#x0*200d);", re.IGNORECASE)


def _protect_joiners(html: str) -> str:
    html = _ZWNJ_ENTITY.sub(_ZWNJ_STAND_IN, _ZWJ_ENTITY.sub(_ZWJ_STAND_IN, html))
    return html.replace(_ZWNJ, _ZWNJ_STAND_IN).replace(_ZWJ, _ZWJ_STAND_IN)


def _restore_joiners(text: str) -> str:
    return text.replace(_ZWNJ_STAND_IN, _ZWNJ).replace(_ZWJ_STAND_IN, _ZWJ)


def _trafilatura_text(html: str) -> str:
    try:
        text = trafilatura.extract(
            html,
            include_comments=False,
            include_tables=True,
            favor_recall=True,
            output_format="txt",
        )
    except Exception:  # trafilatura works on whatever HTML the web serves; give up on this page
        return ""
    return text or ""


def _metadata(html: str) -> tuple[str, date | None]:
    try:
        doc = trafilatura.extract_metadata(html)
    except Exception:  # metadata is a bonus; never fail the page over it
        return "", None
    if doc is None:
        return "", None
    published: date | None = None
    raw_date = getattr(doc, "date", None)
    if isinstance(raw_date, str):
        try:
            published = date.fromisoformat(raw_date.strip()[:10])
        except ValueError:
            published = None
    return _clean_title(getattr(doc, "title", None)), published


def extract_html(html: str, *, max_chars: int = DEFAULT_MAX_CHARS) -> Extracted:
    """Main text of an HTML page (navigation, footers, comments and ads dropped) with its title and
    publication date. Falls back to the plain visible text when the main-text extractor finds nothing.
    """
    try:
        safe = _protect_joiners(html)
        title, published = _metadata(safe)
        text = _restore_joiners(_trafilatura_text(safe))
        title = _restore_joiners(title)
        if not text.strip() or not title:
            root = _parse_tree(html)
            if root is not None:
                if not text.strip():
                    text = _visible_text(root)
                if not title:
                    title = _clean_title(root.findtext(".//title"))
        return Extracted(text=clean_text(text, max_chars), title=title, published=published)
    except Exception:  # deep nesting, odd encodings: this page has no readable text for us
        return Extracted()


# ------------------------------------------------------------------------------------------- PDF

_LINE_END_HYPHEN = re.compile(r"([^\W\d_])[-­][ \t]*\n[ \t]*([^\W\d_])")


def _join_hyphenated(match: re.Match[str]) -> str:
    before, after = match.group(1), match.group(2)
    soft = "­" in match.group(0)
    return before + after if (soft or after.islower()) else match.group(0)


def dehyphenate(text: str) -> str:
    """Join words split across lines by a hyphen (``inter-\\nnational`` -> ``international``).
    A hyphen before a capital letter or a digit is left alone (``Anglo-\\nSaxon``)."""
    return _LINE_END_HYPHEN.sub(_join_hyphenated, text)


def _page_text(reader: PdfReader, index: int) -> str:
    try:
        return reader.pages[index].extract_text() or ""
    except Exception:  # pypdf raises many types on damaged pages; skip this page only
        return ""


def _extract_pdf(data: bytes, max_pages: int, max_chars: int) -> Extracted:
    reader = PdfReader(io.BytesIO(data))
    if reader.is_encrypted and reader.decrypt("") == PasswordType.NOT_DECRYPTED:
        return Extracted()  # needs a password: that is a login wall, not ours to open
    parts: list[str] = []
    total = 0
    for index in range(min(len(reader.pages), max(max_pages, 0))):
        page = dehyphenate(_page_text(reader, index)).strip()
        if page:
            parts.append(page)
            total += len(page) + 2
        if total > max_chars:
            break
    title = ""
    try:
        meta = reader.metadata
        title = _clean_title(meta.title) if meta is not None else ""
    except Exception:  # damaged document-info dictionary
        title = ""
    return Extracted(text=clean_text("\n\n".join(parts), max_chars), title=title)


def extract_pdf(
    data: bytes, *, max_pages: int = DEFAULT_MAX_PDF_PAGES, max_chars: int = DEFAULT_MAX_CHARS
) -> Extracted:
    """Text layer of a PDF (first ``max_pages`` pages, joined by blank lines). No OCR: a scanned PDF
    comes back empty. A damaged or password-protected PDF comes back empty too."""
    try:
        return _extract_pdf(data, max_pages, max_chars)
    except Exception:  # pypdf's error types are many (PdfReadError, ValueError, KeyError, ...)
        return Extracted()


# ----------------------------------------------------------------------------- dispatch and walls


def extract_body(
    data: bytes,
    media_type: str,
    charset: str | None = None,
    *,
    max_pages: int = DEFAULT_MAX_PDF_PAGES,
    max_chars: int = DEFAULT_MAX_CHARS,
) -> Extracted:
    """Main text of a body of a supported media type. Never raises; unsupported types come back
    empty."""
    try:
        kind = content_kind(media_type)
        if kind == "pdf":
            return extract_pdf(data, max_pages=max_pages, max_chars=max_chars)
        if kind == "html":
            return extract_html(decode_body(data, charset, html=True), max_chars=max_chars)
        if kind == "text":
            return Extracted(text=clean_text(decode_body(data, charset), max_chars))
    except Exception:  # last line of defence: the contract is "never raises"
        return Extracted()
    return Extracted()


_BLOCK_PHRASES: Final = (
    "just a moment", "attention required", "access denied", "are you a robot",
    "are you human", "verify you are human", "verify you're human", "verifying you are human",
    "captcha", "security check", "checking your browser", "ddos protection", "unusual traffic",
    "pardon our interruption", "request blocked", "403 forbidden", "bot detection",
    "enable javascript and cookies to continue", "automated access",
)  # fmt: skip
_PAYWALL_PHRASES: Final = (
    "subscribe to continue", "subscribe to read", "subscribers only", "subscription required",
    "sign in to continue", "sign in to read", "sign in to view", "log in to continue",
    "log in to read", "log in to view", "login to continue", "login to view",
    "create a free account to", "become a member to", "members only", "register to continue",
    "register to read", "for subscribers", "premium content",
)  # fmt: skip
_LOGIN_TITLES: Final = ("sign in", "log in", "login", "sign up", "register")
_LOGIN_PATHS = re.compile(r"/(?:log-?in|sign-?in|sign-?up|subscribe|register)(?:[/?#.]|$)", re.I)
_WALL_HEAD_CHARS: Final = 600


def detect_wall(title: str, text: str, final_url: str = "") -> WallKind | None:
    """Label a page that is nearly empty AND looks like a bot wall or a login or subscription wall.

    This only names the reason for a page that is being dropped anyway. It never helps to get past
    the wall, and the caller applies it only to pages too short to be content.
    """
    head = f"{title}\n{text[:_WALL_HEAD_CHARS]}".lower()
    if any(phrase in head for phrase in _BLOCK_PHRASES):
        return "blocked"
    if any(phrase in head for phrase in _PAYWALL_PHRASES):
        return "paywall"
    if title.strip().lower().startswith(_LOGIN_TITLES) or _LOGIN_PATHS.search(final_url):
        return "paywall"
    return None


_NOT_FOUND_PHRASES: Final = (
    "page not found", "404 not found", "error 404", "404 error", "404 - ", "not found (404)",
    "this page doesn't exist", "this page does not exist", "this page isn't available",
    "this page is no longer available", "no longer available", "we couldn't find that page",
    "we could not find that page", "the page you requested", "the page you are looking for",
    "the requested url was not found", "nothing was found",
)  # fmt: skip
_SOFT_HEAD_CHARS: Final = 2048
_SHORT_PAGE_CHARS: Final = 6000
_FIRST_LINE_CHARS: Final = 160


def _first_line(text: str) -> str:
    for line in text.splitlines():
        if line.strip():
            return line.strip().lower()[:_FIRST_LINE_CHARS]
    return ""


def detect_soft_wall(title: str, text: str, final_url: str = "") -> WallKind | None:
    """A page that CAME BACK as content but is really an error or a wall: a soft 404, a bot check, a login or
    subscription wall. Looked for in the title, the first heading line and the first 2 KB (the last only on a short
    page, so a long article that merely mentions a captcha is kept). It names the reason for a page that is dropped;
    it never helps to get past a wall."""
    t = title.strip().lower()
    first = _first_line(text)
    head = text[:_SOFT_HEAD_CHARS].lower()
    short = len(text) < _SHORT_PAGE_CHARS
    for kind, phrases in (
        ("not_found", _NOT_FOUND_PHRASES),
        ("blocked", _BLOCK_PHRASES),
        ("paywall", _PAYWALL_PHRASES),
    ):
        for phrase in phrases:
            # an error title or heading names a missing page even when the page is long; a bot check or a login
            # wall is only believed on a SHORT page (a long article titled "Access denied to bots" is an article)
            by_name = t.startswith(phrase) or phrase in t or first.startswith(phrase)
            if (kind == "not_found" and by_name) or (short and (by_name or phrase in head)):
                return kind  # type: ignore[return-value]
    if t.startswith(_LOGIN_TITLES) and short or (_LOGIN_PATHS.search(final_url) and short):
        return "paywall"
    return None
