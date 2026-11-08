"""Offline handler for C9b: a field or two grounded in the Brief, and research integrations.

Deterministic and schema-valid. Fields come from what the Brief says (sensitive areas, regions,
language) and go on the entity of the people who use the app. Integration additions from research
become mock integrations tagged ``source: research``.
"""

from __future__ import annotations

import re

from mde_contracts.calls import C9bInput, C9bOutput, FieldProposal, Proposal, SpecProposals

MAX_FIELDS = 2
MAX_INTEGRATIONS = 2
_NON_WORD = re.compile(r"[^a-z0-9]+")
_USER_ENTITY = re.compile(r"^ENT-(User|Employee|Customer|Member|Person)$")

# sensitive area (compliance flag) -> a consent or status field on the person's record
_FLAG_FIELDS: dict[str, FieldProposal] = {
    "precise_location": FieldProposal(
        name="location_consent",
        type="bool",
        required=False,
        description="Whether the person agreed to share exact location",
    ),
    "child_safety": FieldProposal(
        name="guardian_email",
        type="string",
        required=False,
        description="Email of the parent or guardian",
    ),
    "health_data_privacy": FieldProposal(
        name="health_consent_at",
        type="datetime",
        required=False,
        description="When the person agreed to share health information",
    ),
    "identity_documents": FieldProposal(
        name="id_checked",
        type="bool",
        required=False,
        description="Whether an identity document was checked by the provider",
    ),
}


def _user_entity(inp: C9bInput) -> tuple[str, set[str]] | None:
    for entity in inp.outline.entities:
        if _USER_ENTITY.match(entity.id):
            return entity.id, set(entity.fields)
    if inp.outline.entities:
        first = inp.outline.entities[0]
        return first.id, set(first.fields)
    return None


def _fields(inp: C9bInput, existing: set[str]) -> list[FieldProposal]:
    wanted: list[FieldProposal] = []
    for flag in inp.brief.compliance_flags:
        if flag in _FLAG_FIELDS:
            wanted.append(_FLAG_FIELDS[flag])
    if inp.brief.regions:
        wanted.append(
            FieldProposal(
                name="preferred_region",
                type="string",
                required=False,
                description="Region the person works or shops in",
            )
        )
    if inp.brief.language != "en":
        wanted.append(
            FieldProposal(
                name="preferred_language",
                type="string",
                required=False,
                description="Language the person wants the app in",
            )
        )
    fresh = [f for f in wanted if f.name not in existing]
    return fresh[:MAX_FIELDS]


def handle(input: C9bInput) -> C9bOutput:
    proposals: list[Proposal] = []
    found = _user_entity(input)
    if found is not None:
        entity_id, existing = found
        fields = _fields(input, existing)
        if fields:
            proposals.append(
                Proposal(
                    kind="add_entity_fields",
                    target=entity_id,
                    rationale="The Brief names sensitive areas, regions or a language the data should record.",
                    fields=fields,
                )
            )
    taken = {i.id for i in input.outline.integrations}
    added = 0
    for addition in input.research:
        if addition.kind != "integration" or added >= MAX_INTEGRATIONS:
            continue
        slug = _NON_WORD.sub("_", addition.title.lower()).strip("_")
        if not slug or f"INT-{slug}" in taken:
            continue
        taken.add(f"INT-{slug}")
        added += 1
        proposals.append(
            Proposal(
                kind="add_integration",
                source="research",
                research_ref=addition.id,
                rationale=f"Research found that {addition.title.lower()} is commonly used by similar apps.",
                name=addition.title[:60],
                purpose=addition.rationale[:160],
            )
        )
    return SpecProposals(
        proposals=proposals, notes="mock: fields from the Brief, research integrations"
    )
