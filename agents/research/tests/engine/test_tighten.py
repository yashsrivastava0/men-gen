"""D-159: a partial item may climb only after the sentences the checker named are dropped and the same checker, asked
again about the shorter claim, says supported. Words are removed, never added; a bare re-ask is never made."""

from __future__ import annotations

import json
import re

from support.testing import atom, extractor, load_fixture, run_engine

from mde_research_agent.calls.check import CheckIn, CheckOut, CheckVerdict, claim_sentences
from mde_research_agent.calls.extract import ExtractIn
from mde_research_agent.engine.checking import tightened_claim

STANDARD = "standard-produce-marketplace-nashik"
EXTRA = "The page also says the provider serves every city."


def padded_rule_extractor():  # type: ignore[no-untyped-def]
    """A rule item per fee sentence whose claim adds a second sentence the quote does not support."""
    rx = re.compile(r"[^.]*\bfees?\b[^.]*\d[^.]*\.", re.I)

    def build(inp: ExtractIn):  # type: ignore[no-untyped-def]
        out = []
        for m in rx.finditer(inp.passages):
            sentence = " ".join(m.group(0).split())
            nums = re.findall(r"\d+(?:\.\d+)?", sentence)
            out.append(
                atom(
                    "rule",
                    sentence,
                    key="payment fee",
                    label="Payment fee",
                    impact="high",
                    claim=f"{sentence} {EXTRA}",
                    statement=sentence,
                    subject="payment fee",
                    value=nums[0] if nums else None,
                    unit="percent",
                    authority=None,
                )
            )
        return out

    return extractor(build)


def test_dropping_sentences_only_removes_words() -> None:
    claim = "A is true. B is true. C is true."
    assert tightened_claim(claim, [2]) == ("A is true. C is true.", ["B is true."])
    assert tightened_claim(claim, [1, 3]) == ("B is true.", ["A is true.", "C is true."])
    assert tightened_claim(claim, [1, 2, 3]) is None  # nothing would be left
    assert tightened_claim(claim, [7, 0, -1]) is None  # numbers that name no sentence
    assert tightened_claim(claim, []) is None
    assert tightened_claim("One sentence only.", [1]) is None
    kept, dropped = tightened_claim(claim, [2]) or ("", [])
    assert set(claim_sentences(kept)) | set(dropped) == set(claim_sentences(claim))


async def test_a_partial_item_climbs_only_after_a_shorter_claim_is_judged_supported() -> None:
    seen: list[tuple[str, bool]] = []  # (claim, was the second sentence still there)

    def check(inp: CheckIn) -> CheckOut:
        verdicts = []
        for i in inp.items:
            padded = EXTRA in i.claim
            seen.append((i.claim, padded))
            if padded:
                verdicts.append(CheckVerdict(id=i.id, verdict="partial", unsupported_sentences=[2]))
            else:
                verdicts.append(CheckVerdict(id=i.id, verdict="supported"))
        return CheckOut(verdicts=verdicts)

    run = await run_engine(
        load_fixture(STANDARD),
        overrides={"rsch.extract.data": padded_rule_extractor(), "rsch.check": check},
    )
    atoms = [a for a in run.evidence["atoms"] if a["kind"] == "rule" and a["status"] == "verified"]
    assert atoms, "the fixture must give at least one fee rule"
    for a in atoms:
        assert a["verdict"] == "supported" and "tightened" in a["flags"]
        assert EXTRA not in a["claim"] and a["claim_original"].endswith(EXTRA)
        assert a["dropped_sentences"] == [EXTRA]
    assert any(padded for _, padded in seen) and any(not padded for _, padded in seen)
    assert run.research["extraction"]["tightened"] >= 1 if "extraction" in run.research else True
    items = [i for i in run.research["items"] if i["kind"] == "rule"]
    assert items and all("partial_support" not in i["flags"] for i in items)
    assert not run.violations


