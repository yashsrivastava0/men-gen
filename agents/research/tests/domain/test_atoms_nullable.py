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


def _sample(model: type) -> dict:
    """One valid element of a nested model: strings "x", the first value of a literal, true for a flag."""
    import typing

    out: dict = {}
    for name, info in model.model_fields.items():  # type: ignore[attr-defined]
        members = [info.annotation, *typing.get_args(info.annotation)]
        literal = next((m for m in members if typing.get_origin(m) is typing.Literal), None)
        if literal is not None:
            out[name] = typing.get_args(literal)[0]
        elif bool in members:
            out[name] = True
        else:
            out[name] = "x"
    return out


def test_nulling_an_attribute_inside_a_list_element_keeps_the_payload_valid() -> None:
    """features[0].level, states[0].content, transitions[0].trigger: the checker may null them. A REQUIRED attribute
    (feature_key, state, from_state ...) takes the whole element out instead, so the item never breaks its schema."""
    from mde_research_agent.domain.atoms import is_identifying
    from mde_research_agent.engine.checking import null_paths

    checked = 0
    for kind, model in sorted(PAYLOADS.items()):
        data = synthesize_payload(kind, "A quoted sentence of the page.")
        for name, info in model.model_fields.items():
            args = getattr(info.annotation, "__args__", ())
            nested = next((a for a in args if hasattr(a, "model_fields")), None)
            if nested is None:
                continue
            data[name] = [_sample(nested), _sample(nested)]
            assert validate_payload(kind, data) == [], (kind, name)
            for sub in nested.model_fields:
                out, applied = null_paths(
                    data,
                    [f"{name}[0].{sub}"],
                    identifying=lambda f, s, k=kind: is_identifying(k, f, s),
                )
                assert applied and validate_payload(kind, out) == [], f"{kind}.{name}[0].{sub}"
                if is_identifying(kind, name, sub):
                    assert len(out[name]) == 1, "a required attribute takes the element out"
                else:
                    assert len(out[name]) == 2 and out[name][0][sub] is None
                checked += 1
    assert checked >= 10
