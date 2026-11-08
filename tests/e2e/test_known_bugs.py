"""Cross-package bugs this suite found, pinned as strict ``xfail`` tests (never fixed from here).

Each test states the contract that is broken. When the product is fixed the test XPASSes, strict
mode turns that into a failure, and whoever fixed it removes the ``xfail`` mark (and, for BUG-1,
``evals/harness/shims.py``, which keeps every other test running while the bug is open).

The scenario files carry the others as ``known_issue`` variants (grep ``known_issue`` in
``tests/scenarios``): BUG-2 (research ignores references), BUG-3 (screen details never reach the
spec), BUG-4 (Plan view diff since Gate 1), BUG-5 (patch builds degrade to full builds), BUG-6
(an answer equal to the current value strands the session), GAP-1 .. GAP-5.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from evals.harness.checks import check_spec
from evals.harness.driver import FlowDriver
from evals.harness.journeys import GUIDED, play_journey
from evals.harness.stack import NetworkGuard, StackOptions, build_stack

pytestmark = pytest.mark.e2e


async def test_the_real_builder_reaches_gate_two_and_the_hash_follows_the_loader_rule(
    tmp_path: Path, socket_guard: NetworkGuard
) -> None:
    stack = build_stack(tmp_path / "projects", StackOptions(hash_compat="off"))
    assert not stack.shim_active
    driver = FlowDriver.for_user(stack)
    result = await play_journey(driver, GUIDED[0])
    checks = check_spec(result.spec, stack.policies("guided-default"))
    assert checks.record_hash_rule == "loader", "the record hash must be the file's embedded hash"
    assert checks.ok, checks.problems
