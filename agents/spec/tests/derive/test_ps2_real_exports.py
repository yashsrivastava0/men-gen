"""PS-2 on the three real exports (docs/compass/research-inputs): intake -> facts -> profile -> inventory -> release plan
-> seed graph -> pack.assemble -> validators. Their structure is taken as the proposal. Asserts the invariants and REPORTS
the real numbers (run with ``-s`` to see the table); where a real export differs from the doc's arithmetic the real number
stands (the doc arithmetic is the acceptance reference, never a number to tune to)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections import Counter
from functools import cache
from pathlib import Path

import pytest

from mde_compass_intake import read_export
from mde_spec_agent.derive import derive_graph
from mde_spec_agent.derive.seed import research_adapter
from mde_spec_core.pack import PackOptions, assemble
from mde_spec_core.validators.base import blockers, run_all
from mde_spec_core.validators.context import make_context

from .conftest import EXPORTS, THREE, best_research, load_export

HERE = Path(__file__).parent


@cache
def run(key: str, with_research: bool = False):
    si = load_export(THREE[key])
    rv = best_research(si) if with_research else None
    d = derive_graph(si, rv)
    pack = assemble(d.graph)
    ctx = make_context(
        d.graph,
        pack=pack,
        research=research_adapter(rv),
        degraded_locales=d.seeded.degraded_locales,
    )
    findings = run_all(ctx)
    return d, pack, findings


def row(key: str) -> dict:
    d, pack, findings = run(key)
    g = d.graph
    per_app = Counter(s.app_id for s in g.screens.values())
    bl = Counter(f.code for f in findings if f.severity == "blocker")
    wa = Counter(f.code for f in findings if f.severity == "warning")
    return {
        "export": key,
        "profile": d.decision.profile,
        "inventory": len(d.inventory.screens),
        "release": len(g.screens),
        "per_app": dict(sorted(per_app.items())),
        "later_by_reason": {k: len(v) for k, v in d.plan.later_by_reason().items()},
        "tasks": len(pack.tasks),
        "max_task_read": pack.budget_report["max_task_read"],
        "task_budget": pack.budget_report["task_budget"],
        "pack_tokens": pack.budget_report["total_tokens"],
        "blockers": dict(bl),
        "warnings": dict(wa),
    }


def test_profile_per_export() -> None:
    assert {k: run(k)[0].decision.profile for k in THREE} == {
        "quick": "compact",
        "standard": "standard",
        "thorough": "deep",
    }


def test_no_budget_breach_after_resharding_and_closure_is_complete() -> None:
    for key in THREE:
        _, pack, findings = run(key)
        codes = {f.code for f in blockers(findings)}
        assert not codes & {"V-26", "V-27"}, (key, codes)
        assert pack.budget_report["tasks_over_budget"] == []
        assert pack.budget_report["max_task_read"] <= pack.budget_report["task_budget"]


def test_determinism_validators_are_clean() -> None:
    for key in THREE:
        _, _, findings = run(key)
        assert not [f for f in findings if f.code in ("V-21", "V-34")], key


def test_the_prefix_is_identical_across_tasks() -> None:
    for key in THREE:
        _, pack, _ = run(key)
        assert {t["reads"]["prefix"] for t in pack.tasks} == {"prefix.bundle.md"}
        assert (
            len(
                {
                    t["read_tokens"].get("prefix", t["read_tokens"]["total"])
                    for t in pack.tasks
                    if "prefix" in t["read_tokens"]
                }
            )
            == 1
        )


def test_only_known_blockers_remain() -> None:
    allowed = {"quick": set(), "standard": {"V-19", "V-40"}, "thorough": set()}
    for key in THREE:
        _d, _, findings = run(key)
        assert {f.code for f in blockers(findings)} <= allowed[key], (
            key,
            list(blockers(findings)),
        )
    # the one Standard blocker is one Compass screen that serves no feature: it is named, not hidden
    _, _, findings = run("standard")
    assert {f.where for f in blockers(findings)} == {"SCR-003"}


def test_counts_against_doc_15_section_14() -> None:
    q, s, t = row("quick"), row("standard"), row("thorough")
    # Quick: the doc says 16; the real export gives 18 (notification settings and the settings screen are in the system set)
    assert 14 <= q["release"] <= 20 and q["later_by_reason"] == {}
    # Standard: the doc says 31; nothing deferred by capacity, FCT-030 (here FTR-008) live tracking is a user later
    assert 28 <= s["release"] <= 40 and "capacity" not in s["later_by_reason"]
    assert s["later_by_reason"] == {"user": 1}
    # Thorough: Dispatch is in Phase 1; the release is over several apps, each far below the cap; a backlog exists
    assert 60 <= t["release"] <= 80 and len(t["per_app"]) >= 4 and max(t["per_app"].values()) < 60
    assert t["later_by_reason"].get("first_release") and "capacity" not in t["later_by_reason"]


def test_report(capsys) -> None:
    rows = [row(k) for k in THREE]
    with capsys.disabled():
        print("\nPS-2 (real exports, zero model cost)")
        for r in rows:
            print(json.dumps(r, sort_keys=True))
    assert len(rows) == 3


def test_research_when_present_changes_nothing_that_is_unsafe() -> None:
    for key in THREE:
        si = load_export(THREE[key])
        if best_research(si) is None:
            continue
        _d, _pack, findings = run(key, True)
        assert not [
            f for f in blockers(findings) if f.code in ("V-26", "V-27", "V-36", "V-21", "V-34")
        ], key


def test_byte_stable_across_locale_and_hash_seed() -> None:
    names = [THREE[k] for k in THREE]
    outs = []
    for lang, seed in (("C", "1"), ("en_US.UTF-8", "2")):
        env = {**os.environ, "LC_ALL": lang, "LANG": lang, "PYTHONHASHSEED": seed}
        proc = subprocess.run(
            [sys.executable, "-I", str(HERE / "_ps2_probe.py"), str(EXPORTS), *names],
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=300,
        )
        assert proc.returncode == 0, proc.stderr[-2000:]
        outs.append(json.loads(proc.stdout.strip().splitlines()[-1]))
    assert outs[0] == outs[1]


def test_a_second_consumer_changes_only_sharding_and_task_cards() -> None:
    for key in THREE:
        d, pack, _ = run(key)
        other = assemble(
            d.graph, PackOptions(chars_per_token=2.4, task_read_budget=40000, consumer_id="second")
        )
        assert (
            other.content_hash == pack.content_hash
        )  # an approval binds the content, not the consumer
        assert other.package_hash != pack.package_hash
        changed = {
            p for p in set(pack.files) | set(other.files) if pack.files.get(p) != other.files.get(p)
        }
        # only re-sharding (contract resources, data-model shards), the manifest, the index and the task cards move
        fixed = {"MANIFEST.json", "index.json", "build/build-plan.json"}
        assert all(c in fixed or c.startswith(("contract/", "data-model/")) for c in changed), (
            key,
            sorted(changed),
        )
        # the screens (card plus closure) are the same bytes for either consumer
        assert all(pack.files[p] == other.files[p] for p in pack.files if p.startswith("screens/"))
        assert [t["read_tokens"]["total"] for t in other.tasks] != [
            t["read_tokens"]["total"] for t in pack.tasks
        ]


def test_the_exports_are_read_not_modified() -> None:
    before = json.loads((EXPORTS / f"{THREE['quick']}.json").read_text(encoding="utf-8"))
    read_export(before)
    assert before == json.loads((EXPORTS / f"{THREE['quick']}.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("key", list(THREE))
def test_every_screen_fits_a_one_screen_task(key) -> None:
    _, pack, _ = run(key)
    assert all(t["read_tokens"]["total"] <= 28000 for t in pack.tasks)


def test_all_nine_exports_derive_and_pack_without_a_budget_breach() -> None:
    """Format 1, a trimmed structure, synthetic format 2: the derivation never crashes and never breaks a budget."""
    paths = sorted(EXPORTS.glob("*.json"))
    if len(paths) < 9:
        pytest.skip("the nine exports are not all present")
    for path in paths:
        si = read_export(json.loads(path.read_text(encoding="utf-8")))
        d = derive_graph(si)
        pack = assemble(d.graph)
        ctx = make_context(d.graph, pack=pack, degraded_locales=d.seeded.degraded_locales)
        codes = {f.code for f in blockers(run_all(ctx))}
        assert not codes & {"V-26", "V-27", "V-21", "V-34", "V-03", "V-06", "V-07", "V-31"}, (
            path.stem,
            codes,
        )
        assert pack.budget_report["tasks_over_budget"] == [], path.stem
