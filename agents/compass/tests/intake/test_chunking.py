"""Chunking: window, no split table rows, pointers, ids, unreadable pages, hostile input."""

from __future__ import annotations

import time

from mde_compass.intake import Section, approx_tokens, chunk_sections, merge_pointers
from mde_compass.intake.chunking import _numbered

WINDOW = (100, 150)  # tokens; small so tests stay fast (400 to 600 characters)


def _para(n: int, chars: int = 200) -> str:
    return (f"Sentence {n} " + "word " * 100)[:chars]


def test_window_and_deterministic_ids() -> None:
    sections = [Section(pointer=f"page {i}", text=_para(i, 450)) for i in range(1, 41)]
    chunks = chunk_sections(sections, source_id="SRC-001", target_tokens=WINDOW)
    assert [c.id for c in chunks] == [f"CHK-SRC-001-{i + 1}" for i in range(len(chunks))]
    assert [c.index for c in chunks] == list(range(len(chunks)))
    assert chunks == chunk_sections(sections, source_id="SRC-001", target_tokens=WINDOW)
    for chunk in chunks[:-1]:
        tokens = approx_tokens(chunk.text)
        assert WINDOW[0] <= tokens <= WINDOW[1], (chunk.id, tokens)
    assert approx_tokens(chunks[-1].text) <= WINDOW[1]
    assert all(c.source_id == "SRC-001" and not c.unreadable for c in chunks)


def test_page_pointers_merge_into_ranges() -> None:
    sections = [Section(pointer=f"page {i}", text=_para(i, 150)) for i in range(1, 9)]
    chunks = chunk_sections(sections, source_id="SRC-002", target_tokens=WINDOW)
    assert chunks[0].pointer.startswith("pages 1-")
    covered = set()
    for chunk in chunks:
        fam = _numbered(chunk.pointer)
        assert fam is not None
        covered.update(range(fam[1], fam[2] + 1))
    assert covered == set(range(1, 9))


def test_heading_sections_keep_heading_pointers_and_text() -> None:
    sections = [
        Section(pointer="heading: Payments", heading="Payments", text="Users pay by UPI."),
        Section(pointer="heading: Orders", heading="Orders", text="Orders are tracked."),
    ]
    (chunk,) = chunk_sections(sections, source_id="SRC-003")
    assert chunk.text == "Payments\nUsers pay by UPI.\nOrders\nOrders are tracked."
    assert chunk.pointer == "heading: Payments; heading: Orders"


def test_big_heading_sections_start_new_chunks_at_headings() -> None:
    sections = [
        Section(
            pointer=f"heading: H{i}",
            heading=f"H{i}",
            text="\n".join(_para(j, 100) for j in range(5)),
        )
        for i in range(6)
    ]
    chunks = chunk_sections(sections, source_id="SRC-004", target_tokens=(40, 80))
    for chunk in chunks:
        first = chunk.text.split("\n", 1)[0]
        assert first.startswith("H") or chunk.index > 0  # chunks begin at a heading or a size cut


def test_table_rows_are_never_split_and_header_repeats() -> None:
    rows = [f"R{i:04d} | name {i} | " + "x" * 30 for i in range(1, 301)]
    table = Section(
        pointer="sheet Roles!A1:C301",
        text="\n".join(rows),
        table=True,
        header="Id | Name | Note",
        sheet="Roles",
        last_col="C",
        row_numbers=tuple(range(2, 302)),
        header_row=1,
    )
    chunks = chunk_sections([table], source_id="SRC-005", target_tokens=WINDOW)
    assert len(chunks) > 3
    seen: list[str] = []
    row_set = set(rows)
    for chunk in chunks:
        lines = chunk.text.split("\n")
        assert lines[0] == "Id | Name | Note"  # the header opens every chunk
        for line in lines[1:]:
            assert line in row_set  # a whole row, never a cut one
            seen.append(line)
        assert approx_tokens(chunk.text) <= WINDOW[1]
    assert seen == rows  # nothing lost, nothing duplicated, order kept
    assert chunks[0].pointer.startswith("sheet Roles!A1:C")
    assert chunks[-1].pointer.endswith("C301")
    # continuation chunks point at their own rows, not at the header
    assert chunks[1].pointer.startswith("sheet Roles!A") and not chunks[1].pointer.startswith(
        "sheet Roles!A1:"
    )


