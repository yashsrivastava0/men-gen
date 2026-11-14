"""How each kind of user signs in, read from the person's own facts (SPC-IN-10).

Compass keeps ONE setting for sign-in methods, but the Brief's facts can say more: "Farmers and drivers use phone code to
sign in", "The manager uses email and password". Reading only the setting would give the whole app one method and lose
the other. This module reads the facts (topic ``authentication``, or a sentence about signing in), names the roles each
sentence is about, and returns the methods per role. Code never invents a method: a sentence that names no method gives
none, and the method words are the ones of ``facts.yaml`` (the same list that reads the setting).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import cache

from mde_compass_intake import SpecInput
from mde_compass_intake.spec_input import Fact, Role
from mde_compass_intake.text import same_thing

from ._data import facts_data, honesty_data


@cache
def _pattern(words: tuple[str, ...]) -> re.Pattern[str]:
    alt = "|".join(re.escape(w) for w in sorted(words, key=len, reverse=True))
    return re.compile(rf"(?<![A-Za-z])(?:{alt})(?![A-Za-z])", re.IGNORECASE)


def _hit(text: str, words: list[str]) -> bool:
    return bool(words) and _pattern(tuple(words)).search(text) is not None


def _norm(value: str) -> str:
    return re.sub(r"[\s\-]+", "_", value.strip().lower())


@dataclass(frozen=True)
class RoleSignIn:
    """One sentence of the Brief about signing in: who, and by which methods."""

    fact_id: str
    role_ids: tuple[str, ...]  # Compass role ids the sentence is about (empty: it names no role)
    methods: tuple[str, ...]  # method facts (``phone_code_login``, ...), in a fixed order
    statement: str
    locked: bool  # the person's own words with a verified quote support it
    quote_id: str | None = None


def _method_facts(text: str) -> tuple[str, ...]:
    out = []
    for fact, words in facts_data().auth_methods.items():
        phrases = [w.replace("_", " ") for w in words]
        if _hit(text, phrases):
            out.append(fact)
    return tuple(sorted(out))


def _is_signin_fact(fact: Fact) -> bool:
    data = honesty_data().signin
    topics = {_norm(t) for t in fact.topics}
    if topics & {_norm(t) for t in data["topics"]}:
        return True
    return _hit(fact.statement, list(data["statement_words"]))


def _roles_of(fact: Fact, roles: tuple[Role, ...]) -> tuple[str, ...]:
    known = {r.id for r in roles}
    by_ref = [x for x in fact.refs if x.startswith("ROL-") and x in known]
    if by_ref:
        return tuple(sorted(dict.fromkeys(by_ref)))
    found = []
    for name in fact.entities:
        for r in roles:
            if same_thing(name, r.name) and r.id not in found:
                found.append(r.id)
    return tuple(sorted(found))


def signin_by_role(spec_input: SpecInput) -> tuple[RoleSignIn, ...]:
    """The sign-in sentences of the Brief with their roles and methods. A sentence that gives no method is skipped."""
    roles = tuple(spec_input.roles)
    out: list[RoleSignIn] = []
    for fact in spec_input.domain_facts:
        if not _is_signin_fact(fact):
            continue
        methods = _method_facts(fact.statement)
        if not methods:
            continue
        out.append(
            RoleSignIn(
                fact_id=fact.id,
                role_ids=_roles_of(fact, roles),
                methods=methods,
                statement=fact.statement,
                locked=fact.trust.grade == "locked" and fact.trust.verified_quote,
                quote_id=fact.trust.quote_ids[0] if fact.trust.quote_ids else None,
            )
        )
    return tuple(out)


def signin_facts(spec_input: SpecInput) -> set[str]:
    """The sign-in predicate facts the Brief's sentences set (the union over every role)."""
    return {m for s in signin_by_role(spec_input) for m in s.methods}


def declared_methods(spec_input: SpecInput) -> set[str]:
    """The sign-in predicate facts of Compass's own single setting (``access.methods``)."""
    methods = {_norm(v) for v in spec_input.auth_methods.values}
    out = set()
    for fact, words in facts_data().auth_methods.items():
        if methods & {_norm(w) for w in words}:
            out.add(fact)
    return out
