"""Structure-aware chunking (IN-01): sections in, ``Chunk`` objects out, each with a source pointer.

* Window: ``target_tokens`` (3000 to 4000 by default), tokens approximated as characters / 4.
* Boundaries: a chunk is closed at a section (heading, page, slide, sheet) boundary once it holds at least the
  lower target; otherwise small sections are packed together. A chunk is also closed, on a LINE boundary, when
  the next line would pass the upper target.
* A table row (one line) is never split. When a table has to continue in the next chunk its header row is
  repeated. A single table row longer than the window keeps its own, oversize chunk rather than being cut.
* Pointers survive: ``page 3``, ``pages 3-9``, ``heading: Payments``, ``sheet Roles!A1:D9``, ``rows 2-40``.
* ``max_table_rows`` (when given) also closes a chunk after that many table rows, header repeated: a dense requirements
  table is cut small enough that the reader can return one statement per row within its output budget.
* An unreadable page becomes its own chunk with ``unreadable=True`` and EMPTY text (coverage counts it).
* Ids are deterministic: ``CHK-<source id>-<n>`` with ``n`` counting from 1 in document order.
* No regex touches document text.
"""

from __future__ import annotations

from dataclasses import dataclass

from mde_compass.domain.claims import Chunk
from mde_compass.intake.model import Section

CHARS_PER_TOKEN = 4
_MAX_POINTER_PARTS = 3
_FAMILIES = {
    "page": "page",
    "pages": "page",
    "slide": "slide",
    "slides": "slide",
    "line": "line",
    "lines": "line",
    "row": "row",
    "rows": "row",
}


def approx_tokens(text: str) -> int:
    """Rough token count: characters / 4, rounded up."""
    return -(-len(text) // CHARS_PER_TOKEN)


@dataclass
class _Ref:
    section: Section
    first: int  # first body line index covered
    last: int  # last body line index covered


def _numbered(label: str) -> tuple[str, int, int] | None:
    """``page 3`` / ``pages 3-9`` / ``rows 2-40`` to (family, 3, 9); anything else to ``None``."""
    word, _, rest = label.partition(" ")
    family = _FAMILIES.get(word)
    if family is None or not rest or len(rest) > 24:
        return None
    first, dash, last = rest.partition("-")
    if not first.isdigit() or (dash and not last.isdigit()):
        return None
    a = int(first)
    return family, a, int(last) if dash else a


def _render(family: str, a: int, b: int) -> str:
    if a == b:
        return f"{family} {a}"
    return f"{family}s {a}-{b}"


def merge_pointers(labels: list[str]) -> str:
    """Combine pointers: neighbouring ``page`` / ``slide`` / ``line`` / ``row`` labels become one range."""
    unique = list(dict.fromkeys(label for label in labels if label))
    merged: list[str] = []
    run: tuple[str, int, int] | None = None
    for label in unique:
        num = _numbered(label)
        if num is None:
            if run is not None:
                merged.append(_render(*run))
                run = None
            merged.append(label)
        elif run is not None and run[0] == num[0]:
            run = (run[0], min(run[1], num[1]), max(run[2], num[2]))
        else:
            if run is not None:
                merged.append(_render(*run))
            run = num
    if run is not None:
        merged.append(_render(*run))
    if len(merged) > _MAX_POINTER_PARTS:
        extra = len(merged) - _MAX_POINTER_PARTS
        merged = [*merged[:_MAX_POINTER_PARTS], f"+{extra} more"]
    return "; ".join(merged)


def _ref_label(ref: _Ref) -> str:
    sec = ref.section
    if not (sec.table and sec.row_numbers):
        return sec.pointer
    rows = sec.row_numbers[ref.first : ref.last + 1] or sec.row_numbers
    r1, r2 = min(rows), max(rows)
    if ref.first == 0 and sec.header_row:
        r1 = min(r1, sec.header_row)
    if sec.sheet:
        return f"sheet {sec.sheet}!A{r1}:{sec.last_col or 'A'}{r2}"
    return _render("row", r1, r2)


def _split_long(line: str, limit: int) -> list[str]:
    """Cut an over-long PROSE line at whitespace so it fits the window (table rows are never cut)."""
    out: list[str] = []
    rest = line
    while len(rest) > limit:
        cut = rest.rfind(" ", 0, limit)
        if cut < limit // 2:
            cut = limit
        out.append(rest[:cut].rstrip())
        rest = rest[cut:].lstrip()
    if rest:
        out.append(rest)
    return out


def chunk_sections(
    sections: list[Section],
    *,
    source_id: str,
    target_tokens: tuple[int, int] = (3000, 4000),
    max_table_rows: int | None = None,
) -> list[Chunk]:
    """Turn sections into chunks of about ``target_tokens`` tokens. See the module docstring for the rules."""
    low = target_tokens[0] * CHARS_PER_TOKEN
    high = max(target_tokens[1], target_tokens[0]) * CHARS_PER_TOKEN
    chunks: list[Chunk] = []
    lines: list[str] = []
    refs: list[_Ref] = []
    size = 0
    rows = 0  # table body rows in the chunk being built

    def emit(text: str, pointer: str, *, unreadable: bool = False) -> None:
        n = len(chunks)
        chunks.append(
            Chunk(
                id=f"CHK-{source_id}-{n + 1}",
                source_id=source_id,
                index=n,
                text=text,
                pointer=pointer,
                unreadable=unreadable,
            )
        )

    def flush() -> None:
        nonlocal lines, refs, size, rows
        if lines:
            emit("\n".join(lines), merge_pointers([_ref_label(r) for r in refs]))
        lines, refs, size, rows = [], [], 0, 0

    def add(line: str, ref: _Ref, idx: int, *, row: bool = False) -> None:
        nonlocal size, rows
        cost = len(line) + 1
        full = row and max_table_rows is not None and rows >= max_table_rows
        if lines and (size + cost > high or full):
            flush()
            repeat = ref.section.header if ref.section.table else ""
            if (
                repeat and idx > 0
            ):  # a table continues: repeat its header, keep the row pointer honest
                lines.append(repeat)
                refs.append(_Ref(ref.section, idx, idx))
                size += len(repeat) + 1
        lines.append(line)
        size += cost
        rows += 1 if row else 0
        if refs and refs[-1].section is ref.section and refs[-1].last <= idx:
            refs[-1].last = idx
        else:
            refs.append(_Ref(ref.section, idx, idx))

    for sec in sections:
        if sec.unreadable:
            flush()
            emit("", sec.pointer, unreadable=True)
            continue
        body = [ln.rstrip() for ln in sec.text.split("\n") if ln.strip()]
        head: list[str] = []
        if sec.heading:
            head.append(sec.heading)
        if sec.table and sec.header:
            head.append(sec.header)
        if not body and not head:
            continue
        if size >= low:
            flush()
        ref = _Ref(sec, 0, 0)
        for text in head:
            if sec.table and text == sec.header:
                add(text, ref, 0)
            else:
                for piece in _split_long(text, high):
                    add(piece, ref, 0)
        for i, ln in enumerate(body):
            pieces = [ln] if sec.table else _split_long(ln, high)
            for piece in pieces:
                add(piece, ref, i, row=sec.table)
    flush()
    return chunks