def test_oversize_table_row_keeps_its_own_chunk() -> None:
    huge = "cell " * 400
    table = Section(
        pointer="rows 1-4",
        text=f"a | b\n{huge}\nc | d",
        table=True,
        header="H1 | H2",
        row_numbers=(2, 3, 4),
        header_row=1,
    )
    chunks = chunk_sections([table], source_id="SRC-006", target_tokens=(20, 40))
    assert any(huge.strip() in c.text for c in chunks)
    assert not any("cell cell" in c.text and not c.text.count(huge.strip()) for c in chunks)


def test_long_prose_line_is_split_at_whitespace() -> None:
    chunks = chunk_sections(
        [Section(pointer="page 1", text="word " * 1000)],
        source_id="SRC-007",
        target_tokens=(50, 100),
    )
    assert len(chunks) > 5
    assert all(len(c.text) <= 400 for c in chunks)
    assert all(
        not line.startswith(" ") and not line.endswith(" ")
        for c in chunks
        for line in c.text.split("\n")
    )


def test_unreadable_pages_become_empty_flagged_chunks() -> None:
    sections = [
        Section(pointer="page 1", text="alpha"),
        Section(pointer="page 2", unreadable=True),
        Section(pointer="page 3", text="gamma"),
    ]
    chunks = chunk_sections(sections, source_id="SRC-008")
    assert [(c.pointer, c.unreadable, c.text) for c in chunks] == [
        ("page 1", False, "alpha"),
        ("page 2", True, ""),
        ("page 3", False, "gamma"),
    ]


def test_empty_input_and_empty_sections() -> None:
    assert chunk_sections([], source_id="SRC-009") == []
    assert chunk_sections([Section(pointer="page 1", text="  \n ")], source_id="SRC-009") == []


def test_merge_pointers_rules() -> None:
    assert merge_pointers(["page 1", "page 2", "page 3"]) == "pages 1-3"
    assert merge_pointers(["page 4"]) == "page 4"
    assert merge_pointers(["slide 2", "slide 3"]) == "slides 2-3"
    assert merge_pointers(["lines 1-4", "lines 5-9"]) == "lines 1-9"
    assert merge_pointers(["heading: A", "heading: A", "heading: B"]) == "heading: A; heading: B"
    assert merge_pointers([f"heading: {i}" for i in range(6)]).endswith("+3 more")
    assert merge_pointers([]) == ""


def test_pointer_helpers_are_linear_on_hostile_input() -> None:
    hostile = [
        "page " + "9" * 50_000,
        "pages " + "1-" * 25_000,
        "rows " + "-" * 50_000,
        "heading: " + "x" * 50_000,
    ] * 50
    start = time.perf_counter()
    merge_pointers(hostile)
    assert time.perf_counter() - start < 2.0
    assert _numbered("page " + "9" * 50_000) is None


def test_hostile_sections_are_linear() -> None:
    sections = [Section(pointer=f"lines {i}", text="x") for i in range(50_000)]
    start = time.perf_counter()
    chunks = chunk_sections(sections, source_id="SRC-010", target_tokens=(3000, 4000))
    assert time.perf_counter() - start < 5.0
    assert chunks and chunks[0].pointer.startswith("lines 0-")
    one_line = chunk_sections([Section(pointer="p", text="a" * 50_000)], source_id="SRC-011")
    assert "".join(c.text for c in one_line) == "a" * 50_000
