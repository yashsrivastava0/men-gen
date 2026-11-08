from __future__ import annotations

from pathlib import Path

import pytest

from mde_contracts.spec import SECTION_ORDER, AppSpec
from mde_spec_builder.render import render_spec
from mde_spec_builder.testing import make_sample_spec
from mde_spec_loader import SpecIndex, SpecParseError, UnknownIdError


def test_index_lists_sections_in_the_fixed_order(index12: SpecIndex) -> None:
    view = index12.index()
    assert [s.key for s in view.sections] == list(SECTION_ORDER)
    assert [s.number for s in view.sections] == list(range(16))
    assert view.spec_version == 1 and view.app_id == "app_sample" and len(view.hash) == 64
    screens = next(s for s in view.sections if s.key == "screens")
    assert screens.ids[0] == "SCR-001" and len(screens.ids) == 12


def test_section_table_in_the_file_matches_the_index(index12: SpecIndex, text12: str) -> None:
    view = index12.index()
    for section in view.sections:
        row = next(
            line for line in text12.splitlines() if line.startswith(f"| {section.number} | ")
        )
        cells = [c.strip() for c in row.strip("|").split("|")]
        assert int(cells[3]) == section.tokens, row
        assert cells[2].split(",")[0].split("..")[0].split(" ")[0] == section.ids[0], row


def test_block_entries_have_locations_and_sizes(index12: SpecIndex, text12: str) -> None:
    view = index12.index()
    by_id = {b.id: b for b in view.blocks}
    assert by_id["SCR-001"].kind == "screen" and by_id["SCR-001"].section == 10
    lines = text12.split("\n")
    entry = by_id["APP"]
    assert lines[entry.line_start - 1].startswith("```yaml id=APP kind=app")
    assert lines[entry.line_end - 1] == "```"
    assert all(b.tokens > 0 and b.chars > 0 for b in view.blocks)
    assert view.total_tokens >= sum(b.tokens for b in view.blocks)


def test_get_returns_exact_blocks(index12: SpecIndex, text12: str) -> None:
    got = index12.get(["SCR-003", "APP"])
    assert list(got) == ["SCR-003", "APP"]
    for text in got.values():
        assert text in text12 and text.startswith("```yaml id=")
    assert index12.get("APP")["APP"].endswith("\n```")
    assert index12.get(["HEADER"])["HEADER"].startswith("---\nspec_version: 1")
    assert (
        index12.get(["SUMMARY"])["SUMMARY"].startswith("Marketly")
        or "shop app" in index12.get(["SUMMARY"])["SUMMARY"]
    )


def test_get_carves_items_out_of_blocks(index12: SpecIndex) -> None:
    got = index12.get(
        ["ENT-Order", "API-getProduct", "ROLE-admin", "INT-payments", "NAV-003", "S5"]
    )
    assert got["ENT-Order"].startswith("```yaml id=ENT-Order kind=entity\n")
    assert "from: placed" in got["ENT-Order"]
    assert got["API-getProduct"].startswith("```yaml id=API-getProduct kind=operation")
    assert "kind=role" in got["ROLE-admin"] and "kind=integration" in got["INT-payments"]
    assert "kind=nav" in got["NAV-003"] and "children" not in got["NAV-003"]
    assert "kind=build_step" in got["S5"] and "stop_for_feedback: true" in got["S5"]


def test_get_unknown_id(index12: SpecIndex) -> None:
    with pytest.raises(UnknownIdError, match="SCR-999"):
        index12.get(["SCR-999"])
    assert index12.has("SCR-001") and index12.has("ENT-Product") and not index12.has("SCR-999")


def test_neighbors_and_refs_delegate_to_the_graph(index12: SpecIndex) -> None:
    assert "SCR-005" in index12.neighbors("SCR-006")
    assert "API-getProduct" in index12.refs("SCR-006").operations
    assert index12.refs("SCR-006", deep=False).entities == []
    with pytest.raises(UnknownIdError):
        index12.neighbors("SCR-999")
    with pytest.raises(UnknownIdError):
        index12.refs("SCR-999")
    assert index12.unresolved_refs() == []


def test_verify_and_tampering(index12: SpecIndex, text12: str) -> None:
    assert index12.verify()
    assert index12.verify(index12.header.hash)
    assert not index12.verify("0" * 64)
    assert not SpecIndex.from_text(text12.replace("Show one product", "Show a product")).verify()


def test_locked_screens_and_spec_tags(spec12: AppSpec) -> None:
    locked = spec12.model_copy(
        update={
            "screens": [
                s.model_copy(update={"locked": True}) if s.id == "SCR-002" else s
                for s in spec12.screens
            ]
        }
    )
    index = SpecIndex.from_text(render_spec(locked))
    assert index.locked_screens() == ["SCR-002"]
    assert index.spec_tag("SCR-014") == "@spec SCR-014@v1"


def test_from_path_reads_utf8_and_crlf(tmp_path: Path, spec12: AppSpec, text12: str) -> None:
    path = tmp_path / "APP_SPEC.md"
    path.write_bytes(text12.replace("\n", "\r\n").encode("utf-8"))
    index = SpecIndex.from_path(path)
    assert index.spec == spec12 and index.verify()
    assert "\r" not in index.text


def test_the_index_of_a_broken_file_fails_lazily() -> None:
    index = SpecIndex.from_text('---\nspec_version: 1\nhash: ""\n---\n\n## 1. Summary\n\nx\n')
    assert [b.id for b in index.document.blocks] == ["HEADER"]  # scanning works...
    with pytest.raises(SpecParseError):  # ...parsing the blocks does not
        _ = index.spec


def test_large_specs_index_quickly_and_completely(index50: SpecIndex) -> None:
    view = index50.index()
    assert sum(1 for b in view.blocks if b.kind == "screen") == 50
    assert make_sample_spec(50).screens[-1].id == "SCR-050"
