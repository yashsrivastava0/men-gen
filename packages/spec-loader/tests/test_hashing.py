from __future__ import annotations

import hashlib

import pytest

from mde_spec_loader import (
    SpecParseError,
    blank_hash,
    compute_spec_hash,
    embed_hash,
    embedded_hash,
    estimate_tokens,
    normalize_newlines,
    verify_hash,
)

BLANK = '---\nspec_version: 1\nhash: ""\nbrief_hash: abc\n---\n\n# App\n'


def test_hash_is_sha256_of_the_text_with_the_hash_blanked() -> None:
    expected = hashlib.sha256(BLANK.encode("utf-8")).hexdigest()
    assert compute_spec_hash(BLANK) == expected
    stamped = embed_hash(BLANK)
    assert f'hash: "{expected}"' in stamped
    assert compute_spec_hash(stamped) == expected  # blanking the embedded value gives the same text
    assert blank_hash(stamped) == BLANK


def test_embed_is_idempotent_and_verify_works() -> None:
    stamped = embed_hash(BLANK)
    assert embed_hash(stamped) == stamped
    assert verify_hash(stamped)
    assert embedded_hash(stamped) == compute_spec_hash(BLANK)
    assert verify_hash(stamped, compute_spec_hash(BLANK))
    assert not verify_hash(stamped, "0" * 64)


def test_tampering_breaks_verification() -> None:
    stamped = embed_hash(BLANK)
    assert not verify_hash(stamped.replace("# App", "# Apps"))
    assert not verify_hash(BLANK)  # a blank hash never verifies
    assert not verify_hash("no front matter at all")


def test_crlf_files_verify() -> None:
    stamped = embed_hash(BLANK)
    assert verify_hash(stamped.replace("\n", "\r\n"))
    assert verify_hash("﻿" + stamped)


def test_only_the_front_matter_hash_line_counts() -> None:
    body_hash = BLANK + '\nhash: "deadbeef"\n'
    stamped = embed_hash(body_hash)
    assert 'hash: "deadbeef"' in stamped  # the body line is untouched
    assert embedded_hash(stamped) == compute_spec_hash(body_hash)


def test_embed_needs_a_hash_line() -> None:
    with pytest.raises(SpecParseError, match="no front-matter hash line"):
        embed_hash("---\na: 1\n---\n")
    assert embedded_hash("---\na: 1\n---\n") is None
    assert blank_hash("---\na: 1\n---\n") == "---\na: 1\n---\n"


def test_normalize_newlines() -> None:
    assert normalize_newlines("﻿a\r\nb\r\n") == "a\nb\n"


def test_estimate_tokens_is_bytes_over_four() -> None:
    assert estimate_tokens("") == 0
    assert estimate_tokens("abcd") == 1
    assert estimate_tokens("abcde") == 2
    assert estimate_tokens("a" * 400) == 100
    assert estimate_tokens("नमस्ते") > estimate_tokens(
        "namaste"
    )  # non-Latin text is not under-counted
