"""The two Compass readers agree (PS-1, D-175): ``mde_research_agent.intake`` (Research) and ``mde_compass_intake`` (Spec).

Spec's reader is a COPY of Research's, not an extraction, so this test is what keeps them in step. For each of the nine
exports in ``docs/compass/research-inputs/`` every field the two share must be equal (idea, geography, locales, seal,
audience, glossary, trust signals, flags, cleaning of features, roles and apps), and ``pack(research, need_card)`` must
give byte-identical output on both copies for the recorded Research outputs. If this fails after a change to either
copy, make the same change in the other one (``docs/research-agent/compass-contract.md`` lists what each reads).

This is the one place that may import both packages.
"""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path
from typing import Any

import pytest

from mde_compass_intake import ExportRefused as SpecRefused
from mde_compass_intake import normalise as spec_normalise
from mde_compass_intake import pack as spec_pack
from mde_compass_intake import read_export as spec_read
from mde_research_agent.catalog import load_catalog
from mde_research_agent.intake import ExportRefused as ResearchRefused
from mde_research_agent.intake import normalise as research_normalise
from mde_research_agent.intake import read_export as research_read
from mde_research_agent.pack import pack as research_pack

ROOT = Path(__file__).resolve().parents[2]
NINE = sorted((ROOT / "docs" / "compass" / "research-inputs").glob("*.json"))
OUTPUTS = ROOT / "docs" / "research-agent" / "outputs" / "claude-2026-11-13"
FORMATS = ROOT / "agents" / "research" / "tests" / "fixtures" / "formats"
OWN_FORMATS = ROOT / "packages" / "compass-intake" / "tests" / "fixtures" / "formats"
CAT = load_catalog()

# flags only the Spec reader raises (it reads the structure and the readiness of Spec, not the research load)
SPEC_ONLY_FLAGS = {"structure_missing"}


@cache
def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _ids(name: str) -> str:
    return Path(name).stem


def test_the_nine_inputs_are_all_there() -> None:
    assert len(NINE) == 9


@pytest.mark.parametrize("path", NINE, ids=_ids)
def test_the_adapter_agrees_on_every_shared_field(path: Path) -> None:
    data = load(path)
    r, s = research_normalise(data, CAT.languages), spec_normalise(data)
    assert s.brief_hash == r.brief_hash and s.brief_version == r.brief_version
    assert s.binding == r.binding and s.compass_format == r.compass_format
    assert (s.session_id, s.program_id) == (r.session_id, r.program_id)
    assert s.idea.model_dump() == r.idea.model_dump()
    assert (s.geo.city, s.geo.region, s.geo.country) == (r.geo.city, r.geo.region, r.geo.country)
    assert (s.geo.tier, s.geo.confidence, s.geo.slot_present) == (
        r.geo.tier,
        r.geo.confidence,
        r.geo.slot_present,
    )
    assert s.locale_codes == r.locale_codes and s.search_languages == r.search_languages
    assert (s.audience, s.glossary, s.declined) == (r.audience, r.glossary, r.declined)
    assert [f.model_dump() for f in s.focus] == [f.model_dump() for f in r.focus]
    assert (s.degraded, s.failing_probes, s.fallback_count) == (
        r.degraded,
        r.failing_probes,
        r.fallback_count,
    )
    assert [f.model_dump() for f in s.failures] == [f.model_dump() for f in r.failures]
    assert s.structure_source == r.structure_source
    assert s.flags == r.flags


@pytest.mark.parametrize(
    "path", sorted(FORMATS.glob("*.json")) + sorted(OWN_FORMATS.glob("*.json")), ids=_ids
)
def test_the_format_fixtures_agree_too(path: Path) -> None:
    data = load(path)
    r, s = research_normalise(data, CAT.languages), spec_normalise(data)
    assert (s.brief_hash, s.binding, s.compass_format, s.locale_codes, s.flags) == (
        r.brief_hash,
        r.binding,
        r.compass_format,
        r.locale_codes,
        r.flags,
    )
    assert s.idea.model_dump() == r.idea.model_dump()


def test_the_fixture_folders_hold_the_same_four_files() -> None:
    mine = {p.name: p.read_bytes() for p in OWN_FORMATS.glob("*.json")}
    theirs = {p.name: p.read_bytes() for p in FORMATS.glob("*.json")}
    assert mine == theirs


