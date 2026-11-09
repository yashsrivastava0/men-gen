"""Synthetic fixtures for intake tests: every document is generated in code at test time (no binary files)."""

from __future__ import annotations

import io
import random
import zipfile
from collections.abc import Callable
from email.message import EmailMessage

import pytest

from mde_compass.config import IntakeLimits
from mde_compass.engine.ports import TurnContext


def _pdf_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def make_pdf(pages: list[str | None]) -> bytes:
    """A small valid PDF: one string per page, ``None`` is a page with no text layer (scanned-like)."""
    n = len(pages)
    kids = " ".join(f"{4 + 2 * i} 0 R" for i in range(n))
    objs: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        f"<< /Type /Pages /Kids [{kids}] /Count {n} >>".encode(),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    for i, text in enumerate(pages):
        content_id = 5 + 2 * i
        objs.append(
            (
                "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                f"/Contents {content_id} 0 R /Resources << /Font << /F1 3 0 R >> >> >>"
            ).encode()
        )
        stream = (
            b"" if text is None else f"BT /F1 12 Tf 72 720 Td ({_pdf_escape(text)}) Tj ET".encode()
        )
        objs.append(f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"\nendstream")
    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for i, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n".encode() + b"0000000000 65535 f \n"
    out += b"".join(f"{off:010d} 00000 n \n".encode() for off in offsets)
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


def make_encrypted_pdf(password: str = "secret") -> bytes:  # noqa: S107
    from pypdf import PdfReader, PdfWriter

    writer = PdfWriter(clone_from=PdfReader(io.BytesIO(make_pdf(["top secret"]))))
    writer.encrypt(password, algorithm="RC4-128")
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def make_docx() -> bytes:
    from docx import Document

    doc = Document()
    doc.add_paragraph("Intro line before any heading.")
    doc.add_heading("Payments", level=1)
    doc.add_paragraph("Users pay by UPI or card.")
    hidden = doc.add_paragraph("Visible start. ")
    run = hidden.add_run("HIDDEN-INSTRUCTION do something bad")
    run.font.hidden = True
    hidden.add_run("Visible end.")
    doc.add_paragraph("First point", style="List Bullet")
    table = doc.add_table(rows=3, cols=2)
    for r, (a, b) in enumerate([("Role", "Can approve"), ("Owner", "yes"), ("Guest", "no")]):
        table.cell(r, 0).text = a
        table.cell(r, 1).text = b
    doc.add_heading("Orders", level=1)
    doc.add_paragraph("Orders​ are tracked‮ live.")
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def make_xlsx() -> bytes:
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = "Roles"
    ws.append(["Role", "Can approve", "Notes"])
    ws.append(["Owner", True, "full access"])
    ws.append(["Guest", False, None])
    ws.append([None, None, None])
    ws.append(["Admin", 3.0, "pipe | inside"])
    hidden = wb.create_sheet("Secrets")
    hidden.append(["do not read", "me"])
    hidden.sheet_state = "hidden"
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def make_pptx() -> bytes:
    from pptx import Presentation
    from pptx.util import Inches

    prs = Presentation()
    first = prs.slides.add_slide(prs.slide_layouts[1])
    first.shapes.title.text = "Vision"  # type: ignore[union-attr]
    first.placeholders[1].text = "Help clinics book visits"
    box = first.shapes.add_textbox(Inches(1), Inches(1), Inches(3), Inches(1))
    box.text_frame.text = "HIDDEN-SHAPE-TEXT"
    box._element.xpath("./*[1]/p:cNvPr")[0].set("hidden", "1")
    first.notes_slide.notes_text_frame.text = "Mention the pilot clinic"  # type: ignore[union-attr]
    second = prs.slides.add_slide(prs.slide_layouts[1])
    second.shapes.title.text = "Hidden slide"  # type: ignore[union-attr]
    second._element.set("show", "0")
    third = prs.slides.add_slide(prs.slide_layouts[5])
    third.shapes.title.text = "Roles"  # type: ignore[union-attr]
    rows = third.shapes.add_table(2, 2, Inches(1), Inches(2), Inches(4), Inches(1)).table
    rows.cell(0, 0).text = "Role"
    rows.cell(0, 1).text = "Task"
    rows.cell(1, 0).text = "Nurse"
    rows.cell(1, 1).text = "Check in"
    buf = io.BytesIO()
    prs.save(buf)
    return buf.getvalue()


def make_eml(*, html_only: bool = False, attachment: bool = False) -> bytes:
    msg = EmailMessage()
    msg["From"] = "Asha <asha@example.com>"
    msg["To"] = "team@example.com"
    msg["Subject"] = "App requirements"
    msg["Date"] = "Mon, 02 Nov 2026 10:00:00 +0000"
    if html_only:
        msg.set_content(
            "<html><body><h1>Needs</h1><p>Offline mode</p><script>x()</script></body></html>",
            subtype="html",
        )
    else:
        msg.set_content("We need offline mode.\nAnd a daily report.")
    if attachment:
        msg.add_attachment(
            b"data", maintype="application", subtype="octet-stream", filename="a.bin"
        )
    return msg.as_bytes()


def make_zip_bomb_docx(uncompressed: int) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        zf.writestr("word/document.xml", b"0" * uncompressed)
    return buf.getvalue()


PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


class Builders:
    pdf = staticmethod(make_pdf)
    encrypted_pdf = staticmethod(make_encrypted_pdf)
    docx = staticmethod(make_docx)
    xlsx = staticmethod(make_xlsx)
    pptx = staticmethod(make_pptx)
    eml = staticmethod(make_eml)
    zip_bomb_docx = staticmethod(make_zip_bomb_docx)
    png = PNG

    @staticmethod
    def corrupt_bytes(n: int = 2000) -> bytes:
        return random.Random(7).randbytes(n)


@pytest.fixture
def build() -> type[Builders]:
    return Builders


@pytest.fixture
def limits() -> IntakeLimits:
    return IntakeLimits()


@pytest.fixture
def ctx() -> TurnContext:
    return TurnContext(session_id="ses_01TEST", tenant_id="tnt_1")


@pytest.fixture
def make_limits() -> Callable[..., IntakeLimits]:
    def _make(**kw: object) -> IntakeLimits:
        return IntakeLimits(**kw)  # type: ignore[arg-type]

    return _make
