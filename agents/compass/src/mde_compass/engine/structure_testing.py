"""A small, VALID app with a structure, for the tests of the structure checks, slices, views and calls (a real package
module: importlib test mode forbids test-to-test imports; the same precedent as ``views/testing.py``).

The app is a two-app clinic: patients book and attend appointments, doctors run the day and write notes, a receptionist
manages the schedule. It says health data is sensitive, signs in with a phone code and has two languages, so the realism
rules of ``structure_rules.yaml`` apply and are all met. It passes ``run_checks`` for the medium band with no error.
"""

from __future__ import annotations

from mde_compass.domain.brief import (
    AppRecord,
    Brief,
    DomainFact,
    FeatureRecord,
    RoleRecord,
)
from mde_compass.domain.common import BriefItem
from mde_compass.domain.ids import IdLedger
from mde_compass.domain.structure import (
    EntityField,
    EntityRecord,
    FlowRecord,
    FlowStep,
    ModuleRecord,
    ScreenEntityUse,
    ScreenRecord,
)

PATIENT, DOCTOR, FRONT_DESK = "ROL-001", "ROL-002", "ROL-003"
PATIENT_APP, STAFF_APP = "APP-001", "APP-002"
BOTH = [PATIENT_APP, STAFF_APP]
EVERYONE = [PATIENT, DOCTOR, FRONT_DESK]


def _screen(
    sid: str,
    name: str,
    module: str,
    pattern: str,
    roles: list[str],
    *,
    apps: list[str] | None = None,
    serves: list[str] | None = None,
    entities: list[tuple[str, str]] | None = None,
    purpose: str = "",
) -> ScreenRecord:
    return ScreenRecord(
        id=sid,
        name=name,
        purpose=purpose or f"{name}.",
        module=module,
        apps=apps or BOTH,
        roles=roles,
        serves=serves or [],
        entities=[ScreenEntityUse(entity=e, use=u) for e, u in (entities or [])],  # type: ignore[arg-type]
        pattern=pattern,
    )


