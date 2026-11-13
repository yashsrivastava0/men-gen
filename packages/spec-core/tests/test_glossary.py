"""The glossary and the delight block (doc 18 5, FN-33): capped in tokens, deterministic, built by code."""

import pytest

from mde_spec_core.canon import canonical_json
from mde_spec_core.config import default_config
from mde_spec_core.glossary import build_glossary, delight_block, glossary_text
from mde_spec_core.graph import Could, Direction
from mde_spec_core.render import TokenCounter
from mde_spec_core.synth import make_graph

CFG = default_config()
COUNT = TokenCounter(CFG.budgets.token_factor_chars_per_token).count


def _direction():
    return Direction(
        id="DIR-001", name="d", mood="calm_trust", voice_line="Warm and plain, like a neighbour."
    )


@pytest.mark.parametrize("preset", ["tiny", "small", "medium", "big", "program", "erp"])
def test_glossary_stays_within_the_token_cap(preset):
    g = make_graph(preset)
    gl = build_glossary(g, _direction())
    assert COUNT(glossary_text(gl)) <= CFG.budgets.glossary_max
    assert gl["tokens"] == COUNT(glossary_text(gl))
    assert gl["voice"] == "Warm and plain, like a neighbour."


def test_glossary_names_entities_roles_statuses_and_verbs():
    g = make_graph("small")
    gl = build_glossary(g, _direction())
    assert gl["entities"]["ENT-001"]["term"] == g.entities["ENT-001"].name
    assert gl["entities"]["ENT-001"]["statuses"] == g.entities["ENT-001"].lifecycle.states
    assert set(gl["roles"]) == set(g.roles)
    assert gl["verbs"]
    assert {"pay", "confirm", "navigate"} & set(gl["verbs"])
    assert gl["dropped"] == []


def test_glossary_is_deterministic():
    g = make_graph("medium")
    assert canonical_json(build_glossary(g, _direction())) == canonical_json(
        build_glossary(g, _direction())
    )


def test_over_the_cap_drops_least_important_first_and_keeps_the_voice():
    g = make_graph("medium")
    cfg = CFG.model_copy(deep=True)
    cfg.budgets.glossary_max = 120
    gl = build_glossary(g, _direction(), config=cfg)
    assert COUNT(glossary_text(gl)) <= 120
    assert gl["dropped"], "something had to go"
    assert gl["dropped"][0].startswith("statuses of")  # statuses of minor entities go first
    primary = [e.id for e in g.entities.values() if e.klass == "primary"]
    assert all(p in gl["entities"] for p in primary)
    assert gl["voice"]


def test_a_tiny_cap_shortens_the_voice_as_a_last_resort():
    g = make_graph("tiny")
    cfg = CFG.model_copy(deep=True)
    cfg.budgets.glossary_max = 60
    long_voice = Direction(
        id="DIR-001", name="d", mood="calm_trust", voice_line=" ".join(["gentle"] * 80)
    )
    gl = build_glossary(g, long_voice, config=cfg)
    assert COUNT(glossary_text(gl)) <= 60
    assert "voice line shortened" in gl["dropped"]


def test_no_direction_gives_an_empty_voice():
    assert build_glossary(make_graph("tiny"), None)["voice"] == ""


# ------------------------------------------------------------------ delight block


def _could(cid, tier="A", kind="delight", effect="a calm moment that says what comes next"):
    return Could(
        id=cid,
        kind=kind,
        when="when the order is placed",
        effect=effect,
        basis="FLW-001 stage 2",
        basis_kind="journey_moment",
        tier=tier,
    )


def test_delight_block_collects_could_entries_of_primary_journeys_only():
    g = make_graph("small")
    g.screens["SCR-001"].could = [_could("CP-002"), _could("CP-001")]
    g.screens["SCR-002"].could = [_could("CP-003", tier="C")]  # not supportable: left out
    g.screens["SCR-009"].could = [_could("CP-009")]  # not on a primary journey
    text = delight_block(g)
    assert text.splitlines()[0].startswith("Delight guidance (COULD")
    assert "CP-001" in text and "CP-002" in text
    assert "CP-003" not in text and "CP-009" not in text
    assert text.index("CP-001") < text.index("CP-002")


def test_delight_block_is_capped_in_tokens():
    g = make_graph("small")
    g.screens["SCR-001"].could = [_could(f"CP-{i:03d}", effect="x" * 60) for i in range(40)]
    text = delight_block(g)
    assert COUNT(text) <= CFG.budgets.delight_block_max
    assert text.count("\n") >= 3
    cfg = CFG.model_copy(deep=True)
    cfg.budgets.delight_block_max = 30
    assert COUNT(delight_block(g, config=cfg)) <= 30


def test_delight_block_is_empty_without_entries_and_deterministic_with_them():
    g = make_graph("small")
    assert delight_block(g) == ""
    g.screens["SCR-001"].could = [_could("CP-001")]
    assert delight_block(g) == delight_block(g)
