"""Redaction hardening (H3 quadratic patterns, M2 hidden characters). Fake keys are built at runtime."""

from __future__ import annotations

import time

import pytest

from mde_core import redact
from mde_core.redact import (
    REDACTED,
    SECRET_PATTERNS,
    contains_secret,
    find_secrets,
    normalize_for_scan,
    redact_text,
    redact_value,
)

# Synthetic key bodies (never real): the literals the scanner would flag are assembled here.
_BODY = "A1b2C3d4E5" * 5
FAKE_KEY = "sk-" + "proj-" + _BODY
FAKE_AWS = "AKIA" + "ABCDEFGHIJKLMNOP"
FAKE_GH = "ghp" + "_" + "a1B2c3D4e5" * 4

N = 100_000

# Adversarial families: every one used to be (or could be) quadratic for some pattern.
FAMILIES: dict[str, str] = {
    "a.a.": "a." * (N // 2),
    "a-a-": "a-" * (N // 2),
    "1.1.": "1." * (N // 2),
    "sk-sk-": "sk-" * (N // 3),
    "sk-a ": "sk-a " * (N // 5),
    "eyJ.": "eyJaaaaaaaa." * (N // 12),
    "token  ": "token " + "  " * (N // 2),
    "password=": "password=" * (N // 9),
    "BEGIN": "-----BEGIN PRIVATE KEY-----" * (N // 27),
    "BEGIN-space": "-----BEGIN " + " " * N,
    "bearer": "bearer " * (N // 7),
    "://": "a://" * (N // 4),
    "a://b:": "a://b:" * (N // 6),
    "spaces": " " * N,
    "AKIA": "AKIA" * (N // 4),
    "hidden": "s\u200bk\u00ad-" * (N // 5),
}


def _run(fn, *args: object) -> float:  # type: ignore[no-untyped-def]
    started = time.perf_counter()
    fn(*args)
    return time.perf_counter() - started


@pytest.mark.parametrize("family", sorted(FAMILIES))
def test_redact_linear_and_hidden_chars(family: str) -> None:
    """100k characters of an adversarial family: every pattern, ``redact_text`` and
    ``find_secrets`` finish well under half a second (the quadratic ``connection_string`` took
    seconds at 40k). Hidden characters are folded first, so they are not a way to slow it down."""
    text = FAMILIES[family]
    for name, pat in SECRET_PATTERNS:
        assert _run(pat.search, text) < 0.5, f"{name} search is not linear on {family!r}"
        assert _run(pat.sub, "X", text) < 0.5, f"{name} sub is not linear on {family!r}"
    assert _run(redact_text, text) < 0.5, f"redact_text is not linear on {family!r}"
    assert _run(find_secrets, text) < 0.5, f"find_secrets is not linear on {family!r}"


def test_pattern_time_scales_linearly() -> None:
    """Doubling the input roughly doubles the time (a 1000x gap would be quadratic)."""
    small, large = "a." * 20_000, "a." * 160_000
    for name, pat in SECRET_PATTERNS:
        t_small = max(_run(pat.sub, "X", small), 1e-4)
        t_large = _run(pat.sub, "X", large)
        assert t_large < max(t_small * 8 * 6, 0.1), (
            f"{name}: 8x input took {t_large / t_small:.0f}x"
        )


def _hidden_variants(key: str) -> dict[str, str]:
    mid = 20
    return {
        "plain": key,
        "zero-width": key[:mid] + "\u200b" + key[mid:],
        "zero-width-joiner": key[:mid] + "\u200d" + key[mid:],
        "soft-hyphen": key[:mid] + "\u00ad" + key[mid:],
        "word-joiner": key[:mid] + "\u2060" + key[mid:],
        "bidi-override": "\u202e" + key[:mid] + "\u202c" + key[mid:],
        "variation-selector": key[:mid] + "\ufe0f" + key[mid:],
        "fullwidth-hyphens": key.replace("-", "\uff0d"),
        "non-breaking-hyphens": key.replace("-", "\u2011"),
        "en-dashes": key.replace("-", "\u2013"),
        "fullwidth-letters": "".join(chr(ord(c) + 0xFEE0) if c.isalnum() else c for c in key),
        "control-char": key[:mid] + "\x00" + key[mid:],
        "space-split": key[:mid] + " " + key[mid:],
        "newline-split": key[:mid] + "\r\n" + key[mid:],
        "multi-split": " ".join(key[i : i + 12] for i in range(0, len(key), 12)),
        "underscore-prefixed": "my_" + key,
        "in-assignment": "export OPENAI_API_KEY=" + key[:mid] + "\u200b" + key[mid:],
    }


@pytest.mark.parametrize("variant", sorted(_hidden_variants(FAKE_KEY)))
def test_hidden_characters_do_not_defeat_redaction(variant: str) -> None:
    text = "please use " + _hidden_variants(FAKE_KEY)[variant] + " for the call"
    assert find_secrets(text), f"{variant}: not detected"
    assert contains_secret(text)
    clean, kinds = redact_text(text)
    assert kinds, f"{variant}: nothing redacted"
    assert REDACTED in clean
    assert _BODY[:10] not in clean and _BODY[-10:] not in clean, f"{variant}: key body survived"
    assert not find_secrets(clean)
    assert redact_text(clean)[0] == clean  # idempotent


@pytest.mark.parametrize("key", [FAKE_AWS, FAKE_GH], ids=["aws", "github"])
def test_other_key_kinds_survive_hidden_characters(key: str) -> None:
    for text in (key[:6] + "\u200b" + key[6:], key[:6] + "\u00ad" + key[6:], "my_" + key):
        clean, kinds = redact_text("k: " + text)
        assert kinds and key[8:16] not in clean


def test_clean_text_is_normalised_but_otherwise_unchanged() -> None:
    plain = "I want an app for my bakery in Pune; मुझे ऐप चाहिए"
    assert redact_text(plain) == (plain, [])
    # fullwidth and invisible characters in innocent text are folded (documented behaviour)
    assert redact_text("ｈｅｌｌｏ\u200b world") == ("hello world", [])
    assert normalize_for_scan("plain ascii") == "plain ascii"


def test_prose_after_a_key_prefix_is_not_a_split_key() -> None:
    for prose in (
        "use sk-learn pipeline for classification of the products we sell online",
        "sk-ab12 and then some long sentence follows here okay",
        "the task-based approach to risk-management and desk-research planning",
    ):
        assert find_secrets(prose) == []
        assert redact_text(prose) == (prose, [])


def test_env_style_assignments_are_detected() -> None:
    """``DB_PASSWORD=...`` was invisible: ``\\b`` does not match after an underscore."""
    clean, kinds = redact_text("DB_PASSWORD=hunter2hunter2 and CLIENT_SECRET: abcdefgh12345")
    assert kinds == ["assignment"]
    assert "hunter2" not in clean and "abcdefgh" not in clean
    assert "DB_PASSWORD=" in clean  # the name stays, the value goes


def test_connection_string_long_scheme_and_tail() -> None:
    url = "a" * 60 + "://admin:s3cretpass@db.example.com/prod?x=1"
    clean, kinds = redact_text("dsn " + url + " end")
    assert kinds == ["connection_string"]
    assert "s3cretpass" not in clean and clean.endswith(" end")


def test_redact_value_removes_a_known_secret_hidden_behind_invisible_characters() -> None:
    secret = "dev-token-12345"
    hidden = "x " + secret[:6] + "\u200b" + secret[6:] + " y"
    assert redact_value(hidden, secret) == "x " + REDACTED + " y"
    assert redact_value("no secret here \u200b", secret) == "no secret here \u200b"
    assert redact_value("short", "abc") == "short"  # under 8 characters: ignored


# ---------------------------------------------------------------- private key blocks


def _pem(body: str = "abc") -> str:
    return f"-----BEGIN RSA PRIVATE KEY-----\n{body}\n-----END RSA PRIVATE KEY-----"  # secret-scan: allow


def test_private_key_blocks_are_removed_through_the_end_marker() -> None:
    text = "before\n" + _pem("MIIEow" * 40) + "\nmiddle\n" + _pem("second" * 10) + "\nafter"
    clean, kinds = redact_text(text)
    assert kinds == ["private_key_block"]
    assert clean == f"before\n{REDACTED}\nmiddle\n{REDACTED}\nafter"


def test_unterminated_private_key_fails_closed() -> None:
    clean, kinds = redact_text(
        "log line -----BEGIN PRIVATE KEY-----\nMIIEvQIBADAN"
    )  # secret-scan: allow
    assert kinds == ["private_key_block"]
    assert clean == f"log line {REDACTED}"
    assert contains_secret("-----BEGIN OPENSSH PRIVATE KEY-----")  # secret-scan: allow


# ---------------------------------------------------------------- windows


@pytest.fixture
def tiny_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(redact, "WINDOW_CHARS", 200)
    monkeypatch.setattr(redact, "OVERLAP_CHARS", 90)


@pytest.mark.parametrize("offset", range(0, 330, 7))
def test_secret_straddling_a_window_edge_is_still_redacted(tiny_windows: None, offset: int) -> None:
    secrets = [
        FAKE_KEY,
        FAKE_AWS,
        "postgres://admin:s3cretpass@db.example.com/prod",
        "password=Zq7Lm2Vx9Kd4Rt8W",
        "Bearer " + "Zq7Lm2Vx9Kd4Rt8Ws3Bn6Hy1",
    ]
    for secret in secrets:
        text = ("." * offset + " " + secret + " " + "." * 300)[: offset + len(secret) + 320]
        clean, kinds = redact_text(text)
        assert kinds, f"{secret[:6]} at {offset}: not redacted"
        assert secret[-12:] not in clean, f"{secret[:6]} at {offset}: tail survived"
        assert find_secrets(text), f"{secret[:6]} at {offset}: find_secrets missed it"


def test_run_longer_than_the_overlap_is_extended_past_the_window_edge(tiny_windows: None) -> None:
    key = "sk-" + "Zq7Lm2Vx9K" * 40  # 403 characters: longer than window and overlap
    for offset in (0, 50, 111, 190):
        clean, _ = redact_text("x" * offset + " " + key + " tail")
        assert "Zq7Lm2Vx9K" not in clean and clean.endswith(" tail")


def test_windowed_text_matches_unwindowed_result(
    tiny_windows: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    text = (" ".join(["filler words here"] * 30) + " " + FAKE_KEY + " ") * 6
    windowed = redact_text(text)
    monkeypatch.setattr(redact, "WINDOW_CHARS", 10**9)
    assert windowed == redact_text(text)
