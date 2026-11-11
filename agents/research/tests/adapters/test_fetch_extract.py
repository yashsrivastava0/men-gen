"""Offline tests for the page extractors: charsets, HTML main text, PDF text layer, wall labels."""

from __future__ import annotations

import hashlib
import io
import unicodedata
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from pypdf import PdfWriter

from mde_research_agent.adapters.fetch import extract
from mde_research_agent.adapters.fetch.extract import (
    clean_text,
    content_hash,
    content_kind,
    decode_body,
    dehyphenate,
    detect_wall,
    extract_body,
    extract_html,
    extract_pdf,
    sniff_media_type,
    split_content_type,
)

PAGES = Path(__file__).parent.parent / "fixtures" / "pages"

# ------------------------------------------------------------------------------------- samples

HINDI = [
    "नासिक जिले में टमाटर उगाने वाले किसान अब अपनी उपज को शीतगृह में रखकर बाजार में बेहतर दाम पाने की "
    "कोशिश कर रहे हैं। पहले खुले ट्रकों में सुबह-सुबह माल मुंबई की मंडी भेजा जाता था और दो दिन के भीतर "
    "बिक्री न होने पर टमाटर नरम पड़ने लगते थे।",
    "जिले में पिछले पांच वर्षों में तीन नए शीतगृह खुले हैं। हर इकाई में दो सौ से पांच सौ टन तक माल रखा जा "
    "सकता है और किसान केवल उन्हीं दिनों के लिए जगह किराये पर लेते हैं जब मंडी में भाव गिरने का डर होता है।",
]
KANNADA = [
    "ನಾಸಿಕ್ ಜಿಲ್ಲೆಯ ರೈತರು ತಮ್ಮ ಟೊಮೆಟೊ ಮತ್ತು ದ್ರಾಕ್ಷಿ ಬೆಳೆಗಳನ್ನು ಶೀತಲ ಗೋದಾಮುಗಳಲ್ಲಿ ಸಂಗ್ರಹಿಸಿ ಉತ್ತಮ ಬೆಲೆಗೆ "
    "ಮಾರಾಟ ಮಾಡಲು ಪ್ರಯತ್ನಿಸುತ್ತಿದ್ದಾರೆ. ಈ ಮೊದಲು ತೆರೆದ ಲಾರಿಗಳಲ್ಲಿ ಬೆಳಗ್ಗೆ ಬೇಗನೆ ಮುಂಬೈ ಮಾರುಕಟ್ಟೆಗೆ ಸರಕು ಕಳುಹಿಸಲಾಗುತ್ತಿತ್ತು.",
    "ಜಿಲ್ಲೆಯಲ್ಲಿ ಕಳೆದ ಐದು ವರ್ಷಗಳಲ್ಲಿ ಮೂರು ಹೊಸ ಶೀತಲ ಘಟಕಗಳು ಆರಂಭವಾಗಿವೆ. ಪ್ರತಿ ಘಟಕದಲ್ಲಿ ಇನ್ನೂರರಿಂದ ಐನೂರು "
    "ಟನ್ ವರೆಗೆ ಸರಕು ಸಂಗ್ರಹಿಸಬಹುದು ಮತ್ತು ರೈತರು ಬೆಲೆ ಕುಸಿಯುವ ದಿನಗಳಿಗೆ ಮಾತ್ರ ಜಾಗವನ್ನು ಬಾಡಿಗೆಗೆ ಪಡೆಯುತ್ತಾರೆ.",
]
ARABIC = [
    "يعتمد المزارعون في منطقة ناشيك اليوم على مخازن التبريد لحفظ محاصيل الطماطم والعنب لفترة أطول وبيعها "
    "بأسعار أفضل. كانت الشاحنات المكشوفة تنقل البضائع في الفجر إلى سوق مومباي، وإذا لم تُبع خلال يومين بدأت الطماطم تلين.",
    "افتُتحت ثلاث وحدات تبريد جديدة في المنطقة خلال السنوات الخمس الماضية. تتسع كل وحدة لما بين مئتين وخمسمئة طن، "
    "ويستأجر المزارعون المساحة فقط في الأيام التي يخشون فيها انخفاض الأسعار في السوق.",
]


