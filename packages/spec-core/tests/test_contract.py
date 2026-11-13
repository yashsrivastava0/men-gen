import json

import pytest
import yaml

from mde_spec_core.config import default_config
from mde_spec_core.contract import (
    build_contract,
    contract_files,
    dump_yaml,
    roles_allowed,
    validate_openapi,
)
from mde_spec_core.graph import Operation
from mde_spec_core.synth import make_graph

PRESETS = ["small", "medium", "big", "program"]


@pytest.fixture(scope="module")
def bundles():
    return {n: build_contract(make_graph(n)) for n in PRESETS}


@pytest.mark.parametrize("name", PRESETS)
def test_openapi_validates_inline_and_as_files(bundles, name):
    b = bundles[name]
    assert b.document["openapi"].startswith("3.1")
    assert validate_openapi(b.document) is None
    assert [f for f in b.findings if f.severity == "blocker"] == [], b.findings[:2]


@pytest.mark.parametrize("name", PRESETS)
def test_two_runs_give_identical_bytes(name):
    a = contract_files(make_graph(name))
    b = contract_files(make_graph(name))
    assert a == b
    assert all(t.endswith("\n") and "\r" not in t for t in a.values())


@pytest.mark.parametrize("name", PRESETS)
def test_every_contract_file_is_within_the_resource_cap(bundles, name):
    cap = default_config().budgets.contract_resource_cap
    factor = default_config().budgets.token_factor_chars_per_token
    assert bundles[name].warnings == []
    for path, text in bundles[name].files.items():
        assert -(-len(text) // factor) <= cap, path


def test_split_root_points_at_resource_files_and_all_files_validate(bundles, tmp_path):
    b = bundles["big"]
    assert b.split
    for p, t in b.files.items():
        (tmp_path / p).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / p).write_text(t, encoding="utf-8")
    assert validate_openapi(tmp_path / "contract" / "openapi.yaml") is None
    root = yaml.safe_load(b.files["contract/openapi.yaml"])
    assert all("$ref" in v for v in root["paths"].values())
    # a broken reference is caught
    res = next(p for p in b.files if p.startswith("contract/resources/"))
    (tmp_path / res).write_text(
        (tmp_path / res)
        .read_text()
        .replace("../components.yaml#/responses/", "../components.yaml#/nope/"),
        encoding="utf-8",
    )
    assert validate_openapi(tmp_path / "contract" / "openapi.yaml") is not None


def test_files_and_tags(bundles):
    b = bundles["medium"]
    g = make_graph("medium")
    assert {"contract/openapi.yaml", "contract/auth-matrix.json", "contract/events.json"} <= set(
        b.files
    )
    for eid in g.entities:
        assert f"contract/schemas/{eid}.json" in b.files
    assert [t["name"] for t in b.document["tags"]] == sorted(g.apps)  # one document, per-app tags
    tagged = {t for p in b.document["paths"].values() for op in p.values() for t in op["tags"]}
    assert tagged <= set(g.apps)


def test_every_operation_is_role_gated_and_carries_phase(bundles):
    doc = bundles["small"].document
    ops = [op for item in doc["paths"].values() for op in item.values()]
    assert ops and all(op["x-roles"] and op["x-phase"] == "now" for op in ops)


def test_crud_is_derived_from_the_access_matrix_only_where_the_graph_has_none():
    g = make_graph("small")
    b = build_contract(g)
    derived = {o["id"] for o in b.operations if o["derived"]}
    assert "update_ENT-001" in derived and "delete_ENT-001" in derived
    assert "list_ENT-001" not in derived  # the graph already has a list operation for it
    # an entity nobody may edit gets no update operation
    ent = g.entities["ENT-002"]
    for row in ent.access:
        row.actions = ["view"]
    ids = {o["id"] for o in build_contract(g, validate=False).operations}
    assert "update_ENT-002" not in ids and "delete_ENT-002" not in ids


