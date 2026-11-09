"""HTML to readable sections with the stdlib ``html.parser`` (no new dependencies).

What is dropped, because a person looking at the page would not see it (hidden text is the classic place to
hide instructions, IN-06): ``script``, ``style``, ``noscript``, ``template``, ``svg``, ``iframe``, ``object``,
``embed``, ``canvas``, the whole ``head`` except ``title``, elements with the ``hidden`` attribute,
``aria-hidden="true"``, ``type="hidden"`` and inline styles that hide or push text off screen. What is kept:
headings (they start sections), paragraphs, list items (``- item``) and table rows (``a | b | c``).

The parser is tolerant (unclosed tags close with their parent) and bounded: nesting deeper than
``MAX_DEPTH`` stops the extraction with a note instead of costing quadratic time. No regex touches page text.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from html.parser import HTMLParser

from mde_compass.intake.model import Section, clean_text
from mde_core.redact import strip_hidden_chars

MAX_DEPTH = 200

_VOID = frozenset(
    [
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    ]
)
_SKIP = frozenset(
    [
        "script",
        "style",
        "noscript",
        "template",
        "svg",
        "iframe",
        "object",
        "embed",
        "canvas",
        "head",
    ]
)
_BLOCK = frozenset(
    [
        "address",
        "article",
        "aside",
        "blockquote",
        "body",
        "dd",
        "details",
        "div",
        "dl",
        "dt",
        "fieldset",
        "figcaption",
        "figure",
        "footer",
        "form",
        "header",
        "hr",
        "li",
        "main",
        "nav",
        "ol",
        "p",
        "pre",
        "section",
        "summary",
        "table",
        "tbody",
        "tfoot",
        "thead",
        "ul",
    ]
)
_HEADINGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})
_OFFSCREEN_PROPS = frozenset({"left", "top", "text-indent", "margin-left", "margin-top"})


def _style_hides(style: str) -> bool:
    for decl in style.lower().split(";"):
        name, sep, value = decl.partition(":")
        if not sep:
            continue
        name, value = name.strip(), value.strip().replace("!important", "").strip()
        if (
            (name == "display" and value == "none")
            or (name == "visibility" and value in ("hidden", "collapse"))
            or (
                name in ("font-size", "width", "height", "max-height", "max-width")
                and value in ("0", "0px", "0pt", "0em", "0rem", "0%")
            )
            or (name == "opacity" and value in ("0", "0.0", "0.00"))
            or (name == "color" and value in ("transparent",))
        ):
            return True
        if name in _OFFSCREEN_PROPS and value.startswith("-"):
            digits = "".join(c for c in value[1:8] if c.isdigit())
            if digits and int(digits) >= 900:
                return True
    return False


def _hidden(tag: str, attrs: dict[str, str]) -> bool:
    if "hidden" in attrs:
        return True
    if attrs.get("aria-hidden", "").strip().lower() == "true":
        return True
    if tag == "input" and attrs.get("type", "").strip().lower() == "hidden":
        return True
    return _style_hides(attrs.get("style", ""))


@dataclass
class HtmlExtract:
    title: str = ""
    sections: list[Section] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    has_password_field: bool = False
    text_chars: int = 0


class _Extractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[str] = []
        self.skip_len: int | None = None
        self.dead = False
        self.title_parts: list[str] = []
        self.in_title = False
        self.inline: list[str] = []
        self.lines: list[str] = []
        self.heading_parts: list[str] | None = None
        self.cells: list[str] | None = None
        self.cell_parts: list[str] | None = None
        self.row: list[str] = []
        self.sections: list[tuple[str, list[str]]] = [("", [])]
        self.has_password = False
        self.notes: list[str] = []
        self.text_chars = 0

    # -- helpers ------------------------------------------------------------------------------------
    @property
    def skipping(self) -> bool:
        return self.dead or self.skip_len is not None

    def _line(self) -> str:
        text = " ".join("".join(self.inline).split())
        self.inline = []
        return text

    def _break(self) -> None:
        text = self._line()
        if text:
            self.sections[-1][1].append(text)
            self.text_chars += len(text)

    def _start_section(self, heading: str) -> None:
        self._break()
        self.sections.append((heading, []))

    # -- parser callbacks ---------------------------------------------------------------------------
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self.dead:
            return
        attr = {k.lower(): (v or "") for k, v in attrs}
        if tag == "input" and attr.get("type", "").lower() == "password":
            self.has_password = True
        if tag in _VOID:
            if tag == "br" and not self.skipping:
                self._break()
            return
        if len(self.stack) >= MAX_DEPTH:
            self.dead = True
            self.notes.append("html_too_deeply_nested")
            return
        self.stack.append(tag)
        if self.skip_len is None and (tag in _SKIP or _hidden(tag, attr)):
            self.skip_len = len(self.stack)
        if tag == "title" and "svg" not in self.stack[:-1]:
            self.in_title = True
            return
        if self.skipping:
            return
        if tag in _HEADINGS:
            self._break()
            self.heading_parts = []
        elif tag == "li":
            self._break()
            self.inline.append("- ")
        elif tag == "tr":
            self._break()
            self.row = []
        elif tag in ("td", "th"):
            self.cell_parts = []
        elif tag in _BLOCK:
            self._break()

    def handle_endtag(self, tag: str) -> None:
        if self.dead or tag in _VOID or tag not in self.stack:
            return
        idx = len(self.stack) - 1
        while self.stack[idx] != tag:
            idx -= 1
        closed = self.stack[idx:]
        del self.stack[idx:]
        was_skipping = self.skipping
        if self.skip_len is not None and len(self.stack) < self.skip_len:
            self.skip_len = None
        for name in reversed(closed):
            self._closed(name, was_skipping and self.skip_len is not None)

    def _closed(self, name: str, still_skipping: bool) -> None:
        if name == "title":
            self.in_title = False
            return
        if still_skipping:
            return
        if name in _HEADINGS and self.heading_parts is not None:
            heading = " ".join("".join(self.heading_parts).split())
            self.heading_parts = None
            if heading:
                self._start_section(heading[:200])
        elif name in ("td", "th") and self.cell_parts is not None:
            self.row.append(" ".join("".join(self.cell_parts).split()).replace("|", "/"))
            self.cell_parts = None
        elif name == "tr":
            while self.row and not self.row[-1]:
                self.row.pop()
            if any(self.row):
                self.sections[-1][1].append(" | ".join(self.row))
                self.text_chars += sum(len(c) for c in self.row)
            self.row = []
        elif name in _BLOCK or name == "li":
            self._break()

    def handle_data(self, data: str) -> None:
        if self.dead:
            return
        if self.in_title:
            self.title_parts.append(data)
            return
        if self.skipping:
            return
        if self.heading_parts is not None:
            self.heading_parts.append(data)
        elif self.cell_parts is not None:
            self.cell_parts.append(data)
        else:
            self.inline.append(data)

    def finish(self) -> None:
        self.close()
        self._break()


def extract_html(html: str) -> HtmlExtract:
    """Parse ``html`` into sections (one per heading). Never raises on hostile markup."""
    parser = _Extractor()
    try:
        parser.feed(strip_hidden_chars(html))
        parser.finish()
    except Exception:  # the stdlib parser should not raise, but this is untrusted input
        parser.notes.append("html_partly_unreadable")
    out = HtmlExtract(
        title=clean_text(" ".join("".join(parser.title_parts).split()))[:200],
        notes=parser.notes,
        has_password_field=parser.has_password,
        text_chars=parser.text_chars,
    )
    first = True
    for heading, lines in parser.sections:
        text = "\n".join(lines).strip()
        if not text and not heading:
            continue
        if first and out.title and not heading:
            text = f"Page title: {out.title}\n{text}".strip()
        elif first and out.title:
            out.sections.append(Section(pointer="page title", text=f"Page title: {out.title}"))
        first = False
        pointer = f"heading: {heading}" if heading else "page start"
        out.sections.append(Section(pointer=pointer, heading=heading, text=text))
    return out


def html_to_sections(html: str) -> list[Section]:
    return extract_html(html).sections