def script_page(lang: str, direction: str, title: str, paragraphs: list[str]) -> str:
    body = "\n".join(f"<p>{p}</p>" for p in paragraphs)
    return f"""<!doctype html>
<html lang="{lang}" dir="{direction}"><head><meta charset="utf-8"><title>{title}</title></head>
<body>
<nav><a href="/">Home</a> <a href="/about">About us</a> <a href="/subscribe">Newsletter</a></nav>
<article><h1>{title}</h1>
{body}
</article>
<footer><p>Privacy Policy | Terms of Use | Contact us</p></footer>
</body></html>"""


def build_pdf(pages: list[list[str]], *, title: str | None = None) -> bytes:
    """A small valid PDF with a text layer: one list of lines per page (ASCII, Helvetica)."""

    def esc(line: str) -> str:
        return line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

    count = len(pages)
    kids = " ".join(f"{4 + 2 * i} 0 R" for i in range(count))
    info_id = 4 + 2 * count
    objs: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        f"<< /Type /Pages /Kids [{kids}] /Count {count} >>".encode(),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
    ]
    for i, lines in enumerate(pages):
        stream = (
            "BT /F1 12 Tf 72 720 Td 14 TL " + " ".join(f"({esc(x)}) Tj T*" for x in lines) + " ET"
        )
        objs.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 3 0 R >> >> /Contents {5 + 2 * i} 0 R >>".encode()
        )
        objs.append(f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream".encode("latin-1"))
    trailer_info = ""
    if title is not None:
        objs.append(f"<< /Title ({esc(title)}) >>".encode())
        trailer_info = f" /Info {info_id} 0 R"
    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for number, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref_at = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{offset:010d} 00000 n \n".encode() for offset in offsets)
    out += (
        f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R{trailer_info} >>\n"
        f"startxref\n{xref_at}\n%%EOF\n"
    ).encode()
    return bytes(out)


def blank_pdf(pages: int = 3) -> bytes:
    """What a scanned PDF looks like to a text extractor: pages, no text layer."""
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(612, 792)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


# ----------------------------------------------------------------------------- HTML main text


def test_article_keeps_the_story_and_drops_boilerplate() -> None:
    got = extract_html((PAGES / "article_en.html").read_text(encoding="utf-8"))
    assert got.title == "How cold storage is changing produce logistics in Nashik"
    assert got.published == date(2024, 3, 5)
    for kept in (
        "Ten years ago a tomato grown near Nashik had about two days to reach a buyer",
        "Three pre-cooling and storage units opened in the district between 2019 and 2023.",
        "What buyers now ask for",
        "Cooperative societies that aggregate lots before they reach the unit report the best results",
    ):
        assert kept in got.text
    assert "Grapes" in got.text and "3 to 4 weeks" in got.text  # tables are kept
    for dropped in (
        "cookies",
        "Subscribe to our newsletter",
        "Related stories",
        "Privacy Policy",
        "All rights reserved",
        "Great piece",  # comments
        "dataLayer",  # script text
        "color: #333",  # style text
    ):
        assert dropped not in got.text


@pytest.mark.parametrize(
    ("lang", "direction", "title", "paragraphs"),
    [
        ("hi", "ltr", "नासिक में शीतगृह और टमाटर", HINDI),
        ("kn", "ltr", "ನಾಸಿಕ್‌ನಲ್ಲಿ ಶೀತಲ ಗೋದಾಮು", KANNADA),
        ("ar", "rtl", "مخازن التبريد في ناشيك", ARABIC),
    ],
)
def test_indic_and_arabic_text_survives_intact(
    lang: str, direction: str, title: str, paragraphs: list[str]
) -> None:
    html = script_page(lang, direction, title, paragraphs)
    got = extract_body(html.encode("utf-8"), "text/html", "utf-8")
    for paragraph in paragraphs:
        assert unicodedata.normalize("NFC", paragraph) in got.text
    assert unicodedata.normalize("NFC", title) in got.text
    assert "�" not in got.text
    assert "Privacy Policy" not in got.text and "Newsletter" not in got.text
    assert got.title == unicodedata.normalize("NFC", title)


def test_zero_width_joiners_are_part_of_the_spelling_and_are_kept() -> None:
    # Persian "mi-khaham" and Kannada "Nashik-nalli" are written with ZWNJ; ZWJ shapes Indic conjuncts
    persian = "\u0645\u06cc\u200c\u062e\u0648\u0627\u0647\u0645 \u06a9\u062a\u0627\u0628\u200c\u0647\u0627 \u0631\u0627 \u0628\u062e\u0648\u0627\u0646\u0645"
    kannada = "ನಾಸಿಕ್\u200cನಲ್ಲಿ ಮತ್ತು ಕ್\u200dಷ"
    filler = "<p>" + "ಇದು ಒಂದು ಪರೀಕ್ಷಾ ವಾಕ್ಯ. " * 12 + "</p>"
    html = (
        f"<html><head><title>{persian}</title></head><body><article><h1>{persian}</h1>{filler}"
        f"<p>{persian}. {kannada}</p><p>entity&zwnj;form and &#8205;numeric and &#x200C;hex.</p>"
        f"{filler}</article></body></html>"
    )
    got = extract_html(html)
    assert f"{persian}. {kannada}" in got.text
    assert "entity\u200cform and \u200dnumeric and \u200chex." in got.text
    assert got.title == persian
    assert "\U0001fbf0" not in got.text and "\U0001fbf1" not in got.text  # stand-ins never leak


def test_text_is_nfc_and_control_characters_are_dropped() -> None:
    raw = "café au lait\r\nline two\x00\x07 ends here\ud800!"
    got = extract_body(raw.encode("utf-8", errors="surrogatepass"), "text/plain", "utf-8")
    assert "café au lait\nline two ends" in got.text
    assert "\x00" not in got.text and "\x07" not in got.text and "\r" not in got.text
    assert unicodedata.is_normalized("NFC", got.text)
    got.text.encode("utf-8")  # no lone surrogates left (Postgres would refuse them)


def test_wording_is_not_rewritten() -> None:
    # quotes, dashes, double spaces inside a line and odd punctuation must reach the verifier as is
    sentence = "He said, “It’s 5 km – maybe 6;  not 7!”  (really?)"
    got = extract_body(sentence.encode(), "text/plain", "utf-8")
    assert got.text == sentence


def test_js_only_shell_has_no_content() -> None:
    got = extract_html((PAGES / "js_shell.html").read_text(encoding="utf-8"))
    assert len(got.text) < 100  # at most the <noscript> hint; the app itself is not rendered
    assert "bundle.js" not in got.text and "__INITIAL_STATE__" not in got.text
    assert got.title == "Fleetly"


def test_visible_text_fallback_when_the_main_text_extractor_finds_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(extract, "_trafilatura_text", lambda _html: "")
    html = (
        "<html><head><title>Plain  page</title><style>p{}</style></head><body>"
        "<script>var secret = 1;</script><div>First block<br>second line</div>"
        "<p>Para <b>with</b> bold</p><noscript>enable js</noscript><!-- hidden --></body></html>"
    )
    got = extract_html(html)
    assert got.text.splitlines() == ["First block", "second line", "Para with bold"]
    assert got.title == "Plain page"


def test_publication_date_is_none_when_absent_or_unparseable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert (
        extract_html("<html><body><p>No date anywhere in here.</p></body></html>").published is None
    )
    monkeypatch.setattr(
        extract.trafilatura,
        "extract_metadata",
        lambda _html: SimpleNamespace(title="T", date="not-a-date"),
    )
    got = extract_html("<html><body><p>text</p></body></html>")
    assert got.published is None and got.title == "T"
    monkeypatch.setattr(
        extract.trafilatura,
        "extract_metadata",
        lambda _html: SimpleNamespace(title="T", date="2023-12-31"),
    )
    assert extract_html("<html><body><p>text</p></body></html>").published == date(2023, 12, 31)


def test_extractor_crash_is_an_empty_result_not_an_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(_html: str) -> None:
        raise RuntimeError("extractor bug")

    monkeypatch.setattr(extract.trafilatura, "extract", boom)
    monkeypatch.setattr(extract.trafilatura, "extract_metadata", boom)
    assert extract_html("<p>hello world</p>").text == "hello world"  # visible-text fallback
    monkeypatch.setattr(extract, "_visible_text", boom)
    assert extract_html("<p>hello world</p>").text == ""


def test_max_chars_truncates_without_failing() -> None:
    got = extract_body(("word " * 500).encode(), "text/plain", None, max_chars=100)
    assert len(got.text) == 100


# --------------------------------------------------------------------------------- charsets


def test_split_content_type() -> None:
    assert split_content_type("Text/HTML; charset=UTF-8") == ("text/html", "UTF-8")
    assert split_content_type('text/plain; format=flowed; charset="iso-8859-1"') == (
        "text/plain",
        "iso-8859-1",
    )
    assert split_content_type("application/pdf") == ("application/pdf", None)
    assert split_content_type("") == ("", None)
    assert split_content_type(";;;charset=") == ("", None)


def test_content_kind() -> None:
    assert content_kind("text/html") == "html"
    assert content_kind("application/xhtml+xml") == "html"
    assert content_kind("text/plain") == "text"
    assert content_kind("application/pdf") == "pdf"
    for other in ("", "image/png", "application/json", "text/css", "application/octet-stream"):
        assert content_kind(other) is None


def test_decode_prefers_bom_then_header_then_meta_then_utf8() -> None:
    text = "café – naïve"
    assert decode_body(text.encode("utf-8-sig")) == text
    assert decode_body(text.encode("utf-16")) == text  # utf-16 with BOM
    assert decode_body(text.encode("cp1252"), "windows-1252") == text
    assert decode_body(text.encode("cp1252"), "iso-8859-1") == text  # latin-1 label means cp1252
    meta = b'<html><head><meta http-equiv="Content-Type" content="text/html; charset=windows-1252">'
    assert decode_body(meta + text.encode("cp1252"), html=True).endswith(text)
    assert decode_body(b'<meta charset="utf-8">' + text.encode("utf-8"), html=True).endswith(text)
    xml = b'<?xml version="1.0" encoding="iso-8859-1"?><html>' + "é".encode("latin-1")
    assert decode_body(xml, html=True).endswith("é")
    # the header wins over the meta tag
    assert decode_body(b'<meta charset="latin1">' + "é".encode(), "utf-8", html=True).endswith("é")


def test_decode_never_raises_on_bad_input() -> None:
    assert decode_body(b"ok \xff\xfe\xfa bytes", "utf-8") == "ok ��� bytes"
    assert decode_body(b"plain", "no-such-charset") == "plain"
    assert decode_body(b"plain", "base64") == "plain"  # a codec that is not a text encoding
    assert decode_body(b"plain", "rot13") == "plain"
    assert decode_body(b"\xff\xfe\x00") != ""  # odd-length UTF-16: replacement, not an exception
    assert decode_body(b"", "utf-8") == ""
    assert decode_body(b'<meta charset="utf-16">hello', html=True).endswith("hello")


def test_sniff_media_type() -> None:
    assert sniff_media_type(b"%PDF-1.7\n...") == "application/pdf"
    assert sniff_media_type(b"\xef\xbb\xbf  \n<!DOCTYPE html><html>") == "text/html"
    assert sniff_media_type(b"<html lang=en>") == "text/html"
    assert sniff_media_type(b"PK\x03\x04 zip") == ""
    assert sniff_media_type(b"just some text") == ""
    assert sniff_media_type(b"") == ""


# ------------------------------------------------------------------------------------- PDF


def test_pdf_text_layer_pages_joined_by_blank_lines() -> None:
    data = build_pdf(
        [["Annual produce report", "Page one text."], ["Second page begins here."]],
        title="Produce report 2024",
    )
    got = extract_pdf(data)
    assert got.text == "Annual produce report\nPage one text.\n\nSecond page begins here."
    assert got.title == "Produce report 2024"
    assert got.published is None


def test_pdf_line_end_hyphens_are_joined() -> None:
    got = extract_pdf(
        build_pdf([["Cold chains need inter-", "national standards and well-", "Known pilots."]])
    )
    assert "international standards" in got.text
    assert "well-\nKnown" in got.text  # capital letter after the break: a real hyphen, kept


def test_dehyphenate_rules() -> None:
    assert dehyphenate("inter-\nnational") == "international"
    assert dehyphenate("inter-\n  national") == "international"
    assert dehyphenate("co­\nordinate") == "coordinate"  # soft hyphen
    assert dehyphenate("Anglo-\nSaxon") == "Anglo-\nSaxon"
    assert dehyphenate("page 4-\n5 pages") == "page 4-\n5 pages"
    assert dehyphenate("a - \nb") == "a - \nb"  # a spaced dash is not a split word
    assert dehyphenate("well-known") == "well-known"
    assert dehyphenate("ಕನ್ನಡ-\nಪಠ್ಯ") == "ಕನ್ನಡ-\nಪಠ್ಯ"  # no letter-case in Kannada: left alone


def test_pdf_page_cap() -> None:
    data = build_pdf([[f"Content of page {n}."] for n in range(1, 6)])
    got = extract_pdf(data, max_pages=2)
    assert got.text == "Content of page 1.\n\nContent of page 2."
    assert extract_pdf(data, max_pages=0).text == ""


def test_pdf_char_cap_truncates() -> None:
    data = build_pdf([["x" * 80] for _ in range(10)])
    got = extract_pdf(data, max_chars=200)
    assert len(got.text) == 200


def test_scanned_like_pdf_has_no_text() -> None:
    assert extract_pdf(blank_pdf(3)).text == ""


def test_encrypted_pdf_is_not_opened() -> None:
    writer = PdfWriter()
    writer.add_blank_page(612, 792)
    writer.encrypt("secret", algorithm="RC4-128")
    buffer = io.BytesIO()
    writer.write(buffer)
    assert extract_pdf(buffer.getvalue()).text == ""


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"%PDF-",
        b"%PDF-1.4\n%%EOF",
        b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\ntrailer\n<< /Root 1 0 R >>\n%%EOF",
        b"<html><body>not a pdf at all</body></html>",
        bytes(range(256)) * 20,
        build_pdf([["Some text here."]])[:-60],  # truncated
        build_pdf([["Some text here."]]).replace(b"Tj", b"Zz"),  # broken operators
    ],
)
def test_bad_pdf_gives_an_empty_result_not_an_exception(data: bytes) -> None:
    got = extract_pdf(data)
    assert got.text in ("", "Some text here.")


