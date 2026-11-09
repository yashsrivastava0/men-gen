"""Document parsers: bytes in, ``ParsedDocument`` out (prompt 6.5, IN-01, IN-08, IN-14).

Rules this module keeps:

* The file is UNTRUSTED DATA. Nothing in it is executed, followed or obeyed; macros, embedded files, links and
  attachments are never opened. Every parse step is wrapped: any library failure becomes a typed ``IntakeError``.
* The type is decided by extension AND magic bytes; a mismatch is rejected, never guessed.
* Limits come from ``IntakeLimits`` (file size, pages); archive members are checked against a decompressed size
  ceiling so a zip bomb cannot exhaust memory.
* A PDF page without a text layer is marked ``unreadable`` and is NEVER OCRed. Images are accepted as sources
  with no text (the gateway has no image input yet).
* Hidden text (DOCX hidden runs, PPTX hidden shapes and slides, hidden sheets) is dropped; zero-width and control
  characters are stripped everywhere (``mde_core.redact.strip_hidden_chars``).
* No regex runs over document text here (line and string methods only), so hostile input stays linear.
"""

from __future__ import annotations

import csv
import io
import zipfile
from collections.abc import Iterator
from datetime import date, datetime, time
from email import policy
from email.parser import BytesParser
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any

from mde_compass.config import IntakeLimits
from mde_compass.domain.claims import SourceKind
from mde_compass.intake.errors import IntakeError
from mde_compass.intake.htmltext import html_to_sections
from mde_compass.intake.model import ParsedDocument, Section, clean_text
from mde_core.redact import strip_hidden_chars

MAX_TABLE_ROWS = 5000
"""Rows kept per sheet or CSV; more are dropped with a note (never silently)."""
MAX_SCANNED_ROWS = 20_000
"""Rows looked at per sheet while hunting for non-empty ones (a bogus sheet dimension cannot hang us)."""
MAX_SHEETS = 40
UNZIPPED_FACTOR = 8
"""A DOCX/XLSX/PPTX may decompress to at most this many times ``max_file_bytes`` (zip-bomb guard)."""

_EXT_KIND: dict[str, SourceKind] = {
    ".pdf": "pdf",
    ".docx": "docx",
    ".xlsx": "xlsx",
    ".pptx": "pptx",
    ".csv": "csv",
    ".tsv": "csv",
    ".txt": "text",
    ".text": "text",
    ".md": "markdown",
    ".markdown": "markdown",
    ".eml": "eml",
    ".png": "image",
    ".jpg": "image",
    ".jpeg": "image",
    ".gif": "image",
    ".webp": "image",
}
_OOXML_MEMBER = {
    "docx": "word/document.xml",
    "xlsx": "xl/workbook.xml",
    "pptx": "ppt/presentation.xml",
}
_TEXT_KINDS = {"csv", "text", "markdown", "eml"}
_IMAGE_MAGIC = {".png": "png", ".jpg": "jpeg", ".jpeg": "jpeg", ".gif": "gif", ".webp": "webp"}


# --- small helpers -------------------------------------------------------------------------------------


def safe_name(name: str) -> str:
    """The base name only (no directories), without hidden characters, at most 120 characters."""
    base = PurePosixPath(PureWindowsPath(name).as_posix()).name
    return strip_hidden_chars(base).strip()[:120] or "file"


def _extension(name: str) -> str:
    return PurePosixPath(safe_name(name)).suffix.lower()


def _sniff(data: bytes) -> str | None:
    head = data[:16]
    if b"%PDF-" in data[:1024]:
        return "pdf"
    if head.startswith(b"PK\x03\x04") or head.startswith(b"PK\x05\x06"):
        return "zip"
    if head.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        return "ole"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if head.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if head.startswith((b"GIF87a", b"GIF89a")):
        return "gif"
    if head.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "webp"
    return None


def decode_text(data: bytes) -> str:
    """Bytes to text: BOMs honoured, UTF-8 first, then Windows-1252 (never fails)."""
    if data.startswith(b"\xef\xbb\xbf"):
        return data[3:].decode("utf-8", errors="replace")
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16", errors="replace")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")