async def test_a_shorter_claim_that_is_still_partial_restores_the_original_and_stays_unverified() -> (
    None
):
    def check(inp: CheckIn) -> CheckOut:
        return CheckOut(
            verdicts=[
                CheckVerdict(id=i.id, verdict="partial", unsupported_sentences=[2])
                for i in inp.items
            ]
        )

    run = await run_engine(
        load_fixture(STANDARD),
        overrides={"rsch.extract.data": padded_rule_extractor(), "rsch.check": check},
    )
    atoms = [a for a in run.evidence["atoms"] if a["kind"] == "rule" and a["status"] == "verified"]
    assert atoms
    for a in atoms:
        assert a["verdict"] == "partial" and "tightened" not in a["flags"]
        assert (
            a["claim"].endswith(EXTRA)
            and a["claim_original"] is None
            and a["dropped_sentences"] == []
        )
    items = [i for i in run.research["items"] if i["kind"] == "rule"]
    assert items and all(i["evidence"] == "unverified" for i in items)


async def test_a_partial_item_with_no_named_sentence_is_never_asked_again() -> None:
    asked: list[str] = []

    def check(inp: CheckIn) -> CheckOut:
        asked.extend(i.claim for i in inp.items if EXTRA in i.claim)
        return CheckOut(verdicts=[CheckVerdict(id=i.id, verdict="partial") for i in inp.items])

    run = await run_engine(
        load_fixture(STANDARD),
        overrides={"rsch.extract.data": padded_rule_extractor(), "rsch.check": check},
    )
    sent = [i for sid, inp in run.gateway.calls if sid == "rsch.check" for i in inp.items]  # type: ignore[attr-defined]
    verified = [a for a in run.evidence["atoms"] if a["status"] == "verified" and a["checked"]]
    unique = {(a["claim"], a["quote"], json.dumps(a["data"], sort_keys=True)) for a in verified}
    assert asked and len(sent) == len(unique)  # every item was put to the checker once and no more


async def test_a_partial_item_whose_unsupported_fields_were_nulled_is_asked_again_about_what_is_left() -> (
    None
):
    seen: list[str] = []  # the data each ask saw

    def check(inp: CheckIn) -> CheckOut:
        verdicts = []
        for i in inp.items:
            seen.append(i.data)
            if '"unit":"percent"' in i.data.replace(" ", ""):
                verdicts.append(
                    CheckVerdict(id=i.id, verdict="partial", unsupported_fields=["unit"])
                )
            else:  # the unit was nulled: what is left is stated
                verdicts.append(CheckVerdict(id=i.id, verdict="supported"))
        return CheckOut(verdicts=verdicts)

    run = await run_engine(
        load_fixture(STANDARD),
        overrides={"rsch.extract.data": padded_rule_extractor(), "rsch.check": check},
    )
    atoms = [a for a in run.evidence["atoms"] if a["kind"] == "rule" and a["status"] == "verified"]
    assert atoms
    for a in atoms:
        assert a["verdict"] == "supported" and "tightened" in a["flags"]
        assert a["data"]["unit"] is None and a["unsupported_fields"] == ["unit"]
        assert a["claim_original"] is None  # no sentence was dropped, only a field
    assert any(
        '"unit":"percent"' in d.replace(" ", "") for d in seen
    )  # the first ask had the field
    assert any('"unit":null' in d.replace(" ", "") for d in seen)  # the second ask did not
    items = [i for i in run.research["items"] if i["kind"] == "rule"]
    assert items and all("partial_support" not in i["flags"] for i in items)


async def test_a_nulled_field_does_not_climb_when_the_checker_still_says_partial() -> None:
    def check(inp: CheckIn) -> CheckOut:
        return CheckOut(
            verdicts=[
                CheckVerdict(
                    id=i.id,
                    verdict="partial",
                    unsupported_fields=["unit"] if "percent" in i.data else [],
                )
                for i in inp.items
            ]
        )

    run = await run_engine(
        load_fixture(STANDARD),
        overrides={"rsch.extract.data": padded_rule_extractor(), "rsch.check": check},
    )
    atoms = [a for a in run.evidence["atoms"] if a["kind"] == "rule" and a["status"] == "verified"]
    assert atoms and all(a["verdict"] == "partial" and "tightened" not in a["flags"] for a in atoms)
    assert all(a["data"]["unit"] is None for a in atoms)  # the field stays removed either way
