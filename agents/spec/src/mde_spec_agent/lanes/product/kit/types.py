"""The closed vocabularies as schema types. A schema needs real `Literal` types (a pydantic enum in the JSON schema, and a
type a checker can read), so the words are written out here once; `tests/lanes/product/test_calls.py` fails when one of
them drifts from the data it mirrors (spec-core's vocabularies, the graph's own enums, `data/vocab.yaml`)."""

from __future__ import annotations

from typing import Literal

FieldType = Literal[
    "text", "number", "money", "date", "phone", "email", "choice", "file", "location", "code",
    "secret", "quantity", "toggle", "id", "enum", "list", "ref",
]  # fmt: skip
EntityClass = Literal["primary", "secondary", "join", "log"]
Method = Literal["GET", "POST", "PUT", "PATCH", "DELETE"]
ErrorCode = Literal[
    "unauthenticated", "forbidden", "not_found", "validation_failed", "conflict", "duplicate",
    "version_conflict", "idempotency_conflict", "payment_declined", "rate_limited", "unavailable",
    "timeout",
]  # fmt: skip
Recovery = Literal[
    "stay", "retry", "go_back", "reload", "offline", "sign_in_again", "contact_support"
]
Voice = Literal["must", "usually", "could"]
Side = Literal["request", "response"]
Mode = Literal["mock", "adapter"]
Emotion = Literal["calm", "curious", "confident", "anxious", "frustrated", "delighted"]
Variant = Literal["main", "first_run", "returning", "cross_app", "recovery"]
MockProfile = Literal["empty", "typical", "busy", "extreme", "broken", "offline"]
Actions = Literal["none", "view", "create", "edit", "delete", "manage"]
Status = Literal["in", "later"]
Reason = Literal["none", "user", "capacity", "first_release"]
