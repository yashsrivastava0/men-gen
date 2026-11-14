"""Library files: every file loads, cross-references resolve, minimums hold, no form words."""

import yaml

from mde_spec_core import libraries as lib
from mde_spec_core import vocab
from mde_spec_core.config import default_config
from mde_spec_core.graph import Acceptance, StateSpec


def test_every_file_has_a_version_and_is_listed():
    for name in lib.LIBRARY_FILES:
        raw = yaml.safe_load((lib.LIB_DIR / f"{name}.yaml").read_text(encoding="utf-8"))
        assert isinstance(raw["version"], int), name
    assert {p.stem for p in lib.LIB_DIR.glob("*.yaml")} == set(lib.LIBRARY_FILES)


def test_cross_references_resolve():
    assert lib.check_libraries() == []


# ------------------------------------------------------------ state library


def test_every_screen_type_has_a_state_library_entry():
    assert set(lib.state_library_file().screen_types) == set(vocab.screen_types())


def test_every_state_kind_used_exists():
    for stype in vocab.screen_types():
        entry = lib.state_library_for(stype, "standard")
        assert entry.states, stype
        for st in entry.states:
            assert vocab.is_known_state_kind(st.kind), (stype, st.kind)
            assert st.message and st.copy_text and st.copy_key in lib.copy_table()
        assert len(entry.required_state_kinds()) == len(set(entry.required_state_kinds()))
        assert entry.behaviors, stype


def test_list_and_detail_have_the_sc05_states():
    # SC-05: loading, empty, error, offline, no-permission for lists; detail has not_found instead of empty
    assert {"loading", "empty", "error", "offline", "forbidden"} <= set(
        lib.state_library_for("list", "compact").required_state_kinds()
    )
    assert {"loading", "error", "offline", "forbidden", "not_found"} <= set(
        lib.state_library_for("detail", "compact").required_state_kinds()
    )


def test_scenario_sets_meet_profile_minimums():
    mins = default_config().profiles.acceptance_scenarios_min
    assert mins == {"compact": 3, "standard": 5, "deep": 8}
    for stype in vocab.screen_types():
        counts = {p: len(lib.state_library_for(stype, p).acceptance) for p in mins}
        for p, need in mins.items():
            assert counts[p] >= need, (stype, p, counts[p])
        deep_ids = {a.id for a in lib.state_library_for(stype, "deep").acceptance}
        assert {"permission_denied", "edit_conflict"} <= deep_ids, stype
        # cumulative: a smaller profile is a prefix of a larger one
        compact_ids = [a.id for a in lib.state_library_for(stype, "compact").acceptance]
        standard_ids = [a.id for a in lib.state_library_for(stype, "standard").acceptance]
        assert standard_ids[: len(compact_ids)] == compact_ids


def test_scenarios_applicable_and_complete():
    sc = lib.scenarios()
    doc_set = [
        "first_launch",
        "sign_in",
        "expired_session",
        "permission_denied",
        "no_network",
        "slow_network",
        "server_error",
        "empty_data",
        "last_page",
        "validation_error",
        "deep_link_signed_out",
        "push_signed_out",
        "forced_update",
        "language_switch",
        "account_deletion",
        "payment_declined",
        "pending",
        "duplicate",
        "edit_conflict",
    ]
    assert set(doc_set) <= set(sc)
    for s in sc.values():
        assert s.given and s.when and s.then
    for stype in vocab.screen_types():
        for a in lib.state_library_for(stype, "deep").acceptance:
            assert lib.scenario(a.id).applies(stype)


def test_acceptance_and_state_instantiate_into_the_graph():
    entry = lib.state_library_for("checkout", "deep")
    acc = entry.acceptance[0].to_acceptance("Checkout")
    assert isinstance(acc, Acceptance) and "{screen}" not in acc.given + acc.when + acc.then
    spec = entry.states[0].to_state_spec()
    assert isinstance(spec, StateSpec) and spec.origin == "library"
    overridden = lib.acceptance_template("auth", "edit_conflict")
    assert "another device" in overridden.given


