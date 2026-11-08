from __future__ import annotations

import hashlib
import re

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from mde_contracts.spec import SECTION_ORDER, AppSpec, BacklogItem, ScreenAction
from mde_spec_builder.render import (
    SECTION_TITLES,
    compute_spec_hash,
    embed_hash,
    render_spec,
    render_unhashed,
    spec_hash,
    verify_hash,
    with_rendered_hash,
)
from mde_spec_builder.testing import make_sample_spec
from mde_spec_loader import SpecIndex, estimate_tokens, normalize_summary, parse_spec

SIZES = st.integers(min_value=1, max_value=30)
SEEDS = st.integers(min_value=0, max_value=50)


@pytest.fixture(scope="module")
def spec() -> AppSpec:
    return make_sample_spec(14, seed=2)


@pytest.fixture(scope="module")
def text(spec: AppSpec) -> str:
    return render_spec(spec)


# --------------------------------------------------------------------------- structure


def test_sections_appear_once_in_the_fixed_order(text: str) -> None:
    headings = re.findall(r"^## (\d+)\. (.+)$", text, flags=re.MULTILINE)
    assert [int(n) for n, _ in headings] == list(range(16))
    assert [title for _, title in headings] == [SECTION_TITLES[key] for key in SECTION_ORDER]
    assert (
        text.index("## 10. Screens")
        < text.index("## 11. Flows")
        < text.index("## 12. Build sequence")
    )


def test_blocks_carry_id_and_kind_in_the_fence(text: str) -> None:
    fences = re.findall(r"^```yaml id=(\S+) kind=(\S+)$", text, flags=re.MULTILINE)
    kinds = dict(fences)
    assert kinds["APP"] == "app" and kinds["DESIGN"] == "design" and kinds["NAV"] == "navigation"
    assert kinds["DATA"] == "data" and kinds["API"] == "api" and kinds["ROLES"] == "roles"
    assert kinds["INTEGRATIONS"] == "integrations" and kinds["BEHAVIOR"] == "behavior"
    assert kinds["BUILD"] == "build_sequence" and kinds["ACCEPT"] == "acceptance"
    assert kinds["ASSUME"] == "assumptions" and kinds["REV"] == "revision"
    assert kinds["SCR-001"] == "screen" and kinds["FLW-001"] == "flow"
    assert len(fences) == len(kinds)  # ids are unique


def test_the_file_starts_with_front_matter_and_ends_with_one_newline(
    text: str, spec: AppSpec
) -> None:
    assert text.startswith("---\nspec_version: 1\nschema: mde.app-spec/1\napp_id: app_sample\n")
    assert text.endswith("\n") and not text.endswith("\n\n")
    assert "\r" not in text
    front = text.split("\n---\n", 1)[0]
    for key in ("mode: guided", "stack_profile: expo-managed", "inherits: []", "language: en"):
        assert key in front
    assert f"brief_hash: {spec.header.brief_hash}" in front
    assert f"research_note_hash: {spec.header.research_note_hash}" in front


def test_research_note_hash_is_omitted_when_there_is_none(spec: AppSpec) -> None:
    header = spec.header.model_copy(update={"research_note_hash": None})
    text = render_spec(spec.model_copy(update={"header": header}))
    assert "research_note_hash" not in text
    assert parse_spec(text).header.research_note_hash is None


def test_section_table_lists_ids_and_token_sizes(text: str) -> None:
    rows = {
        int(m.group(1)): (m.group(2), m.group(3), int(m.group(4)))
        for m in re.finditer(r"^\| (\d+) \| (.+?) \| (.+?) \| (\d+) \|$", text, flags=re.MULTILINE)
    }
    assert sorted(rows) == list(range(16))
    assert rows[0][:2] == ("Header and index", "HEADER")
    assert rows[10][1] == "SCR-001..SCR-014 (14)" and rows[11][1].startswith("FLW-001..")
    assert rows[4][1] == "NAV" and rows[14][1] == "ASSUME"
    # sizes are the deterministic estimate (bytes / 4) of the section text
    sections = {m.group(1): m for m in re.finditer(r"^## (\d+)\. ", text, flags=re.MULTILINE)}
    starts = [sections[str(i)].start() for i in range(1, 16)] + [len(text)]
    for number in range(1, 16):
        section_text = text[starts[number - 1] : starts[number]]
        assert rows[number][2] == estimate_tokens(section_text)


def test_table_agrees_with_the_loader_index(text: str) -> None:
    view = SpecIndex.from_text(text).index()
    for section in view.sections:
        pattern = rf"^\| {section.number} \| .+? \| .+? \| {section.tokens} \|$"
        assert re.search(pattern, text, flags=re.MULTILINE), section


def test_screens_and_flows_have_readable_headings(text: str) -> None:
    assert "### SCR-001 - /login" in text and "### SCR-006 - /product/[id]" in text
    assert re.search(r"^### FLW-001 - Browse and buy$", text, flags=re.MULTILINE)


