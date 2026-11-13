"""Vocabulary files: every file loads, counts match the design, cross-references resolve."""

import pytest
import yaml

from mde_spec_core import vocab
from mde_spec_core.config import default_config


def test_every_file_has_a_version():
    for name in vocab.VOCAB_FILES:
        raw = yaml.safe_load((vocab.VOCAB_DIR / f"{name}.yaml").read_text(encoding="utf-8"))
        assert isinstance(raw["version"], int), name


def test_every_vocab_file_on_disk_is_listed():
    on_disk = {p.stem for p in vocab.VOCAB_DIR.glob("*.yaml")}
    assert on_disk == set(vocab.VOCAB_FILES)


def test_cross_references_resolve():
    assert vocab.check_vocab() == []


def test_counts_match_the_design():
    assert len(vocab.shapes()) == 17
    assert len(vocab.intents()) >= 36
    assert len(vocab.screen_types()) == 21
    assert len(vocab.input_types()) == 13
    assert set(vocab.mock_profiles()) == {
        "empty",
        "typical",
        "busy",
        "extreme",
        "broken",
        "offline",
    }
    assert len(vocab.motion_intents()) == 16


def test_screen_types_are_exactly_doc_15_4_4():
    expected = [
        "auth",
        "onboarding",
        "list",
        "detail",
        "form",
        "wizard",
        "search",
        "dashboard",
        "calendar",
        "map",
        "chat",
        "media",
        "checkout",
        "confirmation",
        "status",
        "notifications",
        "profile",
        "settings",
        "report",
        "permission",
        "legal",
    ]
    assert set(vocab.screen_types()) == set(expected)


def test_no_hover_right_click_tooltip_breadcrumb_intent():
    for word in ("hover", "right_click", "tooltip", "breadcrumb"):
        assert not vocab.is_known_intent(word)
        assert vocab.is_phone_forbidden_intent(word)
    assert vocab.is_phone_forbidden_intent("Right-Click")
    assert all(i.phone_ok for i in vocab.intents().values())


def test_intents_flags_and_inputs():
    pay = vocab.intent("pay")
    assert pay and pay.needs_confirmation
    assert vocab.intent("enter_secret").input_type == "secret"  # type: ignore[union-attr]
    for name in ("sign_in", "toggle", "grant_permission", "adjust_quantity", "scan_code"):
        assert vocab.is_known_intent(name)


def test_graph_field_types_are_known():
    for t in [
        "text",
        "number",
        "money",
        "date",
        "phone",
        "email",
        "choice",
        "file",
        "location",
        "code",
        "secret",
        "quantity",
        "toggle",
    ]:
        assert vocab.is_known_input_type(t)
    for t in ("id", "enum", "list", "ref"):
        assert not vocab.is_known_input_type(t)
        assert vocab.is_known_input_type(t, field=True)


def test_shape_rules():
    assert vocab.is_known_shape("collection") and not vocab.is_known_shape("other:thing")
    assert vocab.shape_status("other:gantt_chart") == "other"
    assert vocab.shape_status("gantt") == "unknown"
    assert vocab.shape_status("other:Bad Name") == "unknown"
    assert vocab.check_region_shape("collection", None, ["columns=3", "selection=multi"]) == []
    assert vocab.check_region_shape("value", "metric") == []
    assert vocab.check_region_shape("value", "nonsense")
    assert vocab.check_region_shape("text", "short", ["live=sometimes"])
    assert vocab.check_region_shape("key_values", "plain")
    assert vocab.check_region_shape("gantt")
    assert vocab.check_region_shape("other:gantt", "x", ["y=z"]) == []


def test_cardinality_buckets():
    assert [vocab.cardinality_bucket(n) for n in (1, 2, 7, 8, 50, 51, 5000)] == [
        "one",
        "few",
        "few",
        "many",
        "many",
        "very_many",
        "very_many",
    ]


def test_state_kinds():
    base = [
        "loading",
        "empty",
        "error",
        "offline",
        "forbidden",
        "not_found",
        "stale",
        "partial",
        "success",
    ]
    assert set(vocab.state_kinds()) == set(base)
    assert vocab.is_known_state_kind("domain:conflict")
    assert not vocab.is_known_state_kind("domain:")
    assert not vocab.is_known_state_kind("domain:Bad Name")
    assert not vocab.is_known_state_kind("spinning")
    assert vocab.mock_profile_for_state("empty") == "empty"
    assert vocab.mock_profile_for_state("domain:x") == "broken"


def test_every_base_state_is_demonstrable_on_some_profile():
    shown = {s for p in vocab.mock_profiles().values() for s in p.demonstrates}
    assert set(vocab.state_kinds()) <= shown
    for kind, sk in vocab.state_kinds().items():
        assert kind in vocab.mock_profiles()[sk.demonstrated_on].demonstrates, kind


