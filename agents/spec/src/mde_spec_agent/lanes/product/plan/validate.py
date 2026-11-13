"""Local checks of a SPEC-PLAN answer: merges and splits that make sense, modules, roles and owners that exist (one owner per
entity), a persona per role, a release proposal that never relabels a user deferral and never defers a user-evidenced must
without a consent request, provenance real, no invented number, no form word. The plan has no safe default: if this fails
after its re-ask the step raises `PlanInvalid`."""

from __future__ import annotations

from collections import Counter

from mde_spec_agent.lanes.product.kit.common import (
    Errors,
    ProvAllowed,
    check_prov,
    invented_numbers,
    new_form_words,
    source_numbers,
)

from .schema import PlanIn, PlanOut


def _pairs(items: list[str]) -> dict[str, str]:
    return dict(x.split("=", 1) for x in items if "=" in x)


def validate(inp: PlanIn, out: PlanOut) -> list[str]:
    e = Errors()
    allowed = ProvAllowed.of(inp)
    source = "\n".join(
        [
            *inp.brief,
            *inp.inventory,
            *inp.release_lines,
            *inp.research,
            inp.baseline.model_dump_json(),
        ]
    )
    nums = source_numbers(source)
    screens, system = set(inp.screen_ids), set(inp.system_screen_ids)
    apps = _pairs(inp.screen_apps)
    roles_of = {k: set(v.split(",")) for k, v in _pairs(inp.screen_roles).items()}
    module_apps = {k: set(v.split(",")) for k, v in _pairs(inp.module_apps).items()}

    # ---- merges and splits ---------------------------------------------------------------------------------------
    if not inp.allow_restructure and (out.merges or out.splits):
        e.add("merges and splits must be empty in this call")
    dropped: set[str] = set()
    for m in out.merges:
        where = f"merge {m.keep} <- {m.drop}"
        if m.keep not in screens or m.drop not in screens:
            e.add(f"{where}: both must be screens of the inventory")
            continue
        if m.keep == m.drop:
            e.add(f"{where}: keep and drop are the same screen")
        if m.keep in system or m.drop in system:
            e.add(f"{where}: never merge a system screen")
        if apps.get(m.keep) != apps.get(m.drop):
            e.add(f"{where}: screens of different apps never merge")
        if m.drop in dropped or m.keep in dropped:
            e.add(f"{where}: a screen takes part in one merge only")
        dropped.add(m.drop)
        if not m.reason.strip():
            e.add(f"{where}: give the reason")
        e.extend(check_prov(where, m.prov, allowed))
    if len(out.merges) > max(1, len(screens) // 5):
        e.add("too many merges: merge only trivial pairs")
    splits: dict[str, list] = {}
    for s in out.splits:
        splits.setdefault(s.screen, []).append(s)
    keys = {x.split("|")[0]: x for x in inp.screen_keys}
    for sid, parts in splits.items():
        where = f"split {sid}"
        if sid not in inp.over_cap_screen_ids:
            e.add(f"{where}: only a screen listed as over the size cap may split")
            continue
        if sid in dropped:
            e.add(f"{where}: a merged screen cannot split")
        nums_p = sorted(p.part for p in parts)
        if nums_p != list(range(1, len(parts) + 1)) or len(parts) < 2:
            e.add(f"{where}: parts must be numbered 1, 2, ... with at least two parts")
        row = keys.get(sid, "")
        have_regions = set(_field(row, "regions"))
        have_actions = set(_field(row, "actions"))
        moved_r: set[str] = set()
        moved_a: set[str] = set()
        for p in parts:
            if not p.name.strip() or not p.purpose.strip():
                e.add(f"{where} part {p.part}: name and purpose must be written")
            for k in p.region_keys:
                if k not in have_regions:
                    e.add(f"{where} part {p.part}: region {k} is not a region of the screen")
                if k in moved_r:
                    e.add(f"{where}: region {k} moves to two parts")
                moved_r.add(k)
            for k in p.action_keys:
                if k not in have_actions:
                    e.add(f"{where} part {p.part}: action {k} is not an action of the screen")
                if k in moved_a:
                    e.add(f"{where}: action {k} moves to two parts")
                moved_a.add(k)
            e.extend(check_prov(f"{where} part {p.part}", p.prov, allowed))
            for w in new_form_words(f"{p.name} {p.purpose} {p.reason}", source):
                e.add(f"{where} part {p.part}: uses the form word {w!r}")

    # ---- modules, roles, owners, access ---------------------------------------------------------------------------
    mod_ids = Counter(m.id for m in out.modules)
    for m in out.modules:
        where = f"module {m.id}"
        if m.id not in inp.module_ids:
            e.add(f"{where}: not a module of the draft")
        if mod_ids[m.id] > 1:
            e.add(f"{where}: listed twice")
        for d in m.depends_on:
            if d not in inp.module_ids or d == m.id:
                e.add(f"{where}: depends_on {d} is not another module of the draft")
        was_shared = any(b.id == m.id and b.shared for b in inp.baseline.modules)
        if m.shared and not was_shared and len(module_apps.get(m.id, set())) < 2:
            e.add(
                f"{where}: shared means used by several apps; this module is used by {sorted(module_apps.get(m.id, set()))}"
            )
        e.extend(check_prov(where, m.prov, allowed))
        for w in new_form_words(m.purpose, source):
            e.add(f"{where}: purpose uses the form word {w!r}")
    for mid in inp.module_ids:
        if mid not in mod_ids:
            e.add(f"module {mid}: no record; write one record per module")
    role_ids = Counter(r.id for r in out.roles)
    for r in out.roles:
        where = f"role {r.id}"
        if r.id not in inp.role_ids:
            e.add(f"{where}: not a role of the draft; never invent a role")
        if role_ids[r.id] > 1:
            e.add(f"{where}: listed twice")
        if r.home_screen and (
            r.home_screen not in screens or r.id not in roles_of.get(r.home_screen, set())
        ):
            e.add(f"{where}: home screen {r.home_screen} is not a screen this role may use")
        e.extend(check_prov(where, r.prov, allowed))
    owners = Counter(o.entity for o in out.owners)
    for o in out.owners:
        where = f"owner of {o.entity}"
        if o.entity not in inp.entity_ids:
            e.add(f"{where}: not an entity of the draft")
        if o.module not in inp.module_ids:
            e.add(f"{where}: module {o.module} does not exist")
        if owners[o.entity] > 1:
            e.add(f"{where}: an entity has exactly ONE owner module")
        e.extend(check_prov(where, o.prov, allowed))
    for ent in inp.entity_ids:
        if ent not in owners:
            e.add(f"entity {ent}: no owner record; every entity has one owner module")
    for a in out.access:
        where = f"access {a.entity}/{a.role}"
        if a.entity not in inp.entity_ids or a.role not in inp.role_ids:
            e.add(f"{where}: the entity and the role must exist")
        if not a.actions:
            e.add(f"{where}: name the actions (use none to deny)")
        e.extend(check_prov(where, a.prov, allowed))

    # ---- personas --------------------------------------------------------------------------------------------------
    per = Counter(p.role for p in out.personas)
    for p in out.personas:
        where = f"persona of {p.role}"
        if p.role not in inp.role_ids:
            e.add(f"{where}: not a role of the draft")
        if per[p.role] > 1:
            e.add(f"{where}: one persona per role")
        if not p.goal.strip():
            e.add(f"{where}: goal is empty")
        e.extend(check_prov(where, p.prov, allowed))
        text = " ".join([p.goal, p.context, p.frequency, p.skill, p.success, *p.pain_points])
        for bad in invented_numbers(text, nums):
            e.add(f"{where}: states {bad}, which no source states (never invent a number)")
        for w in new_form_words(text, source):
            e.add(f"{where}: uses the form word {w!r}")
    for r in inp.role_ids:
        if r not in per:
            e.add(f"role {r}: no persona; write one persona per role")

    # ---- release proposal ------------------------------------------------------------------------------------------
    rel = Counter(r.feature for r in out.release)
    deferred, musts = set(inp.user_deferred), set(inp.user_must)
    for r in out.release:
        where = f"release {r.feature}"
        if r.feature not in inp.feature_ids:
            e.add(f"{where}: not a feature of the plan")
        if rel[r.feature] > 1:
            e.add(f"{where}: listed twice")
        if r.status == "in" and r.reason != "none":
            e.add(f"{where}: an in feature has reason none")
        if r.status == "later" and r.reason == "none":
            e.add(f"{where}: a later feature needs a reason: user, capacity or first_release")
        if r.feature in deferred and not (r.status == "later" and r.reason == "user"):
            e.add(
                f"{where}: the user deferred it; it stays later with reason user (never relabel a user deferral)"
            )
        if (
            r.status == "later"
            and r.feature in musts
            and r.feature not in deferred
            and not r.consent_needed
        ):
            e.add(
                f"{where}: the user asked for it as a must; deferring it needs consent_needed true"
            )
        if r.status == "later" and r.reason == "capacity" and not r.consent_needed:
            e.add(f"{where}: a capacity deferral needs consent_needed true (G1.5 consent)")
        e.extend(check_prov(where, r.prov, allowed))
    for fid in inp.feature_ids:
        if fid not in rel:
            e.add(f"feature {fid}: no release record; write one record per feature")
    return e.items


def _field(row: str, name: str) -> list[str]:
    for part in row.split("|")[1:]:
        if part.startswith(f"{name}="):
            return [x for x in part.split("=", 1)[1].split(",") if x]
    return []
