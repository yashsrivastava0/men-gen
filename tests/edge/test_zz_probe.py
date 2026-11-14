from edge_support.eb_run import Rig, load_input
from mde_compass_intake.spec_input import Locales


def test_p(tmp_path):
    si = load_input("standard")
    si2 = si.model_copy(
        update={
            "locales": Locales(
                codes=("en", "hi", "ar", "kn", "fr"),
                search_languages=("en",),
                source="view",
                non_latin=("hi", "ar", "kn"),
                rtl=("ar",),
            )
        }
    )
    rig = Rig(tmp_path, "standard", spec_input=si2)
    print("S", rig.full_from_input().status)
    g = rig.ctx.graph
    print("LANGS", g.meta.languages, getattr(g.meta, "degraded_locales", None), list(g.locale_copy))
    print("CALLS", {s: len(rig.provider_calls(s)) for s in ("spec.gloss_l", "spec.copy_l")})
    cl = rig.provider_calls("spec.copy_l")
    print(
        "COPYIN",
        [
            (
                c.input.language if hasattr(c.input, "language") else None,
                len(getattr(c.input, "items", [])),
            )
            for c in cl
        ][:8],
    )
    print("BEH", sorted({b.category for b in g.behaviors.values()}))
    print(
        "RTLB",
        [
            (b.category, b.text[:80])
            for b in g.behaviors.values()
            if "right" in b.text.lower() or "rtl" in b.text.lower()
        ][:3],
    )
    for d in list(g.directions.values())[:1]:
        t = g.tokens.get(d.id, {})
        print("SCRIPTS", str(t.get("report", {}).get("scripts"))[:400])
    from mde_spec_agent.views import g2

    v = g2(g, rig.ctx.pack, rig.ctx.check, rig.ctx.derived, flags=rig.ctx.flags)
    print("RISK", [r["text"] for r in v["risks"] if "native" in r["text"] or "words" in r["text"]])
