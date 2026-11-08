"""One app per project: free text that names several apps must not fill ``scope.apps``.

Found by live batch L1 (D-081). The mock handlers never return more than one app, but the real
model, asked for "a customer app, a seller app and an admin panel", volunteered all three as the
``scope.apps`` list. The product rule (spec 3.3 and the ``multi_app`` notice: "each app is its own
project, so let's start with one") is enforced in code: nothing is written, and the "which should
we build first?" card asks.
"""

from __future__ import annotations

from mde_contracts.calls import C1Output, SlotUpdate
from mde_discovery.testing import Conversation, ScriptedGateway

SEVERAL = ["customer", "business", "admin"]


def scripted(apps: list[str]) -> ScriptedGateway:
    return ScriptedGateway(
        {
            "C1.understand": [
                C1Output(
                    intent="idea",
                    confidence=0.9,
                    language="en",
                    slot_updates=[
                        SlotUpdate(slot_id="idea.archetype", value_text="commerce"),
                        SlotUpdate(slot_id="scope.apps", value_list=apps),
                    ],
                )
            ]
        }
    )


async def reach_apps_card(conv: Conversation) -> None:
    for _ in range(12):
        if conv.pending is None or conv.pending.card.id == "Q-apps-01":
            return
        await conv.tap_recommended()


async def test_several_apps_in_free_text_are_not_written_and_the_first_app_card_asks() -> None:
    conv = Conversation(gateway=scripted(SEVERAL))
    await conv.start()
    await conv.say("I need a customer app, a seller app and an admin panel for my shop")
    assert conv.slot("scope.apps") is None  # not 'customer, business, admin'
    await reach_apps_card(conv)
    assert conv.pending is not None and conv.pending.card.id == "Q-apps-01"
    await conv.tap("B")  # "The seller app"
    assert conv.value("scope.apps") == ["business"]


async def test_two_apps_are_also_not_written() -> None:
    conv = Conversation(gateway=scripted(["customer", "admin"]))
    await conv.start()
    await conv.say("a shopper app and an admin app")
    assert conv.slot("scope.apps") is None


async def test_one_app_named_in_free_text_is_still_written() -> None:
    conv = Conversation(gateway=scripted(["business"]))
    await conv.start()
    await conv.say("just the seller app for my shop")
    assert conv.value("scope.apps") == ["business"]