def test_mock_profiles_match_config_sizes():
    cfg = default_config()
    for profile, names in cfg.profiles.mock_profiles.items():
        assert set(names) <= set(vocab.mock_profiles()), profile


def test_motion_essential_flags():
    assert vocab.is_essential_motion("signal_error")
    assert not vocab.is_essential_motion("reward")
    assert {m.group for m in vocab.motion_intents().values()} == {
        "feedback",
        "orientation",
        "change",
    }


def test_asset_kinds_match_doc_17():
    expected = [
        "photo",
        "illustration",
        "icon",
        "avatar",
        "brand_mark",
        "animation_clip",
        "video",
        "audio_cue",
        "haptic_cue",
        "texture",
        "map_style",
    ]
    assert set(vocab.asset_kinds()) == set(expected)
    assert set(vocab.asset_kinds_file().source_policies) == {
        "user_upload",
        "generate",
        "licensed_stock",
        "none",
    }


def test_importance_matches_graph():
    assert vocab.region_importances() == ["primary", "supporting", "optional"]
    assert vocab.action_importances() == ["primary", "secondary"]


def test_direction_axes_allow_two_axis_differences():
    axes = vocab.direction_axes()
    for name in vocab.direction_axes_file().diff_axes:
        assert len(axes[name].values) >= 5, name
    a = {"mood_family": "calm_trust", "shape": "rounded", "density": "comfortable"}
    b = {"mood_family": "bold_energetic", "shape": "sharp", "density": "comfortable"}
    assert (
        len(vocab.direction_axis_differences(a, b))
        >= default_config().checks.min_direction_axis_differences
    )
    assert set(axes["ambition"].value_names) == {"refined", "expressive", "immersive"}


def test_direction_axes_cover_graph_defaults():
    from mde_spec_core.graph import Direction

    d = Direction(id="DIR-1", name="x", mood="calm_trust")
    axes = vocab.direction_axes()
    assert d.motion_personality in axes["motion_personality"].value_names
    assert d.depth in axes["depth"].value_names
    assert d.shape in axes["shape"].value_names
    assert d.density in axes["density"].value_names
    assert d.imagery in axes["imagery"].value_names
    assert d.type_personality in axes["type_personality"].value_names
    assert d.dark_stance in axes["dark_stance"].value_names
    assert d.ambition in axes["ambition"].value_names
    assert d.mood in axes["mood_family"].value_names


def test_behavior_categories():
    cats = vocab.behavior_categories()
    for name in ("interrupted_external_flow", "pending_timeouts", "session", "offline", "errors"):
        assert name in cats
    assert {c.supplied_by for c in cats.values()} == {"library", "mixed", "app_specific"}
    assert cats["pending_timeouts"].supplied_by == "app_specific"
    assert cats["errors"].supplied_by == "library"


def test_behavior_categories_by_profile_and_facts():
    compact = {c.name for c in vocab.behavior_categories_for("compact", {"accounts"})}
    assert {"session", "offline", "errors", "loading"} <= compact
    assert "lifecycle" not in compact and "pending_timeouts" not in compact
    deep = {
        c.name
        for c in vocab.behavior_categories_for(
            "deep", {"accounts", "provider_side_flow", "multi_language"}
        )
    }
    assert {
        "lifecycle",
        "pending_timeouts",
        "interrupted_external_flow",
        "i18n",
        "state",
        "media",
    } <= deep
    assert "notifications" not in deep


def test_predicates():
    assert vocab.evaluate_predicate("always", set())
    assert vocab.evaluate_predicate({"all": ["accounts", {"not": "multi_role_user"}]}, {"accounts"})
    assert not vocab.evaluate_predicate({"any": ["accounts", "payments"]}, {"realtime"})
    assert vocab.evaluate_predicate("accounts", {"accounts": True, "payments": False})
    assert vocab.check_predicate("nonsense")
    assert vocab.check_predicate({"all": []})
    assert vocab.check_predicate({"xor": ["accounts"]})
    assert vocab.check_predicate({"not": "accounts"}) == []


def test_normalise_word():
    assert vocab.normalise_word(" Right-Click ") == "right_click"


def test_vocab_version_is_a_stable_hash():
    first = vocab.vocab_version()
    assert first == vocab.vocab_version() and len(first) == 64


@pytest.mark.parametrize("name", vocab.VOCAB_FILES)
def test_no_yaml_boolean_surprises(name):
    # Plain words such as no, on, off or yes are booleans in YAML 1.1; none may appear as a key or a name.
    def walk(node):
        if isinstance(node, dict):
            for k, v in node.items():
                assert isinstance(k, str), f"{name}: non-string key {k!r}"
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(vocab._raw(name))
