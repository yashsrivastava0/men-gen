import re
from decimal import Decimal
from pathlib import Path

import yaml

from mde_spec_core import profiles as profiles_module
from mde_spec_core.config import DATA_DIR
from mde_spec_core.profiles import load_profiles

ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def test_three_profiles_with_sources_and_dates():
    ps = load_profiles()
    assert [p.id for p in ps.profiles] == [
        "weak-default",
        "standard-default",
        "large-context-default",
    ]
    for p in ps.profiles:
        assert p.source_url.strip(), p.id
        assert ISO_DATE.match(p.checked_on), p.id
        assert p.notes.strip(), p.id
    assert ps.weakest().id == "weak-default"


def test_every_null_field_is_declared_unset():
    raw = yaml.safe_load((DATA_DIR / "consumer-profiles.yaml").read_text(encoding="utf-8"))
    for prof in raw["profiles"]:
        unset = prof.get("unset_fields", [])
        for field in (
            "nominal_context_tokens", "effective_context_tokens", "max_output_tokens",
            "output_tokens_per_second", "tokenizer_chars_per_token",
        ):  # fmt: skip
            if prof.get(field) is None:
                assert field in unset, (prof["id"], field)
        for group in ("limits", "schema_limits", "price"):
            vals = prof.get(group)
            if not vals:
                assert group in unset or any(u.startswith(group + ".") for u in unset), (
                    prof["id"],
                    group,
                )


def test_weak_default_matches_what_the_docs_and_the_repo_say():
    p = load_profiles().get("weak-default")
    assert p.capability_class == "weak"
    assert p.nominal_context_tokens == 1_000_000
    assert p.max_output_tokens == 128_000
    assert p.caching == "explicit_breakpoints"
    assert p.limits.requests_per_minute == 4000
    assert p.limits.input_tokens_per_minute == 2_000_000
    assert p.limits.output_tokens_per_minute == 400_000
    assert (
        p.schema_limits.max_union_params == 16 and p.schema_limits.max_nullable_fields == 16
    )  # D-172
    assert (
        p.effective_context_tokens is None
    )  # not measured: budgets fall back to half the nominal window
    assert p.effective_context() == 500_000


def test_weak_default_prices_agree_with_the_gateway_price_list():
    root = Path(__file__).resolve().parents[3]
    pricing = yaml.safe_load(
        (root / "packages/llm-gateway/src/mde_llm_gateway/pricing.yaml").read_text(encoding="utf-8")
    )
    row = pricing["models"]["claude-haiku-5-5"]
    price = load_profiles().get("weak-default").price
    assert price.input_usd_per_million == row["input_usd_per_million"]
    assert price.output_usd_per_million == row["output_usd_per_million"]
    assert price.cached_input_usd_per_million == row["cached_input_usd_per_million"]
    assert price.long_prompt_threshold_tokens == row["long_context"]["threshold_tokens"]
    assert price.long_prompt_multiplier == "5.0" == row["long_context"]["input_multiplier"]


def test_prices_are_exact_decimal_strings_not_floats():
    for p in load_profiles().profiles:
        for v in (
            p.price.input_usd_per_million,
            p.price.output_usd_per_million,
            p.price.cached_input_usd_per_million,
        ):
            if v is not None:
                assert isinstance(v, str) and Decimal(v) > 0
        if p.price.cached_input_usd_per_million and p.price.input_usd_per_million:
            assert Decimal(p.price.cached_input_usd_per_million) < Decimal(
                p.price.input_usd_per_million
            )


def test_other_profiles_carry_their_verified_numbers():
    ps = load_profiles()
    std = ps.get("standard-default")
    assert std.capability_class == "standard"
    assert std.nominal_context_tokens == 1_050_000 and std.max_output_tokens == 128_000
    assert std.caching == "automatic_prefix"
    assert std.price.long_prompt_threshold_tokens == 272_000
    big = ps.get("large-context-default")
    assert big.capability_class == "strong"
    assert big.nominal_context_tokens == 1_000_000
    assert (
        big.max_output_tokens is None
    )  # the page's "64K / 1M tokens" does not say which is the maximum
    assert big.limits.requests_per_minute is None  # not published on the page read


def test_profiles_module_names_no_model_vendor():
    text = Path(profiles_module.__file__).read_text(encoding="utf-8").lower()
    for name in ("openai", "anthropic", "claude", "gemini", "gpt-"):
        assert name not in text
