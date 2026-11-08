"""Validator 9 must see through hidden characters (a zero-width-split assignment is still a secret)."""

from __future__ import annotations

from mde_spec_builder.validators.leaks import has_secret, secret_matches

ZW = "​"


def test_zero_width_split_assignment_is_an_error() -> None:
    text = "pass" + ZW + "word = hunt" + ZW + "er2hunter2"
    assert has_secret(text)
    assert any(m.kind == "assignment" and m.severity == "error" for m in secret_matches(text))


def test_fullwidth_equals_assignment_is_an_error() -> None:
    assert has_secret("api_key＝abcdef1234567890")  # fullwidth equals sign folds to '='


def test_plain_prose_mention_is_still_only_a_warning() -> None:
    matches = secret_matches("The label says password: incorrect on the form")
    assert matches and all(m.severity == "warn" for m in matches)
    assert not has_secret("The label says password: incorrect on the form")
