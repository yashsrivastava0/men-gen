"""Tokenizer: finds the front matter, the section headings and the fenced blocks of the file.

Block fence: ```` ```yaml id=SCR-014 kind=screen ```` ... ```` ``` ```` at column 0. Section
heading: ``## <n>. <title>`` with n = 1..15 in ascending order (section 0 is the file head, up to
the first heading). Everything else is prose and is ignored. Never edits the text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from mde_contracts.spec import SECTION_ORDER
from mde_spec_loader.errors import SpecParseError

# Linear patterns only (a hostile file must not stall the loader): the trailing blanks are stripped
# with ``rstrip`` instead of a lazy ``(.*?)[ \t]*$`` (quadratic on a long run of blanks), and the
# key of ``key=value`` starts only at the beginning of a word (lookbehind) and never backtracks.
_FENCE_OPEN = re.compile(r"^```([A-Za-z0-9_+-]*)[ \t]*(.*)$")
_SECTION = re.compile(r"^## (\d{1,2})\. (.+)$")
_INFO_KV = re.compile(r"(?<!\w)(\w++)=(\S++)")


@dataclass(frozen=True, slots=True)
class RawBlock:
    id: str
    kind: str
    section: int
    line_start: int  # 1-based line of the opening fence (``---`` for the front matter)
    line_end: int  # 1-based line of the closing fence
    start: int  # offset of the opening fence
    end: int  # offset just after the closing fence (exclusive, no trailing newline)
    body_start: int
    body_end: int


@dataclass(frozen=True, slots=True)
class RawSection:
    number: int
    key: str  # entry of SECTION_ORDER
    title: str
    line_start: int
    start: int  # offset of the heading line (0 for section 0)
    end: int  # offset of the next heading (len(text) for the last section)


@dataclass(frozen=True, slots=True)
class Document:
    text: str
    blocks: tuple[RawBlock, ...]
    sections: tuple[RawSection, ...]

    def block(self, block_id: str) -> RawBlock | None:
        for raw in self.blocks:
            if raw.id == block_id:
                return raw
        return None

    def block_text(self, raw: RawBlock) -> str:
        """The exact fenced block, fences included."""
        return self.text[raw.start : raw.end]

    def body_text(self, raw: RawBlock) -> str:
        """The block body (YAML) without the fences."""
        return self.text[raw.body_start : raw.body_end]

    def section(self, number: int) -> RawSection | None:
        for sec in self.sections:
            if sec.number == number:
                return sec
        return None


def tokenize(text: str) -> Document:
    """Split an (LF-normalised) ``APP_SPEC.md`` into front matter, sections and blocks."""
    lines = text.split("\n")
    offsets: list[int] = []
    pos = 0
    for line in lines:
        offsets.append(pos)
        pos += len(line) + 1

    if lines[0] != "---":
        raise SpecParseError("the file must start with a front-matter block ('---')", line=1)
    close = next((k for k in range(1, len(lines)) if lines[k] == "---"), None)
    if close is None:
        raise SpecParseError("the front matter is not closed ('---')", line=1)

    body_start = offsets[1]
    blocks: list[RawBlock] = [
        RawBlock(
            id="HEADER",
            kind="header",
            section=0,
            line_start=1,
            line_end=close + 1,
            start=0,
            end=offsets[close] + 3,
            body_start=body_start,
            body_end=offsets[close],
        )
    ]
    headings: list[tuple[int, str, int, int]] = []  # number, title, line (1-based), offset
    current = 0
    open_block: tuple[str, str, int] | None = None  # id, kind, line index
    plain_fence = False

    for idx in range(close + 1, len(lines)):
        line = lines[idx]
        if open_block is not None:
            if line.rstrip() == "```":
                block_id, kind, first = open_block
                body_begin = offsets[first + 1]
                blocks.append(
                    RawBlock(
                        id=block_id,
                        kind=kind,
                        section=current,
                        line_start=first + 1,
                        line_end=idx + 1,
                        start=offsets[first],
                        end=offsets[idx] + len(line.rstrip()),
                        body_start=body_begin,
                        body_end=offsets[idx],  # includes the body's final newline
                    )
                )
                open_block = None
            continue
        if plain_fence:
            if line.rstrip() == "```":
                plain_fence = False
            continue
        fence = _FENCE_OPEN.match(line)
        if fence is not None:
            info = dict(_INFO_KV.findall(fence.group(2).rstrip(" \t")))
            if "id" in info or "kind" in info:
                if "id" not in info or "kind" not in info:
                    raise SpecParseError("a block fence needs both id= and kind=", line=idx + 1)
                open_block = (info["id"], info["kind"], idx)
            else:
                plain_fence = True
            continue
        heading = _SECTION.match(line)
        if heading is not None:
            number = int(heading.group(1))
            if number == 0:
                continue  # the head of the file is section 0 whether or not it has a heading
            previous = headings[-1][0] if headings else 0
            if number >= len(SECTION_ORDER) or number <= previous:
                raise SpecParseError(
                    f"section heading {number} is out of order or unknown", line=idx + 1
                )
            title = heading.group(2)
            title = title.rstrip(" \t") or title[:1]  # one blank stays: ``.+`` needs a character
            headings.append((number, title, idx + 1, offsets[idx]))
            current = number

    if open_block is not None:
        raise SpecParseError(f"block {open_block[0]} is not closed", line=open_block[2] + 1)

    seen: set[str] = set()
    for raw in blocks:
        if raw.id in seen:
            raise SpecParseError(f"duplicate block id {raw.id}", line=raw.line_start)
        seen.add(raw.id)

    first_start = headings[0][3] if headings else len(text)
    sections: list[RawSection] = [
        RawSection(
            number=0,
            key=SECTION_ORDER[0],
            title="Header and index",
            line_start=1,
            start=0,
            end=first_start,
        )
    ]
    for pos_idx, (number, title, line_no, start) in enumerate(headings):
        end = headings[pos_idx + 1][3] if pos_idx + 1 < len(headings) else len(text)
        sections.append(
            RawSection(
                number=number,
                key=SECTION_ORDER[number],
                title=title,
                line_start=line_no,
                start=start,
                end=end,
            )
        )
    return Document(text=text, blocks=tuple(blocks), sections=tuple(sections))
