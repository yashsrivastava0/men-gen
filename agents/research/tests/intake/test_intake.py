import copy
import json
from pathlib import Path
from typing import Any

import pytest

from mde_research_agent.catalog import load_catalog
from mde_research_agent.intake import ExportRefused, read_export

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
CAT = load_catalog()
ALL = sorted((FIXTURES / "exports").glob("*.json")) + sorted(
    (FIXTURES / "synthetic").glob("*.json")
)
REAL_CLASSES = {
    "quick-yoga-studio-bengaluru": "focused",
    "standard-produce-marketplace-nashik": "standard",
    "thorough-freight-fleet-india": "deep",
}


def load(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / "exports" / f"{name}.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("path", ALL, ids=lambda p: p.stem)
def test_every_fixture_reads_into_a_valid_input(path: Path) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    inp = read_export(data, CAT)
    assert inp.brief_hash == data["brief"]["_hash"] and inp.load is not None
    assert inp.load.score >= 0 and inp.load.load_class in {"focused", "standard", "deep"}
    assert inp.program_id  # falls back to the session id when the Brief has none
    inp.model_dump_json()  # serialisable


@pytest.mark.parametrize(("name", "klass"), sorted(REAL_CLASSES.items()))
def test_the_three_real_runs_land_in_the_designed_classes(name: str, klass: str) -> None:
    assert read_export(load(name), CAT).load.load_class == klass  # type: ignore[union-attr]


@pytest.mark.parametrize("name", sorted(REAL_CLASSES))
def test_the_interview_tier_is_not_an_input(name: str) -> None:
    base = read_export(load(name), CAT)
    data = load(name)
    guidance = data["consumer_views"]["research"]["guidance"]
    guidance.update(
        {
            "size_band": "large",
            "depth": "deep",
            "search_budget": 99,
            "max_requests": 99,
            "gap_fill": True,
        }
    )
    meta = data["brief"]["meta"]
    meta["interview"].update(
        {"size_band": "large", "size_source": "tier", "budget": "thorough", "target": 99}
    )
    meta["plan"] = {"depth": "deep", "modules": 99}
    data["structure"]["band"] = "large"
    assert read_export(data, CAT).model_dump() == base.model_dump()


@pytest.mark.parametrize(
    ("mutate", "code"),
    [
        (lambda d: d["brief"].update(_hash="0" * 64), "hash_mismatch"),
        (lambda d: d["export"].update(brief_hash="f" * 64), "hash_mismatch"),
        (lambda d: d["export"].pop("approved_baseline"), "not_approved"),
        (lambda d: d["export"].update(format_version=1), "not_format_2"),
        (lambda d: d.pop("brief"), "not_an_export"),
        (lambda d: d["consumer_views"].pop("research"), "missing_research_view"),
    ],
)
def test_a_changed_or_unapproved_export_is_refused(mutate: Any, code: str) -> None:
    data = copy.deepcopy(load("quick-yoga-studio-bengaluru"))
    mutate(data)
    with pytest.raises(ExportRefused) as err:
        read_export(data, CAT)
    assert err.value.code == code and err.value.message


def test_quick_requests_are_template_text_and_the_plan_is_rebuilt() -> None:
    inp = read_export(load("quick-yoga-studio-bengaluru"), CAT)
    assert inp.quality.requests.template >= 4 and inp.quality.plan_source == "rebuilt"
    assert {f.call for f in inp.quality.failures} >= {"cmp.seed_research", "cmp.scan_ambiguity"}
    assert "request_origin_missing" in inp.quality.flags and "no_quality_block" in inp.quality.flags
    assert inp.geography.country_code is None and inp.geography.cities == (
        "Bengaluru",
    )  # country must be inferred later


def test_standard_keeps_its_degraded_flag_and_dedupes_the_payment_feature() -> None:
    inp = read_export(load("standard-produce-marketplace-nashik"), CAT)
    assert inp.quality.degraded and "design" in inp.quality.failing_probes
    assert any(f.duplicate_of for f in inp.features)
    assert inp.quality.requests.trusted == 7 and inp.quality.plan_source == "from_request"
    assert inp.geography.country_code == "in" and not inp.geography.uncertain
    assert any(r.is_app_name for r in inp.roles) and any(
        not a.is_real for a in inp.apps
    )  # "... side" as role and app


def test_thorough_traps_weak_geography_own_brand_duplicate_roles() -> None:
    inp = read_export(load("thorough-freight-fleet-india"), CAT)
    assert inp.geography.uncertain and inp.quality.geo_uncertain
    assert (
        inp.own_brand_terms
        and inp.references
        and inp.references[0].own_brand
        and inp.references[0].ambiguous
    )
    assert not any(
        inp.own_brand_terms[0].casefold() in n.casefold() for n in inp.hints.local_competitors
    )
    assert sum(1 for r in inp.roles if r.duplicate_of) >= 2
    assert inp.quality.structure_source in {"mixed", "fallback"}
    assert inp.sensitive_flags == ("personal_ids", "financial")
    assert any(f.is_template for f in inp.facts)
    assert inp.load.components.references == 0  # the own brand is not a named reference  # pyright: ignore[reportOptionalMemberAccess]


def test_junk_open_questions_never_reach_the_input() -> None:
    inp = read_export(load("thorough-freight-fleet-india"), CAT)
    blob = inp.model_dump_json().casefold()
    assert "pilot_small" not in blob


def test_locked_tier_comes_from_the_brief_and_defaults_are_marked() -> None:
    inp = read_export(load("quick-yoga-studio-bengaluru"), CAT)
    assert inp.geography.tier == "locked" and any(a.tier == "default" for a in inp.apps)
    assert all(f.tier in {"locked", "default", "open"} for f in inp.features)
