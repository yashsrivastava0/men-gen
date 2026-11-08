"""The hand-off guard must accept the hash a REAL built spec carries (BUG-1).

The spec file embeds its own hash (sha256 of the text with that value blanked). The flow's fake
builders used a plain sha256, so nothing exercised the real rule until the end-to-end harness ran
the real builder: with a plain-sha256 check no real session could ever reach ``spec_ready``.
"""

from __future__ import annotations

import pytest

from mde_contracts.gates import ApprovalRecord
from mde_core.errors import GateError
from mde_flow.gates import assert_handoff_allowed
from mde_spec_builder.render import render_spec
from mde_spec_builder.testing import make_sample_spec
from mde_spec_loader import parse_spec
from mde_spec_loader.hashing import verify_hash


def _approval(version: int, spec_hash: str) -> ApprovalRecord:
    return ApprovalRecord(
        id="apr_TEST0000000000000000000001",
        session_id="ses_TEST0000000000000000000001",
        gate="plan",
        version=version,
        hash=spec_hash,
        approver_id="usr_owner",
        role="requester",
        decision="approve",
        via="tap",
    )


def test_a_real_rendered_spec_passes_the_hand_off_guard() -> None:
    text = render_spec(make_sample_spec(6))
    spec_hash = parse_spec(text).header.hash
    assert spec_hash and verify_hash(text, spec_hash)
    assert_handoff_allowed(
        version=1,
        spec_hash=spec_hash,
        spec_text=text,
        roles=["requester"],
        approvals=[_approval(1, spec_hash)],
    )


def test_a_tampered_real_spec_is_refused() -> None:
    text = render_spec(make_sample_spec(6))
    spec_hash = parse_spec(text).header.hash
    tampered = text.replace("Home", "Hxme", 1)
    assert tampered != text
    with pytest.raises(GateError, match="does not match its hash"):
        assert_handoff_allowed(
            version=1,
            spec_hash=spec_hash,
            spec_text=tampered,
            roles=["requester"],
            approvals=[_approval(1, spec_hash)],
        )
