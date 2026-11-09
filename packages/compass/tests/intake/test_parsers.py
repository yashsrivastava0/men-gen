"""Parsers: every format, pointers, limits, typed errors, hidden text, hostile input."""

from __future__ import annotations

import time

import pytest

from mde_compass.config import IntakeLimits
from mde_compass.intake import IntakeError, parse_document, parse_text
from mde_compass.intake import parsers as parsers_mod

LIMITS = IntakeLimits()


def _code(name: str, data: bytes, limits: IntakeLimits = LIMITS) -> str:
    with pytest.raises(IntakeError) as exc:
        parse_document(name, data, limits=limits)
    return exc.value.code


# --- PDF ---------------------------------------------------------------------------------------------


def test_pdf_text_pages_have_page_pointers(build) -> None:
    doc = parse_document("brief.pdf", build.pdf(["Alpha page one", "Beta page two"]), limits=LIMITS)
    assert doc.kind == "pdf" and doc.pages == 2
    assert [s.pointer for s in doc.sections] == ["page 1", "page 2"]
    assert "Alpha page one" in doc.sections[0].text
    assert not any(s.unreadable for s in doc.sections)


def test_pdf_page_without_text_layer_is_unreadable_never_ocred(build) -> None:
    doc = parse_document("scan.pdf", build.pdf(["Has text", None, "More text"]), limits=LIMITS)
    flags = [(s.pointer, s.unreadable) for s in doc.sections]
    assert flags == [("page 1", False), ("page 2", True), ("page 3", False)]
    assert doc.sections[1].text == ""
    assert "unreadable_page: 2" in doc.notes


def test_pdf_encrypted_is_password_protected(build) -> None:
    assert _code("locked.pdf", build.encrypted_pdf()) == "password_protected"


def test_pdf_corrupt_random_bytes(build) -> None:
    assert _code("broken.pdf", build.corrupt_bytes()) == "corrupt"


def test_pdf_truncated_is_corrupt() -> None:
    assert _code("cut.pdf", b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog") == "corrupt"
    assert _code("cut.pdf", b"%PDF-" + b"x" * 5000) == "corrupt"


def test_pdf_too_many_pages(build, make_limits) -> None:
    pdf = build.pdf(["a", "b", "c"])
    assert _code("long.pdf", pdf, make_limits(max_pages=2)) == "too_many_pages"
    assert parse_document("long.pdf", pdf, limits=make_limits(max_pages=3)).pages == 3


def test_too_large_checked_first(build, make_limits) -> None:
    assert _code("big.pdf", build.pdf(["x"]), make_limits(max_file_bytes=100)) == "too_large"


# --- type detection (extension AND magic) ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "kind", "expected"),
    [
        ("report.pdf", "png", "unsupported"),  # a PNG called .pdf
        ("report.pdf", "docx", "unsupported"),  # a zip called .pdf
        ("notes.txt", "pdf", "unsupported"),  # a PDF called .txt
        ("notes.csv", "xlsx", "unsupported"),
        ("pic.png", "pdf", "unsupported"),
        ("spec.docx", "pdf", "unsupported"),
        ("spec.docx", "random", "corrupt"),
        ("pic.png", "random", "corrupt"),
        ("legacy.doc", "random", "unsupported"),
        ("archive.zip", "docx", "unsupported"),
        ("noextension", "pdf", "unsupported"),
    ],
)
def test_extension_and_magic_must_agree(build, name: str, kind: str, expected: str) -> None:
    data = {
        "png": build.png,
        "docx": build.docx(),
        "pdf": build.pdf(["x"]),
        "xlsx": build.xlsx(),
        "random": build.corrupt_bytes(),
    }[kind]
    assert _code(name, data) == expected


def test_binary_data_called_txt_is_rejected(build) -> None:
    assert _code("notes.txt", b"abc\x00\x01\x02" * 50) == "unsupported"


def test_encrypted_office_file_is_password_protected() -> None:
    ole = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 64  # encrypted OOXML is an OLE container
    assert _code("secret.xlsx", ole) == "password_protected"
    assert _code("old.xls", ole) == "unsupported"  # legacy formats are not read


def test_path_in_name_is_dropped_and_names_are_cleaned() -> None:
    doc = parse_document("..\\..\\etc\\notes​.txt", b"hello", limits=LIMITS)
    assert doc.kind == "text"
    assert parsers_mod.safe_name("../../etc/pass​wd.txt") == "passwd.txt"


