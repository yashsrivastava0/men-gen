from mde_core.settings import Settings
from mde_llm_gateway.factory import route_fingerprint


def _s(routes: str) -> Settings:
    return Settings(model_routes=routes)  # type: ignore[arg-type]


def test_fingerprint_is_stable_opaque_and_changes_with_any_route():
    a = route_fingerprint(_s("claude-haiku-5-5"))
    assert a == route_fingerprint(_s("claude-haiku-5-5")) and len(a) == 24
    assert "claude" not in a and "haiku" not in a
    b = route_fingerprint(
        _s("fast=claude-haiku-5-5,standard=claude-haiku-5-5,deep=claude-sonnet-5-5")
    )
    assert a != b
    assert route_fingerprint(_s("gpt-6-luna")) != a


def test_fingerprint_includes_the_effort_floor():
    from mde_llm_gateway.pricing import Pricing

    base = {
        "version": 1,
        "models": {
            "m1": {
                "vendor": "openai",
                "input_usd_per_million": "1",
                "cached_input_usd_per_million": "1",
                "output_usd_per_million": "1",
            }
        },
    }
    floored = {"version": 1, "models": {"m1": {**base["models"]["m1"], "effort_floor": "medium"}}}
    s = _s("m1")
    assert route_fingerprint(s, Pricing.from_dict(base)) != route_fingerprint(
        s, Pricing.from_dict(floored)
    )