# ---------------------------------------------------------------------------- hashes, walls


def test_content_hash_is_stable_and_nfc_based() -> None:
    assert content_hash("") == ""
    text = "café नासिक"
    assert content_hash(text) == hashlib.sha256(text.encode("utf-8")).hexdigest()
    assert content_hash(text) == content_hash(unicodedata.normalize("NFD", text))
    assert content_hash(text) != content_hash(text + ".")


def test_clean_text_is_idempotent() -> None:
    once = clean_text("  a\r\nb\x00  \n")
    assert once == "a\nb"
    assert clean_text(once) == once


@pytest.mark.parametrize(
    ("title", "text", "url", "label"),
    [
        ("Just a moment...", "Enable JavaScript and cookies to continue", "", "blocked"),
        ("Attention Required! | Cloudflare", "", "", "blocked"),
        ("Access Denied", "You don't have permission to access this server.", "", "blocked"),
        ("", "Please verify you are human by completing the captcha below.", "", "blocked"),
        ("Pardon Our Interruption", "", "", "blocked"),
        ("Premium article", "Subscribe to continue reading this story.", "", "paywall"),
        ("", "This content is for subscribers only.", "", "paywall"),
        ("Sign in to your account", "", "", "paywall"),
        ("Welcome", "", "https://example.com/account/login?next=/story", "paywall"),
        ("Welcome", "", "https://example.com/subscribe", "paywall"),
        ("Fleetly", "You need to enable JavaScript to run this app.", "", None),
        ("", "", "https://example.com/article/loginess-of-things", None),
        ("About us", "We build software for logistics teams.", "https://example.com/about", None),
    ],
)
def test_wall_labels(title: str, text: str, url: str, label: str | None) -> None:
    assert detect_wall(title, text, url) == label


