from __future__ import annotations

import re

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from mde_contracts.spec import AppSpec, BacklogItem
from mde_spec_builder.render import render_spec
from mde_spec_builder.testing import make_sample_spec
from mde_spec_loader import (
    SpecParseError,
    escape_summary,
    normalize_summary,
    parse_spec,
    split_blocks,
    unescape_summary,
)


def test_render_then_parse_gives_the_original(spec12: AppSpec, text12: str) -> None:
    assert parse_spec(text12) == spec12


def test_crlf_text_parses_the_same(spec12: AppSpec, text12: str) -> None:
    assert parse_spec(text12.replace("\n", "\r\n")) == spec12


def test_backlog_round_trips() -> None:
    spec = make_sample_spec(5).model_copy(
        update={"backlog": [BacklogItem(title="Gift cards", screens_hint=["gift list"])]}
    )
    parsed = parse_spec(render_spec(spec))
    assert parsed.backlog == spec.backlog
    assert "id=BACKLOG kind=backlog" in render_spec(spec)


def test_split_blocks_returns_the_exact_fenced_text(text12: str) -> None:
    blocks = split_blocks(text12)
    assert blocks["APP"].startswith("```yaml id=APP kind=app\n")
    assert blocks["APP"].endswith("\n```")
    assert blocks["HEADER"].startswith("---\n")
    assert "SCR-012" in blocks and "FLW-001" in blocks


# --------------------------------------------------------------------------- summary


LINES = st.one_of(
    st.text(max_size=40),
    st.sampled_from(
        [
            "## 2. App",
            "## 14. Assumptions and sources",
            "```yaml id=APP kind=app",
            "```",
            "~~~",
            "\\## 3. x",
            "\\",
            "### SCR-001 - /",
            "---",
            "",
        ]
    ),
)


@settings(max_examples=60, deadline=None)
@given(st.lists(LINES, max_size=8))
def test_any_summary_round_trips(lines: list[str]) -> None:
    summary = "\n".join(lines).replace("\r", " ")
    spec = make_sample_spec(3).model_copy(update={"summary": summary})
    parsed = parse_spec(render_spec(spec))
    assert parsed.summary == normalize_summary(summary)
    # the rest of the spec is untouched by the summary text
    assert parsed.screens == spec.screens and parsed.app == spec.app


def test_escape_and_unescape_are_inverse() -> None:
    text = "## 2. App\n```\n\\x\nplain\n## not a heading"
    assert unescape_summary(escape_summary(text)) == text
    assert escape_summary("## 2. App") == "\\## 2. App"
    assert escape_summary("## Not numbered") == "## Not numbered"


def test_normalize_summary() -> None:
    assert normalize_summary("\n\nhello\r\nworld\r\n\n") == "hello\nworld"


# --------------------------------------------------------------------------- errors


def _drop_block(text: str, block_id: str) -> str:
    pattern = re.compile(rf"```yaml id={re.escape(block_id)} kind=\w+\n.*?\n```\n", re.DOTALL)
    return pattern.sub("", text, count=1)


def test_missing_required_block(text12: str) -> None:
    with pytest.raises(SpecParseError, match="missing required block DESIGN"):
        parse_spec(_drop_block(text12, "DESIGN"))


def test_unknown_block_kind(text12: str) -> None:
    with pytest.raises(SpecParseError, match="unknown block kind 'widget'"):
        parse_spec(text12.replace("id=APP kind=app", "id=APP kind=widget"))


def test_block_id_must_match_its_kind(text12: str) -> None:
    with pytest.raises(SpecParseError, match="does not belong to kind"):
        parse_spec(text12.replace("id=APP kind=app", "id=NOPE kind=app"))


def test_backlog_block_needs_its_id(text12: str) -> None:
    broken = text12.replace(
        "```yaml id=REV kind=revision",
        "```yaml id=BAD kind=backlog\nitems: []\n```\n\n```yaml id=REV kind=revision",
    )
    with pytest.raises(SpecParseError, match="must have id BACKLOG"):
        parse_spec(broken)


def test_invalid_block_content(text12: str) -> None:
    with pytest.raises(SpecParseError, match="block SCR-001"):
        parse_spec(
            text12.replace(
                "```yaml id=SCR-001 kind=screen\n", "```yaml id=SCR-001 kind=screen\nbogus: 1\n"
            )
        )


def test_invalid_yaml_in_a_block(text12: str) -> None:
    with pytest.raises(SpecParseError, match="invalid YAML"):
        parse_spec(
            text12.replace("```yaml id=APP kind=app\n", "```yaml id=APP kind=app\nname: [oops\n")
        )


def test_front_matter_must_be_valid(text12: str) -> None:
    with pytest.raises(SpecParseError, match="front matter is invalid"):
        parse_spec(text12.replace("spec_version: 1\n", "", 1))
    with pytest.raises(SpecParseError, match="front matter must be a YAML mapping"):
        parse_spec("---\n- a\n- b\n---\n\n## 1. Summary\n\nx\n")


def test_summary_section_is_required(text12: str) -> None:
    with pytest.raises(SpecParseError, match="missing section 1"):
        parse_spec(text12.replace("## 1. Summary\n\n", ""))
