"""An app named after its people belongs to the role of that name (the Brief links them by id)."""

from __future__ import annotations

from mde_compass.catalog.loader import load_catalog
from mde_compass.domain.brief import Brief
from mde_compass.domain.common import Provenance
from mde_compass.engine.brief_builder import apply_slots
from mde_compass.engine.slots import SlotStore
from mde_core.clock import FakeClock


def test_an_app_is_linked_to_the_role_of_the_same_name() -> None:
    catalog = load_catalog()
    store = SlotStore(catalog, clock=FakeClock())
    prov = Provenance(type="user_text", ref="FCT-001", quote="x")
    store.write(
        "roles.list",
        [{"name": "Members", "goal": "book"}, {"name": "Instructors"}],
        state="volunteered",
        provenance=prov,
        turn_id="evt_1",
    )
    store.write(
        "apps.list",
        [{"name": "Members", "purpose": "Use the app"}, {"name": "Studio"}],
        state="volunteered",
        provenance=prov,
        turn_id="evt_1",
    )
    brief = apply_slots(Brief(), store, catalog)
    by_name = {a.name: a for a in brief.apps}
    members = next(r for r in brief.roles if r.name_in_user_words == "Members")
    assert by_name["Members"].roles == [members.id]
    assert by_name["Studio"].roles == []  # no role of that name: nothing is guessed
