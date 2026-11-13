import itertools

import pytest
from hypothesis import given
from hypothesis import strategies as st

from mde_research_agent.catalog import load_catalog
from mde_research_agent.core.anatomy import check_query, is_repeat
from mde_research_agent.core.passages import render, select_passages, windows
from mde_research_agent.core.quotes import verify_quote
from mde_research_agent.core.rrf import fuse
from mde_research_agent.core.sources import (
    can_support_high_impact,
    classify_source,
    independent_count,
    near_duplicate,
    org_key,
    shingles,
)
from mde_research_agent.ports.search import SearchHit

CAT = load_catalog()


def hit(url: str, rank: int, content: str | None = None, title: str = "") -> SearchHit:
    return SearchHit(url=url, title=title, rank=rank, content=content)


# --- rrf -------------------------------------------------------------------------------------------


def test_a_page_returned_by_two_variants_rises_and_urls_dedupe() -> None:
    a = [
        hit("https://a.com/x?utm_source=z", 1),
        hit("https://b.com/y", 2),
        hit("https://c.com/z", 3),
    ]
    b = [hit("https://www.c.com/z/", 1), hit("https://a.com/x", 4)]
    fused = fuse([a, b])
    assert [f.url for f in fused][:2] == [
        "https://c.com/z",
        "https://a.com/x",
    ]  # c: ranks 3 and 1, a: ranks 1 and 4
    assert fused[0].lists == 2 and len(fused) == 3


def test_fusion_prefers_the_copy_with_page_text() -> None:
    fused = fuse([[hit("https://a.com/x", 1)], [hit("https://a.com/x", 5, content="full text")]])
    assert fused[0].hit.content == "full text"


@given(
    st.lists(
        st.lists(st.sampled_from(["a", "b", "c", "d", "e"]), unique=True, max_size=5),
        min_size=1,
        max_size=4,
    )
)
def test_fusion_ignores_the_order_of_the_lists(lists: list[list[str]]) -> None:
    results = [[hit(f"https://{n}.com/p", i + 1) for i, n in enumerate(lst)] for lst in lists]
    base = [f.url for f in fuse(results)]
    for perm in itertools.islice(itertools.permutations(results), 6):
        assert [f.url for f in fuse(list(perm))] == base


# --- passages -------------------------------------------------------------------------------------


PAGE = " ".join(
    [f"Filler sentence number {i} about nothing in particular here." for i in range(120)]
    + ["Settlement for card payments takes two business days after capture."]
    + [f"More filler {i} that wanders off topic again and again." for i in range(120)]
    + ["Refunds are returned to the original payment method within five days."]
)


def test_a_short_page_is_returned_whole() -> None:
    ps = select_passages("Short page about payments.", ["payments"], max_chars=1000)
    assert len(ps) == 1 and ps[0].text == "Short page about payments."


def test_bm25_picks_the_windows_with_the_slot_terms_and_keeps_page_order() -> None:
    ps = select_passages(
        PAGE, ["settlement", "card payments", "refunds"], max_chars=1500, window_chars=300
    )
    text = render(ps)
    assert "Settlement for card payments" in text and "Refunds are returned" in text
    assert sum(p.end - p.start for p in ps) <= 1500 + 300
    assert [p.start for p in ps] == sorted(p.start for p in ps)
    assert all(
        PAGE[p.start : p.end] == p.text for p in ps
    )  # exact slices, so quotes verify in the page


def test_a_quote_copied_from_a_passage_verifies_in_the_page() -> None:
    ps = select_passages(PAGE, ["settlement"], max_chars=800, window_chars=300)
    assert verify_quote(
        PAGE, "Settlement for card payments takes two business days after capture"
    ).ok
    assert "Settlement for card payments" in ps[0].text or any(
        "Settlement for card" in p.text for p in ps
    )


def test_selection_is_deterministic() -> None:
    a = select_passages(PAGE, ["refunds", "settlement"], max_chars=1200)
    b = select_passages(PAGE, ["settlement", "refunds"], max_chars=1200)
    assert [(p.start, p.end) for p in a] == [(p.start, p.end) for p in b]


def test_windows_split_on_the_devanagari_danda() -> None:
    text = "पहला वाक्य। दूसरा वाक्य। " * 40
    assert len(windows(text, window_chars=100)) > 3


# --- sources --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "title", "entity", "expected"),
    [
        ("https://www.gst.gov.in/help", "", None, "official"),
        ("https://services.gst.gov.in/x", "", None, "official"),
        ("https://developer.apple.com/design/human-interface-guidelines/", "", None, "official"),
        ("https://docs.razorpay.com/api", "", None, "official"),
        (
            "https://razorpay.com/pricing",
            "",
            "Razorpay",
            "official_inferred",
        ),  # a provider's own site, by name
        ("https://razorpay.com/pricing", "", None, "blog"),
        ("https://help.example.com/article", "", None, "help"),
        ("https://apps.apple.com/in/app/x/id1", "", None, "store"),
        ("https://www.reddit.com/r/foo", "", None, "forum"),
        ("https://community.example.com/t/1", "", None, "forum"),
        ("https://medium.com/@x/post", "", None, "blog"),
        ("https://www.g2.com/products/x", "", None, "seo"),
        ("https://some-site.example/news", "Top 10 best apps in 2026", None, "seo"),
        ("https://www.reuters.com/article/1", "", None, "press"),
        ("https://unknown-company.example/about", "", None, "blog"),
    ],
)
def test_source_classes(url: str, title: str, entity: str | None, expected: str) -> None:
    assert classify_source(url, CAT, title=title, entity=entity) == expected


