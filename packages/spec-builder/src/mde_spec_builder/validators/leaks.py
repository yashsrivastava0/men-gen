"""Validator 9, ``no_secrets``: no secrets or credentials in the file."""

from __future__ import annotations

import re
from dataclasses import dataclass

from mde_contracts.calls import Severity, ValidatorFinding
from mde_contracts.spec import AppSpec
from mde_core.redact import SECRET_PATTERNS, find_secrets, normalize_for_scan
from mde_spec_builder.render import render_spec
from mde_spec_builder.validators._common import finding
from mde_spec_loader.blocks import tokenize
from mde_spec_loader.parse import extract_summary

NAME = "no_secrets"
_ASSIGNMENT = next(pattern for name, pattern in SECRET_PATTERNS if name == "assignment")
_SYMBOLS = re.compile(r"[!@#$%^&*+/\\~|]")
_LONG_MIXED = 16


@dataclass(frozen=True)
class SecretMatch:
    kind: str
    severity: Severity


def _strong_assignment(separator: str, value: str) -> bool:
    """Does ``password: <value>`` look like a real secret rather than prose?

    ``key=value`` always does. After a colon the value must look like a credential: it has a digit
    or a symbol, or it is long and mixed case. A plain word (``password: incorrect``) is prose.
    """
    if separator == "=":
        return True
    if any(c.isdigit() for c in value) or _SYMBOLS.search(value):
        return True
    return len(value) >= _LONG_MIXED and value != value.lower() and value != value.upper()


def secret_matches(text: str) -> list[SecretMatch]:
    """Secret-looking spans of ``text`` by kind. Everything ``find_secrets`` knows is an error,
    except an ``assignment`` whose value reads like prose (``password: incorrect``): a warning."""
    text = normalize_for_scan(text)  # zero-width / fullwidth tricks must not hide an assignment
    out = [SecretMatch(kind, "error") for kind in find_secrets(text) if kind != "assignment"]
    strong = False
    weak = False
    for match in _ASSIGNMENT.finditer(text):
        before = match.group(0)[: match.start(1) - match.start(0)]
        separator = "=" if "=" in before else ":"
        if _strong_assignment(separator, match.group(1)):
            strong = True
        else:
            weak = True
    if strong:
        out.append(SecretMatch("assignment", "error"))
    elif weak:
        out.append(SecretMatch("assignment", "warn"))
    return out


def has_secret(text: str) -> bool:
    """True when ``text`` holds a secret of error strength (prose-like mentions do not count)."""
    return any(m.severity == "error" for m in secret_matches(text))


def no_secrets(spec: AppSpec, *, rendered: str | None = None) -> list[ValidatorFinding]:
    """Validator 9: scan every block (and the summary) of the rendered file with
    ``mde_core.redact`` patterns. Findings name the pattern and the block, never the value.
    A password or token *mentioned* in prose (``password: incorrect``) is a warning only."""
    text = rendered if rendered is not None else render_spec(spec)
    doc = tokenize(text)
    out: list[ValidatorFinding] = []
    scanned: list[tuple[str, str]] = [(raw.id, doc.block_text(raw)) for raw in doc.blocks]
    scanned.append(("SUMMARY", extract_summary(doc)))
    for block_id, block_text in scanned:
        for found in secret_matches(block_text):
            if found.severity == "error":
                message = (
                    f"Block {block_id} contains something that looks like a secret "
                    f"({found.kind}); remove it"
                )
            else:
                message = (
                    f"Block {block_id} mentions a password or token ({found.kind}); "
                    "check that no real value is written there"
                )
            out.append(finding(NAME, message, block_id, severity=found.severity))
    return out
