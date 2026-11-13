"""Write the synthetic Briefs used by the generality tests (different domains, languages and sizes).

These are NOT copies of any real run: they are small hand-written Compass format-2 exports (same key layout as a real
export) for ideas the Research code was never tuned on, so a test can prove it does not depend on the three real
examples. Run: ``uv run python agents/research/scripts/make_synthetic_fixtures.py``.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

OUT = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "synthetic"


def slot(value: Any, tier: str = "Locked", confidence: float = 0.9) -> dict[str, Any]:
    return {
        "tier": tier,
        "provenance": [{"type": "user_text", "ref": "FCT-001"}],
        "confidence": confidence,
        "status": "volunteered",
        "value": value,
    }


def rec(tier: str = "Locked", **fields: Any) -> dict[str, Any]:
    return {
        "tier": tier,
        "provenance": [{"type": "user_text", "ref": "FCT-001"}],
        "confidence": 0.9,
        "status": "volunteered",
        **fields,
    }


def build(name: str, spec: dict[str, Any]) -> dict[str, Any]:
    h = hashlib.sha256(name.encode()).hexdigest()
    features = [
        rec(
            id=f"FTR-{i:03d}", name=n, intent=n, priority=p, scope_status="in", apps=[], family=None
        )
        for i, (n, p) in enumerate(spec["features"], 1)
    ]
    roles = [
        rec(id=f"ROL-{i:03d}", name_in_user_words=n, goal=g, rough_permissions=[])
        for i, (n, g) in enumerate(spec.get("roles", []), 1)
    ]
    apps = [
        rec(
            "Default", id=f"APP-{i:03d}", name=n, purpose=p, surface="mobile", audience="", roles=[]
        )
        for i, (n, p) in enumerate(spec.get("apps", []), 1)
    ]
    entities = [
        rec("Default", id=f"ENT-{i:03d}", key=n.lower(), name=n, module=None)
        for i, n in enumerate(spec.get("entities", []), 1)
    ]
    facts = [
        rec(
            id=f"FCT-{i:03d}",
            type=t,
            kind=k,
            statement=s,
            topics=tp,
            entities=[],
            consumers=["research", "all"],
            quote=s,
            refs=[],
        )
        for i, (t, k, s, tp) in enumerate(spec.get("facts", []), 1)
    ]
    geo = spec.get("geo") or {}
    requests = [
        {
            "id": f"RR-{i:03d}",
            "topic": t,
            "question": q,
            "decision_unlocked": d,
            "consumer": "research",
            "entity": e,
            "geography": spec.get("geo_text", ""),
            "source_languages": spec["locales"],
            "terms": terms,
            "references": [],
            "prefer_sources": [],
            "avoid": [],
            "depth": "standard",
            "priority": "high",
            "shown_to_user_as": t,
            "user_added": False,
        }
        for i, (t, q, d, e, terms) in enumerate(spec.get("requests", []), 1)
    ]
    view = {
        "idea": {
            "summary": spec["summary"],
            "pitch": "",
            "category": spec["category"],
            "subcategory": spec.get("subcategory", ""),
            "business_model": spec.get("business_model", ""),
        },
        "geography": geo,
        "languages": {
            "locales": spec["locales"],
            "meta_locales": spec["locales"],
            "search_languages": spec["locales"],
        },
        "audience": {"segments": spec.get("audience", []), "usage_context": []},
        "references": [],
        "feature_intents": [{"name": n, "intent": n, "family": None} for n, _ in spec["features"]],
        "glossary": [],
        "research_requests": requests,
        "search_hints": {
            "disambiguated_names": [],
            "synonyms": spec.get("synonyms", []),
            "local_competitors": [],
            "languages": spec["locales"],
        },
        "declined": [],
        "guidance": {
            "size_band": "small",
            "depth": "light",
            "search_budget": 6,
            "gap_fill": False,
            "max_requests": 6,
            "category": spec["category"],
            "focus": [],
        },
        "modules": list(spec.get("modules", [])),
        "entities": spec.get("entities", []),
        "domain_facts": [],
    }
    brief = {
        "version": 1,
        "meta": {
            "session_id": f"ses_synthetic_{name}",
            "program_id": "",
            "degraded": False,
            "probes": {"research": True, "blueprint": True, "design": True},
            "locales": spec["locales"],
            "interview": {"fallback_count": 0},
        },
        "idea": {"summary": slot(spec["summary"]), "category": slot(spec["category"])},
        "context": {"geo": slot(geo, confidence=0.95)} if geo else {},
        "audience": {"segments": slot(spec.get("audience", []))},
        "apps": apps,
        "roles": roles,
        "scope": features,
        "references": [],
        "experience": {"brand.name": slot(spec["brand"])} if spec.get("brand") else {},
        "integrations": [
            rec(name=n, preference="real_later", notes="") for n in spec.get("integrations", [])
        ],
        "data": {"sensitive": slot(spec["sensitive"])} if spec.get("sensitive") else {},
        "glossary": [],
        "domain_facts": facts,
        "research_requests": requests,
        "search_hints": view["search_hints"],
        "declined": [],
        "modules": [
            rec("Default", id=f"MOD-{i:03d}", name=m, apps=[], roles=[])
            for i, m in enumerate(spec.get("modules", []), 1)
        ],
        "entities": entities,
        "flows": [],
        "ids": {"counters": {}},
        "_hash": h,
        "_section_hashes": {},
    }
    return {
        "_fixture": {
            "source": f"synthetic:{name}",
            "note": "Hand-written synthetic Brief for the generality tests.",
        },
        "export": {
            "format": "compass-full-output",
            "format_version": 2,
            "brief_version": 1,
            "brief_hash": h,
            "session_id": f"ses_synthetic_{name}",
            "approved_baseline": {"version": 1, "hash": h, "section_hashes": {}},
        },
        "brief": brief,
        "consumer_views": {"research": view},
        "structure": {"band": None, "source": None, "counts": None, "issues_count": 0},
        "spend": {"calls": 0, "cost_micros": 0, "by_call": {}},
    }


SPECS: dict[str, dict[str, Any]] = {
    "dental-clinic-dubai-ar-en": {
        "summary": "A booking and patient app for a dental clinic in Dubai, in Arabic and English.",
        "category": "dental clinic app",
        "locales": ["ar", "en"],
        "business_model": "Patients pay per visit; insurance claims for covered treatments",
        "geo": {"city": "Dubai", "country": "United Arab Emirates"},
        "geo_text": "Dubai, United Arab Emirates",
        "brand": "Smile Corner",
        "audience": ["Patients", "Reception staff", "Dentists"],
        "sensitive": ["health", "personal_ids"],
        "features": [
            ("Appointment booking", "must"),
            ("Patient records", "must"),
            ("Appointment reminders", "must"),
            ("Insurance claim submission", "nice"),
            ("Doctor schedules", "must"),
            ("Treatment plans and invoices", "nice"),
        ],
        "roles": [
            ("Patient", "Book and manage visits"),
            ("Receptionist", "Manage the daily schedule and check patients in"),
            ("Dentist", "See the schedule and update treatment notes"),
            ("Clinic manager", "See reports and manage staff"),
        ],
        "apps": [
            ("Patient app", "Book visits and see records"),
            ("Clinic app", "Run the clinic day"),
        ],
        "entities": ["Patient", "Appointment", "Treatment", "Invoice", "Insurance claim"],
        "integrations": ["payments", "messaging"],
        "synonyms": ["حجز موعد عيادة أسنان", "عيادة أسنان دبي"],
        "facts": [
            (
                "rule",
                "rule",
                "Patients can cancel an appointment free of charge up to 24 hours before it.",
                ["cancellations"],
            ),
            (
                "rule",
                "compliance",
                "The clinic must follow the health authority rules for patient records in Dubai.",
                ["compliance"],
            ),
            ("number", "number", "A standard check-up visit is 30 minutes.", ["scheduling"]),
            (
                "context",
                "integration_detail",
                "Insurance claims are submitted electronically to the insurer.",
                ["insurance"],
            ),
        ],
        "requests": [
            (
                "Dental clinic booking apps in Dubai",
                "Which features do dental clinic booking apps in Dubai usually offer?",
                "Which features belong in the first release",
                "dental clinic booking apps",
                ["dental clinic", "booking", "Dubai"],
            ),
            (
                "Insurance claim submission in the UAE",
                "How are dental insurance claims submitted electronically in the UAE?",
                "How to model claims",
                "dental insurance claims UAE",
                ["insurance", "claims", "UAE"],
            ),
        ],
        "modules": ["Appointments", "Patients", "Billing"],
    },
    "coffee-loyalty-nairobi-sw-en": {
        "summary": "A loyalty app for a small chain of coffee shops in Nairobi, in Swahili and English.",
        "category": "coffee shop loyalty app",
        "locales": ["sw", "en"],
        "business_model": "Free app; rewards drive repeat visits",
        "geo": {"city": "Nairobi", "country": "Kenya"},
        "geo_text": "Nairobi, Kenya",
        "brand": "Kahawa Corner",
        "audience": ["Customers", "Baristas", "Shop owner"],
        "features": [
            ("Digital stamp card", "must"),
            ("Pay with mobile money", "must"),
            ("Order ahead", "nice"),
            ("Vouchers and offers", "nice"),
            ("Branch locator", "nice"),
        ],
        "roles": [
            ("Customer", "Collect stamps and pay"),
            ("Barista", "Scan codes and confirm orders"),
            ("Shop owner", "See sales and manage offers"),
        ],
        "apps": [("Customer app", "Collect stamps and order")],
        "entities": ["Customer", "Stamp", "Reward", "Order"],
        "integrations": ["payments"],
        "synonyms": ["programu ya zawadi ya kahawa", "kadi ya stempu"],
        "facts": [
            (
                "rule",
                "rule",
                "A customer earns a free coffee after collecting ten stamps.",
                ["rewards"],
            ),
            (
                "context",
                "integration_detail",
                "Customers pay with mobile money from their phone.",
                ["payments"],
            ),
        ],
        "requests": [
            (
                "Coffee loyalty apps in Nairobi",
                "Which features do coffee shop loyalty apps in Nairobi usually offer?",
                "Which features belong in the first release",
                "coffee shop loyalty apps",
                ["coffee", "loyalty", "Nairobi"],
            )
        ],
        "modules": ["Loyalty", "Orders"],
    },
    "unit-converter-tiny": {
        "summary": "A tiny app that converts one unit to another.",
        "category": "unit converter",
        "locales": ["en"],
        "features": [("Convert units", "must")],
        "roles": [("User", "Convert a value")],
    },
}


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, spec in SPECS.items():
        path = OUT / f"{name}.json"
        path.write_text(
            json.dumps(build(name, spec), ensure_ascii=False, indent=1, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        sys.stdout.write(f"wrote {path.name}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
