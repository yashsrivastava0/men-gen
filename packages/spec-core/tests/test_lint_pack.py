import json
import re

import pytest

from mde_spec_core.lint_pack import (
    lint_pack_dir,
    pack_from_files,
    read_pack_dir,
    write_pack_dir,
)
from mde_spec_core.pack import assemble
from mde_spec_core.synth import make_graph
from mde_spec_core.validators import Context, run_all


def blockers(findings):
    return [f for f in findings if f.severity == "blocker"]


@pytest.fixture
def pack_dir(tmp_path):
    g = make_graph("small")
    write_pack_dir(assemble(g), tmp_path)
    return tmp_path


def edit(root, rel, fn):
    p = root / rel
    p.write_bytes(fn(p.read_bytes().decode()).encode())


def details(findings):
    return {f.detail for f in findings if f.severity == "blocker"}


@pytest.mark.parametrize("name", ["tiny", "small", "medium", "big", "program"])
def test_a_clean_assembled_pack_has_no_blockers(tmp_path, name):
    g = make_graph(name)
    write_pack_dir(assemble(g), tmp_path)
    assert blockers(lint_pack_dir(tmp_path, graph=g)) == []


def test_round_trip_through_a_folder_keeps_every_byte(pack_dir):
    p = assemble(make_graph("small"))
    assert read_pack_dir(pack_dir) == p.files
    assert pack_from_files(read_pack_dir(pack_dir)).tasks == p.tasks


def test_a_changed_byte_is_a_manifest_mismatch(pack_dir):
    edit(pack_dir, "screens/SCR-002.md", lambda t: t.replace("purpose: The", "purpose: Tha", 1))
    f = lint_pack_dir(pack_dir)
    hit = [x for x in f if x.detail == "MANIFEST_HASH_MISMATCH"]
    assert [x.where for x in hit] == ["screens/SCR-002.md"] and hit[0].code == "G-1"


def test_package_hash_and_content_hash_checks(pack_dir):
    edit(
        pack_dir,
        "build/build-plan.json",
        lambda t: t.replace('"escalate_after": 2', '"escalate_after": 3', 1),
    )
    assert "PACKAGE_HASH_STALE" in details(lint_pack_dir(pack_dir))
    other = make_graph("tiny")
    assert "CONTENT_HASH_MISMATCH" in details(lint_pack_dir(pack_dir, graph=other))


def test_a_volatile_timestamp_in_a_body(pack_dir):
    edit(pack_dir, "global/rules.md", lambda t: t + "generated at 2026-11-14T10:00\n")
    f = lint_pack_dir(pack_dir)
    assert "VOLATILE_IN_BODY" in details(f)


def test_crlf_and_missing_final_newline_are_not_byte_stable(pack_dir):
    edit(pack_dir, "global/glossary.md", lambda t: t.replace("\n", "\r\n"))
    edit(pack_dir, "global/rules.md", lambda t: t.rstrip("\n"))
    f = [x for x in lint_pack_dir(pack_dir) if x.detail == "NOT_NORMALISED"]
    assert {x.where for x in f} >= {"global/glossary.md", "global/rules.md"}


def test_a_build_order_phrase(pack_dir):
    edit(pack_dir, "screens/SCR-003.md", lambda t: t + "Build this after SCR-002 is done.\n")
    f = [x for x in lint_pack_dir(pack_dir) if x.code == "G-4"]
    assert f and f[0].where == "screens/SCR-003.md"


def test_wizard_steps_and_given_when_then_are_allowed(pack_dir):
    edit(
        pack_dir,
        "screens/SCR-003.md",
        lambda t: (
            t
            + "wizard: step 1 of 3, then the next step\n  - GIVEN a user WHEN it pays THEN it is paid\n"
        ),
    )
    assert [x for x in lint_pack_dir(pack_dir) if x.code == "G-4"] == []


def test_an_unresolved_id_in_a_screen_file(pack_dir):
    text = (pack_dir / "screens/SCR-002.md").read_text()
    op = re.search(r"call (OP-\d+)", text).group(1)
    edit(
        pack_dir,
        "screens/SCR-002.md",
        lambda t: "\n".join(ln for ln in t.split("\n") if not ln.startswith(f"{op} ")),
    )
    f = [x for x in lint_pack_dir(pack_dir) if x.code == "G-6"]
    assert f and op in f[0].why and f[0].where == "screens/SCR-002.md"


