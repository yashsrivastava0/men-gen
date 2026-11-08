"""Compatibility shims for KNOWN cross-package bugs found by this suite.

A shim lets the rest of the suite keep proving everything else while the bug is open. Each shim:

* is switched on only while a probe shows the bug is still there (``hash_compat="auto"``), so
  once the product is fixed the shim turns itself off and the matching strict ``xfail`` test in
  ``tests/e2e/test_known_bugs.py`` flips to XPASS (strict: it then FAILS, telling the lead to drop
  the ``xfail`` mark and this module);
* is named after the bug in the report (``BUG-1``, ...).

BUG-1 (hash rule): ``BuildResult.spec_hash`` / ``SpecVersionRecord.hash`` are defined by the spec
loader as sha256 of the file with the front-matter ``hash:`` line blanked, but ``FlowService`` (its
``_check_build``, ``gates.assert_handoff_allowed`` and the hand-off docs) treats the hash as
``sha256(spec_text)``. Every unit test of the flow used ``FakeSpecBuilder``, whose hash follows the
flow's rule, so only the real builder behind the real flow exposes it: the plan job fails with
"the builder's hash does not match its spec text" and no session ever reaches Gate 2.
"""

from __future__ import annotations

import asyncio
import tempfile
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from pathlib import Path

from mde_contracts.engines import BuildRequest, BuildResult, RunContext, SpecBuilderEngine
from mde_core.hashing import sha256_hex


class HashCompatBuilder:
    """Wraps a real ``SpecBuilderEngine`` and reports ``sha256(spec_text)`` as ``spec_hash``
    (what the flow checks), leaving the file itself, and its embedded loader-rule hash, untouched."""

    def __init__(self, inner: SpecBuilderEngine) -> None:
        self.inner = inner

    async def build(self, req: BuildRequest, ctx: RunContext) -> BuildResult:
        result = await self.inner.build(req, ctx)
        if result.spec_text is not None and result.spec_hash is not None:
            return result.model_copy(update={"spec_hash": sha256_hex(result.spec_text)})
        return result


async def _probe() -> bool:
    """True when the REAL builder behind the REAL flow cannot reach Gate 2 (the bug is open)."""
    from evals.harness.driver import FlowDriver
    from evals.harness.stack import StackOptions, build_stack

    with tempfile.TemporaryDirectory() as tmp:
        stack = build_stack(Path(tmp), StackOptions(hash_compat="off"))
        d = FlowDriver.for_user(stack)
        await d.start()
        await d.say("An online shop for handmade candles")
        for _ in range(14):
            if (await d.snapshot()).state != "DISCOVERING":
                break
            await d.tap_recommended()
        await d.decide("brief")
        await d.run_plan()
        return (await d.snapshot()).state != "PLAN_REVIEW"


@lru_cache(maxsize=1)
def hash_shim_needed() -> bool:
    """Run the probe once per process (in its own event loop, so it works inside a test)."""
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, _probe()).result()