@pytest.mark.parametrize("path", NINE, ids=_ids)
def test_the_readers_agree_on_geography_locales_and_the_flags_they_share(path: Path) -> None:
    data = load(path)
    r, s = research_read(data, CAT), spec_read(data)
    assert s.geography.country == (r.geography.country or "")
    assert s.geography.country_code == r.geography.country_code
    assert [c.model_dump() for c in s.geography.countries] == [
        c.model_dump() for c in r.geography.countries
    ]
    assert s.geography.city == (r.geography.cities[0] if r.geography.cities else "")
    assert s.geography.region == (r.geography.region or "")
    assert (s.geography.uncertain, s.geography.uncertain_reason) == (
        r.geography.uncertain,
        r.geography.uncertain_reason,
    )
    assert s.locales.codes == r.locales.codes and s.locales.non_latin == r.locales.non_latin
    assert s.locales.search_languages == r.locales.search_languages
    assert (s.brief_hash, s.brief_version, s.input_binding) == (
        r.brief_hash,
        r.brief_version,
        r.quality.input_binding,
    )
    assert (s.trust.degraded, s.trust.probes, s.trust.structure_source) == (
        r.quality.degraded,
        r.quality.failing_probes,
        r.quality.structure_source,
    )
    assert [f.model_dump() for f in s.trust.failures] == [
        f.model_dump() for f in r.quality.failures
    ]
    assert set(s.flags) - SPEC_ONLY_FLAGS == set(r.quality.flags), (
        "the flags both readers raise are the same"
    )
    assert s.idea.model_dump() == r.idea.model_dump()
    assert s.audience == r.audience and s.glossary == r.glossary and s.declined == r.declined
    assert s.sensitive_data == r.sensitive_flags


@pytest.mark.parametrize("path", NINE, ids=_ids)
def test_the_readers_clean_features_roles_and_apps_the_same_way(path: Path) -> None:
    data = load(path)
    r, s = research_read(data, CAT), spec_read(data)
    # one deliberate difference: Spec's representative of merged duplicates keeps the strongest user-evidenced
    # priority (a user-evidenced must is never lost, SPC-IN-07), so priority is compared for the others only
    assert [(f.id, f.name, f.scope_status, f.duplicate_of) for f in s.features] == [
        (f.id, f.name, f.scope_status, f.duplicate_of) for f in r.features
    ]
    assert [(f.id, f.priority) for f in s.features if not f.alias_ids] == [
        (f.id, f.priority)
        for f in r.features
        if f.id in {x.id for x in s.features if not x.alias_ids}
    ]
    assert [
        (x.id, x.name, x.goal, x.duplicate_of, x.is_app_name) for x in s.roles if not x.assumed
    ] == [(x.id, x.name, x.goal, x.duplicate_of, x.is_app_name) for x in r.roles]
    assert [(a.id, a.name, a.is_real) for a in s.apps] == [
        (a.id, a.name, a.is_real) for a in r.apps
    ]
    assert [(m.id, m.name) for m in s.modules] == [(m.id, m.name) for m in r.modules]
    assert [i.name for i in s.integrations] == [i.name for i in r.integrations]
    assert [(f.id, f.kind, f.statement) for f in s.domain_facts] == [
        (f.id, f.kind, f.statement) for f in r.facts
    ]
    assert [(e.id, e.key, e.module) for e in s.structure.entities] == [
        (e.id, e.key, e.module or "") for e in r.entities
    ]


@pytest.mark.parametrize("path", NINE, ids=_ids)
def test_both_readers_refuse_the_same_files_with_the_same_code(path: Path) -> None:
    def no_brief(d: dict[str, Any]) -> None:
        d.pop("brief")

    def empty_brief(d: dict[str, Any]) -> None:
        d["brief"].update(idea={}, summary="", research_requests=[], domain_facts=[])
        d.pop("consumer_views", None)

    for mutate, code in ((no_brief, "not_an_export"), (empty_brief, "unusable_brief")):
        data = json.loads(json.dumps(load(path)))
        mutate(data)
        got = []
        for read in (lambda d: research_read(d, CAT), spec_read):
            with pytest.raises((ResearchRefused, SpecRefused)) as err:
                read(data)
            got.append(err.value.code)
        assert got == [code, code]


CARDS: list[dict[str, Any]] = [
    {"brief_ids": ["FTR-001"]},
    {"brief_ids": ["FTR-001", "FTR-002", "ENT-002"], "token_budget": 1500},
    {"brief_ids": ["INT:payments"], "lane": "integrations", "token_budget": 900},
    {"brief_ids": ["NEW"], "lane": "blueprint", "essentials_per_kind": 1, "token_budget": 2500},
    {"brief_ids": ["FTR-404"], "lane": "design"},
    {"brief_ids": ["FTR-003"], "kinds": ["competitor", "rule"], "token_budget": 500},
    {"brief_ids": [" FTR-001 ", "FTR-001", ""], "token_budget": 1},
]


def _outputs() -> list[Path]:
    return sorted(OUTPUTS.glob("*/research.json"))


@pytest.mark.parametrize("path", _outputs(), ids=lambda p: p.parent.name)
def test_pack_gives_identical_output_on_both_copies(path: Path) -> None:
    research = load(path)
    for card in CARDS:
        assert spec_pack(research, card) == research_pack(research, card), card
    # a card that names real Brief ids of this file
    ids = sorted(
        {a["id"] for i in research["items"] for a in i.get("applies_to", []) if a.get("id")}
    )[:6]
    for lane in (None, "blueprint", "integrations", "screens"):
        card = {"brief_ids": ids, "lane": lane, "token_budget": 2000}
        assert json.dumps(spec_pack(research, card), sort_keys=True) == json.dumps(
            research_pack(research, card), sort_keys=True
        )


def test_the_recorded_research_outputs_exist() -> None:
    assert len(_outputs()) >= 4
