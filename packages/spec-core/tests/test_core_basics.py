import pytest

from mde_spec_core.canon import canonical_json, normalise_text, sha256_json
from mde_spec_core.config import Budgets, ConfigError, default_config, load_config
from mde_spec_core.graph import Entity, Screen, SpecGraph
from mde_spec_core.ids import IdRegistry, format_id, namespaced_copy_key, parse_id
from mde_spec_core.profiles import load_profiles


def test_default_config_is_consistent():
    cfg = default_config()
    assert (
        cfg.budgets.prefix_cap
        + cfg.budgets.task_shards_cap
        + cfg.budgets.module_cap
        + cfg.budgets.slice_cap
        + cfg.budgets.front_end_allowance
        == cfg.budgets.task_read_budget
    )
    assert cfg.budgets.whole_pack_tokens == 22500
    assert cfg.money.cap_micro_usd == 5_000_000


def test_inconsistent_budget_is_refused(tmp_path):
    text = (
        (load_config.__globals__["DATA_DIR"] / "spec-config.yaml")
        .read_text()
        .replace("slice_cap: 5000", "slice_cap: 6000")
    )
    p = tmp_path / "c.yaml"
    p.write_text(text)
    with pytest.raises((ConfigError, ValueError)):
        load_config(p)


def test_budgets_model_rejects_sum_mismatch():
    cfg = default_config().budgets.model_dump()
    cfg["module_cap"] = 1
    with pytest.raises((ConfigError, ValueError)):
        Budgets(**cfg)


def test_canonical_bytes_are_stable():
    a = {"b": 1, "a": [3, 2, {"y": 1, "x": 2}]}
    b = {"a": [3, 2, {"x": 2, "y": 1}], "b": 1}
    assert canonical_json(a) == canonical_json(b)
    assert sha256_json(a) == sha256_json(b)
    assert normalise_text("a  \r\nb\r\n\r\n") == "a\nb\n"


def test_ids_namespaces_aliases_and_tombstones():
    reg = IdRegistry()
    reg.carry("SCR-029")
    new = reg.mint("SCR")
    assert new == "SCR-S001" and parse_id(new).spec_minted
    assert reg.mint("SCR") == "SCR-S002"
    with pytest.raises(ValueError):
        reg.carry("SCR-029")
    reg.alias("SCR-029", "SCR-S001")
    assert reg.resolve("SCR-029") == "SCR-S001"
    reg.tombstone("SCR-S002")
    assert reg.resolve("SCR-S002") is None
    assert format_id("SCR", 12, spec_minted=True) == "SCR-S012"
    assert namespaced_copy_key("SCR-S012", "pay_action") == "scr_s012.pay_action"


def test_graph_is_deterministic_and_rejects_layout_fields():
    g = SpecGraph()
    g.add("entities", Entity(id="ENT-001", name="Order"))
    g.add("screens", Screen(id="SCR-001", name="Home", app_id="APP-001", screen_type="list"))
    h1 = g.content_hash()
    g2 = SpecGraph.model_validate_json(g.model_dump_json())
    assert g2.content_hash() == h1
    with pytest.raises(ValueError):
        g.add("entities", Entity(id="ENT-001", name="dup"))
    with pytest.raises(ValueError):
        Screen(
            id="SCR-002", name="x", app_id="A", screen_type="list", layout="grid"
        )  # no layout field exists


def test_profiles_load_and_say_what_is_unset():
    ps = load_profiles()
    weak = ps.weakest()
    assert weak.id == "weak-default" and weak.schema_limits.max_nullable_fields == 16
    assert "max_output_tokens" in weak.unset_fields
