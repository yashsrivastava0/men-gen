"""Cross-check at engine level: sources and dates, the two-independent-sources rule,
deterministic ids, de-duplication, and what the synthesis may and may not do with facts."""

from __future__ import annotations

from research_helpers import (
    NOW,
    DirectMockGateway,
    ScriptedGateway,
    full_slots,
    make_context,
    make_engine,
    make_plan,
    make_request,
    ok_c7,
    s1_text,
)

from mde_contracts.calls import C8Input, C8Output, SuggestionDraft
from mde_contracts.research import ResearchNote

CARD = "Card data must stay with the payment provider and never touch the shop servers."
TEXT_A = (
    f"{CARD} ([x](https://example.com/a)) Another useful claim here ([y](https://example.org/b))."
)


def two_queries(job: str = "R4") -> object:
    return make_plan({job: ["first query words", "second query words"]})


def synth(*suggestions: SuggestionDraft) -> C8Output:
    return C8Output(suggestions=list(suggestions))


def draft(
    fact_ids: list[str], *, included: bool = True, title: str = "Handle card data safely"
) -> SuggestionDraft:
    return SuggestionDraft(
        kind="compliance",
        title=title,
        rationale="Similar apps leave card data to the provider.",
        fact_ids=fact_ids,
        default_included=included,
        applies_to="behavior",
    )


async def run_two(
    first: tuple[str, ...], second: tuple[str, ...], c7: tuple[object, object], c8: C8Output
) -> ResearchNote:
    gw = ScriptedGateway(fallback=DirectMockGateway())
    gw.script("C6.plan_research", two_queries())
    gw.script("S1.web_search", s1_text(TEXT_A, *first), s1_text(TEXT_A, *second))
    gw.script("C7.extract", *c7)
    gw.script("C8.synthesize_research", c8)
    return await make_engine(gw).run(make_request(slots=full_slots()), make_context()[0])


# --------------------------------------------------------------------------- two-source rule


async def test_a_high_impact_fact_with_one_source_is_unverified() -> None:
    note = await run_two(
        ("https://example.com/a",),
        ("https://example.org/b",),
        (ok_c7((CARD, "https://example.com/a"), impact="high", kind="compliance"), ok_c7()),
        synth(draft(["F-001"], included=True)),
    )
    (fact,) = note.facts
    assert fact.impact == "high"
    assert fact.verified is False
    assert fact.confidence == "low"
    (suggestion,) = note.suggestions
    assert suggestion.verified is False  # inherited from its facts
    assert suggestion.default_included is False  # an unverified suggestion is never on by default


async def test_two_sources_on_independent_domains_verify_a_high_impact_fact() -> None:
    note = await run_two(
        ("https://example.com/a",),
        ("https://example.org/b",),
        (
            ok_c7((CARD, "https://example.com/a"), impact="high", kind="compliance"),
            ok_c7((CARD, "https://example.org/b"), impact="high", kind="compliance"),
        ),
        synth(draft(["F-001"], included=True)),
    )
    (fact,) = note.facts
    assert fact.verified is True
    assert {s.url for s in fact.sources} == {"https://example.com/a", "https://example.org/b"}
    (suggestion,) = note.suggestions
    assert suggestion.verified is True
    assert suggestion.default_included is True


async def test_two_sources_on_the_same_registrable_domain_are_not_independent() -> None:
    note = await run_two(
        ("https://a.example.com/a",),
        ("https://b.example.com/b",),
        (
            ok_c7((CARD, "https://a.example.com/a"), impact="high", kind="compliance"),
            ok_c7((CARD, "https://b.example.com/b"), impact="high", kind="compliance"),
        ),
        synth(draft(["F-001"])),
    )
    (fact,) = note.facts
    assert len(fact.sources) == 2
    assert fact.verified is False


async def test_a_low_impact_fact_needs_just_one_source() -> None:
    note = await run_two(
        ("https://example.com/a",),
        ("https://example.org/b",),
        (
            ok_c7(("Shops often offer a wish list for saved products.", "https://example.com/a")),
            ok_c7(),
        ),
        synth(draft(["F-001"], title="Wish list")),
    )
    (fact,) = note.facts
    assert fact.verified is True
    assert note.suggestions[0].default_included is True


