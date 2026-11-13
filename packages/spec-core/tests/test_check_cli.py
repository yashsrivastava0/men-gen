import io
import json

from mde_spec_core.__main__ import main, run_check
from mde_spec_core.lint_pack import write_pack_dir
from mde_spec_core.pack import assemble
from mde_spec_core.synth import make_graph


def run(argv):
    return main(argv)


def test_synth_presets_exit_zero(capsys):
    assert run(["check", "--synth", "tiny"]) == 0
    out = capsys.readouterr().out
    assert "RESULT: OK" in out and "spec check: OK" in out and "Contract v0" in out


def test_a_clean_folder_with_a_graph_beside_it_exits_zero(tmp_path, capsys):
    g = make_graph("small")
    pack = tmp_path / "pack"
    write_pack_dir(assemble(g), pack)
    (tmp_path / "graph.json").write_text(g.canonical_text(), encoding="utf-8")
    assert run(["check", str(pack)]) == 0
    assert "spec check: OK" in capsys.readouterr().out


def test_a_changed_byte_exits_one_and_says_it_in_plain_words(tmp_path, capsys):
    write_pack_dir(assemble(make_graph("small")), tmp_path)
    p = tmp_path / "screens" / "SCR-002.md"
    p.write_text(p.read_text().replace("purpose: The", "purpose: Tha", 1))
    assert run(["check", str(tmp_path)]) == 1
    out = capsys.readouterr().out
    assert "[BLOCKER] G-1 at screens/SCR-002.md" in out and "RESULT: BLOCKED" in out


def test_a_graph_with_a_missing_need_blocks_even_when_the_pack_is_clean(tmp_path, capsys):
    g = make_graph("small")
    pack = tmp_path / "pack"
    write_pack_dir(assemble(g), pack)
    g.behaviors.clear()
    (tmp_path / "graph.json").write_text(g.canonical_text(), encoding="utf-8")
    code = run(["check", str(pack), "--graph", str(tmp_path / "graph.json")])
    out = capsys.readouterr().out
    assert code == 1 and "BN-16" in out


def test_usage_errors_exit_two(tmp_path, capsys):
    assert run(["check"]) == 2
    assert run(["check", str(tmp_path / "nope")]) == 2
    assert run(["check", "--synth", "nonesuch"]) == 2


def test_run_check_writes_to_the_given_stream(tmp_path):
    g = make_graph("tiny")
    write_pack_dir(assemble(g), tmp_path)
    buf = io.StringIO()
    assert run_check(tmp_path, g, buf) == 0 and "Pack linter on" in buf.getvalue()
    json.loads(g.canonical_text())