def test_empty_collections_render_and_parse() -> None:
    base = make_sample_spec(3)
    empty = base.model_copy(update={"screens": [], "flows": []})
    rendered = render_spec(empty)
    assert "## 10. Screens\n\nNone." in rendered
    parsed = parse_spec(rendered)
    assert parsed.screens == [] and parsed.flows == []


def test_titles_with_line_breaks_are_kept_on_one_line(spec: AppSpec) -> None:
    app = spec.app.model_copy(update={"name": "Two\nLines"})
    text = render_spec(spec.model_copy(update={"app": app}))
    assert "# Two Lines - APP_SPEC" in text
    assert parse_spec(text).app.name == "Two\nLines"  # the block keeps the real value


# --------------------------------------------------------------------------- hash rule


def test_hash_is_sha256_of_the_text_with_the_hash_blanked(text: str, spec: AppSpec) -> None:
    blanked = re.sub(r'^hash: "[0-9a-f]{64}"$', 'hash: ""', text, count=1, flags=re.MULTILINE)
    digest = hashlib.sha256(blanked.encode("utf-8")).hexdigest()
    assert f'hash: "{digest}"' in text
    assert compute_spec_hash(text) == digest == spec_hash(spec)
    assert render_unhashed(spec) == blanked
    assert verify_hash(text)


def test_render_ignores_the_hash_field_of_the_input(spec: AppSpec) -> None:
    other = spec.model_copy(update={"header": spec.header.model_copy(update={"hash": "f" * 64})})
    blank = spec.model_copy(update={"header": spec.header.model_copy(update={"hash": ""})})
    assert render_spec(other) == render_spec(blank) == render_spec(spec)


def test_with_rendered_hash_makes_the_round_trip_exact(spec: AppSpec) -> None:
    blank = spec.model_copy(update={"header": spec.header.model_copy(update={"hash": ""})})
    stamped = with_rendered_hash(blank)
    assert stamped.header.hash == spec_hash(blank) and len(stamped.header.hash) == 64
    assert parse_spec(render_spec(blank)) == stamped
    assert make_sample_spec(14, seed=2) == spec  # the sample generator already stamps its spec


def test_embed_hash_is_idempotent(text: str) -> None:
    assert embed_hash(text) == text
    assert embed_hash(render_unhashed(make_sample_spec(3))) == render_spec(make_sample_spec(3))


def test_any_change_changes_the_hash(spec: AppSpec) -> None:
    other = spec.model_copy(update={"summary": spec.summary + " "})
    assert spec_hash(other) != spec_hash(spec)
    changed = spec.model_copy(
        update={
            "screens": [
                s.model_copy(update={"checks": [*s.checks, "x"]}) if s.id == "SCR-001" else s
                for s in spec.screens
            ]
        }
    )
    assert spec_hash(changed) != spec_hash(spec)


def test_a_tampered_file_does_not_verify(text: str) -> None:
    assert not verify_hash(text.replace("Show one product", "Show a product"))
    assert verify_hash(text.replace("\n", "\r\n"))  # CRLF checkouts still verify


# --------------------------------------------------------------------------- canonical YAML


def test_screen_block_is_canonical(text: str) -> None:
    block = text.split("```yaml id=SCR-006 kind=screen\n", 1)[1].split("\n```", 1)[0]
    lines = block.split("\n")
    assert lines[0] == "route: /product/[id]"  # id is in the fence, not the body
    assert "id: SCR-006" not in block
    assert any(line.startswith("copy: {") or line == "copy:" for line in lines)
    assert "texts" not in block
    assert "  - {bind:" in block or "  - bind: API-getProduct(id)" in block
    assert "- add_to_cart: {calls: API-addCartItem, on_success: toast + cart badge}" in block
    keys = [ln.split(":")[0] for ln in lines if ln and not ln.startswith((" ", "-"))]
    assert keys == sorted(
        keys,
        key=[
            "route",
            "recipe",
            "purpose",
            "flow",
            "data",
            "sections",
            "actions",
            "states",
            "copy",
            "checks",
            "notes",
        ].index,
    )


def test_defaults_are_omitted(text: str) -> None:
    block = text.split("```yaml id=SCR-001 kind=screen\n", 1)[1].split("\n```", 1)[0]
    assert "surface:" not in block and "locked:" not in block and "source:" not in block
    app = text.split("```yaml id=APP kind=app\n", 1)[1].split("\n```", 1)[0]
    assert "platforms" not in app and "language" not in app  # defaults


def test_free_form_maps_are_sorted(text: str) -> None:
    colors = text.split("  colors_light:\n", 1)[1].split("\n  colors_dark:", 1)[0]
    names = [line.strip().split(":")[0] for line in colors.split("\n")]
    assert names == sorted(names)


