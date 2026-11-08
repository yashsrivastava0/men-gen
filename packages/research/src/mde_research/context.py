"""From the approved Brief and the slots to the compact ``BriefContext`` the research calls read.

``ResearchRequest`` carries the Brief and the slot snapshot. Two things the research jobs need are
not slots in v1: the apps the user named ("like X") and the user's own website. They are read from
optional ``references`` and ``brand_url`` attributes of the request when the contract has them (a
requested additive change) and otherwise recovered conservatively: ``brand_url`` from a link the
user typed into the ``brand.name`` or ``brand.assets`` card.
"""

from __future__ import annotations

import re
from typing import cast

from mde_contracts.calls import BriefContext, OptionLite
from mde_contracts.engines import ResearchRequest
from mde_contracts.slots import SlotData, SlotRecord
from mde_research.domains import safe_http_url
from mde_research.privacy import QueryPrivacy

DEFAULT_BRAND_NAME = "My App"
_URL = re.compile(r"(?:https?://|www\.)[^\s<>\"')]+", re.IGNORECASE)
_BARE_DOMAIN = re.compile(r"^(?:[a-z0-9-]+\.)+[a-z]{2,}(?:/\S*)?$", re.IGNORECASE)
MAX_ASSUMPTIONS = 10
MAX_REFERENCES = 4


def slot_value(slots: dict[str, SlotRecord], slot_id: str) -> SlotData:
    record = slots.get(slot_id)
    return record.value if record is not None else None


def _text(value: SlotData) -> str:
    return value.strip() if isinstance(value, str) else ""


def _list(value: SlotData) -> list[str]:
    if isinstance(value, list):
        return [str(v) for v in value if str(v).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _find_url(value: SlotData) -> str | None:
    text = _text(value)
    if not text:
        return None
    match = _URL.search(text)
    candidate = match.group(0) if match else (text if _BARE_DOMAIN.match(text) else None)
    if candidate is None:
        return None
    if not candidate.lower().startswith("http"):
        candidate = f"https://{candidate}"
    return safe_http_url(candidate.rstrip(".,;"))


def brand_url_of(req: ResearchRequest) -> str | None:
    """The user's own website: an explicit request attribute, else a link typed into a brand card."""
    explicit = cast("object", getattr(req, "brand_url", None))
    if isinstance(explicit, str) and explicit.strip():
        return safe_http_url(explicit if "://" in explicit else f"https://{explicit.strip()}")
    for slot_id in ("brand.assets", "brand.name"):
        found = _find_url(slot_value(req.slots, slot_id))
        if found:
            return found
    return None


def brand_name_of(req: ResearchRequest) -> str | None:
    """The brand name the user gave (not the placeholder, not a pasted link)."""
    record = req.slots.get("brand.name")
    name = _text(record.value) if record else ""
    if not name or name == DEFAULT_BRAND_NAME or _URL.search(name) or _BARE_DOMAIN.match(name):
        return None
    if record is not None and record.state in ("empty", "assumed") and record.source == "default":
        return None
    return name


def references_of(req: ResearchRequest) -> list[str]:
    explicit = cast("object", getattr(req, "references", None))
    if isinstance(explicit, list):
        items = [str(x).strip() for x in cast("list[object]", explicit)]
        return [x for x in dict.fromkeys(items) if x][:MAX_REFERENCES]
    return []


def build_brief_context(req: ResearchRequest) -> BriefContext:
    """The compact, trusted view of the approved Brief (not yet privacy-scrubbed)."""
    brief = req.brief
    features_row = brief.row("features")
    features = [
        OptionLite(id=f.id, label=f.label)
        for f in (features_row.features if features_row else [])
        if f.on
    ]
    if not features:
        features = [
            OptionLite(id=fid, label=fid.replace("_", " ").capitalize())
            for fid in _list(slot_value(req.slots, "scope.features"))
        ]
    look_row = brief.row("look")
    look = _text(slot_value(req.slots, "design.direction")) or (look_row.text if look_row else "")
    login = _text(slot_value(req.slots, "auth.method"))
    if _text(slot_value(req.slots, "auth.sso")) == "required":
        login = "sso"
    assumptions = [f"{a.label}: {a.value_text}".strip() for a in brief.assumptions if a.included][
        :MAX_ASSUMPTIONS
    ]
    return BriefContext(
        app_summary=brief.app_summary.strip(),
        archetype=_text(slot_value(req.slots, "idea.archetype")) or "custom",
        language=brief.language,
        mode=brief.mode,
        roles=_list(slot_value(req.slots, "users.roles")),
        features=features,
        look=look,
        login=login,
        regions=_list(slot_value(req.slots, "regions")),
        references=references_of(req),
        compliance_flags=_list(slot_value(req.slots, "compliance.flags")),
        assumptions=assumptions,
        brand_name=brand_name_of(req),
        brand_url=brand_url_of(req),
    )


def build_privacy(req: ResearchRequest, ctx: BriefContext) -> QueryPrivacy:
    """The query-privacy rule of the request's policy, with every name the Brief carries."""
    web = req.policy.research.web
    return QueryPrivacy.build(
        allow_company_name=web.allow_company_name_in_queries,
        names=[ctx.brand_name, *ctx.references],
        urls=[ctx.brand_url],
        org=req.policy.org,
    )


def scrub_context(ctx: BriefContext, privacy: QueryPrivacy) -> BriefContext:
    """The context handed to the planning and synthesis calls under a restrictive policy: no
    brand, reference or website, and no forbidden name left inside the free-text fields."""
    if not privacy.restricted:
        return ctx
    return ctx.model_copy(
        update={
            "app_summary": privacy.scrub(ctx.app_summary, "the company"),
            "assumptions": [privacy.scrub(a, "the company") for a in ctx.assumptions],
            "references": [],
            "brand_name": None,
            "brand_url": None,
        }
    )
