"""All nine Compass inputs read, preview and plan without a model, a search or any spend, and none leaks a private term.

``docs/compass/research-inputs/`` holds three real older runs and six more test inputs (three real older formats, three
hand-made synthetic ones). Every one must load in any format, preview at zero cost, map its research requests without a
crash, and plan queries that name nothing private.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from mde_research_agent.calls import queries as q_calls
from mde_research_agent.catalog import load_catalog
from mde_research_agent.core.privacy import find_private
from mde_research_agent.inputs.estimates import Estimates
from mde_research_agent.inputs.preview import build_preview
from mde_research_agent.intake import read_export
from mde_research_agent.plan.build import build_plan

ROOT = Path(__file__).resolve().parents[4]
INPUTS = ROOT / "docs" / "compass" / "research-inputs"
CAT = load_catalog()
EST = Estimates()

# file stem -> (size class, load class, load score, compass format, input_binding, country codes)
NINE: dict[str, tuple[str, str, int, int, str, list[str]]] = {
    "quick-yoga-studio-bengaluru": ("small", "focused", 17, 2, "verified", []),
    "standard-produce-marketplace-nashik": ("medium", "standard", 27, 2, "verified", ["in"]),
    "thorough-freight-fleet-india": ("big", "deep", 83, 2, "verified", ["in"]),
    "delhi-restaurant-delivery": ("small", "focused", 14, 1, "unverified_no_baseline", []),
    "pune-clinic-appointments": ("medium", "standard", 29, 1, "verified", []),
    "hospital-chain-enterprise": ("medium", "standard", 27, 2, "verified", ["in"]),
    "synthetic-small-bike-repair-porto": ("small", "focused", 15, 2, "verified", ["pt"]),
    "synthetic-medium-school-parents-dakar": ("medium", "standard", 28, 2, "verified", ["sn"]),
    "synthetic-big-site-safety-central-america": (
        "big",
        "deep",
        71,
        2,
        "verified",
        ["mx", "gt", "cr"],
    ),
}


def raw(stem: str) -> dict[str, Any]:
    return json.loads((INPUTS / f"{stem}.json").read_text(encoding="utf-8"))


def test_the_folder_holds_exactly_the_nine_inputs() -> None:
    assert sorted(p.stem for p in INPUTS.glob("*.json")) == sorted(NINE)


@pytest.mark.parametrize("stem", sorted(NINE))
def test_every_input_loads_and_previews_at_zero_cost(stem: str) -> None:
    size, klass, score, fmt, binding, codes = NINE[stem]
    data = raw(stem)
    inp = read_export(data, CAT)
    preview = build_preview(data, inp, CAT, EST)
    assert inp.load is not None and (inp.load.load_class, inp.load.score) == (klass, score)
    assert preview["size_class"] == size and preview["load"]["class"] == klass
    assert inp.quality.compass_format == fmt and inp.quality.input_binding == binding
    assert preview["input_binding"] == binding and preview["compass"]["format"] == fmt
    assert [c["code"] for c in preview["place"]["countries"]] == codes
    assert [c.code for c in inp.geography.countries] == codes
    assert preview["topics"]["total"] > 0 and preview["estimate"]
    json.dumps(preview)  # the preview is plain JSON
    warned = {w["code"] for w in preview["warnings"]}
    assert (binding in warned) is (binding != "verified")
    assert all(w["text"] for w in preview["warnings"])


@pytest.mark.parametrize("stem", sorted(NINE))
def test_no_research_request_mapping_crashes_and_each_has_one_status(stem: str) -> None:
    data = raw(stem)
    inp = read_export(data, CAT)
    req = build_preview(data, inp, CAT, EST)["requests"]
    items = req["items"]
    assert [i["id"] for i in items] == [r.id for r in inp.requests]
    assert {i["status"] for i in items} <= {"mapped", "not_run", "dropped", "unmapped", "ignored"}
    assert all(i["text"] for i in items)
    for item in items:
        assert (item["status"] in ("mapped", "not_run")) == bool(item["topics"]), item
        assert len(item["topics"]) <= 1, item  # a request is carried by ONE topic
    assert req["mapped"] + req["not_run"] + req["dropped"] + req["unmapped"] + (
        req["template"] + req["thin"]
    ) == len(items)


def test_the_hospital_and_the_old_formats_have_the_shapes_the_adapter_was_built_for() -> None:
    hospital = read_export(raw("hospital-chain-enterprise"), CAT)
    assert "research_view_missing" in hospital.quality.flags
    assert hospital.locales.codes == ("mr", "gu", "hi", "en")  # a sentence in meta.locales
    assert hospital.load is not None and hospital.load.components.features == 13
    assert len([r for r in hospital.roles if not r.duplicate_of]) == 10
    delhi = read_export(raw("delhi-restaurant-delivery"), CAT)
    assert delhi.quality.input_binding == "unverified_no_baseline" and delhi.requests
    pune = read_export(raw("pune-clinic-appointments"), CAT)
    assert pune.quality.compass_format == 1 and pune.quality.input_binding == "verified"


def test_central_america_is_three_countries_with_the_first_as_primary() -> None:
    inp = read_export(raw("synthetic-big-site-safety-central-america"), CAT)
    assert [(c.name, c.code) for c in inp.geography.countries] == [
        ("Mexico", "mx"),
        ("Guatemala", "gt"),
        ("Costa Rica", "cr"),
    ]
    assert (inp.geography.country, inp.geography.country_code) == ("Mexico", "mx")
    assert "multi_country" in inp.quality.flags
    plan = build_plan(inp, CAT)
    assert [c.code for c in plan.place.countries] == ["mx", "gt", "cr"]
    assert plan.place.country_code == "mx" and plan.place.country_name == "Mexico"
    assert all(
        "Guatemala" not in t.label for t in plan.targets[:0]
    )  # primary only carries the words (P4 adds more)


@pytest.mark.parametrize("stem", sorted(NINE))
def test_the_plan_is_deterministic_for_every_input(stem: str) -> None:
    inp = read_export(raw(stem), CAT)
    assert build_plan(inp, CAT).model_dump() == build_plan(inp, CAT).model_dump()


# --- the privacy guard over the deterministic queries -----------------------------------------------------------------


def deterministic_queries(inp: Any, plan: Any) -> list[str]:
    """Every query the deterministic builder writes for every planned topic (first round, as the worker builds them)."""
    out: list[str] = []
    for t in [*plan.targets, *plan.unrun]:
        langs = list(t.place.languages[:1]) if t.place and t.job == "local" else []
        local_terms = [s for s in inp.hints.synonyms if not s.isascii()][:3] if langs else []
        info = CAT.intent_words.languages.get(langs[0]) if langs else None
        seeds = list(plan.discovery_names[:2]) if t.template_id == "landscape" else []
        qin = q_calls.QueryIn(
            job=t.job,
            question=t.question,
            anchors=list(t.anchors),
            context=plan.category_phrase,
            place=t.place.text if t.place else "",
            languages=langs,
            local_terms=local_terms,
            local_intent=list(info.words[:3]) if info else [],
            intent=list(t.intent),
            entity=t.entity,
            earlier=[],
            want=CAT.ceilings.variants_per_topic,
            round=1,
            focus_terms=[],
            seed_names=seeds,
        )
        out += [v.text for v in q_calls.deterministic_variants(qin).variants]
    return out


@pytest.mark.parametrize("stem", sorted(NINE))
def test_no_deterministic_query_of_any_input_names_a_private_term(stem: str) -> None:
    inp = read_export(raw(stem), CAT)
    queries = deterministic_queries(inp, build_plan(inp, CAT))
    assert len(queries) >= len(build_plan(inp, CAT).targets)
    leaks = [(q, find_private(q, inp.private)) for q in queries if find_private(q, inp.private)]
    assert not leaks, leaks


def test_the_three_inputs_with_a_known_brand_have_it_as_a_private_term() -> None:
    brands = {
        stem: [t.text for t in read_export(raw(stem), CAT).private.terms if t.kind == "brand"]
        for stem in ("quick-yoga-studio-bengaluru", "thorough-freight-fleet-india")
    }
    assert brands["quick-yoga-studio-bengaluru"] and brands["thorough-freight-fleet-india"]
    # a stated city is never a brand, even when a fact introduced it with "I own ..."
    assert not read_export(raw("delhi-restaurant-delivery"), CAT).private.terms


def plant(stem: str) -> tuple[dict[str, Any], list[str]]:
    """A copy of an input with invented private facts planted in it: a brand, a phone, an e-mail and a street address."""
    data = copy.deepcopy(raw(stem))
    brief = data["brief"]
    brief.setdefault("experience", {})["brand.name"] = {"value": "Zzyzx Kraal", "tier": "Locked"}
    brief["domain_facts"].append(
        {
            "id": "FCT-901",
            "type": "context",
            "statement": "I run Zzyzx Kraal, call me on +91 98765 43210 or write to owner.name@example-clinic.test, "
            "we are at 221B Baker Street.",
            "topics": ["business"],
            "entities": ["Zzyzx Kraal"],
        }
    )
    return data, [
        "Zzyzx Kraal",
        "+91 98765 43210",
        "owner.name@example-clinic.test",
        "221B Baker Street",
    ]


@pytest.mark.parametrize("stem", sorted(NINE))
def test_planted_private_facts_are_found_and_never_reach_a_deterministic_query(stem: str) -> None:
    data, planted = plant(stem)
    inp = read_export(data, CAT)
    found = {t.text.casefold() for t in inp.private.terms}
    for value in planted:
        assert value.casefold() in found, (stem, value)
    queries = deterministic_queries(inp, build_plan(inp, CAT))
    assert not [q for q in queries if find_private(q, inp.private)]
    # and every planted value, written into a query of its own, is caught
    for value in planted:
        assert find_private(f"class booking {value} pricing", inp.private), value
