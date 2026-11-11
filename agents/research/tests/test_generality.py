"""Nothing is hard-coded to the three real examples: every fixture runs, synthetic Briefs work, and a scan finds no example names."""

import re
from pathlib import Path

import pytest

from mde_research_agent.core.countries import countries
from support.testing import load_fixture, run_engine

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "tests" / "fixtures"
REAL = sorted(p.stem for p in (FIX / "exports").glob("*.json"))
ALL = REAL + sorted(p.stem for p in (FIX / "synthetic").glob("*.json"))
SRC = ROOT / "src" / "mde_research_agent"


@pytest.mark.parametrize("name", ALL)
async def test_every_fixture_runs_end_to_end_with_clean_validators(name: str) -> None:
    run = await run_engine(load_fixture(name))
    assert run.result.status in ("complete", "partial"), (
        name,
        run.result.status,
        run.result.reason,
    )
    assert run.research["items"] and run.research["slices"]["blueprint"]["digest"]
    assert not run.violations, [(v.rule, v.message) for v in run.violations]
    assert run.research["profile"]["load_class"] in ("focused", "standard", "deep")


@pytest.mark.parametrize(
    "name", ["dental-clinic-dubai-ar-en", "coffee-loyalty-nairobi-sw-en", "unit-converter-tiny"]
)
async def test_synthetic_briefs_in_other_domains_and_languages_give_valid_output(name: str) -> None:
    run = await run_engine(load_fixture(name))
    assert run.research["schema"] == "research.v4" and run.research["brief_hash"]
    countries_seen = {r.country for r in run.search.requests}
    if name.startswith("dental"):
        assert countries_seen <= {"ae", None}
        assert any(r.language == "ar" for r in run.search.requests), "Arabic is searched"
    if name.startswith("coffee"):
        assert countries_seen <= {"ke", None}
        assert any(r.language == "sw" for r in run.search.requests)
    if name.startswith("unit"):
        assert run.research["profile"]["load_class"] == "focused" and run.ctx.plan.place.unknown
        assert all(r.country is None for r in run.search.requests), "no place is invented"


def _identifiers() -> set[str]:
    """Strings that identify the three real examples: brands, cities and regions, named references and companies."""
    ids: set[str] = set()
    for name in REAL:
        d = load_fixture(name)
        b = d["brief"]
        view = d["consumer_views"]["research"]
        brand = (b.get("experience", {}).get("brand.name") or {}).get("value")
        if brand:
            ids.add(brand)
        geo = view.get("geography") or {}
        ids |= {v for k, v in geo.items() if k in ("city", "region") and v}
        ids |= {r["label"] for r in b.get("references", []) if r.get("label")}
        ids |= set(b.get("search_hints", {}).get("local_competitors", []))
    known_countries = {c["name"].lower() for c in countries().values()}
    return {i for i in ids if len(i) >= 4 and i.lower() not in known_countries}


def _scan_files() -> list[Path]:
    suffixes = (".py", ".yaml", ".jinja", ".json")
    files = [
        p
        for p in SRC.rglob("*")
        if p.is_file() and p.suffix in suffixes and "__pycache__" not in p.parts
    ]
    # legitimate reference data: official domains and country tables name places on purpose
    exempt = (
        SRC / "data" / "authority",
        SRC / "data" / "countries.yaml",
        SRC / "data" / "provider_prices.yaml",
    )
    return [p for p in files if not any(e == p or e in p.parents for e in exempt)]


def test_the_scan_finds_a_real_list_of_identifiers() -> None:
    ids = _identifiers()
    assert len(ids) >= 4, ids
    assert any(
        "sunrise" in i.lower() or "bengaluru" in i.lower() or "nashik" in i.lower() for i in ids
    ), ids


def test_no_example_brand_city_or_company_appears_in_source_data_or_prompts() -> None:
    ids = _identifiers()
    offenders = []
    for path in _scan_files():
        text = path.read_text(encoding="utf-8", errors="ignore")
        for ident in ids:
            if re.search(rf"(?<!\w){re.escape(ident)}(?!\w)", text, re.I):
                offenders.append((str(path.relative_to(ROOT)), ident))
    assert not offenders, offenders


async def test_a_brief_with_garbage_requests_still_runs() -> None:
    data = load_fixture("standard-produce-marketplace-nashik")
    junk = [
        {"id": "RR-001", "topic": "", "entity": "", "terms": ["x"] * 80},
        {"id": "RR-002"},
        {"id": "RR-003", "topic": "app apps app apps", "question": "app apps"},
    ]
    data["brief"]["research_requests"] = junk
    data["consumer_views"]["research"]["research_requests"] = junk
    run = await run_engine(data)
    assert run.research["input_quality"]["plan_source"] == "rebuilt"
    assert run.research["items"] and not run.violations


async def test_the_tier_never_changes_the_research() -> None:
    from mde_research_agent.core.hashing import content_hash

    a = await run_engine(load_fixture("quick-yoga-studio-bengaluru"))
    data = load_fixture("quick-yoga-studio-bengaluru")
    data["consumer_views"]["research"]["guidance"].update(
        {"size_band": "large", "depth": "deep", "search_budget": 99, "max_requests": 99}
    )
    data["brief"]["meta"]["interview"].update({"size_band": "large", "budget": "thorough"})
    b = await run_engine(data)
    assert content_hash(a.research) == content_hash(b.research)


def test_the_package_does_not_import_the_other_agents_or_the_older_research_engine() -> None:
    banned = re.compile(
        r"^\s*(from|import)\s+(mde_compass|mde_discovery|mde_spec_builder|mde_flow|mde_research\b)",
        re.M,
    )
    offenders = [
        str(p.relative_to(ROOT))
        for p in SRC.rglob("*.py")
        if banned.search(p.read_text(encoding="utf-8"))
    ]
    assert not offenders, offenders
