import pytest

from mde_spec_core.callkit import (
    BypassCache,
    InMemoryResultCache,
    ResultCache,
    checkpoint_key,
    result_cache_key,
)

BASE = dict(
    tenant="t1",
    step="plan",
    input_hash="h" * 8,
    route_fingerprint="fp1",
    prompt_version="p1",
    schema_version="s1",
    library_version="l1",
)


def test_key_is_hex_and_stable():
    k = result_cache_key(**BASE)
    assert k == result_cache_key(**BASE)
    assert len(k) == 64 and int(k, 16) >= 0


@pytest.mark.parametrize("field", list(BASE))
def test_key_changes_when_any_component_changes(field):
    changed = {**BASE, field: BASE[field] + "x"}
    assert result_cache_key(**changed) != result_cache_key(**BASE)


def test_key_has_no_boundary_ambiguity():
    a = result_cache_key(**{**BASE, "step": "ab", "input_hash": "c"})
    b = result_cache_key(**{**BASE, "step": "a", "input_hash": "bc"})
    assert a != b


def test_empty_component_refused():
    with pytest.raises(ValueError):
        result_cache_key(**{**BASE, "route_fingerprint": ""})


def test_cache_never_shared_across_tenants():
    cache = InMemoryResultCache()
    assert isinstance(cache, ResultCache)
    key = result_cache_key(**BASE)
    cache.put("t1", key, {"v": [1, 2]})
    assert cache.get("t1", key) == {"v": [1, 2]}
    assert cache.get("t2", key) is None
    other = result_cache_key(**{**BASE, "tenant": "t2"})
    assert other != key
    cache.put("t2", other, {"v": 9})
    assert cache.get("t1", other) is None
    cache.clear_tenant("t1")
    assert cache.get("t1", key) is None and cache.get("t2", other) == {"v": 9}


def test_cached_value_cannot_be_mutated_through_a_copy():
    cache = InMemoryResultCache()
    cache.put("t", "k", {"a": [1]})
    got = cache.get("t", "k")
    got["a"].append(2)
    assert cache.get("t", "k") == {"a": [1]}


def test_route_change_misses_old_cache():
    cache = InMemoryResultCache()
    cache.put("t1", result_cache_key(**BASE), {"x": 1})
    assert cache.get("t1", result_cache_key(**{**BASE, "route_fingerprint": "fp2"})) is None


def test_bypass_cache_never_returns():
    c = BypassCache()
    c.put("t", "k", {"a": 1})
    assert c.get("t", "k") is None


CK = dict(
    program="prog",
    input_hash="ih",
    step="screens",
    batch=2,
    versions={"call_spec": "1", "prompt": "2", "schema": "3", "library": "4"},
    route_fingerprint="fp",
)


def test_checkpoint_key_carries_versions_and_batch():
    base = checkpoint_key(**CK)
    assert base == checkpoint_key(
        **{**CK, "versions": dict(reversed(list(CK["versions"].items())))}
    )
    for name in CK["versions"]:
        v = {**CK["versions"], name: "9"}
        assert checkpoint_key(**{**CK, "versions": v}) != base
    assert checkpoint_key(**{**CK, "batch": 3}) != base
    assert checkpoint_key(**{**CK, "batch": "2"}) == base
    assert checkpoint_key(**{**CK, "route_fingerprint": "other"}) != base
    assert checkpoint_key(**{**CK, "program": "p2"}) != base