def test_copy_keys_unique_and_behaviour_keys_resolve():
    table = lib.copy_table()
    assert all(k.startswith("lib.") and v.strip() for k, v in table.items())
    for stype in vocab.screen_types():
        for b in lib.state_library_for(stype, "compact").behaviors:
            assert all(k in table for k in b.copy_keys)


def test_unknown_lookups_raise():
    import pytest

    with pytest.raises(lib.LibraryError):
        lib.state_library_for("nonsense", "compact")
    with pytest.raises(lib.LibraryError):
        lib.state_library_for("list", "huge")
    with pytest.raises(lib.LibraryError):
        lib.scenario("nope")
    with pytest.raises(lib.LibraryError):
        lib.error("NOPE")


# ------------------------------------------------------------ system screens


def test_system_screen_types_and_vocabularies_exist():
    assert len(lib.system_screens()) >= 15
    for s in lib.system_screens().values():
        assert s.screen_type in vocab.screen_types()
        assert lib.has_state_library(s.screen_type)
        assert s.provenance.kind in ("policy", "library")
        assert (
            sum(r.importance == "primary" for r in s.regions)
            <= default_config().checks.mobile_fit.max_primary_regions
        )
        assert sum(a.importance == "primary" for a in s.actions) <= 1


def test_system_set_follows_the_predicates():
    def ids(facts, profile=None):
        return {s.id for s in lib.system_screens_for(facts, profile)}

    assert ids(set()) == {"help_support", "terms", "privacy"}
    phone = ids({"accounts", "phone_code_login"})
    assert {"sign_in", "verify_code", "profile", "settings"} <= phone
    assert "forgot_password" not in phone and "account_deletion" not in phone
    email = ids({"accounts", "email_password_login", "accounts_creatable_in_app"})
    assert {"forgot_password", "reset_password", "account_deletion"} <= email
    assert "verify_code" not in email
    assert "role_chooser" in ids({"accounts", "multi_role_user"})
    assert "consent_age_gate" in ids({"minors_or_health_or_identity"})
    assert "notifications_inbox" in ids({"push_notifications"})
    assert "notifications_inbox" not in ids({"accounts"})
    assert "onboarding" in ids({"value_needs_explaining"}, "standard")
    assert "onboarding" not in ids({"value_needs_explaining"}, "compact")
    assert "permission_rationale" in ids({"sensitive_permission"})


def test_system_screen_variants_and_non_screens():
    sign_in = lib.system_screens()["sign_in"]
    assert lib.active_variants(sign_in, {"phone_code_login", "social_login"}) == [
        "phone_code",
        "social",
    ]
    names = {n.name for n in lib.not_screens()}
    assert {"launch", "error", "maintenance", "update_required", "offline"} == names


def test_account_deletion_is_policy_provenance():
    s = lib.system_screens()["account_deletion"]
    assert s.provenance.kind == "policy"
    assert any(a.destructive and a.irreversible for a in s.actions)


def test_permission_rationale_is_per_permission():
    assert lib.system_screens()["permission_rationale"].per == "permission"


# ------------------------------------------------------------ error catalogue


def test_error_catalogue_unique_and_complete():
    raw = yaml.safe_load((lib.LIB_DIR / "error_catalogue.yaml").read_text(encoding="utf-8"))
    codes = [e["code"] for e in raw["errors"]]
    assert len(codes) == len(set(codes))
    for needed in (
        "TASK_OVER_BUDGET",
        "SCREEN_TOO_DENSE_FOR_PHONE",
        "CLOSURE_INCOMPLETE",
        "BUDGET_SET_INCONSISTENT",
    ):
        assert needed in codes
    for e in lib.errors().values():
        assert e.code == e.code.upper() and " " not in e.code
        assert e.severity in ("error", "warning", "info")
        assert e.audience in ("agent", "owner")
        assert e.repair_class in ("A", "B", "C")


