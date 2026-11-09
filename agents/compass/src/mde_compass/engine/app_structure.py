"""Expand an "how many apps" answer into app records (the catalog option values are structure codes).

`apps.list` holds app records (name, purpose, surface); the card options are the three structures a user can
pick from. Names come from the catalog strings (so they are localized), never from the code value itself.
"""

from __future__ import annotations

from collections.abc import Callable

from pydantic import JsonValue

# structure code -> [(string key of the app name, string key of its purpose)]
STRUCTURES: dict[str, list[tuple[str, str]]] = {
    "one_app": [("app.main", "app.main_purpose")],
    "customer_and_staff": [
        ("app.customer", "app.customer_purpose"),
        ("app.staff", "app.staff_purpose"),
    ],
    "app_plus_admin": [("app.main", "app.main_purpose"), ("app.manager", "app.manager_purpose")],
}


def expand_structure(value: JsonValue, t: Callable[[str], str]) -> JsonValue:
    """A structure code becomes a list of app dicts; any other value passes through unchanged."""
    if not isinstance(value, str) or value not in STRUCTURES:
        return value
    return [
        {"name": t(name_key), "purpose": t(purpose_key), "surface": "mobile"}
        for name_key, purpose_key in STRUCTURES[value]
    ]