def test_zip_bomb_is_too_large(build, make_limits) -> None:
    limits = make_limits(max_file_bytes=1024 * 1024)
    bomb = build.zip_bomb_docx(10 * 1024 * 1024)
    assert len(bomb) < limits.max_file_bytes
    assert _code("bomb.docx", bomb, limits) == "too_large"


def test_zip_without_the_office_part_is_unsupported() -> None:
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("hello.txt", "hi")
    assert _code("fake.docx", buf.getvalue()) == "unsupported"


# --- DOCX / XLSX / PPTX ---------------------------------------------------------------------------------


def test_docx_headings_paragraphs_tables_and_hidden_text(build) -> None:
    doc = parse_document("spec.docx", build.docx(), limits=LIMITS)
    pointers = [s.pointer for s in doc.sections]
    assert pointers[0] == "start of document"
    assert "heading: Payments" in pointers
    assert "heading: Payments, table 1" in pointers
    assert "heading: Orders" in pointers
    payments = next(s for s in doc.sections if s.pointer == "heading: Payments")
    assert payments.heading == "Payments"
    assert "Users pay by UPI or card." in payments.text
    assert "Visible start. Visible end." in payments.text
    assert "HIDDEN-INSTRUCTION" not in "\n".join(s.text for s in doc.sections)
    assert "- First point" in payments.text
    table = next(s for s in doc.sections if s.table)
    assert table.header == "Role | Can approve"
    assert table.text.split("\n") == ["Owner | yes", "Guest | no"]
    orders = next(s for s in doc.sections if s.pointer == "heading: Orders")
    assert orders.text == "Orders are tracked live."  # zero-width and bidi controls stripped


def test_xlsx_rows_pointers_hidden_sheet_and_cap(build, monkeypatch) -> None:
    doc = parse_document("roles.xlsx", build.xlsx(), limits=LIMITS)
    assert [s.pointer for s in doc.sections] == ["sheet Roles!A1:C5"]
    sec = doc.sections[0]
    assert sec.table and sec.header == "Role | Can approve | Notes"
    assert sec.text.split("\n") == [
        "Owner | yes | full access",
        "Guest | no",
        "Admin | 3 | pipe / inside",
    ]
    assert sec.row_numbers == (2, 3, 5) and sec.header_row == 1
    assert "hidden_sheet_skipped: Secrets" in doc.notes
    assert "do not read" not in sec.text

    monkeypatch.setattr(parsers_mod, "MAX_TABLE_ROWS", 2)
    capped = parse_document("roles.xlsx", build.xlsx(), limits=LIMITS)
    assert any(n.startswith("rows_truncated: Roles") for n in capped.notes)


def test_pptx_slide_text_notes_tables_and_hidden_things(build) -> None:
    doc = parse_document("deck.pptx", build.pptx(), limits=LIMITS)
    assert doc.pages == 3
    assert [s.pointer for s in doc.sections] == ["slide 1", "slide 3"]
    first = doc.sections[0]
    assert first.heading == "Vision"
    assert "Help clinics book visits" in first.text
    assert "Speaker notes: Mention the pilot clinic" in first.text
    assert "HIDDEN-SHAPE-TEXT" not in first.text
    assert "hidden_slide_skipped: 2" in doc.notes
    assert "Role | Task" in doc.sections[1].text and "Nurse | Check in" in doc.sections[1].text


def test_pptx_too_many_slides(build, make_limits) -> None:
    assert _code("deck.pptx", build.pptx(), make_limits(max_pages=2)) == "too_many_pages"


# --- text-like ------------------------------------------------------------------------------------------


def test_csv_is_header_aware_with_row_pointers() -> None:
    data = b"Task,Owner,Due\nSend invoice,Asha,Mon\nCall client,Ravi,Tue\n\n"
    doc = parse_document("tasks.csv", data, limits=LIMITS)
    sec = doc.sections[0]
    assert sec.pointer == "rows 1-3" and sec.table
    assert sec.header == "Task | Owner | Due"
    assert sec.text.split("\n") == ["Send invoice | Asha | Mon", "Call client | Ravi | Tue"]
    assert sec.row_numbers == (2, 3)


