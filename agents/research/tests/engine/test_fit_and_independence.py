"""RC-4: a page about another country or company is flagged and kept out of corroboration, norms and prevalence;
the same paragraph on two domains is one source (RS-36, RS-37)."""

from __future__ import annotations

from datetime import date

from support.fakes import FakeWorld
from support.testing import atom, extractor, load_fixture, run_engine

from mde_research_agent.assemble.items import atom_fit_flags, evidence_of
from mde_research_agent.domain.plan import Place, Target
from mde_research_agent.engine.state import AtomRecord

STANDARD = "standard-produce-marketplace-nashik"
PAGE = "The tax authority requires every invoice to show a registration number within 7 days of the sale."


def world(url: str = "https://regulator.example/rule", text: str = PAGE) -> FakeWorld:
    w = FakeWorld(generate=False)
    w.route(r".", [w.add_page(url, text, published=date(2026, 8, 1))])
    return w


def rule_of(region: str, entity: str | None = None):
    def build(inp):  # type: ignore[no-untyped-def]
        return [
            atom(
                "rule",
                "requires every invoice to show a registration number within 7 days",
                key="invoice registration rule",
                claim="Every invoice must show a registration number within 7 days.",
                impact="high",
                about_region=region,
                about_entity=entity,
                statement="registration number on invoices",
                subject="invoice registration",
                value="7",
                unit="days",
                authority=None,
            )
        ]

    return extractor(build)


async def test_a_rule_from_another_country_is_flagged_open_and_unverified_rs36() -> None:
    run = await run_engine(load_fixture(STANDARD), world=world(), overrides={"rsch.extract.data": rule_of("ae")})
    rules = [i for i in run.research["items"] if i["kind"] == "rule"]
    assert rules
    assert all("other_region" in r["flags"] and r["evidence"] == "unverified" and r["tier"] == "open" for r in rules)
    assert all(r["about_region"] == "ae" for r in rules)
    assert run.research["flagged"]["other_region"] == len(rules)
    in_norms = {i for n in run.research["norms"] for i in n["item_ids"]}
    assert not ({r["id"] for r in rules} & in_norms)
    assert not run.violations  # RV-20 agrees


async def test_the_same_rule_for_the_plans_own_country_or_global_is_not_flagged() -> None:
    for region in ("in", "global", "unknown"):
        run = await run_engine(load_fixture(STANDARD), world=world(), overrides={"rsch.extract.data": rule_of(region)})
        rules = [i for i in run.research["items"] if i["kind"] == "rule"]
        assert rules and not any("other_region" in r["flags"] for r in rules), region


def test_atom_fit_flags_for_region_and_entity() -> None:
    def a(region: str, entity: str | None) -> AtomRecord:
        return AtomRecord(
            id="atm_1", kind="rule", job="data", target_id="T-01", norm_key="k", label="l", claim="c", quote="q",
            impact="high", published_on=None, data={}, page_id="pg_1", round=1, about_region=region, about_entity=entity,
        )

    t = Target(
        id="T-01", job="data", template_id="x", spec_need="n", lane="blueprint", label="l", question="q",
        decision_unlocked="d", anchors=("Acme",), intent=("rules",), needs=(), entity="Acme Payments",
        place=Place(country_code="in", country_name="India"),
    )
    assert atom_fit_flags(a("in", "Acme Payments Ltd"), t) == set()
    assert atom_fit_flags(a("global", None), t) == set()
    assert atom_fit_flags(a("pk", "Acme Payments"), t) == {"other_region"}
    assert atom_fit_flags(a("in", "Other Namesake Corp"), t) == {"other_entity"}
    assert atom_fit_flags(a("pk", "Other Namesake Corp"), t) == {"other_region", "other_entity"}


def test_the_evidence_ladder() -> None:
    kw = {"flags": set(), "independent_non_low": 1}
    assert evidence_of(verdicts=["supported"], classes=["official"], independent=1, **kw) == "official"
    assert evidence_of(verdicts=["supported"], classes=["press", "blog"], independent=2, flags=set(), independent_non_low=2) == "corroborated"
    assert evidence_of(verdicts=["supported"], classes=["press", "blog"], independent=2, flags=set(), independent_non_low=1) == "single_source"
    assert evidence_of(verdicts=["supported"], classes=["blog"], independent=1, **kw) == "single_source"
    assert evidence_of(verdicts=["partial"], classes=["official"], independent=1, **kw) == "unverified"
    assert evidence_of(verdicts=[], classes=["official"], independent=1, flags={"unchecked"}, independent_non_low=1) == "unverified"
    assert evidence_of(verdicts=["supported"], classes=["official"], independent=1, flags={"other_region"}, independent_non_low=1) == "unverified"
    assert evidence_of(verdicts=["supported"], classes=["press"], independent=1, flags={"number_widened"}, independent_non_low=1) == "unverified"
    assert evidence_of(verdicts=["supported"], classes=["press"], independent=2, flags={"number_widened"}, independent_non_low=2) == "corroborated"


async def test_a_paraphrase_is_a_second_source_but_the_same_paragraph_is_not_rs37() -> None:
    text = PAGE
    w = FakeWorld(generate=False)
    w.route(
        r".",
        [
            w.add_page("https://docs.alpha.example/rule", text, published=date(2026, 8, 1)),
            w.add_page("https://news.beta.example/story", "A different outlet explains that invoices need a registration number, and sellers have a week to add it after each sale.", published=date(2026, 8, 3)),
        ],
    )

    def build(inp):  # type: ignore[no-untyped-def]
        out = []
        for quote in (
            "requires every invoice to show a registration number within 7 days",
            "invoices need a registration number, and sellers have a week to add it",
        ):
            if quote.split()[0] in inp.passages:
                out.append(
                    atom("rule", quote, key="invoice registration rule", claim="Invoices need a registration number.",
                         impact="normal", about_region="in", statement="registration number", subject="invoice registration",
                         value=None, unit=None, authority=None)
                )
        return out

    run = await run_engine(load_fixture(STANDARD), world=w, overrides={"rsch.extract.data": extractor(build)})
    rules = [i for i in run.research["items"] if i["kind"] == "rule" and "invoice registration" in i["data"].get("subject", "")]
    assert rules and max(i["independent_domains"] for i in rules) == 2, [i["independent_domains"] for i in rules]
