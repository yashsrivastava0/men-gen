"""Layering and vendor-independence checks by text: no vendor or model name, no Spec import, no model SDK."""

from __future__ import annotations

import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "mde_verify_agent"
FILES = [p for p in SRC.rglob("*") if p.is_file() and p.suffix in {".py", ".jinja"}]

# Vendor and model names, assembled so that this file does not itself contain them as plain words.
VENDORS = [
    "open" + "ai",
    "anth" + "ropic",
    "gem" + "ini",
    "gpt",
    "clau" + "de",
    "haiku",
    "sonnet",
    "opus",
    "llama",
    "mistral",
    "deepseek",
    "qwen",
    "grok",
    "cohere",
]
VENDOR_RE = re.compile(r"(?<![a-z])(" + "|".join(VENDORS) + r")(?![a-z])", re.IGNORECASE)


def test_sources_exist() -> None:
    assert len(FILES) >= 25


def test_no_vendor_or_model_name_anywhere_in_verify_sources() -> None:
    hits = [
        f"{p.relative_to(SRC)}:{n}: {line.strip()}"
        for p in FILES
        for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), start=1)
        if VENDOR_RE.search(line)
    ]
    assert hits == []


def test_verify_never_imports_the_spec_agent_nor_any_model_sdk() -> None:
    spec_agent = "mde_spec" + "_agent"
    sdk = re.compile(r"^\s*(from|import)\s+(open" + r"ai|anth" + r"ropic)\b", re.M)
    for p in FILES:
        if p.suffix != ".py":
            continue
        text = p.read_text(encoding="utf-8")
        assert spec_agent not in text, p
        assert not sdk.search(text), p


def test_verify_imports_only_core_contracts_gateway_spec_core_and_libraries() -> None:
    allowed = {"mde_core", "mde_contracts", "mde_llm_gateway", "mde_spec_core", "mde_verify_agent"}
    pat = re.compile(r"^\s*(?:from|import)\s+(mde_[a-z_]+)", re.M)
    for p in FILES:
        if p.suffix == ".py":
            assert set(pat.findall(p.read_text(encoding="utf-8"))) <= allowed, p


def test_the_gateway_is_imported_for_call_spec_types_only() -> None:
    """Engines reach a model only through the injected ModelCall: no gateway client or provider in Verify's code."""
    for p in FILES:
        if p.suffix != ".py":
            continue
        text = p.read_text(encoding="utf-8")
        assert "GatewayClient" not in text and "create_gateway" not in text, p