def test_every_validator_has_an_error_code():
    covered = {v for e in lib.errors().values() for v in e.validators}
    for n in range(1, 46):
        if n != 18:
            assert f"V-{n:02d}" in covered, n


def test_make_error_is_structured():
    err = lib.make_error("TASK_OVER_BUDGET", task_id="T-0142", read="31k", budget="28k")
    assert err["where"] == "task T-0142" and "31k read against 28k" in err["why"]
    assert set(err) == {"code", "severity", "audience", "repair_class", "where", "why", "fix"}
    import pytest

    with pytest.raises(lib.LibraryError):
        lib.make_error("TASK_OVER_BUDGET", task_id="T-1")


# ------------------------------------------------------------ synonyms


def test_synonyms():
    assert lib.synonym("screen_type", "login") == "auth"
    assert lib.synonym("screen_type", "List-View") == "list"
    assert lib.synonym("shape", "table") == "collection"
    assert lib.synonym_entry("shape", "table").attrs == ["columns=2"]  # type: ignore[union-attr]
    assert lib.synonym("intent", "logout") == "sign_out"
    assert lib.canonical("screen_type", "detail") == "detail"
    assert lib.canonical("screen_type", "login") == "auth"
    assert lib.canonical("screen_type", "zzz") is None
    for vname in lib.synonyms_file().vocabs:
        assert lib.synonym(vname, "btn") is None
    for banned in lib.synonyms_file().not_synonyms:
        for vname in lib.synonyms_file().vocabs:
            assert lib.synonym(vname, banned.word) is None


def test_synonym_tables_cover_every_closed_vocabulary():
    assert set(lib.synonyms_file().vocabs) == set(vocab.SYNONYM_VOCABS)


# ------------------------------------------------------------ form words


def test_form_word_lint_flags_and_allows():
    kinds = {
        f.category
        for f in lib.form_word_findings("a button at the top of the list, 12px, fade in, red background, #ff0000")
    }
    assert kinds == {"widget", "position", "unit", "motion", "colour"}
    assert lib.form_word_findings("shown in right-to-left scripts, top 20 sellers") == []
    # ambiguous single words are ordinary prose (tuned on live model output, D-185)
    assert lib.form_word_findings("the right to cancel, green chillies, orange juice, left over stock") == []
    assert [f.word for f in lib.form_word_findings("the map of stores")] == []
    assert lib.form_word_findings("a bottom sheet")[0].category in ("position", "widget")
    assert lib.form_word_findings("animated card")[0].category == "motion"


def test_screen_type_allow_list():
    assert lib.form_word_findings("a marker on the map", "map") == []
    assert [f.word for f in lib.form_word_findings("slides about the app", "onboarding")] == []
    assert [f.word for f in lib.form_word_findings("slides about the app", "list")] == ["slides"]


def test_widget_key_suffix_helper():
    assert lib.key_names_a_widget("pay_now") and lib.key_names_a_widget("submit_button")
    assert not lib.key_names_a_widget("submit_credentials")


def test_form_words_keep_widget_key_suffixes_out():
    raw = yaml.safe_load((lib.LIB_DIR / "form_words.yaml").read_text(encoding="utf-8"))
    assert "widget_key_suffixes" not in raw
    assert set(raw["deny"]) == {"position", "widget", "unit", "motion", "colour"}


def test_no_form_word_in_any_library_text():
    bad = [
        (where, f.word)
        for where, text, stype in lib.library_texts()
        for f in lib.form_word_findings(text, stype)
    ]
    assert bad == []


def test_library_texts_are_non_trivial():
    assert sum(1 for _ in lib.library_texts()) > 400


def test_versions_are_stable_hashes():
    assert len(lib.libraries_version()) == 64 and len(lib.content_version()) == 64
    assert lib.content_version() != lib.libraries_version()
