from __future__ import annotations

from mde_compass.domain.claims import Claim, Statement
from mde_compass.engine.document_flow import plan_from_claims


def _c(i: int, type_: str, text: str, status: str = "used", quote: str | None = None) -> Claim:
    return Claim(
        id=f"CLM-{i:03d}",
        source_id="SRC-001",
        statement=Statement(
            type=type_, text=text, quote=quote or text, pointer="page 2", confidence=0.8
        ),  # type: ignore[arg-type]
        status=status,  # type: ignore[arg-type]
    )


def test_used_claims_become_cited_writes_and_the_rest_become_questions() -> None:
    claims = [
        _c(1, "requirement", "Customers can reorder a previous meal in two taps"),
        _c(2, "requirement", "Restaurants can pause new orders"),
        _c(3, "user_or_role", "Delivery rider"),
        _c(4, "exclusion", "No cash on delivery in the first release"),
        _c(5, "open_issue", "Payment provider: TBD"),
        _c(6, "requirement", "Loyalty points", status="needs_confirming"),
        _c(7, "requirement", "Free delivery always", status="conflicting"),
        _c(8, "glossary_term", "Thali means a plated set meal"),
    ]
    plan = plan_from_claims(claims)
    slots = {w.slot_id: w for w in plan.writes}
    assert [f["name"] for f in slots["scope.features"].value] == [  # type: ignore[index,union-attr]
        "Customers can reorder a previous meal in two",
        "Restaurants can pause new orders",
    ]
    assert slots["roles.list"].provenance.type == "document"
    assert (
        slots["roles.list"].provenance.ref == "SRC-001 page 2"
        and slots["roles.list"].provenance.quote
    )
    assert slots["scope.must_not"].value == ["No cash on delivery in the first release"]
    assert slots["glossary.terms"].value == [{"term": "Thali", "synonyms": [], "avoid": []}]
    assert plan.open_issues == ["Payment provider: TBD"]
    assert plan.needs_confirming == 1 and plan.conflicts == 1  # never written silently


def test_existing_user_values_are_extended_not_overwritten() -> None:
    existing = {
        "scope.features": [{"name": "Track my order", "intent": "see where it is"}],
        "idea.problem_value": "mine",
    }
    plan = plan_from_claims(
        [_c(1, "requirement", "Pay online"), _c(2, "business_context", "Restaurants lose orders")],
        existing,
    )
    feats = next(w for w in plan.writes if w.slot_id == "scope.features")
    assert [f["name"] for f in feats.value] == ["Track my order", "Pay online"]  # type: ignore[index,union-attr]
    assert all(w.slot_id != "idea.problem_value" for w in plan.writes)  # chat beats documents


def test_hostile_text_is_linear() -> None:
    import time

    claims = [_c(i, "requirement", "x " * 25_000) for i in range(3)]
    t = time.perf_counter()
    plan_from_claims(claims)
    assert time.perf_counter() - t < 1.0