def test_a_bound_field_missing_from_the_closure(pack_dir):
    def drop_field(t):
        return re.sub(r"(ENT-\d+ [^\n]*fields used: )[^;\n]*;? ?", r"\1", t, count=1)

    edit(pack_dir, "screens/SCR-002.md", drop_field)
    assert any(x.code == "G-6" for x in lint_pack_dir(pack_dir))


def test_a_task_over_budget(pack_dir):
    edit(
        pack_dir,
        "MANIFEST.json",
        lambda t: re.sub(r'"task_read_budget": \d+', '"task_read_budget": 3000', t),
    )
    f = lint_pack_dir(pack_dir)
    assert "TASK_OVER_BUDGET" in details(f)


def test_a_stale_task_total(pack_dir):
    edit(
        pack_dir,
        "build/build-plan.json",
        lambda t: re.sub(
            r'"total": (\d+)', lambda m: f'"total": {int(m.group(1)) + 5}', t, count=1
        ),
    )
    assert "TASK_TOTAL_STALE" in details(lint_pack_dir(pack_dir))


def test_a_key_in_a_pack(pack_dir):
    secret = "sk" + "-" + "q" * 30
    edit(pack_dir, "global/rules.md", lambda t: t + f"token: {secret}\n")
    f = [x for x in lint_pack_dir(pack_dir) if x.code == "V-20"]
    assert f and secret not in f[0].why and f[0].where == "global/rules.md"


def test_component_library_names_are_refused(pack_dir):
    edit(pack_dir, "screens/SCR-001.md", lambda t: t + "use Tailwind here\n")
    assert "LIBRARY_NAME" in details(lint_pack_dir(pack_dir))


def test_prefix_bundle_must_be_the_concatenation(pack_dir):
    edit(pack_dir, "prefix.bundle.md", lambda t: t.replace("# AGENTS", "# AGENTS EDITED", 1))
    assert "PREFIX_NOT_CONCAT" in details(lint_pack_dir(pack_dir))


def test_a_task_reading_a_second_prefix(pack_dir):
    def sneak(t):
        plan = json.loads(t)
        plan["tasks"][-1]["reads"]["global"].append("INDEX.md")
        return json.dumps(plan, sort_keys=True, indent=1) + "\n"

    edit(pack_dir, "build/build-plan.json", sneak)
    assert "TASK_PREFIX_DUP" in details(lint_pack_dir(pack_dir))


def test_copy_keys_must_be_namespaced_by_screen(pack_dir):
    edit(
        pack_dir,
        "screens/SCR-004.md",
        lambda t: t.replace("copy_keys: [", "copy_keys: [title, SCR-005.title, ", 1),
    )
    d = details(lint_pack_dir(pack_dir))
    assert {"COPY_KEY_NOT_NAMESPACED", "COPY_KEY_OTHER_OWNER"} <= d


def test_tiers_and_self_description(pack_dir):
    edit(pack_dir, "screens/SCR-001.md", lambda t: t.replace("tiers:", "tears:", 1))
    (pack_dir / "AGENTS.md").unlink()
    d = details(lint_pack_dir(pack_dir))
    assert "NO_TIERS" in d and "NO_AGENTS" in d


def test_missing_listed_file_and_missing_manifest(pack_dir):
    (pack_dir / "global/glossary.md").unlink()
    assert "LISTED_MISSING" in details(lint_pack_dir(pack_dir))
    (pack_dir / "MANIFEST.json").unlink()
    assert details(lint_pack_dir(pack_dir)) == {"NO_MANIFEST"}


def test_a_pack_read_from_a_folder_runs_the_validators(pack_dir):
    g = make_graph("small")
    pack = pack_from_files(read_pack_dir(pack_dir))
    assert [
        f
        for f in run_all(Context(g, pack=pack))
        if f.severity == "blocker" and f.code in ("V-26", "V-34")
    ] == []