def _cell(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return clean_text(str(value)).replace("\n", " ").replace("|", "/").strip()


def _join_row(cells: list[str]) -> str:
    while cells and not cells[-1]:
        cells = cells[:-1]
    return " | ".join(cells)


def _table_section(
    pointer: str,
    rows: list[tuple[int, list[str]]],
    *,
    sheet: str = "",
    heading: str = "",
    cols: int = 0,
) -> Section | None:
    """A table section from ``(row_number, cells)`` pairs; the first non-empty row is the header."""
    lines: list[tuple[int, str]] = [(n, _join_row(c)) for n, c in rows]
    lines = [(n, line) for n, line in lines if line.strip()]
    if not lines:
        return None
    width = cols or max(len(c) for _, c in rows)
    has_header = len(lines) > 1
    body = lines[1:] if has_header else lines
    return Section(
        pointer=pointer,
        heading=heading,
        text="\n".join(line for _, line in body),
        table=True,
        header=lines[0][1] if has_header else "",
        sheet=sheet,
        last_col=_col_letter(width),
        row_numbers=tuple(n for n, _ in body),
        header_row=lines[0][0] if has_header else 0,
    )


def _col_letter(n: int) -> str:
    n = max(n, 1)
    out = ""
    while n > 0:
        n, rem = divmod(n - 1, 26)
        out = chr(65 + rem) + out
    return out


def _check_zip(name: str, data: bytes, kind: str, limits: IntakeLimits) -> zipfile.ZipFile:
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
        infos = zf.infolist()
    except (zipfile.BadZipFile, OSError, ValueError, NotImplementedError) as exc:
        raise IntakeError("corrupt", name) from exc
    if len(infos) > 20_000:
        raise IntakeError("corrupt", name, "too many archive members")
    if sum(i.file_size for i in infos) > limits.max_file_bytes * UNZIPPED_FACTOR:
        raise IntakeError("too_large", name, "decompressed size over the limit")
    names = {i.filename for i in infos}
    if _OOXML_MEMBER[kind] not in names:
        if "EncryptedPackage" in names or "encryption" in " ".join(names).lower():
            raise IntakeError("password_protected", name)
        raise IntakeError("unsupported", name, f"not a real {kind} file")
    return zf


# --- PDF -----------------------------------------------------------------------------------------------


def _parse_pdf(name: str, data: bytes, limits: IntakeLimits) -> ParsedDocument:
    from pypdf import PdfReader

    try:
        reader = PdfReader(io.BytesIO(data), strict=False)
    except Exception as exc:
        raise IntakeError("corrupt", name) from exc
    try:
        encrypted = bool(reader.is_encrypted)
    except Exception as exc:
        raise IntakeError("corrupt", name) from exc
    if encrypted:
        try:
            opened = int(reader.decrypt(""))
        except Exception as exc:  # wrong password, or a cipher we cannot run: unreadable either way
            raise IntakeError("password_protected", name) from exc
        if opened == 0:
            raise IntakeError("password_protected", name)
    try:
        count = len(reader.pages)
    except Exception as exc:
        raise IntakeError("corrupt", name) from exc
    if count > limits.max_pages:
        raise IntakeError("too_many_pages", name, f"{count} pages")
    doc = ParsedDocument(kind="pdf", pages=count)
    for i in range(count):
        try:
            text = clean_text(reader.pages[i].extract_text() or "")
        except Exception:
            text = ""
        pointer = f"page {i + 1}"
        if text.strip():
            doc.sections.append(Section(pointer=pointer, text=text.strip()))
        else:
            doc.sections.append(Section(pointer=pointer, unreadable=True))
            doc.notes.append(f"unreadable_page: {i + 1}")
    return doc


# --- DOCX ----------------------------------------------------------------------------------------------


def _docx_paragraph_text(par: Any) -> str:
    parts: list[str] = []
    inner = getattr(par, "iter_inner_content", None)
    items: Any = inner() if callable(inner) else par.runs
    for item in items:
        font = getattr(item, "font", None)
        if font is not None and getattr(font, "hidden", None):
            continue  # hidden run: the reader of the page cannot see it, so neither do we
        parts.append(item.text)
    return clean_text("".join(parts)).strip()


def _parse_docx(name: str, data: bytes, limits: IntakeLimits) -> ParsedDocument:
    from docx import Document
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    zf = _check_zip(name, data, "docx", limits)
    zf.close()
    try:
        doc = Document(io.BytesIO(data))
        body = list(doc.element.body.iterchildren())
    except Exception as exc:
        raise IntakeError("corrupt", name) from exc

    out = ParsedDocument(kind="docx")
    heading = ""
    pointer = "start of document"
    lines: list[str] = []
    table_n = 0
    total_chars = 0

    def flush() -> None:
        nonlocal lines
        text = "\n".join(line for line in lines if line.strip())
        if text.strip() or heading:
            out.sections.append(Section(pointer=pointer, heading=heading, text=text))
        lines = []

    try:
        for child in body:
            tag = child.tag.rsplit("}", 1)[-1]
            if tag == "p":
                par = Paragraph(child, doc)  # type: ignore[arg-type]
                text = _docx_paragraph_text(par)
                if not text:
                    continue
                style = ""
                try:
                    style = (par.style.name or "") if par.style is not None else ""
                except Exception:
                    style = ""
                if style == "Title" or style.startswith("Heading"):
                    flush()
                    heading = text[:200]
                    pointer = f"heading: {heading}"
                else:
                    lines.append(f"- {text}" if style.startswith("List") else text)
                total_chars += len(text)
            elif tag == "tbl":
                table_n += 1
                table = Table(child, doc)  # type: ignore[arg-type]
                rows: list[tuple[int, list[str]]] = []
                for r, row in enumerate(table.rows, start=1):
                    cells: list[str] = []
                    seen: set[int] = set()
                    for cell in row.cells:
                        if id(cell._tc) in seen:  # a merged cell repeats itself
                            continue
                        seen.add(id(cell._tc))
                        cells.append(
                            " ".join(
                                t for p in cell.paragraphs if (t := _docx_paragraph_text(p))
                            ).replace("|", "/")
                        )
                    rows.append((r, cells))
                flush_pointer = f"{pointer}, table {table_n}"
                section = _table_section(flush_pointer, rows)
                if section is not None:
                    flush()
                    out.sections.append(section)
                    total_chars += len(section.text)
    except IntakeError:
        raise
    except Exception as exc:
        raise IntakeError("corrupt", name) from exc
    flush()
    out.pages = max(1, -(-total_chars // 3000))
    return out


# --- XLSX ----------------------------------------------------------------------------------------------


def _parse_xlsx(name: str, data: bytes, limits: IntakeLimits) -> ParsedDocument:
    from openpyxl import load_workbook

    _check_zip(name, data, "xlsx", limits).close()
    try:
        wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as exc:
        raise IntakeError("corrupt", name) from exc
    out = ParsedDocument(kind="xlsx")
    try:
        sheets = list(wb.worksheets)
        out.pages = len(sheets)
        for ws in sheets[:MAX_SHEETS]:
            title = clean_text(str(ws.title)).strip()
            if getattr(ws, "sheet_state", "visible") != "visible":
                out.notes.append(f"hidden_sheet_skipped: {title}")
                continue
            rows: list[tuple[int, list[str]]] = []
            kept = scanned = 0
            truncated = False
            for r, values in enumerate(ws.iter_rows(values_only=True), start=1):
                scanned += 1
                if scanned > MAX_SCANNED_ROWS:
                    truncated = True
                    break
                cells = [_cell(v) for v in values]
                if not any(cells):
                    continue
                if kept >= MAX_TABLE_ROWS:
                    truncated = True
                    break
                kept += 1
                rows.append((r, cells))
            if truncated:
                out.notes.append(f"rows_truncated: {title} (first {kept} rows kept)")
            if not rows:
                continue
            width = max(len(c) for _, c in rows)
            last_col = _col_letter(width)
            first, last = rows[0][0], rows[-1][0]
            section = _table_section(
                f"sheet {title}!A{first}:{last_col}{last}", rows, sheet=title, cols=width
            )
            if section is not None:
                out.sections.append(section)
        if len(sheets) > MAX_SHEETS:
            out.notes.append(f"sheets_truncated: first {MAX_SHEETS} of {len(sheets)} read")
    except IntakeError:
        raise
    except Exception as exc:
        raise IntakeError("corrupt", name) from exc
    finally:
        wb.close()
    return out


# --- PPTX ----------------------------------------------------------------------------------------------


def _pptx_hidden(shape: Any) -> bool:
    try:
        return "1" in [str(v) for v in shape._element.xpath("./*[1]/p:cNvPr/@hidden")]
    except Exception:
        return False


def _pptx_walk(shapes: Any, skip_id: int | None) -> Iterator[Any]:
    for shape in shapes:
        if _pptx_hidden(shape):
            continue
        if getattr(shape, "shape_type", None) == 6:  # MSO_SHAPE_TYPE.GROUP
            yield from _pptx_walk(shape.shapes, skip_id)
            continue
        if skip_id is not None and getattr(shape, "shape_id", None) == skip_id:
            continue
        yield shape


def _parse_pptx(name: str, data: bytes, limits: IntakeLimits) -> ParsedDocument:
    from pptx import Presentation

    _check_zip(name, data, "pptx", limits).close()
    try:
        prs = Presentation(io.BytesIO(data))
        slides = list(prs.slides)
    except Exception as exc:
        raise IntakeError("corrupt", name) from exc
    if len(slides) > limits.max_pages:
        raise IntakeError("too_many_pages", name, f"{len(slides)} slides")
    out = ParsedDocument(kind="pptx", pages=len(slides))
    try:
        for idx, slide in enumerate(slides, start=1):
            if slide._element.get("show") == "0":
                out.notes.append(f"hidden_slide_skipped: {idx}")
                continue
            title_shape = slide.shapes.title
            title = (
                clean_text(title_shape.text_frame.text).strip() if title_shape is not None else ""
            )
            skip_id = title_shape.shape_id if title_shape is not None else None
            lines: list[str] = []
            for shape in _pptx_walk(slide.shapes, skip_id):
                if getattr(shape, "has_table", False) and shape.has_table:
                    for row in shape.table.rows:
                        line = _join_row([_cell(c.text) for c in row.cells])
                        if line.strip():
                            lines.append(line)
                elif getattr(shape, "has_text_frame", False) and shape.has_text_frame:
                    text = clean_text(shape.text_frame.text).strip()
                    if text:
                        lines.append(text)
            if slide.has_notes_slide:
                frame = slide.notes_slide.notes_text_frame
                notes = clean_text(frame.text).strip() if frame is not None else ""
                if notes:
                    lines.append(f"Speaker notes: {notes}")
            if lines or title:
                out.sections.append(
                    Section(
                        pointer=f"slide {idx}",
                        heading=title[:200],
                        text="\n".join(lines),
                    )
                )
    except Exception as exc:
        raise IntakeError("corrupt", name) from exc
    return out


# --- text-like -----------------------------------------------------------------------------------------


def _parse_csv(name: str, data: bytes, ext: str, limits: IntakeLimits) -> ParsedDocument:
    text = clean_text(decode_text(data))
    sample = text[:4096]
    delimiter = "\t" if ext == ".tsv" else ","
    if ext != ".tsv":
        try:
            delimiter = csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
        except csv.Error:
            delimiter = ","
    out = ParsedDocument(kind="csv")
    rows: list[tuple[int, list[str]]] = []
    try:
        for n, record in enumerate(csv.reader(io.StringIO(text), delimiter=delimiter), start=1):
            cells = [_cell(c) for c in record]
            if not any(cells):
                continue
            if len(rows) >= MAX_TABLE_ROWS:
                out.notes.append(f"rows_truncated: first {MAX_TABLE_ROWS} rows kept")
                break
            rows.append((n, cells))
    except csv.Error as exc:
        raise IntakeError("corrupt", name) from exc
    if rows:
        first, last = rows[0][0], rows[-1][0]
        section = _table_section(f"rows {first}-{last}", rows)
        if section is not None:
            out.sections.append(section)
    out.pages = 1 if rows else 0
    return out


def _parse_markdown(text: str) -> ParsedDocument:
    out = ParsedDocument(kind="markdown")
    heading = ""
    pointer = "start of document"
    lines: list[str] = []
    in_fence = False

    def flush() -> None:
        nonlocal lines
        body = "\n".join(lines).strip()
        if body or heading:
            out.sections.append(Section(pointer=pointer, heading=heading, text=body))
        lines = []

    for line in text.split("\n"):
        stripped = line.lstrip()
        if stripped.startswith(("```", "~~~")):
            in_fence = not in_fence
        if not in_fence and stripped.startswith("#"):
            level = len(stripped) - len(stripped.lstrip("#"))
            rest = stripped[level:]
            if 1 <= level <= 6 and rest[:1] in (" ", "\t") and rest.strip():
                flush()
                heading = rest.strip().rstrip("#").strip()[:200]
                pointer = f"heading: {heading}"
                continue
        lines.append(line)
    flush()
    out.pages = max(1, -(-len(text) // 3000)) if out.sections else 0
    return out


def _parse_plain(text: str) -> ParsedDocument:
    out = ParsedDocument(kind="text")
    block: list[str] = []
    start = 0
    for n, line in enumerate(text.split("\n"), start=1):
        if line.strip():
            if not block:
                start = n
            block.append(line)
        elif block:
            out.sections.append(_line_section(start, n - 1, block))
            block = []
    if block:
        out.sections.append(_line_section(start, start + len(block) - 1, block))
    out.pages = max(1, -(-len(text) // 3000)) if out.sections else 0
    return out


def _line_section(start: int, end: int, block: list[str]) -> Section:
    pointer = f"lines {start}-{end}" if end > start else f"line {start}"
    return Section(pointer=pointer, text="\n".join(block))


def _parse_eml(name: str, data: bytes) -> ParsedDocument:
    try:
        msg = BytesParser(policy=policy.default).parsebytes(data)
    except Exception as exc:
        raise IntakeError("corrupt", name) from exc
    out = ParsedDocument(kind="eml", pages=1)
    head = [
        f"{label}: {clean_text(str(msg[key]))[:300]}"
        for key, label in (("From", "From"), ("To", "To"), ("Date", "Date"), ("Subject", "Subject"))
        if msg[key] is not None
    ]
    body_text = ""
    try:
        plain = msg.get_body(preferencelist=("plain",))
        if plain is not None:
            body_text = clean_text(str(plain.get_content()))
        else:
            html = msg.get_body(preferencelist=("html",))
            if html is not None:
                sections = html_to_sections(str(html.get_content()))
                body_text = "\n".join(
                    "\n".join(x for x in (s.heading, s.text) if x) for s in sections
                )
        attachments = sum(1 for _ in msg.iter_attachments())
    except Exception as exc:
        raise IntakeError("corrupt", name) from exc
    if attachments:
        out.notes.append(f"attachments_not_read: {attachments}")
    text = "\n".join([*head, "", body_text.strip()]).strip()
    if body_text.strip() or head:
        out.sections.append(Section(pointer="email", text=text))
    return out


# --- entry points --------------------------------------------------------------------------------------


def parse_text(text: str, *, markdown: bool = False) -> ParsedDocument:
    """Parse already-decoded text (pasted documents): markdown headings or blank-line paragraphs."""
    cleaned = clean_text(text)
    return _parse_markdown(cleaned) if markdown else _parse_plain(cleaned)


def parse_document(name: str, data: bytes, *, limits: IntakeLimits) -> ParsedDocument:
    """Parse one uploaded file. Raises ``IntakeError`` (typed) for anything that cannot be read."""
    label = safe_name(name)
    if len(data) > limits.max_file_bytes:
        raise IntakeError("too_large", label)
    ext = _extension(label)
    kind = _EXT_KIND.get(ext)
    if kind is None:
        raise IntakeError("unsupported", label, f"extension {ext or '(none)'}")
    magic = _sniff(data)

    if kind in _TEXT_KINDS:
        if magic is not None or (
            b"\x00" in data[:8192] and not data.startswith((b"\xff\xfe", b"\xfe\xff"))
        ):
            raise IntakeError("unsupported", label, "content does not match the file name")
    elif kind == "pdf":
        if magic is None:
            raise IntakeError("corrupt", label)
        if magic != "pdf":
            raise IntakeError("unsupported", label, "content does not match the file name")
    elif kind in ("docx", "xlsx", "pptx"):
        if magic == "ole":
            raise IntakeError(
                "password_protected", label
            )  # encrypted Office files are OLE containers
        if magic is None:
            raise IntakeError("corrupt", label)
        if magic != "zip":
            raise IntakeError("unsupported", label, "content does not match the file name")
    elif kind == "image":
        expected = _IMAGE_MAGIC[ext]
        if magic is None:
            raise IntakeError("corrupt", label)
        if magic != expected:
            raise IntakeError("unsupported", label, "content does not match the file name")

    doc: ParsedDocument
    if kind == "pdf":
        doc = _parse_pdf(label, data, limits)
    elif kind == "docx":
        doc = _parse_docx(label, data, limits)
    elif kind == "xlsx":
        doc = _parse_xlsx(label, data, limits)
    elif kind == "pptx":
        doc = _parse_pptx(label, data, limits)
    elif kind == "csv":
        doc = _parse_csv(label, data, ext, limits)
    elif kind == "markdown":
        doc = _parse_markdown(clean_text(decode_text(data)))
    elif kind == "text":
        doc = _parse_plain(clean_text(decode_text(data)))
    elif kind == "eml":
        doc = _parse_eml(label, data)
    else:  # image: accepted as a source, no text (the gateway has no image input yet)
        return ParsedDocument(kind="image", pages=1, notes=["image_not_readable_yet"])
    if not doc.sections:
        doc.notes.append("no_readable_text")
    return doc