def test_conventions_pagination_idempotency_concurrency_errors():
    g = make_graph("small")
    b = build_contract(g)
    paths = {}
    for item in b.document["paths"].values():
        paths.update({op["operationId"]: (m, op) for m, op in item.items()})
    _, lst = paths["OP-001"]  # paged list
    names = [p.get("name") or p["$ref"] for p in lst["parameters"]]
    assert (
        any("Cursor" in n for n in names) and any("Limit" in n for n in names) and "sort" in names
    )
    assert any(n.startswith("filter[") for n in names)
    _, save = paths["OP-003"]  # idempotent POST
    assert save["x-idempotent"] and any(
        "IdempotencyKey" in p.get("$ref", "") for p in save["parameters"]
    )
    _, upd = paths["update_ENT-001"]  # editable entity: optimistic concurrency
    assert upd["x-concurrency"] == "etag" and any(
        "IfMatch" in p.get("$ref", "") for p in upd["parameters"]
    )
    assert "412" in upd["responses"]
    ent = b.schemas["ENT-001"]
    assert "version" in ent["properties"] and ent["properties"]["status"]["enum"]
    err = b.document["components"]["schemas"]["Error"]
    assert {"forbidden", "payment_declined", "duplicate"} <= set(err["properties"]["code"]["enum"])
    chk = next(
        op
        for op in (o for it in b.document["paths"].values() for o in it.values())
        if op["operationId"].startswith("OP-") and "402" in op["responses"]
    )
    assert chk["responses"]["402"]["x-error-codes"] == ["payment_declined"]
    assert "network" not in json.dumps(
        b.document["components"]["schemas"]["Error"]
    )  # client-only code


def test_auth_matrix_is_default_deny_and_matches_roles():
    g = make_graph("small")
    b = build_contract(g)
    am = b.auth_matrix
    assert am["default"] == "deny" and am["public_operations"] == []
    assert set(am["roles"]) == set(g.roles)
    ops = {o["id"]: o["roles"] for o in b.operations}
    assert set(am["operations"]) == set(ops)
    assert all(roles_allowed(am, oid) == roles for oid, roles in ops.items())
    assert roles_allowed(am, "OP-NOPE") == []  # default deny


def test_an_operation_without_roles_is_denied_and_reported():
    g = make_graph("tiny")
    next(iter(g.operations.values())).roles = []
    b = build_contract(g, validate=False)
    assert any(f.code == "V-08" for f in b.findings)
    denied = [op for it in b.document["paths"].values() for op in it.values() if op.get("x-deny")]
    assert denied and denied[0]["x-roles"] == []


def test_later_flags_events_and_fixture_hook():
    g = make_graph("small")
    g.entities["ENT-002"].later = True
    b = build_contract(g)
    phases = {o["id"]: o["phase"] for o in b.operations if o["entity"] == "ENT-002"}
    assert phases and set(phases.values()) == {"later"}
    ev = b.events
    assert {e["source"] for e in ev["events"] if e["kind"] == "integration"} == set(g.integrations)
    assert ev["callbacks"] and all(c["signed"] for c in ev["callbacks"])
    assert "webhooks" in b.document
    fx = {"ENT-001": [{"id": "demo-1", "name": "Sample"}]}
    c = build_contract(g, fixtures=fx)
    assert c.schemas["ENT-001"]["examples"] == fx["ENT-001"]
    assert "contract/fixtures/ENT-001.json" in c.files


def test_a_path_clash_is_reported_not_silently_dropped():
    g = make_graph("tiny")
    first = next(iter(g.operations.values()))
    g.operations["OP-999"] = Operation(
        id="OP-999",
        name="dup",
        entity=first.entity,
        method=first.method,
        path=first.path,
        roles=["ROL-001"],
        errors=[],
    )
    b = build_contract(g, validate=False)
    assert any("already taken" in f.why for f in b.findings)


def test_dump_is_sorted_and_free_of_anchors():
    shared = {"a": 1}
    text = dump_yaml({"z": shared, "a": shared})
    assert text.index("a:") < text.index("z:") and "&id" not in text and "*id" not in text