def test_forum_blog_and_seo_cannot_support_high_impact_claims() -> None:
    assert not any(can_support_high_impact(c, CAT) for c in ("forum", "blog", "seo"))  # type: ignore[arg-type]
    assert all(can_support_high_impact(c, CAT) for c in ("official", "help", "press", "store"))  # type: ignore[arg-type]


def test_authority_registry_is_scoped_by_country() -> None:
    assert classify_source("https://kra.go.ke/x", CAT, country="ke") == "official"
    assert classify_source("https://www.rbi.org.in/x", CAT, country="in") == "official"


def test_independent_domains_merge_subdomains_and_organisations() -> None:
    urls = [
        "https://docs.stripe.com/a",
        "https://stripe.com/b",
        "https://stripe.dev/c",
        "https://razorpay.com/d",
    ]
    assert independent_count(urls) == 2
    assert org_key("https://www.ey.co.in/x") == org_key("https://ey.com/y") == "ey"


def test_near_duplicate_pages_count_once() -> None:
    text = (
        "The platform supports instant refunds for card payments and settles funds in two days " * 3
    )
    urls = ["https://a.example/1", "https://b.example/2", "https://c.example/3"]
    assert (
        independent_count(
            urls,
            texts=[
                text,
                text + " extra",
                "totally different words about another topic entirely here",
            ],
        )
        == 2
    )
    assert near_duplicate(shingles(text), shingles(text))
    assert not near_duplicate(shingles(text), shingles("nothing alike at all in these few words"))


@given(
    st.lists(
        st.sampled_from(["a.com", "b.com", "c.co.in", "d.org", "www.a.com"]), min_size=1, max_size=8
    )
)
def test_independent_count_never_exceeds_distinct_hosts_and_is_monotone(hosts: list[str]) -> None:
    urls = [f"https://{h}/p{i}" for i, h in enumerate(hosts)]
    n = independent_count(urls)
    assert 1 <= n <= len({h.removeprefix("www.") for h in hosts})
    assert independent_count([*urls, "https://new-unique-site.net/x"]) >= n


# --- anatomy --------------------------------------------------------------------------------------

INTENT = [w for lang in CAT.intent_words.languages.values() for w in lang.words]
GENERIC = CAT.intent_words.generic_words
ARGS = {
    "anchors": ["yoga studio", "class packs"],
    "places": ["Bengaluru", "India"],
    "intent_words": INTENT,
    "generic_words": GENERIC,
    "own_brand": ["Sunrise Yoga"],
}


def test_a_query_that_names_an_entity_needs_no_place() -> None:
    """The gap analyst's best follow-ups ("<provider> API webhook documentation") were dropped for lacking the city."""
    q = "Exotel SMS WhatsApp API webhook documentation"
    assert "no_place" in check_query(q, **{**ARGS, "anchors": ["Exotel"]}).problems
    assert check_query(q, **{**ARGS, "anchors": ["Exotel"]}, named=["Exotel"]).ok
    # naming something else does not excuse the place
    assert (
        "no_place" in check_query(q, **{**ARGS, "anchors": ["Exotel"]}, named=["Razorpay"]).problems
    )


@pytest.mark.parametrize(
    "query",
    [
        "yoga studio app features Bengaluru",
        "class packs pricing Bengaluru yoga studio",
        '"class packs" yoga studio Bengaluru fees',
    ],
)
def test_good_queries_pass(query: str) -> None:
    assert check_query(query, **ARGS).ok, check_query(query, **ARGS)


@pytest.mark.parametrize(
    ("query", "problem"),
    [
        ("yoga studio app", "no_place"),  # category only
        ("yoga studio Bengaluru", "no_intent"),
        ("features Bengaluru pricing", "no_anchor"),
        ("Sunrise Yoga", "own_brand_alone"),
        ("Sunrise Yoga Bengaluru features", "no_anchor"),  # the brand is never an anchor
        ("yoga studio features Bengaluru https://example.com/x", "url"),
        ("yoga studio features Bengaluru www.example.com", "url"),
        ("yoga studio features Bengaluru tvly-abcdefghijklmnop1234", "key_like"),
        ("yoga studio features Bengaluru " + "word " * 25, "too_long"),
        ("", "empty"),
    ],
)
def test_bad_queries_fail_with_the_reason(query: str, problem: str) -> None:
    res = check_query(query, **ARGS)
    assert not res.ok and problem in res.problems, res


def test_global_targets_need_no_place() -> None:
    args = {**ARGS, "require_place": False}
    assert check_query(
        "material design guidelines navigation", **{**args, "anchors": ["material design"]}
    ).ok


def test_non_english_queries_pass_with_their_own_intent_words() -> None:
    hi = check_query(
        "योग स्टूडियो बेंगलुरु शुल्क",  # yoga studio Bengaluru fees
        anchors=["योग स्टूडियो"],
        places=["बेंगलुरु"],
        intent_words=INTENT,
        generic_words=GENERIC,
    )
    assert hi.ok, hi


def test_repeat_detection_ignores_order_case_and_punctuation() -> None:
    assert is_repeat("Yoga studio: features, Bengaluru", ["bengaluru features yoga studio"])
    assert not is_repeat("yoga studio fees Bengaluru", ["bengaluru features yoga studio"])