async def test_a_suggestion_is_only_as_verified_as_its_weakest_fact() -> None:
    note = await run_two(
        ("https://example.com/a",),
        ("https://example.org/b",),
        (
            ok_c7(("Shops often offer a wish list for saved products.", "https://example.com/a")),
            ok_c7((CARD, "https://example.org/b"), impact="high", kind="compliance"),
        ),
        synth(draft(["F-001", "F-002"], title="Mixed evidence")),
    )
    (suggestion,) = note.suggestions
    assert suggestion.fact_ids == ["F-001", "F-002"] or sorted(suggestion.fact_ids) == [
        "F-001",
        "F-002",
    ]
    assert suggestion.verified is False
    assert suggestion.default_included is False


# --------------------------------------------------------------------------- sources and dates


async def test_every_fact_has_a_source_url_and_a_retrieval_date() -> None:
    report = await make_engine(DirectMockGateway()).run_with_report(
        make_request(slots=full_slots()), make_context()[0]
    )
    assert report.note.facts
    for fact in report.note.facts:
        assert fact.sources
        for source in fact.sources:
            assert source.url.startswith("https://")
            assert source.retrieved_at == NOW.date()
            assert source.domain


async def test_identical_claims_from_two_results_are_merged_into_one_fact() -> None:
    claim = "Shops often offer a wish list for saved products."
    note = await run_two(
        ("https://example.com/a",),
        ("https://example.org/b",),
        (ok_c7((claim, "https://example.com/a")), ok_c7((claim, "https://example.org/b"))),
        empty_c8(),
    )
    assert len(note.facts) == 1
    assert len(note.facts[0].sources) == 2


def empty_c8() -> C8Output:
    return C8Output(suggestions=[])


# --------------------------------------------------------------------------- determinism


async def test_ids_and_hashes_are_deterministic() -> None:
    first = await make_engine(DirectMockGateway()).run(
        make_request(slots=full_slots()), make_context()[0]
    )
    second = await make_engine(DirectMockGateway()).run(
        make_request(slots=full_slots()), make_context()[0]
    )
    assert first == second
    assert first.hash == second.hash
    assert [f.id for f in first.facts] == [f"F-{i:03d}" for i in range(1, len(first.facts) + 1)]


async def test_suggestion_numbers_are_assigned_by_code_not_by_the_model() -> None:
    note = await run_two(
        ("https://example.com/a",),
        ("https://example.org/b",),
        (
            ok_c7(
                ("Shops often offer a wish list for saved products.", "https://example.com/a"),
                ("Guest checkout reduces drop-off for new shoppers.", "https://example.com/a"),
            ),
            ok_c7(),
        ),
        synth(draft(["F-001"], title="Wish list"), draft(["F-002"], title="Guest checkout")),
    )
    assert [s.id for s in note.suggestions] == ["RA-001", "RA-002"]


# --------------------------------------------------------------------------- synthesis validation


async def test_suggestions_are_deduplicated_capped_and_must_cite_real_facts() -> None:
    drafts = [draft(["F-001"], title=f"Idea number {i} for shops") for i in range(20)]
    drafts.append(draft(["F-001"], title="idea number 0 for shops!"))  # near duplicate of the first
    drafts.append(draft(["F-777"], title="Cites nothing real"))
    drafts.append(draft([], title="Cites nothing at all"))
    note = await run_two(
        ("https://example.com/a",),
        ("https://example.org/b",),
        (
            ok_c7(("Shops often offer a wish list for saved products.", "https://example.com/a")),
            ok_c7(),
        ),
        synth(*drafts),
    )
    assert len(note.suggestions) == 12  # the default cap
    titles = [s.title for s in note.suggestions]
    assert len(set(titles)) == 12
    assert "Cites nothing real" not in titles and "Cites nothing at all" not in titles


async def test_a_feature_the_brief_already_has_is_not_suggested_again() -> None:
    note = await run_two(
        ("https://example.com/a",),
        ("https://example.org/b",),
        (
            ok_c7(("Shops often offer a wish list for saved products.", "https://example.com/a")),
            ok_c7(),
        ),
        synth(
            SuggestionDraft(
                kind="feature",
                title="Payments again",
                rationale="Already in the brief.",
                fact_ids=["F-001"],
                applies_to="scope.features:payments",
            ),
            SuggestionDraft(
                kind="feature",
                title="Favorites",
                rationale="A wish list helps shoppers.",
                fact_ids=["F-001"],
                applies_to="scope.features:favorites",
            ),
        ),
    )
    assert [s.title for s in note.suggestions] == ["Favorites"]
    assert note.suggestions[0].applies_to == "scope.features:favorites"


