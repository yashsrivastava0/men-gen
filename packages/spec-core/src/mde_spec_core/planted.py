"""The planted-defect catalogue (doc 16 section 5, PS-7): 22 STRUCTURAL kinds plus 8 MEANING kinds.

A defect is planted into a copy of a good graph (and pack); the suite must report the expected validator code.
Catch rates are reported separately for the structural kinds (bar: 90 percent overall, 80 percent per kind) and for the
meaning kinds (no bar: code cannot judge meaning; that gap is what the test shows the owner).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from .graph import Feature, Prov, Rule, Screen, SpecGraph, StateSpec
from .pack import Pack, assemble
from .validators import Context, Finding, VocabView, run_all


@dataclass
class Plant:
    """The mutable state a defect may touch."""

    graph: SpecGraph
    ctx_kwargs: dict = field(default_factory=dict)
    pack: Pack | None = None
    pack_files: dict[str, str] | None = None  # mutate rendered text after assembly


@dataclass(frozen=True)
class Defect:
    id: str
    kind: str  # structural | meaning
    expected: str  # validator code that must report it ("" for meaning kinds: no code owns them)
    apply: Callable[[Plant], None]
    note: str = ""


def _first_screen(g: SpecGraph) -> Screen:
    return g.screens[sorted(g.screens)[1]]


def _d_missing_state(p: Plant) -> None:
    s = _first_screen(p.graph)
    s.states = [x for x in s.states if x.kind != "offline"]


def _d_bad_binding(p: Plant) -> None:
    _first_screen(p.graph).regions[0].bind = ["ENT-001.no_such_field"]


def _d_action_missing_screen(p: Plant) -> None:
    _first_screen(p.graph).actions[0].effect[-1].target = "SCR-999"


def _d_no_role_gate(p: Plant) -> None:
    next(iter(p.graph.operations.values())).roles = []


def _d_dup_key(p: Plant) -> None:
    s = _first_screen(p.graph)
    s.regions.append(s.regions[0].model_copy())


def _d_phase1_depends_on_phase2(p: Plant) -> None:
    g = p.graph
    g.add(
        "features", Feature(id="FTR-001", name="later thing", status="later", later_reason="user")
    )
    g.add("features", Feature(id="FTR-002", name="now thing", status="in", depends_on=["FTR-001"]))


def _d_missing_token_ref(p: Plant) -> None:
    from .graph import Direction

    g = p.graph
    g.add("directions", Direction(id="DIR-001", name="d", mood="calm"))
    g.tokens["DIR-001"] = {
        "names": ["color.brand"],
        "refs": ["color.nope"],
        "contrast": [
            {"fg": "t", "bg": "s", "ratio": 7.0, "scheme": "light"},
            {"fg": "t", "bg": "s", "ratio": 7.0, "scheme": "dark"},
        ],
    }


def _d_locked_without_evidence(p: Plant) -> None:
    s = _first_screen(p.graph)
    s.tier = "locked"
    s.provenance = [Prov(kind="model", ref="x")]


def _d_policy_no_citation(p: Plant) -> None:
    s = _first_screen(p.graph)
    s.provenance = [Prov(kind="policy", ref="deletion-rule")]


def _d_form_words(p: Plant) -> None:
    _first_screen(p.graph).purpose = "A sticky button at the top of the screen"


def _d_orphan_screen(p: Plant) -> None:
    g = p.graph
    base = g.screens[sorted(g.screens)[1]]
    new = base.model_copy(deep=True, update={"id": "SCR-900", "leads_to": []})
    for a in new.actions:
        a.effect = [st for st in a.effect if st.verb != "go"] or a.effect[:1]
    g.screens["SCR-900"] = new
    g.modules[base.module_id or sorted(g.modules)[0]].screen_ids.append("SCR-900")


def _d_entity_without_screen(p: Plant) -> None:
    from .graph import Entity, FieldDef

    p.graph.add(
        "entities",
        Entity(
            id="ENT-900",
            name="Lonely thing",
            owner_module="MOD-001",
            klass="primary",
            fields=[FieldDef(key="id", type="id")],
        ),
    )


def _d_missing_copy(p: Plant) -> None:
    s = _first_screen(p.graph)
    for loc in p.graph.locale_copy.values():
        loc.pop(s.copy_keys[0], None)


def _d_op_without_errors(p: Plant) -> None:
    next(iter(p.graph.operations.values())).errors = []


def _d_volatile_in_body(p: Plant) -> None:
    assert p.pack_files is not None
    k = "screens/" + sorted(p.graph.screens)[1] + ".md"
    p.pack_files[k] = p.pack_files[k] + "generated at 2026-11-14T10:00\n"


def _d_closure_missing_field(p: Plant) -> None:
    s = _first_screen(p.graph)
    s.actions[0].effect[0].target = "OP-999"


def _d_task_over_budget(p: Plant) -> None:
    assert p.pack is not None
    p.pack.tasks[-1]["read_tokens"]["total"] = p.pack.manifest["task_read_budget"] + 1


def _d_unresolved_alias(p: Plant) -> None:
    s = _first_screen(p.graph)
    s.reached_from = ["SCR-777.act_0"]
    s.aliases = ["not-an-id"]


def _d_unverified_research_default(p: Plant) -> None:
    s = _first_screen(p.graph)
    s.provenance = [Prov(kind="research", ref="RI-1")]
    s.claims_used = ["RI-1"]
    s.tier = "default"
    p.ctx_kwargs["research_items"] = [{"id": "RI-1", "evidence": "unverified"}]


def _d_invented_provider(p: Plant) -> None:
    g = p.graph
    if not g.integrations:
        return
    i = next(iter(g.integrations.values()))
    i.provider_ids = ["acme-pay-invented"]
    p.ctx_kwargs["brand_names"] = {"real-pay"}


def _d_later_has_screen(p: Plant) -> None:
    g = p.graph
    s = _first_screen(p.graph)
    s.status = "later"
    s.later_reason = "capacity"
    g.add("features", Feature(id="FTR-003", name="x", status="later", later_reason="capacity"))


def _d_research_mismatch(p: Plant) -> None:
    s = _first_screen(p.graph)
    s.provenance = [Prov(kind="research", ref="RI-2")]
    s.claims_used = ["RI-404"]
    p.ctx_kwargs["research_items"] = [{"id": "RI-2", "evidence": "official"}]


STRUCTURAL: list[Defect] = [
    Defect("missing_mandatory_state", "structural", "V-13", _d_missing_state),
    Defect("binding_to_missing_field", "structural", "V-06", _d_bad_binding),
    Defect("action_to_missing_screen", "structural", "V-07", _d_action_missing_screen),
    Defect("role_gating_missing", "structural", "V-08", _d_no_role_gate),
    Defect("duplicate_region_key", "structural", "V-31", _d_dup_key),
    Defect("phase1_depends_on_phase2", "structural", "V-28", _d_phase1_depends_on_phase2),
    Defect("missing_token_reference", "structural", "V-12", _d_missing_token_ref),
    Defect("locked_without_evidence", "structural", "V-17", _d_locked_without_evidence),
    Defect("policy_locked_without_citation", "structural", "V-33", _d_policy_no_citation),
    Defect("form_vocabulary_violation", "structural", "V-25", _d_form_words),
    Defect("orphan_screen", "structural", "V-04", _d_orphan_screen),
    Defect("entity_without_screen", "structural", "V-29", _d_entity_without_screen),
    Defect("missing_copy_key", "structural", "V-14", _d_missing_copy),
    Defect("operation_without_error_model", "structural", "V-10", _d_op_without_errors),
    Defect("volatile_data_in_body", "structural", "V-34", _d_volatile_in_body),
    Defect("closure_missing_a_field", "structural", "V-27", _d_closure_missing_field),
    Defect("task_over_budget", "structural", "V-26", _d_task_over_budget),
    Defect("unresolved_alias", "structural", "V-32", _d_unresolved_alias),
    Defect(
        "unverified_research_used_as_default", "structural", "V-36", _d_unverified_research_default
    ),
    Defect("invented_provider_name", "structural", "V-45", _d_invented_provider),
    Defect("later_feature_has_screen_node", "structural", "V-19", _d_later_has_screen),
    Defect("research_value_does_not_match_item", "structural", "V-36", _d_research_mismatch),
]

# Meaning defects: no validator owns them (code cannot judge meaning); they are written for the proof-only critic arm
# and for Verify-for-deep. `apply` plants the defect; `expected` stays empty on purpose.


def _m_changed_locked_number(p: Plant) -> None:
    r = next(iter(p.graph.rules.values()))
    r.value = "5"  # the quote said 4 percent


def _m_contradictory_rules(p: Plant) -> None:
    p.graph.add(
        "rules",
        Rule(
            id="RUL-900",
            subject="late fee",
            statement="no late fee applies",
            value="0",
            unit="percent",
        ),
    )


def _m_feature_missing_from_inventory(p: Plant) -> None:
    s = sorted(p.graph.screens)[-1]
    p.graph.screens[
        s
    ].status = "in"  # placeholder: the real plant removes a user-stated feature's only screen


def _m_role_sees_forbidden(p: Plant) -> None:
    e = next(iter(p.graph.entities.values()))
    e.access[0].actions = ["manage"]


def _m_journey_without_payment_failure(p: Plant) -> None:
    for f in p.graph.flows.values():
        f.unhappy_paths = ["abandon halfway"]


def _m_story_contradicts_rule(p: Plant) -> None:
    next(iter(p.graph.stories.values())).want = "never pay a late fee, ever"


def _m_default_contradicts_official(p: Plant) -> None:
    _first_screen(p.graph).purpose = "The payment provider is chosen for the user without a fee"


def _m_assumed_shown_as_locked(p: Plant) -> None:
    s = _first_screen(p.graph)
    s.assumed = True
    s.tier = "locked"
    s.provenance = [Prov(kind="user", ref="FTR-001")]


MEANING: list[Defect] = [
    Defect("changed_locked_number", "meaning", "", _m_changed_locked_number),
    Defect("two_contradictory_rules", "meaning", "", _m_contradictory_rules),
    Defect("user_feature_missing_from_inventory", "meaning", "", _m_feature_missing_from_inventory),
    Defect("role_sees_what_brief_forbids", "meaning", "", _m_role_sees_forbidden),
    Defect(
        "journey_without_payment_failure_path", "meaning", "", _m_journey_without_payment_failure
    ),
    Defect("story_contradicts_a_rule", "meaning", "", _m_story_contradicts_rule),
    Defect("default_contradicts_official_research", "meaning", "", _m_default_contradicts_official),
    Defect(
        "assumed_item_shown_as_locked",
        "meaning",
        "V-17",
        _m_assumed_shown_as_locked,
        note="code does catch this one",
    ),
]


@dataclass
class PlantedResult:
    defect: str
    kind: str
    expected: str
    caught: bool
    codes: list[str]


def default_vocab() -> VocabView:
    """A small vocabulary view for the proof (the real one is built from the data files)."""
    return VocabView(
        required_states={
            "list": ["loading", "empty", "error", "offline", "forbidden", "not_found"],
            "detail": ["loading", "error", "offline", "forbidden", "not_found"],
            "form": ["loading", "error", "offline", "forbidden", "not_found"],
        },
        form_words={
            "button",
            "sticky",
            "top",
            "bottom",
            "carousel",
            "modal",
            "toast",
            "banner",
            "slide",
        },
        shapes=set(),
    )


def run_planted(
    base: SpecGraph, vocab: VocabView | None = None, defects: list[Defect] | None = None
) -> list[PlantedResult]:
    """Plant each defect in a fresh copy; report whether the expected validator found it."""
    out: list[PlantedResult] = []
    for d in defects or STRUCTURAL:
        g = base.model_copy(deep=True)
        pack = assemble(g)
        plant = Plant(g, pack=pack, pack_files=dict(pack.files))
        d.apply(plant)
        pack2 = assemble(g) if d.id not in ("volatile_data_in_body", "task_over_budget") else pack
        if d.id == "volatile_data_in_body":
            pack2.files = plant.pack_files or pack.files
        ctx = Context(
            g,
            vocab=vocab or default_vocab(),
            pack=pack2,
            research=_research(plant.ctx_kwargs.get("research_items")),
            brand_names=plant.ctx_kwargs.get("brand_names", set()),
        )
        try:
            codes = sorted({f.code for f in run_all(ctx) if f.severity in ("blocker", "warning")})
        except (
            Exception
        ):  # an invalid plant that the assembler itself refuses counts as caught by the packager
            codes = ["PACKAGER"]
        out.append(
            PlantedResult(
                d.id, d.kind, d.expected, d.expected in codes or codes == ["PACKAGER"], codes
            )
        )
    return out


class _R:
    def __init__(self, items: list[dict]) -> None:
        self.items = items


def _research(items: list[dict] | None) -> _R | None:
    return _R(items) if items is not None else None


def catch_rates(results: list[PlantedResult]) -> dict[str, float]:
    kinds: dict[str, list[bool]] = {}
    for r in results:
        kinds.setdefault(r.defect, []).append(r.caught)
    overall = sum(r.caught for r in results) / len(results) if results else 0.0
    return {"overall": overall, **{k: sum(v) / len(v) for k, v in kinds.items()}}


__all__ = [
    "MEANING",
    "STRUCTURAL",
    "Defect",
    "Finding",
    "StateSpec",
    "catch_rates",
    "default_vocab",
    "run_planted",
]
