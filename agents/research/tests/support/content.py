"""Scripted extraction and hand-built items for the content aggregates: provider dossiers, platform constraints, the
compliance register and the lane decisions (D-159, D-162).

``fact_extractor`` / ``platform_extractor`` / ``compliance_extractor`` are extraction handlers for ``run_engine`` overrides:
they read the REAL sentences of the page passages (so every quote is verbatim) and return atoms of the new kinds.
``make_item`` builds an ``Item`` directly for unit tests of the builders.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from mde_research_agent.assemble.capability import need_key_of
from mde_research_agent.catalog import load_catalog
from mde_research_agent.domain.research import AppliesTo, Item, Recency, SourceRef
from support.testing import Handler, atom, extractor

SENTENCE = re.compile(r"[^.]+\.")


def _sentences(passages: str) -> list[str]:
    return [
        " ".join(m.group(0).split())
        for m in SENTENCE.finditer(passages)
        if len(m.group(0).split()) > 6
    ]


def fact_extractor(provider: str = "Payco", need: str = "payments") -> Handler:
    """``provider_fact`` atoms from the fee, settlement and sandbox sentences of the fake pages."""

    def build(inp):  # type: ignore[no-untyped-def]
        out = []
        for s in _sentences(inp.passages):
            fee = re.search(r"start at (\d+(?:\.\d+)?) percent", s)
            days = re.search(r"settle in (\d+) business days", s)
            if fee:
                out.append(
                    atom(
                        "provider_fact",
                        s,
                        key=f"{provider} fee",
                        label=f"{provider} fee",
                        impact="high",
                        provider=provider,
                        need=need,
                        field="fees",
                        value=f"{fee.group(1)} percent",
                        typed={
                            "amount": fee.group(1),
                            "currency": None,
                            "unit": "percent",
                            "period": None,
                            "condition": None,
                        },
                        countries=[],
                    )
                )
            if days:
                out.append(
                    atom(
                        "provider_fact",
                        s,
                        key=f"{provider} settlement",
                        label=f"{provider} settlement",
                        impact="high",
                        provider=provider,
                        need=need,
                        field="settlement",
                        value=f"{days.group(1)} business days",
                        typed={
                            "amount": days.group(1),
                            "currency": None,
                            "unit": "business days",
                            "period": None,
                            "condition": None,
                        },
                        countries=[],
                    )
                )
            if "sandbox" in s.lower():
                out.append(
                    atom(
                        "provider_fact",
                        s,
                        key=f"{provider} sandbox",
                        label=f"{provider} sandbox",
                        provider=provider,
                        need=need,
                        field="sandbox",
                        value="test keys",
                        typed=None,
                        countries=[],
                    )
                )
        return out

    return extractor(build)


def platform_extractor() -> Handler:
    """``platform_constraint`` atoms from the sentences that name a platform."""

    def build(inp):  # type: ignore[no-untyped-def]
        out = []
        for s in _sentences(inp.passages):
            if "Android and iOS" in s:
                out.append(
                    atom(
                        "platform_constraint",
                        s,
                        key=f"{inp.anchor} rule",
                        label=f"{inp.anchor} rule",
                        capability=inp.anchor,
                        platform="both",
                        rule=s,
                        effect_on_build="needs_permission",
                        status=None,
                    )
                )
        return out

    return extractor(build)


def compliance_extractor(country: str = "in", regime: str = "Test Regime Act") -> Handler:
    """``compliance_note`` atoms (with the register fields) from the sentences that have a status and a date."""

    def build(inp):  # type: ignore[no-untyped-def]
        out = []
        for s in _sentences(inp.passages):
            if "created date" in s:
                out.append(
                    atom(
                        "compliance_note",
                        s,
                        key=f"{inp.anchor} duty",
                        label=f"{inp.anchor} duty",
                        about_region=country,
                        topic=inp.anchor,
                        authority="The Authority",
                        summary=s,
                        effective_date=None,
                        regime=regime,
                        country=country,
                        applies_to=["owner records"],
                        penalty=None,
                    )
                )
        return out

    return extractor(build)


def combine(*handlers: Handler) -> Handler:
    """One extraction handler that returns the items of every handler (kinds merged), for a job with several scripted kinds."""

    def handle(inp):  # type: ignore[no-untyped-def]
        outs = [h(inp) for h in handlers]
        merged: dict[str, list[dict[str, Any]]] = {k: [] for k in inp.kinds}
        for out in outs:
            for kind, atoms in out.model_dump().items():  # type: ignore[union-attr]
                merged.setdefault(kind, []).extend(atoms)
        return type(outs[0]).model_validate(merged)

    return handle


def make_item(
    iid: str,
    provider: str,
    field: str,
    value: str,
    *,
    typed: dict[str, Any] | None = None,
    evidence: str = "official",
    need: str = "payments",
    countries: list[str] | None = None,
    region: str = "global",
    domain: str = "docs.payco.example",
    source_class: str = "official",
    url: str | None = None,
    flags: list[str] | None = None,
    kind: str = "provider_fact",
    published: date = date(2026, 9, 1),
    data: dict[str, Any] | None = None,
) -> Item:
    """A ``provider_fact`` item (or another kind with ``data``) as assembly would build it."""
    body = data or {
        "provider": provider,
        "need": need,
        "field": field,
        "value": value,
        "typed": typed,
        "countries": countries or [],
    }
    return Item(
        id=iid,
        job="services",
        spec_need="Contract and integrations",
        kind=kind,
        label=f"{provider} {field}",
        claim=value,
        evidence=evidence,  # type: ignore[arg-type]
        flags=flags or [],  # type: ignore[arg-type]
        about_region=region,
        applies_to=[AppliesTo(kind="INT", id=key, relation="expects")]
        if (key := need_key_of(need, load_catalog().needs))
        else [AppliesTo(kind="NEW", id=None, relation="suggests")],
        sources=[
            SourceRef(
                id=f"S-{iid[-3:]}",
                domain=domain,
                source_class=source_class,  # type: ignore[arg-type]
                url=url or f"https://{domain}/{iid}",
                retrieved_on=date(2026, 11, 11),
                published_on=published,
            )
        ],
        recency=Recency(newest_source=published),
        data=body,
    )
