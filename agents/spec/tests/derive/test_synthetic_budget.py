"""The same pipeline tail (pack -> validators) on the synthetic preset graphs 'big' and 'program' (doc 19 2.7, 2.8 a):
every task card within the task read budget, no unrepaired breach, the prefix bundle identical across tasks."""

from __future__ import annotations

import pytest

from mde_spec_core.pack import assemble
from mde_spec_core.synth import make_graph
from mde_spec_core.validators.base import blockers, run_all
from mde_spec_core.validators.context import make_context


@pytest.mark.parametrize(("preset", "screens"), [("big", 60), ("program", 140)])
def test_budget_on_the_big_presets(preset, screens, capsys) -> None:
    g = make_graph(preset)
    assert len(g.screens) == screens
    pack = assemble(g)
    findings = run_all(make_context(g, pack=pack))
    assert not blockers(findings), [f.as_dict() for f in blockers(findings)]
    rep = pack.budget_report
    assert rep["tasks_over_budget"] == [] and rep["max_task_read"] <= rep["task_budget"]
    assert {t["reads"]["prefix"] for t in pack.tasks} == {"prefix.bundle.md"}
    # determinism: the same graph packs to the same bytes
    assert assemble(g).package_hash == pack.package_hash
    with capsys.disabled():
        print(
            f"\n{preset}: {len(g.screens)} screens, {rep['total_tokens']} tokens, {len(pack.tasks)} tasks, "
            f"max task read {rep['max_task_read']} of {rep['task_budget']}, tasks over budget {rep['tasks_over_budget']}"
        )