def test_state_machine_transitions_use_from(text: str) -> None:
    assert "from: placed" in text and "source:" not in text


def test_no_render_time_timestamps(text: str) -> None:
    assert not re.search(r"\d{2}:\d{2}:\d{2}", text)
    assert not re.search(r"\d{4}-\d{2}-\d{2}T", text)
    assert "generated at" not in text.lower()


def test_backlog_gets_its_own_block_inside_the_assumptions_section(spec: AppSpec) -> None:
    withlog = spec.model_copy(update={"backlog": [BacklogItem(title="Gift cards")]})
    text = render_spec(withlog)
    section = text.split("## 14. ", 1)[1].split("## 15. ", 1)[0]
    assert "id=ASSUME kind=assumptions" in section and "id=BACKLOG kind=backlog" in section
    assert "| 14 | Assumptions and sources | ASSUME, BACKLOG |" in text
    assert "BACKLOG" not in render_spec(spec)


def test_hindi_and_odd_characters_survive() -> None:
    base = make_sample_spec(4)
    screens = [
        s.model_copy(
            update={
                "purpose": "ग्राहक को उत्पाद दिखाएँ \u0085 और   पंक्ति",
                "notes": "line one\nline two\n",
                "actions": [ScreenAction(name="खोजें", go="SCR-001")],
            }
        )
        if s.id == "SCR-002"
        else s
        for s in base.screens
    ]
    spec = base.model_copy(update={"screens": screens})
    text = render_spec(spec)
    assert "ग्राहक को उत्पाद दिखाएँ" in text
    assert parse_spec(text) == with_rendered_hash(spec)


# --------------------------------------------------------------------------- properties


@settings(max_examples=25, deadline=None)
@given(SIZES, SEEDS)
def test_same_input_gives_identical_bytes(n: int, seed: int) -> None:
    first = make_sample_spec(n, seed=seed)
    second = make_sample_spec(n, seed=seed)
    assert first == second
    assert render_spec(first) == render_spec(second)
    # a structurally rebuilt copy renders to the same bytes too
    rebuilt = AppSpec.model_validate(first.model_dump(mode="python"))
    assert render_spec(rebuilt).encode("utf-8") == render_spec(first).encode("utf-8")


@settings(max_examples=25, deadline=None)
@given(SIZES, SEEDS)
def test_render_then_parse_gives_the_original(n: int, seed: int) -> None:
    spec = make_sample_spec(n, seed=seed)
    text = render_spec(spec)
    assert parse_spec(text) == spec
    assert verify_hash(text)
    assert spec.header.hash == compute_spec_hash(text)
    assert render_spec(parse_spec(text)) == text  # render o parse is the identity on rendered text


FREE_TEXT = st.text(max_size=50)  # any Unicode (no surrogates): control characters, NEL, emoji ...


@settings(max_examples=60, deadline=None)
@given(
    purpose=FREE_TEXT,
    notes=st.one_of(st.none(), FREE_TEXT),
    texts=st.dictionaries(FREE_TEXT.filter(bool), FREE_TEXT, max_size=4),
    checks=st.lists(FREE_TEXT, max_size=4),
    sections=st.lists(FREE_TEXT, max_size=4),
    states=st.dictionaries(
        FREE_TEXT.filter(bool),
        st.dictionaries(FREE_TEXT.filter(bool), FREE_TEXT, max_size=2),
        max_size=2,
    ),
    action_name=FREE_TEXT.filter(bool),
    on_success=st.one_of(st.none(), FREE_TEXT),
    route=FREE_TEXT.filter(bool),
    summary=FREE_TEXT,
    app_name=FREE_TEXT.filter(bool),
)
def test_arbitrary_text_in_any_field_round_trips(
    purpose: str,
    notes: str | None,
    texts: dict[str, str],
    checks: list[str],
    sections: list[str],
    states: dict[str, dict[str, str]],
    action_name: str,
    on_success: str | None,
    route: str,
    summary: str,
    app_name: str,
) -> None:
    base = make_sample_spec(3)
    screen = base.screens[1].model_copy(
        update={
            "purpose": purpose,
            "notes": notes,
            "texts": texts,
            "checks": checks,
            "sections": sections,
            "states": states,
            "route": route,
            "actions": [ScreenAction(name=action_name, go="SCR-001", on_success=on_success)],
        }
    )
    spec = base.model_copy(
        update={
            "screens": [screen if s.id == screen.id else s for s in base.screens],
            "summary": normalize_summary(summary),
            "app": base.app.model_copy(update={"name": app_name}),
        }
    )
    text = render_spec(spec)
    assert parse_spec(text) == with_rendered_hash(spec)
    assert verify_hash(text)
    assert render_spec(parse_spec(text)) == text


def test_different_seeds_give_different_specs() -> None:
    assert render_spec(make_sample_spec(12, seed=1)) != render_spec(make_sample_spec(12, seed=2))
