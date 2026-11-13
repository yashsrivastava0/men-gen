#!/usr/bin/env python3
"""A stand-in for the Research agent's HTTP API (``docs/api/research-agent.md``), standard library only.

Two jobs: the console tests drive it (``test_research_ui.py``), and you can run the console against it by hand
while the real Research server is not up::

    python apps/web/tests/fake_research_server.py --port 8120
    uv run python -m mde_compass --memory --port 8110      # then open http://127.0.0.1:8110/ui/

It speaks the documented contract: the dev auth headers (``Bearer dev-token``), CORS with preflight, the export
refusals (422), ``run_active`` (409), the snapshot, the resumable event stream (``Last-Event-ID`` or ``?after=``),
skip, cancel a topic, the review (findings) page data, ``latest`` / ``result`` (``research.json``) and reject.

A run plays a scripted, realistic sequence on a background thread: a plan of eight topics (two "we already know"),
queries, results, pages (one skipped, one behind a paywall), facts moving from "checking" to "checked", entity
chips, two rounds, a gap, a slow-provider warning and a finish. ``--speed`` scales the pauses (1.0 is about a
minute; 0 plays instantly). The scenario is ``complete`` by default; ``partial`` leaves one topic with nothing
found, ``fail`` ends in ``run.failed`` and ``interrupted`` stops without a final event. A program id or an
export field ``_fake_scenario`` containing one of those words picks it per run (a Compass export has neither, so
use the ``--scenario`` flag or the word in a hand-made file).

Nothing here is real research: the findings are made-up examples for a yoga studio app.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import secrets
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

PREFIX = "/v1/research"
MAX_EXPORT_BYTES = 8 * 1024 * 1024
ALLOWED_HEADERS = "Authorization, Content-Type, X-Tenant-Id, X-User-Id, Last-Event-ID, Accept"
DEFAULT_ORIGINS = ("http://127.0.0.1:8110", "http://localhost:8110")
TOTAL_SIM_SECONDS = 62  # what the scripted run claims it will take (for eta_s)

JOB_LANE = {
    "features": "blueprint",
    "structure": "blueprint",
    "flows": "blueprint",
    "data": "mock",
    "services": "integrations",
    "design": "design",
    "screens": "screens",
    "local": "verify",
}

# (id, label, job, priority, slots required)
# fmt: off
TOPICS: list[tuple[str, str, str, int, int]] = [
    ("T-01", "Comparable yoga and fitness class apps", "features", 95, 3),
    ("T-02", "How studios sell class packs and memberships", "features", 90, 3),
    ("T-03", "Payment options studios use in India", "services", 88, 3),
    ("T-04", "Reminders and messaging members expect", "services", 80, 3),
    ("T-05", "Booking and cancellation flows", "flows", 85, 3),
    ("T-06", "Look and feel of wellness apps", "design", 70, 3),
    ("T-07", "Key screens in class booking apps", "screens", 75, 3),
    ("T-08", "GST and local rules for fitness studios", "local", 82, 3),
]
DECLINED = ["a currency symbol", "the app's name"]

# What each topic does. Ops: ("q", query, variant, [domains]) | ("page", domain, title, skip_reason|None)
# | ("fact", label, kind, domain, level, rejected?) | ("ent", name, kind). "r2" runs only for topics that get a
# second look (T-07 and T-08, and T-06 when the scenario is "partial").
SCRIPT: dict[str, dict[str, list[tuple[Any, ...]]]] = {
    "T-01": {
        "r1": [
            ("q", "yoga studio app features pricing Bengaluru", "descriptive", ["mindbodyonline.com", "cultfit.com", "wellnessliving.com"]),
            ("page", "mindbodyonline.com", "Class booking software for studios", None),
            ("fact", "Class timetable with live availability", "feature", "mindbodyonline.com", "core"),
            ("fact", "Waitlist with automatic promotion", "feature", "mindbodyonline.com", "core"),
            ("ent", "Mindbody", "competitor"),
            ("page", "cultfit.com", "Cult.fit app overview", None),
            ("fact", "Instructor profiles on class cards", "feature", "cultfit.com", "common"),
            ("ent", "Cult.fit", "competitor"),
        ],
    },
    "T-02": {
        "r1": [
            ("q", "yoga studio class pack membership pricing India", "literal", ["yogajournal.com", "wellnessliving.com"]),
            ("page", "wellnessliving.com", "How studios price class packs", None),
            ("page", "yogajournal.com", "The business of yoga studios", "paywall"),
            ("fact", "Class packs with an expiry date", "feature", "wellnessliving.com", "core"),
            ("fact", "Unlimited monthly membership", "feature", "wellnessliving.com", "common"),
            ("fact", "Pack: credits, price and validity", "data_entity", "wellnessliving.com", "core"),
        ],
    },
    "T-03": {
        "r1": [
            ("q", "yoga studio payment gateway UPI cards India", "local", ["razorpay.com", "docs.razorpay.com", "npci.org.in"]),
            ("page", "docs.razorpay.com", "Payments API overview", None),
            ("fact", "Razorpay payments (UPI, cards)", "service_option", "docs.razorpay.com", "core"),
            ("ent", "Razorpay", "provider"),
            ("page", "npci.org.in", "UPI product statistics", None),
            ("fact", "UPI is the main way Indians pay", "fact", "npci.org.in", "core"),
            ("fact", "PhonePe and Paytm gateways", "service_option", "razorpay.com", "advanced"),
            ("ent", "NPCI", "authority"),
        ],
    },
    "T-04": {
        "r1": [
            ("q", "class reminders WhatsApp Business API studios India", "descriptive", ["developers.facebook.com", "firebase.google.com"]),
            ("page", "firebase.google.com", "Cloud messaging for mobile apps", None),
            ("page", "developers.facebook.com", "WhatsApp Business Platform", None),
            ("fact", "WhatsApp reminders (Business API)", "service_option", "developers.facebook.com", "common"),
            ("fact", "Push notifications with Firebase", "service_option", "firebase.google.com", "core"),
            ("fact", "Reminder channel preferences", "feature", "firebase.google.com", "advanced"),
        ],
    },
    "T-05": {
        "r1": [
            ("q", "class booking flow cancellation window refund credits", "descriptive", ["mindbodyonline.com", "acuityscheduling.com"]),
            ("page", "acuityscheduling.com", "Cancellation policies that work", None),
            ("page", "mindbodyonline.com", "Late cancel and no-show settings", None),
            ("fact", "Book a class in three taps", "flow", "mindbodyonline.com", "core"),
            ("fact", "Cancel with credit refund", "flow", "acuityscheduling.com", "core"),
            ("fact", "Late-cancel window between 2 and 12 hours", "rule", "acuityscheduling.com", "common"),
            ("fact", "Bottom tabs: Classes, Bookings, Packs, Profile", "structure", "mindbodyonline.com", "core"),
        ],
    },
    "T-06": {
        "r1": [
            ("q", "wellness app design calm colours photography", "literal", ["material.io", "uxdesign.cc"]),
            ("page", "uxdesign.cc", "Designing calm: wellness apps", None),
            ("fact", "Calm earthy palette with one accent colour", "design_pattern", "uxdesign.cc", "common"),
            ("fact", "Photography-led class cards", "design_pattern", "uxdesign.cc", "common"),
        ],
        "r2": [
            ("q", "accessible type sizes mobile app guidelines", "descriptive", ["material.io", "w3.org"]),
            ("page", "w3.org", "Mobile accessibility mapping", None),
            ("fact", "Large touch targets and readable type", "design_pattern", "w3.org", "core"),
        ],
    },
    "T-07": {
        "r1": [
            ("q", "class booking app screens class detail packs wallet", "descriptive", ["mindbodyonline.com", "uxdesign.cc"]),
            ("page", "uxdesign.cc", "Booking app screen patterns", None),
            ("fact", "Class detail screen", "screen", "uxdesign.cc", "core"),
        ],
        "r2": [
            ("q", "studio app packs and wallet screen examples", "literal", ["mindbodyonline.com"]),
            ("page", "mindbodyonline.com", "Member app: packs and passes", None),
            ("page", "paywalled-journal.example", "Studio technology review", "paywall"),
            ("fact", "Packs and wallet screen", "screen", "mindbodyonline.com", "core"),
            ("fact", "Booking confirmation with add to calendar", "screen", "mindbodyonline.com", "common"),
            ("fact", "Studio and instructor directory", "structure", "mindbodyonline.com", "advanced"),
        ],
    },
    "T-08": {
        "r1": [
            ("q", "GST rate fitness yoga services India invoice", "local", ["cleartax.in", "cbic.gov.in"]),
            ("page", "cleartax.in", "GST on gym and fitness services", None),
            ("fact", "GST on fitness services is 18 percent", "rule", "cleartax.in", "common"),
        ],
        "r2": [
            ("q", "GST invoice GSTIN requirements small studios", "local", ["cbic.gov.in", "gst.gov.in"]),
            ("page", "cbic.gov.in", "Tax invoice rules", None),
            ("page", "gst.gov.in", "Registration thresholds", "blocked"),
            ("fact", "GST invoice on every payment", "rule", "cbic.gov.in", "common"),
            ("fact", "Bengaluru studios take bookings on WhatsApp", "fact", "cleartax.in", "limited_evidence", True),
            ("ent", "GSTN", "authority"),
        ],
    },
}

# ---- findings: (id, job, kind, label, claim, use_note, impact, confidence, flags, target, sources, prevalence)
# sources: (domain, url, published_on); prevalence: (seen_in, of, level) or None
ITEMS: list[tuple[Any, ...]] = [
    ("RI-001", "features", "feature", "Class timetable with live availability", "Every comparable app opens on a weekly timetable that shows how many seats are left in each class.", "Make the timetable the home screen.", "high", "high", [], "T-01", [("mindbodyonline.com", "https://www.mindbodyonline.com/business/scheduling", "2026-04-18"), ("cultfit.com", "https://www.cult.fit/", "2026-05-02")], (5, 5, "core")),
    ("RI-002", "features", "feature", "Waitlist with automatic promotion", "Full classes offer a waitlist. The first person waiting is moved in when a seat opens and is told at once.", "Add a waitlist state to bookings.", "medium", "high", [], "T-01", [("mindbodyonline.com", "https://www.mindbodyonline.com/business/scheduling", "2026-04-18")], (4, 5, "core")),
    ("RI-003", "features", "feature", "Instructor profiles", "Class cards show the instructor's photo and a short bio so members can pick a teacher they like.", "Add a teacher to every class.", "low", "medium", ["single-source"], "T-01", [("cultfit.com", "https://www.cult.fit/", "2026-05-02")], (3, 5, "common")),
    ("RI-004", "features", "feature", "Class packs with an expiry date", "Studios sell packs of 5, 10 or 20 classes, usually valid for 60 to 90 days after purchase.", "Model packs with credits and validity.", "high", "high", [], "T-02", [("wellnessliving.com", "https://www.wellnessliving.com/blog/class-pack-pricing/", "2026-03-11"), ("yogajournal.com", "https://yogajournal.com/studio-business/", "2025-12-03")], (4, 5, "core")),
    ("RI-005", "features", "feature", "Unlimited monthly membership", "Most studios also offer an unlimited monthly plan next to class packs.", "Offer it as a later phase.", "medium", "medium", [], "T-02", [("wellnessliving.com", "https://www.wellnessliving.com/blog/class-pack-pricing/", "2026-03-11")], (3, 5, "common")),
    ("RI-006", "data", "data_entity", "Pack: credits, price and validity", "A pack needs a number of credits, a price, a validity in days and which class types it covers.", "Use these fields for the Pack record.", "high", "high", [], "T-02", [("wellnessliving.com", "https://www.wellnessliving.com/blog/class-pack-pricing/", "2026-03-11")], (4, 5, "core")),
    ("RI-007", "services", "service_option", "Razorpay payments (UPI, cards)", "Razorpay takes UPI and card payments and has the best-documented developer tools for small studios in India.", "Plan the payments integration on Razorpay.", "high", "high", [], "T-03", [("docs.razorpay.com", "https://docs.razorpay.com/docs/payments", "2026-05-02"), ("razorpay.com", "https://razorpay.com/pricing/", "2026-04-20")], (4, 5, "core")),
    ("RI-008", "services", "service_option", "PhonePe and Paytm gateways", "Other gateways exist, but Razorpay is the one most small studios start with.", "Keep as a fallback option.", "low", "low", ["single-source", "limited_evidence"], "T-03", [("razorpay.com", "ftp://files.example.org/gateways-comparison.pdf", "2026-04-20")], (2, 5, "advanced")),
    ("RI-009", "local", "fact", "UPI is the main way Indians pay", "UPI carries most small digital payments in India, so a studio app should offer it first at checkout.", "Put UPI first in the payment list.", "high", "medium", ["stale"], "T-03", [("npci.org.in", "https://www.npci.org.in/what-we-do/upi/product-statistics", "2024-01-10")], (5, 5, "core")),
    ("RI-010", "services", "service_option", "WhatsApp reminders (Business API)", "Studios in Indian cities send class reminders on WhatsApp more often than by SMS.", "Plan WhatsApp as a reminder channel.", "medium", "medium", ["entity_from_model"], "T-04", [("developers.facebook.com", "https://developers.facebook.com/docs/whatsapp", "2026-02-14")], (3, 5, "common")),
    ("RI-011", "services", "service_option", "Push notifications with Firebase", "Push reminders usually go out two hours and fifteen minutes before a class.", "Use push for the default reminder.", "medium", "high", [], "T-04", [("firebase.google.com", "https://firebase.google.com/docs/cloud-messaging", "2026-05-20")], (4, 5, "core")),
    ("RI-012", "features", "feature", "Reminder channel preferences", "Members can switch reminder channels on and off from their profile.", "Add a small settings screen.", "low", "low", ["limited_evidence"], "T-04", [("firebase.google.com", "https://firebase.google.com/docs/cloud-messaging", "2026-05-20")], (2, 5, "advanced")),
    ("RI-013", "flows", "flow", "Book a class in three taps", "Pick a class, choose a pack or a credit, confirm. Anything longer loses members.", "Design the booking flow as three steps.", "high", "high", [], "T-05", [("mindbodyonline.com", "https://www.mindbodyonline.com/business/scheduling", "2026-04-18")], (5, 5, "core")),
    ("RI-014", "flows", "flow", "Cancel with credit refund", "Cancelling before the cut-off returns the credit to the member's pack.", "Return credits on early cancel.", "high", "medium", ["conflict"], "T-05", [("acuityscheduling.com", "https://acuityscheduling.com/learn/cancellation-policy", "2026-01-22")], (4, 5, "core")),
    ("RI-015", "flows", "rule", "Late-cancel window", "Studios set the cut-off between two and twelve hours before class. Six hours is the most common.", "Make the window a studio setting.", "medium", "low", ["conflict", "needs-confirm"], "T-05", [("acuityscheduling.com", "https://acuityscheduling.com/learn/cancellation-policy", "2026-01-22"), ("mindbodyonline.com", "https://www.mindbodyonline.com/business/scheduling", "2026-04-18")], (3, 5, "common")),
    ("RI-016", "structure", "structure", "Bottom tabs: Classes, Bookings, Packs, Profile", "Four bottom tabs keep booking one tap away from anywhere in the app.", "Use four bottom tabs.", "medium", "high", [], "T-05", [("mindbodyonline.com", "https://www.mindbodyonline.com/business/scheduling", "2026-04-18")], (4, 5, "core")),
    ("RI-017", "design", "design_pattern", "Calm earthy palette with one accent", "Wellness apps use soft earth tones with a single accent colour for the main action.", "Pick one accent for Book.", "low", "medium", [], "T-06", [("uxdesign.cc", "https://uxdesign.cc/designing-calm", "2026-02-01")], (4, 5, "common")),
    ("RI-018", "design", "design_pattern", "Photography-led class cards", "Class cards lead with a large photo of the studio or teacher.", "Plan for photo assets.", "low", "medium", ["single-source"], "T-06", [("uxdesign.cc", "https://uxdesign.cc/designing-calm", "2026-02-01")], (3, 5, "common")),
    ("RI-019", "design", "design_pattern", "Large touch targets and readable type", "Buttons at least 44 points tall and body text of 16 points or more.", "Use these sizes everywhere.", "medium", "high", [], "T-06", [("w3.org", "https://www.w3.org/TR/mobile-accessibility-mapping/", "2026-03-30")], (5, 5, "core")),
    ("RI-020", "screens", "screen", "Class detail", "A class page shows time, teacher, level, seats left and one Book button.", "Build it as the main detail screen.", "high", "high", [], "T-07", [("uxdesign.cc", "https://uxdesign.cc/booking-app-patterns", "2026-02-08")], (5, 5, "core")),
    ("RI-021", "screens", "screen", "Packs and wallet", "A wallet screen shows credits left, expiry and a Buy more button.", "Build it behind the Packs tab.", "high", "high", [], "T-07", [("mindbodyonline.com", "https://www.mindbodyonline.com/business/member-app", "2026-04-18")], (4, 5, "core")),
    ("RI-022", "screens", "screen", "Booking confirmation with add to calendar", "After booking, members see a confirmation with an add-to-calendar button.", "Add a confirmation step.", "medium", "medium", [], "T-07", [("mindbodyonline.com", "https://www.mindbodyonline.com/business/member-app", "2026-04-18")], (4, 5, "common")),
    ("RI-023", "structure", "structure", "Studio and instructor directory", "Some larger studios add a directory of teachers and locations.", "Keep it for a later release.", "low", "low", ["limited_evidence"], "T-07", [("mindbodyonline.com", "https://www.mindbodyonline.com/business/member-app", "2026-04-18")], (2, 5, "advanced")),
    ("RI-024", "local", "rule", "GST on fitness services is 18 percent", "Fitness and yoga services are charged 18 percent GST in India.", "Show prices with GST included.", "high", "medium", ["needs-confirm", "legal-adjacent", "single-source"], "T-08", [("cleartax.in", "https://cleartax.in/s/gst-rates-fitness-services", "2025-10-14")], None),
    ("RI-025", "local", "rule", "GST invoice on every payment", "Studios issue a tax invoice with the studio's GSTIN for each pack sold.", "Generate an invoice after payment.", "high", "medium", ["legal-adjacent", "needs-confirm"], "T-08", [("cbic.gov.in", "https://www.cbic.gov.in/gst-invoice-rules", "2026-01-30")], None),
    ("RI-026", "local", "fact", "Bengaluru studios take bookings on WhatsApp", "Many boutique studios in Bengaluru still take bookings in WhatsApp groups.", "Offer a WhatsApp share link.", "low", "low", ["geo_uncertain", "single-source"], "T-08", [("cleartax.in", "https://cleartax.in/s/studio-business-india", "2025-08-05")], (1, 5, "limited_evidence")),
]
NORMS: list[tuple[Any, ...]] = [
    ("feature", "class-booking", "Class booking", (5, 5, "core"), "2026-05-02", ["RI-001", "RI-013"], "FTR-001"),
    ("feature", "waitlist", "Waitlist", (4, 5, "core"), "2026-04-18", ["RI-002"], None),
    ("feature", "class-packs", "Class packs", (4, 5, "core"), "2026-03-11", ["RI-004"], "FTR-002"),
    ("feature", "instructor-profiles", "Instructor profiles", (3, 5, "common"), "2026-05-02", ["RI-003"], None),
    ("feature", "referral-credits", "Referral credits", (1, 5, "limited_evidence"), "2025-08-05", [], None),
    ("screen", "class-detail", "Class detail", (5, 5, "core"), "2026-02-08", ["RI-020"], "SCR-003"),
    ("flow", "book-class", "Book a class", (5, 5, "core"), "2026-04-18", ["RI-013"], "FLW-001"),
    ("entity", "class", "Class", (5, 5, "core"), "2026-04-18", ["RI-001"], "ENT-001"),
    ("entity", "pack", "Pack", (4, 5, "core"), "2026-03-11", ["RI-006"], "ENT-002"),
    ("integration", "payments", "Payments", (5, 5, "core"), "2026-05-02", ["RI-007"], "INT-001"),
    ("integration", "whatsapp", "WhatsApp messaging", (3, 5, "common"), "2026-02-14", ["RI-010"], None),
    ("feature", "unlimited-membership", "Unlimited monthly membership", (2, 5, "advanced"), "2026-03-11", ["RI-005"], None),
    ("integration", "wallet-passes", "Apple and Google Wallet passes", (1, 5, "emerging"), "2026-01-09", [], None),
    ("design", "photo-cards", "Photography-led cards", (3, 5, "common"), "2026-02-01", ["RI-018"], None),
]
SUGGESTIONS: dict[str, list[tuple[str, list[str], tuple[int, int, str] | None]]] = {
    "features": [
        ("Guest passes for a first class", ["RI-001"], (3, 5, "common")),
        ("Referral credits", [], (1, 5, "limited_evidence")),
        ("Workshops and events", [], (2, 5, "advanced")),
    ],
    "screens": [
        ("Teacher schedule", ["RI-003"], (3, 5, "common")),
        ("Studio map and directions", [], None),
    ],
    "fields": [
        ("Injury notes at sign-up", [], (2, 5, "advanced")),
        ("Emergency contact", [], (3, 5, "common")),
    ],
    "states": [
        ("Fully booked class", ["RI-002"], (4, 5, "core")),
    ],
    "validations": [
        ("Indian phone number format", [], None),
    ],
}
CONFIRM = [
    ("RI-024", "A tax rate is a number you should confirm with your accountant."),
    ("RI-025", "Invoice rules are legal-adjacent; check them for your studio's registration."),
]
CONFLICTS = [
    {"topic": "How late a member can cancel", "item_ids": ["RI-014", "RI-015"], "note": "One source says a refund is always given before the cut-off; another says the window ranges from two to twelve hours."},
]

# fmt: on


def now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(13).upper()}"


class _Skip(Exception):
    """The owner pressed Skip."""


class _Fail(Exception):
    """The scenario ends in run.failed."""


class _Interrupted(Exception):
    """The scenario ends with no final event."""


class Run:
    """One scripted run: stored events, live counters, topic rows, and the thread that plays the script."""

    def __init__(self, run_id: str, program_id: str, scenario: str, speed: float) -> None:
        self.id = run_id
        self.program_id = program_id
        self.scenario = scenario
        self.speed = speed
        self.cond = threading.Condition()
        self.events: list[dict[str, Any]] = []
        self.status = "queued"
        self.phase = ""
        self.round = 0
        self.finished = False
        self.skip_flag = False
        self.cancelled: set[str] = set()
        self.rejected: dict[str, str] = {}
        self.entities: list[str] = []
        self.reason: str | None = None
        self.content_hash: str | None = None
        self.result_status: str | None = None
        self.started_at = now_iso()
        self.finished_at: str | None = None
        self.sim_t = 0.0
        self.dropped = False
        self.gap_done = False
        self.counters: dict[str, Any] = {
            "searches_used": 0, "searches_planned": 28, "searches_ceiling": 40, "pages_read": 0,
            "facts_verified": 0, "targets_done": 0, "targets_total": len(TOPICS), "elapsed_s": 0, "eta_s": None,
        }  # fmt: skip
        self.targets = [
            {"id": t[0], "label": t[1], "job": t[2], "lane": JOB_LANE[t[2]], "priority": t[3], "status": "queued", "slots_filled": 0, "slots_required": t[4]}
            for t in TOPICS
        ]  # fmt: skip
        self.thread = threading.Thread(target=self._play, name=f"fake-run-{run_id}", daemon=True)

    # -- events ---------------------------------------------------------------------------------------

    def emit(
        self,
        type_: str,
        label: str,
        detail: dict[str, Any] | None = None,
        *,
        target_id: str | None = None,
        phase: str | None = None,
    ) -> dict[str, Any]:
        with self.cond:
            if phase:
                self.phase = phase
            c = self.counters
            c["elapsed_s"] = int(self.sim_t)
            c["eta_s"] = (
                None
                if self.sim_t < 3 or self.finished
                else max(0, TOTAL_SIM_SECONDS - int(self.sim_t))
            )
            event = {
                "id": new_id("evt"), "seq": len(self.events) + 1, "ts": now_iso(), "run_id": self.id, "type": type_,
                "target_id": target_id, "round": self.round, "phase": self.phase, "label": label,
                "detail": detail or {}, "counters": dict(c), "schema": "research.event.v1",
            }  # fmt: skip
            self.events.append(event)
            self.cond.notify_all()
            return event

    def tick(self, delay: float) -> None:
        """Advance the scripted clock; really wait ``delay * speed`` seconds, noticing a Skip within 50 ms."""
        self.sim_t += delay
        end = time.monotonic() + delay * self.speed
        while True:
            if self.skip_flag:
                raise _Skip
            left = end - time.monotonic()
            if left <= 0:
                return
            time.sleep(min(0.05, left))

    def target(self, tid: str) -> dict[str, Any]:
        return next(t for t in self.targets if t["id"] == tid)

    # -- the script -----------------------------------------------------------------------------------

    def _play(self) -> None:
        try:
            self._script()
        except _Skip:
            self._end("skipped")
        except _Fail:
            self._fail()
        except _Interrupted:
            with self.cond:
                self.status = "interrupted"
                self.finished = True
                self.finished_at = now_iso()
                self.cond.notify_all()

    def _script(self) -> None:
        with self.cond:
            self.status = "running"
        self.emit(
            "run.started",
            "Researching your idea",
            {
                "load_class": "standard",
                "load_score": 27,
                "minutes": 4,
                "plan_source": "mixed",
                "degraded": False,
                "depth_word": "a standard-depth study",
            },
            phase="intake",
        )
        self.tick(0.8)
        plan = [
            {k: t[k] for k in ("id", "label", "job", "lane", "priority", "status")}
            for t in self.targets
        ]
        self.emit(
            "plan.ready",
            f"Plan ready: {len(plan)} topics to look into",
            {
                "targets": plan,
                "declined": DECLINED,
                "planned_searches": 28,
                "plan_source": "mixed",
                "country": "in",
            },
            phase="plan",
        )
        self.round = 1
        second_look = ["T-07", "T-08"] + (["T-06"] if self.scenario == "partial" else [])
        for tid, *_ in TOPICS:
            self._topic(tid, "r1", finish=tid not in second_look)
            if self.scenario == "fail" and tid == "T-02":
                raise _Fail
            if self.scenario == "interrupted" and tid == "T-03":
                raise _Interrupted
        self._round_done(1, new_slots=sum(t["slots_filled"] for t in self.targets))
        self.tick(1.0)
        self.emit(
            "gap.detected",
            f"Taking a second look at {len(second_look)} topics",
            {"targets": second_look, "count": len(second_look)},
            phase="audit",
        )
        self.tick(0.8)
        self.emit(
            "warning",
            "A search service is slow, so this may take a bit longer",
            {
                "code": "provider_slow",
                "message": "A search service is answering slowly. We are waiting for it.",
            },
            phase="search",
        )
        self.round = 2
        for tid in second_look:
            self._topic(tid, "r2", finish=True)
        self._round_done(2, new_slots=3)
        with self.cond:
            self.status = "committing"
        self.tick(1.0)
        self._end("partial" if self._has_gaps() else "complete")

    def _topic(self, tid: str, stage: str, *, finish: bool) -> None:
        if tid in self.cancelled:
            return
        t = self.target(tid)
        if stage == "r1" or t["status"] == "queued":
            with self.cond:
                t["status"] = "running"
            self.emit(
                "target.started",
                f"Looking into: {t['label']}",
                {
                    "job": t["job"],
                    "topic": t["label"],
                    "slots_filled": t["slots_filled"],
                    "slots_required": t["slots_required"],
                },
                target_id=tid,
                phase="search",
            )
        checking: list[tuple[Any, ...]] = []
        for op in SCRIPT[tid].get(stage, []):
            if tid in self.cancelled:
                return
            self.tick(0.55 if op[0] != "q" else 0.9)
            kind = op[0]
            if kind == "q":
                _, query, variant, domains = op
                self.counters["searches_used"] += 1
                self.emit(
                    "query.issued",
                    f"Searching: {query}",
                    {
                        "query": query,
                        "variant": variant,
                        "lang": "en",
                        "country": "in",
                        "provider": "fake",
                    },
                    target_id=tid,
                    phase="search",
                )
                self.tick(0.6)
                self.emit(
                    "search.results",
                    f"Found {len(domains) * 4} results",
                    {"count": len(domains) * 4, "domains": domains, "query": query},
                    target_id=tid,
                    phase="search",
                )
            elif kind == "page":
                _, domain, title, skip_reason = op
                self.emit(
                    "page.reading",
                    f"Reading {domain}",
                    {"domain": domain, "title": title},
                    target_id=tid,
                    phase="read",
                )
                self.tick(0.5)
                if skip_reason:
                    self.emit(
                        "page.skipped",
                        f"Skipped {domain}",
                        {"domain": domain, "title": title, "reason": skip_reason},
                        target_id=tid,
                        phase="read",
                    )
                else:
                    self.counters["pages_read"] += 1
                    self.emit(
                        "page.read",
                        f"Read {domain}",
                        {"domain": domain, "title": title, "chars": 8200},
                        target_id=tid,
                        phase="read",
                    )
            elif kind == "fact":
                self.emit(
                    "fact.found",
                    f"Checking: {op[1]}",
                    {"kind": op[2], "label": op[1], "topic": t["label"]},
                    target_id=tid,
                    phase="extract",
                )
                checking.append(op)
            elif kind == "ent":
                self.emit(
                    "entity.discovered",
                    f"Found: {op[1]}",
                    {"name": op[1], "kind": op[2]},
                    target_id=tid,
                    phase="read",
                )
                if op[1] not in self.entities:
                    self.entities.append(op[1])
            while len(checking) > 1:
                self._verify(tid, t, checking.pop(0))
        while checking:
            self.tick(0.4)
            self._verify(tid, t, checking.pop(0))
        if not finish:
            return
        found_all = not (self.scenario == "partial" and tid == "T-06")
        with self.cond:
            t["status"] = "covered" if found_all else "not_found"
            self.counters["targets_done"] += 1
        if found_all:
            t["slots_filled"] = t["slots_required"]
            self.emit(
                "target.covered",
                f"Done: {t['label']}",
                {
                    "job": t["job"],
                    "topic": t["label"],
                    "slots_filled": t["slots_filled"],
                    "slots_required": t["slots_required"],
                },
                target_id=tid,
                phase="verify",
            )
        else:
            self.emit(
                "target.not_found",
                f"Nothing reliable found for: {t['label']}",
                {
                    "job": t["job"],
                    "topic": t["label"],
                    "slots_filled": t["slots_filled"],
                    "slots_required": t["slots_required"],
                    "queries": [op[1] for op in SCRIPT[tid].get("r1", []) if op[0] == "q"],
                },
                target_id=tid,
                phase="verify",
            )

    def _verify(self, tid: str, t: dict[str, Any], op: tuple[Any, ...]) -> None:
        rejected = bool(
            op[5:6] and op[5]
        )  # the optional sixth field says this fact fails the check
        if rejected:
            self.emit(
                "fact.rejected",
                "A fact did not pass the check",
                {"reason": "quote_not_found", "label": op[1]},
                target_id=tid,
                phase="verify",
            )
            return
        self.counters["facts_verified"] += 1
        t["slots_filled"] = min(t["slots_required"], t["slots_filled"] + 1)
        self.emit(
            "fact.verified",
            f"Checked: {op[1]}",
            {"kind": op[2], "label": op[1], "domain": op[3], "level": op[4]},
            target_id=tid,
            phase="verify",
        )

    def _round_done(self, n: int, *, new_slots: int) -> None:
        filled = sum(t["slots_filled"] for t in self.targets)
        required = sum(t["slots_required"] for t in self.targets)
        self.emit(
            "round.completed",
            f"Round {n} finished: {filled} of {required} checks filled",
            {
                "round": n,
                "slots_filled": filled,
                "slots_required": required,
                "new_slots": new_slots,
            },
            phase="audit",
        )

    def _has_gaps(self) -> bool:
        return any(t["status"] in {"not_found", "cancelled"} for t in self.targets)

    def _end(self, status: str) -> None:
        c = self.counters
        detail = {
            "status": status,
            "facts": c["facts_verified"],
            "pages": c["pages_read"],
            "searches": c["searches_used"],
            "usd": "0.31",
            "elapsed_s": int(self.sim_t),
        }
        with self.cond:
            if status == "skipped":
                for t in self.targets:
                    if t["status"] in {"queued", "running"}:
                        t["status"] = "skipped"
            self.result_status = status
            self.status = status
            self.finished_at = now_iso()
            self.reason = {
                "partial": "Some topics were not found or were stopped before they finished.",
                "skipped": "Stopped early by the owner.",
            }.get(status)
            self.content_hash = hashlib.sha256(
                json.dumps(findings_file(self), sort_keys=True).encode()
            ).hexdigest()
        label = {"skipped": "Research stopped. We kept what was checked so far."}.get(
            status, "Research finished"
        )
        self.emit(
            "run.skipped" if status == "skipped" else "run.finished", label, detail, phase="commit"
        )
        with self.cond:
            self.finished = True
            self.cond.notify_all()

    def _fail(self) -> None:
        with self.cond:
            self.status = "failed"
            self.finished_at = now_iso()
            self.reason = "The search service stopped answering after several tries."
        self.emit(
            "run.failed",
            "Research could not finish",
            {"status": "failed", "reason": self.reason, "code": "provider_down"},
            phase="commit",
        )
        with self.cond:
            self.finished = True
            self.cond.notify_all()

    # -- API actions ----------------------------------------------------------------------------------

    def cancel_target(self, tid: str) -> str:
        """'ok', 'unknown' or 'not_running'."""
        with self.cond:
            row = next((t for t in self.targets if t["id"] == tid), None)
            if row is None:
                return "unknown"
            if self.finished or row["status"] not in {"queued", "running"}:
                return "not_running"
            self.cancelled.add(tid)
            row["status"] = "cancelled"
            self.counters["targets_done"] += 1
        self.emit(
            "target.cancelled",
            f"Stopped: {row['label']}",
            {
                "job": row["job"],
                "topic": row["label"],
                "slots_filled": row["slots_filled"],
                "slots_required": row["slots_required"],
            },
            target_id=tid,
        )
        return "ok"

    def snapshot(self) -> dict[str, Any]:
        with self.cond:
            return {
                "run_id": self.id, "program_id": self.program_id, "status": self.status, "phase": self.phase, "round": self.round,
                "started_at": self.started_at, "finished_at": self.finished_at, "load_class": "standard", "load_score": 27,
                "depth_word": "a standard-depth study", "plan_source": "mixed",
                "input_quality": {"requests": {"trusted": 7, "template": 0, "thin": 0}, "degraded": False, "geo_uncertain": False, "flags": []},
                "counters": dict(self.counters), "targets": [dict(t) for t in self.targets], "declined": list(DECLINED),
                "entities": list(self.entities), "feed": [dict(e) for e in self.events[-60:]], "last_seq": len(self.events),
                "result_status": self.result_status, "content_hash": self.content_hash, "reason": self.reason,
            }  # fmt: skip


# ---- findings ----------------------------------------------------------------------------------------------------


def _covered_targets(run: Run) -> set[str]:
    return {t["id"] for t in run.targets if t["status"] == "covered"}


def review_items(run: Run) -> list[dict[str, Any]]:
    keep = _covered_targets(run)
    items: list[dict[str, Any]] = []
    for id_, job, kind, label, claim, note, impact, conf, flags, target_id, sources, prev in ITEMS:
        if target_id not in keep:
            continue
        newest = max(s[2] for s in sources)
        items.append({
            "id": id_, "job": job, "spec_need": f"{job.title()} decisions", "kind": kind, "lane": JOB_LANE[job], "label": label, "claim": claim,
            "use_note": note, "slot": kind, "impact": impact, "confidence": conf, "tier": "default", "flags": list(flags),
            "applies_to": [{"kind": "FTR", "id": "class-booking", "relation": "expects"}],
            "sources": [{"id": f"S-{i + 1:03d}", "domain": d, "source_class": "official" if "docs" in d or ".gov" in d else "secondary", "url": u, "retrieved_on": "2026-11-11", "published_on": p} for i, (d, u, p) in enumerate(sources)],
            "independent_domains": len({s[0] for s in sources}),
            "prevalence": {"seen_in": prev[0], "of": prev[1], "level": prev[2]} if prev else None,
            "recency": {"newest_source": newest, "age_months": 6, "trend": "stable"},
            "request_ids": ["RR-001"], "target_id": target_id, "data": {"kind": kind, "fields": {"example": label}},
            "rejected": id_ in run.rejected,
        })  # fmt: skip
    return items


def review_payload(run: Run) -> dict[str, Any]:
    items = review_items(run)
    ids = {i["id"] for i in items}
    by_lane: dict[str, list[dict[str, Any]]] = {}
    for it in items:
        by_lane.setdefault(it["lane"], []).append(it)
    slices: dict[str, Any] = {}
    for lane in ("blueprint", "integrations", "design", "screens", "mock", "verify"):
        rows = by_lane.get(lane, [])
        if not rows:
            slices[lane] = {}
            continue
        digest = "\n".join(
            [
                f"LANE {lane.upper()} ({len(rows)} findings)",
                *(f"- {r['label']}: {r['claim']}" for r in rows),
            ]
        )
        slices[lane] = {"item_ids": [r["id"] for r in rows], "digest": digest}
    covered = sum(1 for t in run.targets if t["status"] == "covered")
    gaps = []
    for t in run.targets:
        if t["status"] in {"not_found", "cancelled", "skipped", "queued", "running"}:
            reason = {"not_found": "not_found", "cancelled": "user_cancelled"}.get(
                t["status"], "skipped"
            )
            tried = (
                [op[1] for op in SCRIPT[t["id"]].get("r1", []) if op[0] == "q"]
                if reason == "not_found"
                else []
            )
            gaps.append(
                {"target_id": t["id"], "reason": reason, "label": t["label"], "queries": tried}
            )
    norms = [
        {"area": a, "key": k, "label": lbl, "prevalence": {"seen_in": p[0], "of": p[1], "level": p[2]}, "recency": {"newest_source": d, "age_months": 6, "trend": "stable"}, "item_ids": [i for i in ids_ if i in ids], "matches_brief": m}
        for a, k, lbl, p, d, ids_, m in NORMS
    ]  # fmt: skip
    suggestions = {
        group: [{"label": lbl, "item_ids": [i for i in ids_ if i in ids], **({"prevalence": {"seen_in": p[0], "of": p[1], "level": p[2]}} if p else {}), "state": "undecided"} for lbl, ids_, p in rows]
        for group, rows in SUGGESTIONS.items()
    }  # fmt: skip
    c = run.counters
    return {
        "run_id": run.id, "status": run.result_status, "content_hash": run.content_hash,
        "profile": {"load_score": 27, "load_class": "standard", "rounds": 2},
        "coverage": {"targets": len(run.targets), "covered": covered, "not_found": sum(1 for t in run.targets if t["status"] == "not_found"), "slots_required": sum(t["slots_required"] for t in run.targets), "slots_filled": sum(t["slots_filled"] for t in run.targets)},
        "budget": {"searches_used": c["searches_used"], "pages_read": c["pages_read"], "seconds": c["elapsed_s"], "usd": "0.31", "usd_micros": 310000, "failures": 1},
        "input_quality": {"requests": {"trusted": 7, "template": 0, "thin": 0}, "degraded": False, "geo_uncertain": False, "flags": []},
        "items": items, "norms": norms, "suggestions": suggestions,
        "confirm": [{"item_id": i, "why": why} for i, why in CONFIRM if i in ids],
        "gaps": gaps, "conflicts": [c_ for c_ in CONFLICTS if all(i in ids for i in c_["item_ids"])], "slices": slices,
    }  # fmt: skip


def findings_file(run: Run) -> dict[str, Any]:
    """The committed research.json (rejections are an overlay, so it never carries them)."""
    payload = review_payload(run)
    for it in payload["items"]:
        it.pop("rejected", None)
    return {
        "schema": "research.v4",
        "program_id": run.program_id,
        **{k: v for k, v in payload.items() if k != "content_hash"},
    }


# ---- the HTTP server -----------------------------------------------------------------------------------------------


class Refused(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def check_export(body: dict[str, Any]) -> dict[str, Any]:
    """The documented refusals (422), in the order the real intake checks them."""
    exp = body.get("brief_export")
    if (
        not isinstance(exp, dict)
        or not isinstance(exp.get("export"), dict)
        or not isinstance(exp.get("brief"), dict)
    ):
        raise Refused(422, "not_an_export", "This is not a Compass export.")
    meta = exp["export"]
    if meta.get("format_version") != 2:
        raise Refused(422, "not_format_2", "Only Compass export format 2 can be researched.")
    baseline = meta.get("approved_baseline")
    if not isinstance(baseline, dict) or not baseline.get("hash"):
        raise Refused(
            422, "not_approved", "The Brief has not been approved (no approved baseline)."
        )
    approved = str(baseline["hash"])
    if not (
        approved == str(exp["brief"].get("_hash") or "") == str(meta.get("brief_hash") or approved)
    ):
        raise Refused(
            422,
            "hash_mismatch",
            "The Brief changed after it was approved (the hashes do not match).",
        )
    views = exp.get("consumer_views")
    if not isinstance(views, dict) or not isinstance(views.get("research"), dict):
        raise Refused(422, "missing_research_view", "The export has no research view.")
    return exp


class FakeResearch:
    """The server state: runs per program, the request log and the knobs the tests turn."""

    def __init__(
        self,
        *,
        speed: float = 1.0,
        scenario: str = "complete",
        token: str = "dev-token",  # noqa: S107 - the documented dev token
        origins: tuple[str, ...] = DEFAULT_ORIGINS,
        keepalive: float = 15.0,
        drop_stream_after: int | None = None,
        gap_at: int | None = None,
    ) -> None:
        self.speed = speed
        self.scenario = scenario
        self.token = token
        self.origins = origins
        self.keepalive = keepalive
        self.drop_stream_after = drop_stream_after
        self.gap_at = gap_at
        self.runs: dict[str, Run] = {}
        self.programs: dict[str, list[str]] = {}
        self.requests: list[dict[str, Any]] = []
        self.lock = threading.Lock()

    def pick_scenario(self, program_id: str, export: dict[str, Any]) -> str:
        wanted = str(export.get("_fake_scenario") or "")
        for name in ("partial", "fail", "interrupted", "complete"):
            if name in wanted or name in program_id.lower():
                return name
        return self.scenario

    def create(self, body: dict[str, Any]) -> Run:
        export = check_export(body)
        program_id = str(body.get("program_id") or new_id("prg"))
        with self.lock:
            for rid in self.programs.get(program_id, []):
                if not self.runs[rid].finished:
                    raise Refused(
                        409,
                        "run_active",
                        f"A research run ({rid}) is already in progress for this program. Open that run instead.",
                    )
            run = Run(new_id("rrn"), program_id, self.pick_scenario(program_id, export), self.speed)
            self.runs[run.id] = run
            self.programs.setdefault(program_id, []).append(run.id)
        run.thread.start()
        return run

    def latest_committed(self, program_id: str) -> Run | None:
        runs = [
            self.runs[r] for r in self.programs.get(program_id, []) if self.runs[r].result_status
        ]
        return runs[-1] if runs else None


def make_handler(fake: FakeResearch) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "FakeResearch/1"

        def log_message(self, format: str, *args: Any) -> None:
            return

        # -- plumbing ---------------------------------------------------------------------------
        def _cors(self) -> list[tuple[str, str]]:
            origin = self.headers.get("Origin")
            if not origin:
                return []
            if "*" in fake.origins or origin in fake.origins:
                return [("Access-Control-Allow-Origin", origin), ("Vary", "Origin")]
            return []

        def _send(
            self,
            status: int,
            body: bytes,
            ctype: str = "application/json",
            extra: list[tuple[str, str]] | None = None,
        ) -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for k, v in [*self._cors(), *(extra or [])]:
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status: int, obj: Any) -> None:
            self._send(status, json.dumps(obj).encode())

        def _error(self, status: int, code: str, message: str) -> None:
            self._json(status, {"code": code, "message": message})

        def _body(self) -> bytes:
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_EXPORT_BYTES:
                remaining = length
                while remaining > 0:  # read it so the client can still read our answer
                    chunk = self.rfile.read(min(65536, remaining))
                    if not chunk:
                        break
                    remaining -= len(chunk)
                raise Refused(413, "payload_too_large", "That export is larger than 8 MB.")
            return self.rfile.read(length) if length else b""

        def _authorized(self) -> bool:
            return self.headers.get("Authorization") == f"Bearer {fake.token}" and bool(self.headers.get("X-Tenant-Id")) and bool(self.headers.get("X-User-Id"))  # fmt: skip

        # -- verbs ------------------------------------------------------------------------------
        def do_OPTIONS(self) -> None:
            self._log("OPTIONS")
            extra = [
                ("Access-Control-Allow-Methods", "GET, POST, OPTIONS"),
                ("Access-Control-Allow-Headers", ALLOWED_HEADERS),
                ("Access-Control-Max-Age", "600"),
            ]
            self.send_response(204)
            self.send_header("Content-Length", "0")
            for k, v in [*self._cors(), *extra]:
                self.send_header(k, v)
            self.end_headers()

        def do_GET(self) -> None:
            self._dispatch("GET")

        def do_POST(self) -> None:
            self._dispatch("POST")

        def _log(self, method: str, body_size: int = 0) -> None:
            with fake.lock:
                fake.requests.append(
                    {
                        "method": method,
                        "path": self.path,
                        "headers": {k.lower(): v for k, v in self.headers.items()},
                        "body_size": body_size,
                    }
                )

        def _dispatch(self, method: str) -> None:
            url = urlparse(self.path)
            path = url.path
            query = parse_qs(url.query)
            self._log(method, int(self.headers.get("Content-Length") or 0))
            try:
                if path == "/healthz":
                    self._json(200, {"ok": True})
                    return
                if not path.startswith(PREFIX + "/"):
                    raise Refused(404, "not_found", "Not found.")
                if not self._authorized():
                    raise Refused(401, "unauthorized", "Sign in to use the research service.")
                self._route(method, unquote(path[len(PREFIX) :]), query)
            except Refused as r:
                self._error(r.status, r.code, r.message)
            except (BrokenPipeError, ConnectionResetError):
                self.close_connection = True

        def _run(self, run_id: str) -> Run:
            run = fake.runs.get(run_id)
            if run is None:
                raise Refused(404, "not_found", "That research run does not exist.")
            return run

        def _route(self, method: str, path: str, query: dict[str, list[str]]) -> None:
            routes: list[tuple[str, str, Callable[..., None]]] = [
                ("GET", r"/config", self._config),
                ("POST", r"/runs", self._create),
                ("GET", r"/runs/([^/]+)", self._snapshot),
                ("GET", r"/runs/([^/]+)/events", self._events),
                ("POST", r"/runs/([^/]+)/skip", self._skip),
                ("POST", r"/runs/([^/]+)/targets/([^/]+)/cancel", self._cancel),
                ("GET", r"/runs/([^/]+)/result", self._result),
                ("POST", r"/runs/([^/]+)/items/([^/]+)/reject", self._reject),
                ("GET", r"/programs/([^/]+)/latest", self._latest),
                ("GET", r"/programs/([^/]+)/review", self._review),
            ]
            for verb, pattern, fn in routes:
                m = re.fullmatch(pattern, path)
                if m and verb == method:
                    fn(*m.groups(), query=query) if fn in {self._events} else fn(*m.groups())
                    return
                if m:
                    raise Refused(405, "method_not_allowed", "That method is not allowed here.")
            raise Refused(404, "not_found", "Not found.")

        # -- endpoints --------------------------------------------------------------------------
        def _config(self) -> None:
            self._json(
                200,
                {
                    "mode": "mock",
                    "search_provider": "fake",
                    "keys": {"tavily": False, "serper": False},
                    "model": "fake-model",
                    "checker_vendor": "none",
                },
            )

        def _create(self) -> None:
            raw = self._body()
            try:
                body = json.loads(raw.decode("utf-8")) if raw else None
            except (ValueError, UnicodeDecodeError):
                body = None
            if not isinstance(body, dict):
                raise Refused(422, "bad_request", "The request body must be a JSON object.")
            run = fake.create(body)
            self._json(
                201,
                {
                    "run_id": run.id,
                    "program_id": run.program_id,
                    "status": "queued",
                    "events_url": f"{PREFIX}/runs/{run.id}/events",
                },
            )

        def _snapshot(self, run_id: str) -> None:
            self._json(200, self._run(run_id).snapshot())

        def _skip(self, run_id: str) -> None:
            run = self._run(run_id)
            if run.finished:
                raise Refused(409, "not_running", "That research has already finished.")
            run.skip_flag = True
            self._json(202, {"status": "skipping"})

        def _cancel(self, run_id: str, target_id: str) -> None:
            outcome = self._run(run_id).cancel_target(target_id)
            if outcome == "unknown":
                raise Refused(404, "not_found", "That topic does not exist.")
            if outcome == "not_running":
                raise Refused(409, "not_running", "That topic is not being researched any more.")
            self._json(202, {"status": "cancelled"})

        def _result(self, run_id: str) -> None:
            run = self._run(run_id)
            if not run.result_status:
                raise Refused(404, "not_found", "That run has no committed result yet.")
            self._json(200, findings_file(run))

        def _latest(self, program_id: str) -> None:
            run = fake.latest_committed(program_id)
            if run is None:
                raise Refused(404, "not_found", "No committed research for this program yet.")
            self._json(
                200, {**findings_file(run), "content_hash": run.content_hash, "run_id": run.id}
            )

        def _review(self, program_id: str) -> None:
            run = fake.latest_committed(program_id)
            if run is None:
                raise Refused(404, "not_found", "No findings for this program yet.")
            self._json(200, review_payload(run))

        def _reject(self, run_id: str, item_id: str) -> None:
            run = self._run(run_id)
            raw = self._body()
            reason = ""
            try:
                body = json.loads(raw.decode("utf-8")) if raw else {}
                reason = str(body.get("reason") or "") if isinstance(body, dict) else ""
            except ValueError:
                raise Refused(422, "bad_request", "The request body must be JSON.") from None
            if not run.result_status or item_id not in {i["id"] for i in review_items(run)}:
                raise Refused(404, "not_found", "That finding does not exist.")
            run.rejected[item_id] = reason
            self._json(200, {"item_id": item_id, "rejected": True})

        def _events(self, run_id: str, query: dict[str, list[str]]) -> None:
            run = self._run(run_id)
            raw_after = (query.get("after") or [self.headers.get("Last-Event-ID") or "0"])[0]
            sent = int(raw_after) if raw_after.isdigit() else 0
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.send_header("X-Accel-Buffering", "no")
            for k, v in self._cors():
                self.send_header(k, v)
            self.end_headers()
            self.close_connection = True
            written = 0
            last_write = time.monotonic()
            try:
                self.wfile.write(b"retry: 3000\n\n")
                self.wfile.flush()
                while True:
                    with run.cond:
                        batch = [e for e in run.events if e["seq"] > sent]
                        finished = run.finished
                        if not batch and not finished:
                            run.cond.wait(timeout=0.25)
                            batch = [e for e in run.events if e["seq"] > sent]
                            finished = run.finished
                    for ev in batch:
                        if fake.gap_at == ev["seq"] and not run.gap_done:
                            run.gap_done = (
                                True  # leave this event out once, to see the client notice the gap
                            )
                            sent = ev["seq"]
                            continue
                        frame = f"id: {ev['seq']}\nevent: {ev['type']}\ndata: {json.dumps(ev)}\n\n"
                        self.wfile.write(frame.encode())
                        self.wfile.flush()
                        sent = ev["seq"]
                        written += 1
                        last_write = time.monotonic()
                        if (
                            fake.drop_stream_after
                            and not run.dropped
                            and written >= fake.drop_stream_after
                            and not run.finished
                        ):
                            run.dropped = True  # hang up once, mid-run, so the client has to resume
                            return
                    if finished and not batch:
                        return
                    if time.monotonic() - last_write >= fake.keepalive:
                        self.wfile.write(b": keepalive\n\n")
                        self.wfile.flush()
                        last_write = time.monotonic()
            except (BrokenPipeError, ConnectionResetError, OSError):
                return

    return Handler


class FakeServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    fake: FakeResearch


def make_server(host: str = "127.0.0.1", port: int = 0, **options: Any) -> FakeServer:
    fake = FakeResearch(**options)
    server = FakeServer((host, port), make_handler(fake))
    server.fake = fake
    return server


def start_in_thread(
    host: str = "127.0.0.1", port: int = 0, **options: Any
) -> tuple[FakeServer, str]:
    """Start a server on a background thread; returns it and its base URL (call ``shutdown()`` when done)."""
    server = make_server(host, port, **options)
    threading.Thread(target=server.serve_forever, name="fake-research", daemon=True).start()
    return server, f"http://{host}:{server.server_address[1]}"


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0] if __doc__ else None)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8120)
    p.add_argument(
        "--speed",
        type=float,
        default=1.0,
        help="scale the scripted pauses (1.0 is about a minute, 0 is instant)",
    )
    p.add_argument(
        "--scenario", choices=["complete", "partial", "fail", "interrupted"], default="complete"
    )
    p.add_argument(
        "--origin",
        action="append",
        help="an allowed CORS origin (repeatable, default the console on :8110; '*' allows any)",
    )
    p.add_argument("--token", default="dev-token")
    p.add_argument("--keepalive", type=float, default=15.0)
    p.add_argument(
        "--drop-stream-after",
        type=int,
        default=None,
        help="hang up each run's first stream after N events",
    )
    p.add_argument(
        "--gap-at", type=int, default=None, help="leave this seq out of each run's first stream"
    )
    a = p.parse_args()
    server = make_server(a.host, a.port, speed=a.speed, scenario=a.scenario, token=a.token, origins=tuple(a.origin or DEFAULT_ORIGINS), keepalive=a.keepalive, drop_stream_after=a.drop_stream_after, gap_at=a.gap_at)  # fmt: skip
    print(
        f"fake research server on http://{a.host}:{a.port}{PREFIX} (scenario {a.scenario}, speed {a.speed}); Ctrl+C stops it",
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
