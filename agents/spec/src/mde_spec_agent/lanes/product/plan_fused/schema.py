"""SPEC-PLAN fused with the product of a compact app (doc 18 1.2: the Plan also writes the product, the integrations and the
main journey) when `callkit.fusion.can_fuse` says the fused schema fits the writer profile. The output is the union of the
four calls' output records (every array has its own name, so the union is one flat object); the input carries the four
parts' own inputs, structured and not shown, and their lines, shown as untrusted data. A fused call writes no merges, no
splits and no new entity: those need the one-call-per-part form."""

from __future__ import annotations

from pydantic import Field

from mde_spec_agent.lanes.product.kit import vocab
from mde_spec_agent.lanes.product.kit.common import ProvIn
from mde_spec_agent.lanes.product.plan.schema import PlanIn, PlanOut
from mde_spec_agent.lanes.product.prod_integ.schema import IntegIn, IntegOut
from mde_spec_agent.lanes.product.prod_journeys.schema import JourneysIn, JourneysOut
from mde_spec_agent.lanes.product.prod_module.schema import ModuleIn, ModuleOut

PLAN_FIELDS = tuple(PlanOut.model_fields)
MODULE_FIELDS = tuple(ModuleOut.model_fields)
INTEG_FIELDS = tuple(IntegOut.model_fields)
JOURNEY_FIELDS = tuple(JourneysOut.model_fields)


class FusedOut(PlanOut, ModuleOut, IntegOut, JourneysOut):
    """Plan, module, integration and journey records in one flat object."""


class FusedIn(ProvIn):
    language: str
    profile: str
    plan: PlanIn  # structured parts (not shown): the mock, the fallback and the checks read them
    module: ModuleIn
    integ: IntegIn
    journeys: JourneysIn
    brief: list[str]
    inventory: list[str]
    release_lines: list[str]
    module_slice: list[str]
    facts: list[str]
    integ_slice: list[str]
    journey_slice: list[str]
    research: list[str]
    research_note: str = ""
    over_cap_screen_ids: list[str] = Field(default_factory=list)
    scope_cap: int = 60
    slice_cap_tokens: int = 5000
    user_deferred: list[str] = Field(default_factory=list)
    user_must: list[str] = Field(default_factory=list)
    allow_restructure: bool = False
    integration_ids: list[str] = Field(default_factory=list)
    provider_names: list[str] = Field(default_factory=list)
    flow_ids: list[str] = Field(default_factory=list)
    vocab_field_types: list[str] = Field(default_factory=lambda: list(vocab.field_types()))
    vocab_error_codes: list[str] = Field(default_factory=lambda: list(vocab.error_codes()))
    vocab_recoveries: list[str] = Field(default_factory=lambda: list(vocab.recoveries()))
    vocab_entity_classes: list[str] = Field(default_factory=lambda: list(vocab.entity_classes()))
    vocab_voices: list[str] = Field(default_factory=lambda: list(vocab.voices()))
    vocab_actions: list[str] = Field(default_factory=lambda: list(vocab.ACCESS_ACTIONS))
    vocab_reasons: list[str] = Field(default_factory=lambda: list(vocab.release_reasons()))
    vocab_modes: list[str] = Field(default_factory=lambda: list(vocab.integration_modes()))
    vocab_emotions: list[str] = Field(default_factory=lambda: list(vocab.emotions()))
    vocab_variants: list[str] = Field(default_factory=lambda: list(vocab.flow_variants()))
    vocab_mock_profiles: list[str] = Field(default_factory=lambda: list(vocab.mock_profile_names()))


def parts_of(out: FusedOut) -> tuple[PlanOut, ModuleOut, IntegOut, JourneysOut]:
    dump = out.model_dump()
    return (
        PlanOut.model_validate({k: dump[k] for k in PLAN_FIELDS}),
        ModuleOut.model_validate({k: dump[k] for k in MODULE_FIELDS}),
        IntegOut.model_validate({k: dump[k] for k in INTEG_FIELDS}),
        JourneysOut.model_validate({k: dump[k] for k in JOURNEY_FIELDS}),
    )


def join_parts(
    plan: PlanOut, module: ModuleOut, integ: IntegOut, journeys: JourneysOut
) -> FusedOut:
    data = {
        **plan.model_dump(),
        **module.model_dump(),
        **integ.model_dump(),
        **journeys.model_dump(),
    }
    return FusedOut.model_validate(data)