def test_csv_semicolons_tsv_and_quoted_newlines() -> None:
    semi = parse_document("a.csv", b"a;b\n1;2\n", limits=LIMITS).sections[0]
    assert semi.header == "a | b"
    tsv = parse_document("a.tsv", b"x\ty\n1\t2\n", limits=LIMITS).sections[0]
    assert tsv.header == "x | y"
    quoted = parse_document("q.csv", b'a,b\n"line1\nline2",2\n', limits=LIMITS).sections[0]
    assert quoted.text == "line1 line2 | 2"


def test_csv_row_cap(monkeypatch) -> None:
    monkeypatch.setattr(parsers_mod, "MAX_TABLE_ROWS", 3)
    data = ("h\n" + "\n".join(str(i) for i in range(10))).encode()
    doc = parse_document("long.csv", data, limits=LIMITS)
    assert any(n.startswith("rows_truncated") for n in doc.notes)
    assert len(doc.sections[0].text.split("\n")) == 2  # header + 2 body rows were kept


def test_txt_paragraph_sections_with_line_pointers_and_hidden_chars() -> None:
    raw = "First para​ line one\nline two\n\nSecond para\n".encode()
    doc = parse_document("notes.txt", raw, limits=LIMITS)
    assert [s.pointer for s in doc.sections] == ["lines 1-2", "line 4"]
    assert doc.sections[0].text == "First para line one\nline two"


def test_markdown_headings_and_fences() -> None:
    md = b"intro\n# Payments\nUsers pay.\n```\n# not a heading\n```\n## Orders\nTracked.\n"
    doc = parse_document("brief.md", md, limits=LIMITS)
    assert [s.pointer for s in doc.sections] == [
        "start of document",
        "heading: Payments",
        "heading: Orders",
    ]
    assert "# not a heading" in doc.sections[1].text


def test_text_decoding_bom_and_cp1252() -> None:
    assert (
        parse_document("a.txt", "café".encode("utf-16"), limits=LIMITS).sections[0].text == "café"
    )
    assert parse_document("a.txt", b"caf\xe9", limits=LIMITS).sections[0].text == "café"


def test_eml_headers_body_and_attachments(build) -> None:
    doc = parse_document("mail.eml", build.eml(attachment=True), limits=LIMITS)
    text = doc.sections[0].text
    assert "Subject: App requirements" in text and "From: Asha" in text
    assert "We need offline mode." in text
    assert "attachments_not_read: 1" in doc.notes
    html = parse_document("mail.eml", build.eml(html_only=True), limits=LIMITS).sections[0].text
    assert "Offline mode" in html and "x()" not in html


def test_image_is_accepted_without_text(build) -> None:
    doc = parse_document("whiteboard.png", build.png, limits=LIMITS)
    assert doc.kind == "image" and doc.sections == []
    assert doc.notes == ["image_not_readable_yet"]


def test_empty_document_is_noted_not_failed() -> None:
    doc = parse_document("empty.txt", b"   \n\n", limits=LIMITS)
    assert doc.sections == [] and "no_readable_text" in doc.notes


# --- hostile input: linear time ---------------------------------------------------------------------------


def _fast(fn, *args, **kw) -> None:
    start = time.perf_counter()
    fn(*args, **kw)
    assert time.perf_counter() - start < 3.0


@pytest.mark.parametrize(
    "hostile",
    [
        "\n" * 50_000,
        " \n" * 25_000,
        "#" * 50_000,
        "# " * 25_000,
        "```\n" * 12_500,
        "a" * 50_000,
        "line\n\n" * 10_000,
        "​" * 50_000,
    ],
)
def test_text_and_markdown_parsers_are_linear(hostile: str) -> None:
    _fast(parse_text, hostile)
    _fast(parse_text, hostile, markdown=True)


@pytest.mark.parametrize(
    "hostile", [",\n" * 25_000, '"' * 50_000, ("a," * 100 + "\n") * 500, "|" * 50_000]
)
def test_csv_parser_is_linear(hostile: str) -> None:
    _fast(parse_document, "x.csv", hostile.encode(), limits=LIMITS)


def test_eml_parser_hostile_headers() -> None:
    hostile = ("Subject: " + "a" * 50_000 + "\n\nbody").encode()
    _fast(parse_document, "x.eml", hostile, limits=LIMITS)