def sample_app() -> Brief:
    brief = Brief()
    brief.ids = IdLedger()
    for prefix, n in (
        ("APP", 2),
        ("ROL", 3),
        ("FTR", 4),
        ("MOD", 3),
        ("SCR", 13),
        ("FLW", 2),
        ("ENT", 3),
    ):
        for _ in range(n):
            brief.ids.allocate(prefix)
    brief.apps = [
        AppRecord(id=PATIENT_APP, name="Patient app", roles=[PATIENT]),
        AppRecord(id=STAFF_APP, name="Staff app", roles=[DOCTOR, FRONT_DESK]),
    ]
    brief.roles = [
        RoleRecord(id=PATIENT, name_in_user_words="Patient", goal="book and attend appointments"),
        RoleRecord(id=DOCTOR, name_in_user_words="Doctor", goal="see patients and write notes"),
        RoleRecord(
            id=FRONT_DESK, name_in_user_words="Receptionist", goal="manage the daily schedule"
        ),
    ]
    brief.scope = [
        FeatureRecord(
            id="FTR-001", name="Book appointments", intent="Patients book a slot", priority="must"
        ),
        FeatureRecord(
            id="FTR-002",
            name="Video consultation",
            intent="Patients and doctors meet online",
            priority="must",
        ),
        FeatureRecord(
            id="FTR-003", name="Schedule view", intent="Staff see the day", priority="nice"
        ),
        FeatureRecord(
            id="FTR-004",
            name="Loyalty points",
            intent="Reward repeat visits",
            priority="later",
            scope_status="later",
        ),
    ]
    brief.data = {"sensitive": BriefItem(value=["health"], tier="Default", status="assumed")}
    brief.access = {
        "auth_methods": BriefItem(value=["phone_otp"], tier="Default", status="assumed")
    }
    brief.experience = {"locales": BriefItem(value=["en", "hi"], tier="Default", status="assumed")}
    brief.modules = [
        ModuleRecord(
            id="MOD-001",
            name="Account and privacy",
            purpose="Sign in, verify, profile, language, consent",
            apps=BOTH,
            roles=EVERYONE,
            entry="SCR-001",
        ),
        ModuleRecord(
            id="MOD-002",
            name="Appointments",
            purpose="Find a doctor, book and manage visits",
            apps=BOTH,
            features=["FTR-001", "FTR-003"],
            roles=EVERYONE,
            entry="SCR-006",
        ),
        ModuleRecord(
            id="MOD-003",
            name="Consultations",
            purpose="Run the visit and keep the notes",
            apps=BOTH,
            features=["FTR-002"],
            roles=[PATIENT, DOCTOR],
            entry="SCR-011",
        ),
    ]
    brief.screens = [
        _screen("SCR-001", "Sign in", "MOD-001", "auth", EVERYONE),
        _screen("SCR-002", "Verify code", "MOD-001", "auth", EVERYONE),
        _screen("SCR-003", "Profile", "MOD-001", "profile", EVERYONE),
        _screen("SCR-004", "Language settings", "MOD-001", "settings", EVERYONE),
        _screen("SCR-005", "Consent and privacy", "MOD-001", "settings", EVERYONE),
        _screen(
            "SCR-006",
            "My appointments",
            "MOD-002",
            "list",
            [PATIENT],
            apps=[PATIENT_APP],
            serves=["FTR-001"],
            entities=[("ENT-001", "view")],
        ),
        _screen(
            "SCR-007",
            "Find a doctor",
            "MOD-002",
            "search",
            [PATIENT],
            apps=[PATIENT_APP],
            serves=["FTR-001"],
        ),
        _screen(
            "SCR-008",
            "Book a slot",
            "MOD-002",
            "form",
            [PATIENT, FRONT_DESK],
            serves=["FTR-001"],
            entities=[("ENT-001", "create")],
        ),
        _screen(
            "SCR-009",
            "Appointment details",
            "MOD-002",
            "detail",
            EVERYONE,
            serves=["FTR-001"],
            entities=[("ENT-001", "view")],
        ),
        _screen(
            "SCR-010",
            "Day schedule",
            "MOD-002",
            "calendar",
            [DOCTOR, FRONT_DESK],
            apps=[STAFF_APP],
            serves=["FTR-003"],
            entities=[("ENT-001", "edit")],
        ),
        _screen(
            "SCR-011", "Consultation room", "MOD-003", "chat", [PATIENT, DOCTOR], serves=["FTR-002"]
        ),
        _screen(
            "SCR-012",
            "Visit notes",
            "MOD-003",
            "form",
            [DOCTOR],
            apps=[STAFF_APP],
            serves=["FTR-002"],
            entities=[("ENT-002", "edit")],
        ),
        _screen(
            "SCR-013",
            "Access log",
            "MOD-003",
            "report",
            [DOCTOR],
            apps=[STAFF_APP],
            purpose="Who opened which record.",
        ),
    ]
    brief.entities = [
        EntityRecord(
            id="ENT-001",
            key="appointment",
            name="Appointment",
            module="MOD-002",
            fields=[
                EntityField(key="starts_at", name="Starts at", type="datetime", required=True),
                EntityField(
                    key="status", name="Status", type="enum", values=["booked", "done", "cancelled"]
                ),
                EntityField(key="fee", name="Fee", type="money"),
            ],
            states=["booked", "done", "cancelled"],
            access={PATIENT: "create", DOCTOR: "edit", FRONT_DESK: "manage"},
        ),
        EntityRecord(
            id="ENT-002",
            key="visit_note",
            name="Visit note",
            module="MOD-003",
            fields=[
                EntityField(key="text", name="Note", type="text", required=True),
                EntityField(key="appointment", name="Appointment", type="ref", ref="ENT-001"),
            ],
            access={PATIENT: "view", DOCTOR: "edit", FRONT_DESK: "none"},
        ),
        EntityRecord(
            id="ENT-003",
            key="doctor",
            name="Doctor",
            module="MOD-002",
            fields=[
                EntityField(key="name", name="Name", type="text", required=True),
                EntityField(key="speciality", name="Speciality", type="text"),
            ],
            access={PATIENT: "view", DOCTOR: "edit", FRONT_DESK: "manage"},
        ),
    ]
    # the doctor entity is shown by the search screen
    brief.screens[6] = brief.screens[6].model_copy(
        update={"entities": [ScreenEntityUse(entity="ENT-003", use="view")]}
    )
    brief.flows = [
        FlowRecord(
            id="FLW-001",
            name="Book an appointment",
            goal="A patient gets a slot",
            role=PATIENT,
            features=["FTR-001"],
            steps=[
                FlowStep(screen="SCR-007", action="pick a doctor"),
                FlowStep(screen="SCR-008", action="choose a slot"),
                FlowStep(screen="SCR-009", action="see the booking"),
            ],
            acceptance=[
                "The booking shows in My appointments",
                "A taken slot cannot be booked twice",
            ],
            alternates=["No slot is free: the patient is offered the next day"],
        ),
        FlowRecord(
            id="FLW-002",
            name="Run a consultation",
            goal="A doctor sees a patient and writes the notes",
            role=DOCTOR,
            features=["FTR-002"],
            steps=[
                FlowStep(screen="SCR-010", action="open today's visit"),
                FlowStep(screen="SCR-011", action="talk"),
                FlowStep(screen="SCR-012", action="write notes"),
            ],
            acceptance=["The notes are saved against the appointment"],
        ),
    ]
    brief.domain_facts = [
        DomainFact(
            id="FCT-001",
            type="rule",
            statement="Patients can only see their own visit notes",
            kind="rule",
            refs=["ENT-002"],
        ),
        DomainFact(
            id="FCT-002",
            type="number",
            statement="About 45 doctors across six clinics",
            kind="number",
            refs=["ROL-002"],
        ),
        DomainFact(
            id="FCT-003",
            type="requirement",
            statement="Receptionists manage the daily appointment schedule",
            kind="flow",
            refs=["ROL-003"],
        ),
    ]
    return brief
