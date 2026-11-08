"""BriefCompiler: the Brief (spec 2.3) from the slot table.

Six rows (what you are building, who uses it, main features, look, login, name and logo), each
tap-to-edit on its first slot, plus "We assumed" (every assumed value with a one-line reason).
Rows are deterministic; only the one-sentence summary uses an LLM (C11), with a template fallback.
"""

from __future__ import annotations

from datetime import UTC, datetime

from mde_content import packs, themes
from mde_content.questions import lang_chain
from mde_contracts.brief import AssumedItem, Brief, BriefFeature, BriefRow
from mde_contracts.calls import C11Input, C11Output
from mde_contracts.content import Catalogs
from mde_contracts.engines import DiscoveryRequest, RunContext
from mde_contracts.slots import SlotRecord
from mde_discovery import copy as texts
from mde_discovery.llm import Llm
from mde_discovery.selector import QuestionSelector
from mde_discovery.slots import SlotStore
from mde_discovery.vocab import Vocab
from mde_prompts.calls.c1_understand import lexicon as lx

SWATCH_TOKENS = ("primary", "accent", "background", "text")
MAX_FEATURES_SHOWN = 8
_EPOCH = datetime(2000, 1, 1, tzinfo=UTC)


class BriefCompiler:
    def __init__(
        self, llm: Llm, vocab: Vocab, catalogs: Catalogs, selector: QuestionSelector
    ) -> None:
        self.llm = llm
        self.vocab = vocab
        self.catalogs = catalogs
        self.selector = selector

    async def compile(self, req: DiscoveryRequest, ctx: RunContext) -> Brief:
        """Deterministic rows plus the C11 sentence. ``version`` and ``hash`` are set by the flow."""
        state = req.state
        lang = state.language
        store = SlotStore(state.slots, lambda: _EPOCH)
        archetype = store.text_value("idea.archetype")
        sentence = await self._sentence(store, archetype, lang, ctx)
        rows = [
            self._building(store, sentence, lang),
            self._users(store, archetype, req, lang),
            self._features(store, archetype, state.extra, lang),
            self._look(store, lang, archetype),
            self._login(store, lang, archetype),
            self._name_logo(store, lang),
        ]
        return Brief(
            version=0,
            mode=req.mode,
            language=lang,
            app_summary=sentence,
            rows=rows,
            assumptions=self._assumptions(state.slots, req, lang),
        )

    # ----------------------------------------------------------------- the sentence
    def facts(self, store: SlotStore, archetype: str | None, lang: str) -> list[str]:
        idea = (store.text_value("idea.summary") or "").strip().rstrip(".")
        facts: list[str] = []
        if idea:
            label = self.vocab.archetype_label(archetype, lang) if archetype else ""
            if label and label.casefold() not in idea.casefold() and archetype != "custom":
                facts.append(f"{label}: {idea}")
            else:
                facts.append(idea)
        feature_labels = self._feature_labels(store, archetype, lang)
        if feature_labels:
            facts.append(
                texts.say(
                    "fact_features",
                    lang,
                    features=", ".join(x.lower() for x in feature_labels[:MAX_FEATURES_SHOWN]),
                )
            )
        look = store.text_value("design.direction")
        if look:
            facts.append(
                texts.say(
                    "fact_look",
                    lang,
                    look=self.vocab.label("design.direction", look, lang, archetype).lower(),
                )
            )
        return facts

    async def _sentence(
        self, store: SlotStore, archetype: str | None, lang: str, ctx: RunContext
    ) -> str:
        facts = self.facts(store, archetype, lang)
        inp = C11Input(
            kind="brief_sentence",
            language=lang,
            app_name=store.text_value("brand.name"),
            facts=facts,
            max_words=40,
        )
        out, _, _ = await self.llm.run(ctx, "C11.write_summary", inp)
        assert isinstance(out, C11Output)
        return out.text.strip() or (facts[0] if facts else "")

    # ----------------------------------------------------------------- rows
    def _building(self, store: SlotStore, sentence: str, lang: str) -> BriefRow:
        """Row 1 is the C11 sentence itself: what the user is building, in one line."""
        return BriefRow(
            key="building",
            title=texts.say("row_building", lang),
            text=sentence or (store.text_value("idea.summary") or ""),
            slot_ids=["idea.summary", "idea.archetype"],
        )

    def _users(
        self, store: SlotStore, archetype: str | None, req: DiscoveryRequest, lang: str
    ) -> BriefRow:
        apps = [a for a in store.list_value("scope.apps") if a != "main"]
        roles = store.list_value("users.roles")
        parts: list[str] = []
        if apps:
            parts.append(self.vocab.label("scope.apps", apps, lang, archetype))
        if roles and roles != ["user"]:
            parts.append(self.vocab.label("users.roles", roles, lang, archetype))
        text = ". ".join(parts) if parts else texts.say("users_default", lang)
        ctx = store.context(req.state.sensitive_flags)
        applicable = [
            s
            for s in ("scope.apps", "users.roles")
            if self.selector.applicable_cards(s, archetype, req.mode, ctx)
        ]
        slot_ids = applicable or ["users.roles"]
        return BriefRow(
            key="users",
            title=texts.say("row_users", lang),
            text=text,
            slot_ids=slot_ids,  # type: ignore[arg-type]
        )

    def _pack_features(self, archetype: str | None) -> list[tuple[str, str, bool]]:
        """(id, English label, always on) for every feature of the archetype's pack."""
        if archetype and archetype in packs.pack_ids():
            return [(f.id, f.label, f.tier == "core") for f in packs.load_pack(archetype).features]
        return []

    def _feature_label(self, archetype: str | None, fid: str, default: str, lang: str) -> str:
        if archetype and archetype in packs.pack_ids():
            for f in packs.load_pack(archetype).features:
                if f.id == fid:
                    for tag in lang_chain(lang):
                        if tag in f.i18n:
                            return f.i18n[tag]
                    return f.label
        return default

    def _feature_labels(self, store: SlotStore, archetype: str | None, lang: str) -> list[str]:
        selected = store.list_value("scope.features")
        return [
            self._feature_label(
                archetype, fid, self.vocab.label("scope.features", [fid], lang, archetype), lang
            )
            for fid in selected
        ]

    def _features(
        self, store: SlotStore, archetype: str | None, extra: dict[str, str], lang: str
    ) -> BriefRow:
        selected = store.list_value("scope.features")
        pack = self._pack_features(archetype)
        features: list[BriefFeature] = []
        for fid, label, core in pack:
            features.append(
                BriefFeature(
                    id=fid,
                    label=self._feature_label(archetype, fid, label, lang),
                    on=core or fid in selected,
                )
            )
        known = {f.id for f in features}
        for fid in selected:
            if fid not in known:
                features.append(
                    BriefFeature(
                        id=fid,
                        label=self.vocab.label("scope.features", [fid], lang, archetype),
                        on=True,
                    )
                )
        on_labels = [f.label for f in features if f.on]
        if on_labels:
            text = texts.say(
                "features_text",
                lang,
                n=len(on_labels),
                list=", ".join(on_labels[:MAX_FEATURES_SHOWN]),
            )
        else:
            text = texts.say("features_none", lang)
        custom = extra.get("custom_features", "")
        if custom:
            text = f"{text} {texts.say('features_extra', lang, list=custom)}"
        return BriefRow(
            key="features",
            title=texts.say("row_features", lang),
            text=text,
            slot_ids=["scope.features"],
            features=features,
        )

    def _look(self, store: SlotStore, lang: str, archetype: str | None) -> BriefRow:
        direction = store.text_value("design.direction")
        text = ""
        swatch: list[str] = []
        if direction:
            label = self.vocab.label("design.direction", direction, lang, archetype)
            text = (
                texts.say("look_dark", lang, look=label)
                if store.text_value("design.mode") == "dark"
                else label
            )
            try:
                colors = themes.theme(direction).tokens.colors_light
                swatch = [colors[t] for t in SWATCH_TOKENS if t in colors]
            except KeyError:
                swatch = []
        return BriefRow(
            key="look",
            title=texts.say("row_look", lang),
            text=text,
            slot_ids=["design.direction"],
            swatch=swatch,
        )

    def _login(self, store: SlotStore, lang: str, archetype: str | None) -> BriefRow:
        method = store.text_value("auth.method") or ""
        label = self.vocab.label("auth.method", method, lang, archetype) if method else ""
        with_sso = store.text_value("auth.sso") == "required" and method != "sso"
        return BriefRow(
            key="login",
            title=texts.say("row_login", lang),
            text=texts.say("login_sso", lang, method=label) if with_sso else label,
            slot_ids=["auth.method"],
        )

    def _name_logo(self, store: SlotStore, lang: str) -> BriefRow:
        name = store.text_value("brand.name") or ""
        assets = store.text_value("brand.assets")
        logo = texts.say("logo_file" if assets else "logo_none", lang)
        return BriefRow(
            key="name_logo",
            title=texts.say("row_name_logo", lang),
            text=texts.say("name_text", lang, name=name, logo=logo),
            slot_ids=["brand.name", "brand.assets"],
        )

    # ----------------------------------------------------------------- assumptions
    def _assumptions(
        self, slots: dict[str, SlotRecord], req: DiscoveryRequest, lang: str
    ) -> list[AssumedItem]:
        out: list[AssumedItem] = []
        for sd in self.selector.slots_in_order(req.mode, req.policy):
            rec = slots.get(sd.slot_id)
            if rec is None or rec.state != "assumed" or rec.value in (None, "", []):
                continue
            if self.selector.level(sd, req.mode, req.policy) == "may":
                continue
            default = texts.reason(sd.slot_id, lang, sd.default_reason)
            out.append(
                AssumedItem(
                    slot_id=sd.slot_id,
                    label=lx.slot_label(sd.slot_id, lang),
                    value_text=self.vocab.label(sd.slot_id, rec.value, lang),
                    reason=texts.reason_in(rec.evidence, lang, default)
                    if rec.evidence
                    else default,
                    included=True,
                )
            )
        return out
