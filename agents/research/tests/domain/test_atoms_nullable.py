"""RV-01 must hold after the checker nulls a field the page does not state (Small run 2026-11-11: purpose null x4)."""

import pytest

from mde_research_agent.calls.extract import synthesize_payload
from mde_research_agent.catalog import load_catalog
from mde_research_agent.domain.atoms import PAYLOADS, validate_payload

LANES = load_catalog().lanes


@pytest.mark.parametrize("kind", sorted(PAYLOADS))
def test_every_field_the_checker_may_null_leaves_a_valid_payload(kind: str) -> None:
    """The checker nulls a scalar and empties a list; an item is dropped only when its kind's minimum fields are gone.
    Every other field must therefore accept that, or the committed item breaks its own schema."""
    data = synthesize_payload(kind, "A quoted sentence of the page.")
    assert validate_payload(kind, data) == []
    keep = set(LANES.kinds[kind].min_all)
    for name in data:
        if name in keep:
            continue
        nulled = {**data, name: [] if isinstance(data[name], list) else None}
        assert validate_payload(kind, nulled) == [], f"{kind}.{name} cannot be null"
