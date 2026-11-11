"""repair_mojibake and the decode path: the Small run's absoluteyoga.in page, rebuilt in miniature."""

from mde_research_agent.adapters.fetch.extract import decode_body, extract_body
from mde_research_agent.core.numbers import claim_figures, figures, missing_figures
from mde_research_agent.core.text import repair_mojibake


def _double(text: str, codec: str) -> str:
    """What a site publishes when it decodes UTF-8 as ``codec`` and encodes the result as UTF-8 again."""
    return text.encode("utf-8").decode(codec, errors="strict")


def test_the_real_pages_sequences_are_repaired_in_both_flavours() -> None:
    # the page held U+00E2 U+0080 U+0099 (Latin-1 flavour, controls kept) and U+00E2 U+0082 U+00B9 for the rupee sign
    assert (
        repair_mojibake("Absolute Yogaâ\x80\x99s drop-in is â\x82¹350")
        == "Absolute Yoga’s drop-in is ₹350"
    )
    assert (
        repair_mojibake(_double("₹3,200 for ‘12 classes’ – “limited", "cp1252"))
        == "₹3,200 for ‘12 classes’ – “limited"
    )
    assert repair_mojibake(_double("naïve café ₹ ₹ — ok", "latin-1")) == "naïve café ₹ ₹ — ok"


def test_ordinary_text_is_never_touched() -> None:
    for text in (
        "café, naïve, Ünïcödé, señor, ₹350, €5, “quotes”, — dash",
        "Ã is a letter and Â too, but alone",
        "नमस्ते ಕನ್ನಡ 日本語 🙂",
        "â€",  # a lone lead and an incomplete tail: not one valid sequence
        "plain ascii only",
        "",
    ):
        assert repair_mojibake(text) == text, text


def test_a_declared_latin1_page_that_is_really_utf8_is_read_as_utf8() -> None:
    raw = "Plans start at ₹999 per month — “no contracts”".encode()
    assert decode_body(raw, "ISO-8859-1") == raw.decode("utf-8")
    assert decode_body(raw, "iso_8859-1") == raw.decode("utf-8")
    assert decode_body(raw, "windows-1252") == raw.decode("utf-8")
    # genuine Latin-1 text still decodes as Latin-1
    assert decode_body("café ₹".encode("cp1252", errors="replace"), "ISO-8859-1") == "café ?"


def test_a_double_encoded_page_gives_a_quote_with_the_right_figures() -> None:
    body = _double(
        "Absolute Yoga’s live page lists a ₹350 drop-in option. A monthly plan is ₹3,200.",
        "latin-1",
    )
    html = f"<html><head><title>Fees</title></head><body><article><p>{body} Filler sentence so the extractor keeps the paragraph for sure.</p><p>Another paragraph with enough words to stay in the main text of this page.</p></article></body></html>"
    page = extract_body(html.encode("utf-8"), "text/html", "utf-8", max_pages=1, max_chars=100000)
    assert "₹350 drop-in" in page.text and "â" not in page.text
    quote = "Absolute Yoga’s live page lists a ₹350 drop-in option."
    claim = "Absolute Yoga lists a drop-in option for ₹350."
    assert not missing_figures(
        claim_figures(claim, "Drop-in class price", {"value": "₹350"}), figures(quote)
    )
