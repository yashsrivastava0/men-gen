"""The ``no_secrets`` assignment rule: real key=value secrets are errors, prose is a warning."""

from __future__ import annotations

import pytest

from mde_spec_builder.testing import make_sample_spec
from mde_spec_builder.validators import no_secrets
from mde_spec_builder.validators.leaks import has_secret, secret_matches


@pytest.mark.parametrize(
    "text",
    [
        "password=hunter2hunter2",
        "password: hunter2hunter2",
        "api_key = abcdefghijkl",
        'secret: "Zx9!aaaaaaaa"',
        "token=abcdefghij",
        "pwd: S3cretValue99",
        "password: AbCdEfGhIjKlMnOpQr",
    ],
)
def test_real_assignments_are_errors(text: str) -> None:
    assert [(m.kind, m.severity) for m in secret_matches(text)] == [("assignment", "error")]
    assert has_secret(text)


@pytest.mark.parametrize(
    "text",
    [
        "password: incorrect",
        "Your password: incorrect, try again",
        "token: required",
        "secret: classified",
        "password: re-enter-your-password",
        "Password: invalid_credentials",
    ],
)
def test_prose_is_only_a_warning(text: str) -> None:
    assert [(m.kind, m.severity) for m in secret_matches(text)] == [("assignment", "warn")]
    assert not has_secret(text)


def test_other_kinds_stay_errors_and_no_match_is_empty() -> None:
    key = "sk-" + "A1b2C3d4" * 4
    assert [(m.kind, m.severity) for m in secret_matches(f"use {key}")] == [("openai_key", "error")]
    assert secret_matches("Sign in with your phone number") == []
    assert secret_matches("a password is needed") == []


def test_prose_in_a_block_is_a_warning_finding_and_a_real_secret_an_error() -> None:
    spec = make_sample_spec(6)
    screen = spec.screens[0].model_copy(
        update={"notes": "Show: password: incorrect under the field"}
    )
    prose = spec.model_copy(update={"screens": [screen, *spec.screens[1:]]})
    found = no_secrets(prose)
    assert [(f.severity, f.target_ids) for f in found] == [("warn", [screen.id])]
    assert "mentions a password" in found[0].message
    real = screen.model_copy(update={"notes": "password=hunter2hunter2"})
    bad = no_secrets(prose.model_copy(update={"screens": [real, *spec.screens[1:]]}))
    assert [(f.severity, f.target_ids) for f in bad] == [("error", [screen.id])]
    assert "hunter2" not in bad[0].message
