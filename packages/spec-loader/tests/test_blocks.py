from __future__ import annotations

import pytest

from mde_spec_loader import SpecParseError, tokenize

MINIMAL = """---
spec_version: 1
hash: ""
---

# App

## 0. Header and index

table

## 1. Summary

Hello.

## 2. App

```yaml id=APP kind=app
name: x
```

## 10. Screens

### SCR-001 - /

```yaml id=SCR-001 kind=screen
route: /
```
"""


def test_tokenizes_front_matter_sections_and_blocks() -> None:
    doc = tokenize(MINIMAL)
    assert [b.id for b in doc.blocks] == ["HEADER", "APP", "SCR-001"]
    assert [s.number for s in doc.sections] == [0, 1, 2, 10]
    header = doc.block("HEADER")
    assert header is not None
    assert doc.block_text(header).startswith("---\nspec_version: 1")
    assert doc.block_text(header).endswith("\n---")
    app = doc.block("APP")
    assert app is not None
    assert doc.block_text(app) == "```yaml id=APP kind=app\nname: x\n```"
    assert doc.body_text(app) == "name: x\n"
    assert app.section == 2 and app.line_start == 18 and app.line_end == 20
    screens = doc.section(10)
    assert screens is not None and screens.key == "screens"
    assert doc.block("NOPE") is None and doc.section(7) is None


def test_sections_cover_the_whole_text_without_gaps() -> None:
    doc = tokenize(MINIMAL)
    assert doc.sections[0].start == 0
    for left, right in zip(doc.sections, doc.sections[1:], strict=False):
        assert left.end == right.start
    assert doc.sections[-1].end == len(MINIMAL)


def test_prose_fences_without_id_are_ignored_and_hide_headings() -> None:
    text = MINIMAL.replace("Hello.", "```text\n## 3. Not a heading\n```")
    doc = tokenize(text)
    assert [s.number for s in doc.sections] == [0, 1, 2, 10]


def test_a_missing_section_zero_heading_is_fine() -> None:
    text = MINIMAL.replace("## 0. Header and index\n\ntable\n\n", "")
    assert [s.number for s in tokenize(text).sections] == [0, 1, 2, 10]


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("# no front matter\n", "must start with a front-matter"),
        ("---\na: 1\n", "front matter is not closed"),
        (MINIMAL.removesuffix("```\n"), "is not closed"),  # the last block never closes
        (MINIMAL.replace("```yaml id=APP kind=app", "```yaml id=APP"), "needs both id= and kind="),
        (
            MINIMAL.replace("```yaml id=APP kind=app", "```yaml kind=app"),
            "needs both id= and kind=",
        ),
        (MINIMAL.replace("## 10. Screens", "## 1. Again"), "out of order"),
        (MINIMAL.replace("## 10. Screens", "## 16. Too far"), "out of order or unknown"),
        (
            MINIMAL.replace("id=SCR-001 kind=screen", "id=APP kind=screen"),
            "duplicate block id APP",
        ),
    ],
)
def test_malformed_files_raise(text: str, message: str) -> None:
    with pytest.raises(SpecParseError, match=message):
        tokenize(text)


def test_error_carries_the_line_number() -> None:
    with pytest.raises(SpecParseError) as info:
        tokenize(MINIMAL.replace("## 10. Screens", "## 1. Again"))
    assert info.value.line is not None and "line " in str(info.value)


def test_empty_block_body() -> None:
    doc = tokenize("---\na: 1\n---\n\n## 2. App\n\n```yaml id=APP kind=app\n```\n")
    app = doc.block("APP")
    assert app is not None and doc.body_text(app) == ""


def test_trailing_blanks_on_fence_and_heading_lines_are_not_part_of_the_value() -> None:
    doc = tokenize(
        "---\na: 1\n---\n\n## 1. Summary \t \n\n```yaml id=APP kind=app \t \nname: x\n```\n"
    )
    app = doc.block("APP")
    assert app is not None and app.kind == "app"
    assert doc.section(1) is not None and doc.section(1).title == "Summary"  # type: ignore[union-attr]
    blank = tokenize("---\na: 1\n---\n\n## 1.   \n")  # a heading whose title is only blanks
    assert blank.section(1) is not None and blank.section(1).title == " "  # type: ignore[union-attr]


def test_a_line_padded_with_blanks_is_tokenized_in_linear_time() -> None:
    """The old fence and heading patterns (a lazy group then optional blanks) were quadratic."""
    import time

    padded = " " * 60_000
    head = "---\na: 1\n---\n\n## 1. Summary\n"
    for line, sections in (
        (f"```!{padded}!\n```", [0, 1]),  # a fence line padded with blanks (a prose fence)
        (f"## 2. t{padded}x", [0, 1, 2]),  # a heading padded with blanks
        ("```" + "a=" * 30_000 + "\n```", [0, 1]),  # an info string that is one long word
    ):
        started = time.perf_counter()
        doc = tokenize(head + line + "\n")
        assert time.perf_counter() - started < 0.5
        assert [s.number for s in doc.sections] == sections