async def test_applies_to_forms_are_normalised() -> None:
    cases = {
        "favorites": "scope.features:favorites",
        "scope.features": "scope.features",
        "scope.features:favorites": "scope.features:favorites",
        "scope.features:nonexistent": "scope.features",
        "screens": "screens",
        "auth.sso": "auth.sso",
        "nonsense target": None,
    }
    drafts = [
        SuggestionDraft(
            kind="feature",
            title=f"Idea {i}",
            rationale="A fine rationale.",
            fact_ids=["F-001"],
            applies_to=target,
        )
        for i, target in enumerate(cases)
    ]
    note = await run_two(
        ("https://example.com/a",),
        ("https://example.org/b",),
        (
            ok_c7(("Shops often offer a wish list for saved products.", "https://example.com/a")),
            ok_c7(),
        ),
        synth(*drafts),
    )
    assert [s.applies_to for s in note.suggestions] == list(cases.values())


async def test_c8_receives_verified_flags_and_source_urls() -> None:
    gw = ScriptedGateway(fallback=DirectMockGateway())
    gw.script("C6.plan_research", two_queries())
    gw.script(
        "S1.web_search",
        s1_text(TEXT_A, "https://example.com/a"),
        s1_text(TEXT_A, "https://example.org/b"),
    )
    gw.script(
        "C7.extract",
        ok_c7((CARD, "https://example.com/a"), impact="high", kind="compliance"),
        ok_c7(),
    )
    await make_engine(gw).run(make_request(slots=full_slots()), make_context()[0])
    (c8_in,) = gw.inputs("C8.synthesize_research")
    assert isinstance(c8_in, C8Input)
    (fact,) = c8_in.facts
    assert fact.id == "F-001" and fact.verified is False and fact.impact == "high"
    assert fact.source_urls == ["https://example.com/a"]
    assert c8_in.max_suggestions == 12 and c8_in.gap_round is False


async def test_compliance_pointers_need_a_flag_of_the_brief_and_a_known_url() -> None:
    from mde_contracts.calls import PointerDraft

    c8 = C8Output(
        suggestions=[],
        compliance=[
            PointerDraft(
                flag="financial_data", title="Card data basics", url="https://example.com/a"
            ),
            PointerDraft(flag="FINANCIAL_DATA", title="Duplicate", url="https://example.com/a"),
            PointerDraft(
                flag="health_data_privacy", title="Not flagged", url="https://example.com/a"
            ),
            PointerDraft(
                flag="precise_location", title="Unknown source", url="https://example.com/zzz"
            ),
        ],
    )
    note = await run_two(
        ("https://example.com/a",),
        ("https://example.org/b",),
        (ok_c7((CARD, "https://example.com/a"), impact="high", kind="compliance"), ok_c7()),
        c8,
    )
    assert [(p.flag, p.title, p.url) for p in note.compliance] == [
        ("financial_data", "Card data basics", "https://example.com/a")
    ]
    assert note.compliance[0].note == "Pointer only; not legal advice."


async def test_integration_notes_keep_a_source_only_when_it_is_a_fact_source() -> None:
    from mde_contracts.calls import IntegrationDraft

    c8 = C8Output(
        suggestions=[],
        integrations=[
            IntegrationDraft(
                provider="Pay provider",
                sdk_available=True,
                expo_compatible=None,
                caveats=["Needs a development build", "<b>x</b> https://evil.example"],
                source_url="https://example.com/a",
            ),
            IntegrationDraft(provider="pay provider", source_url=None),  # duplicate provider
        ],
    )
    note = await run_two(
        ("https://example.com/a",),
        ("https://example.org/b",),
        (ok_c7((CARD, "https://example.com/a")), ok_c7()),
        c8,
    )
    (item,) = note.integrations
    assert item.provider == "Pay provider"
    assert item.source is not None and item.source.url == "https://example.com/a"
    assert item.sdk_available is True and item.expo_compatible is None
    assert item.caveats == ["Needs a development build"]