# ------------------------------------------------------------------------- never raises


@pytest.mark.parametrize(
    "media", ["text/html", "application/xhtml+xml", "text/plain", "application/pdf"]
)
@settings(
    max_examples=25, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture]
)
@given(data=st.binary(max_size=2000), charset=st.one_of(st.none(), st.text(max_size=12)))
def test_extract_body_never_raises_on_arbitrary_bytes(
    media: str, data: bytes, charset: str | None
) -> None:
    got = extract_body(data, media, charset)
    assert isinstance(got.text, str)
    got.text.encode("utf-8")  # always storable


@pytest.mark.parametrize(
    "html",
    [
        "",
        "<",
        "<<<>>>",
        "<html",
        "\x00\x01\x02",
        '<?xml version="1.0" encoding="utf-8"?><html><body>x</body></html>',
        "<div>" * 3000 + "deep" + "</div>" * 3000,
        "<p>" + "a" * 200_000 + "</p>",
        "<script>" + "x" * 1000,
        "<html><body>\ud800 lone surrogate</body></html>",
    ],
)
def test_extract_html_never_raises_on_nasty_markup(html: str) -> None:
    got = extract_html(html)
    got.text.encode("utf-8")
    assert len(got.text) <= extract.DEFAULT_MAX_CHARS
