"""The link check (doc 16 section 6): the build FAILS when an ID in the design documents has no fixture, a fixture has no
ID, or an ID has no test that proves it."""

import json

from edge_support import doc_case_ids, fixture_ids
from edge_support.registry import registered_ids_by_ast

EXPECTED_TOTAL = 200  # 113 (doc 15) + 35 (doc 17) + 36 (doc 18) + 16 (doc 19)
KINDS = {"real", "mut", "syn", "mock", "cfg"}


def test_the_documents_define_exactly_200_cases():
    ids = doc_case_ids()
    assert len(ids) == EXPECTED_TOTAL, f"{len(ids)} case ids found in the documents"


def test_every_id_has_a_fixture_and_every_fixture_has_an_id():
    docs, fx = doc_case_ids(), fixture_ids()
    assert not docs - fx, f"ids without a fixture: {sorted(docs - fx)[:20]}"
    assert not fx - docs, f"fixtures without an id: {sorted(fx - docs)[:20]}"


def test_every_fixture_is_well_formed():
    from edge_support.registry import FIXTURES

    for fid in sorted(fixture_ids()):
        meta = json.loads((FIXTURES / fid / "case.json").read_text(encoding="utf-8"))
        assert (
            meta["id"] == fid
            and meta["kind"] in KINDS
            and meta["handling"].strip()
            and meta["test"].strip()
        ), fid


def test_every_id_is_proved_by_at_least_one_test():
    docs, proved = doc_case_ids(), registered_ids_by_ast()
    assert not docs - set(proved), f"ids with no test: {sorted(docs - set(proved))[:20]}"
    assert not set(proved) - docs, f"tests for unknown ids: {sorted(set(proved) - docs)[:20]}"
