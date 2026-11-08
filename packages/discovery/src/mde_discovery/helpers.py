"""Small deterministic helpers: sensitive-domain detection, region ids, asked-for features."""

from __future__ import annotations

from mde_content import packs
from mde_contracts.content import Catalogs
from mde_prompts.calls.c1_understand import lexicon as lx
from mde_prompts.calls.c2_interpret_brief.keyword import sensitive_hits


def region_ids(labels: list[str]) -> list[str]:
    """Region labels from the interpreter ("India") as region ids ("in"); unknown labels dropped."""
    table: dict[str, str] = lx.load_lexicon().raw["region_ids"]
    return list(dict.fromkeys(table[label] for label in labels if label in table))


def detect_sensitive(text: str, archetype: str | None, catalogs: Catalogs) -> list[str]:
    """Sensitive domain ids the text touches: catalog keywords plus the Domain Pack's triggers.

    A cheap deterministic safety net next to C2 (which reads the same text with the full idea in
    mind). A false alarm costs one must-confirm card the user can dismiss; a miss would skip care.
    """
    found = sensitive_hits(text, [d.id for d in catalogs.sensitive_domains])
    if archetype and archetype in packs.pack_ids():
        for trigger in packs.load_pack(archetype).compliance_triggers:
            if trigger.sensitive_domain not in found and lx.keyword_hits(text, trigger.keywords):
                found.append(trigger.sensitive_domain)
    return found
