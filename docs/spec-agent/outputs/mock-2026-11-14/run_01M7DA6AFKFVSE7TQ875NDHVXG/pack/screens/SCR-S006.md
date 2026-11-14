---
id: SCR-S006   kind: screen   name: Settings
app: APP-001   module: MOD-001   screen_type: settings   surface: phone
roles: [ROL-001, ROL-002, ROL-003, ROL-004, ROL-005, ROL-006, ROL-007, ROL-008, ROL-009]
tiers: {purpose: default, regions: default, actions: default, states: default}
provenance: ["library:system_screens.settings"]
---
purpose: The user manages app preferences and account controls.
route: {name: settings, path: /settings, params: [], guard: [signed_in, role_has_access]}
reached_from: [SCR-S008.keep_account]   leads_to: [SCR-028, SCR-S007, SCR-S008]
regions:   # alphabetical by key; the order carries no meaning
  - {key: account_controls, shape: collection, what: account actions such as sign out and delete account, importance: supporting, cardinality: many}
  - {key: preferences, shape: key_values, what: the app's preferences, importance: primary}
actions:
  - {key: change_preference, importance: primary, intent: toggle, what: change a preference, effect: [stay]}
  - {key: go_account_deletion, importance: secondary, intent: navigate, what: go to account deletion, effect: [go SCR-S008]}
states:   # what each must communicate, not how
  - {kind: "domain:save_failed", message: The change was not saved; the previous value is restored and the reason is given.}
  - {kind: error, message: Something failed; say what happened in plain words and offer a retry.}
  - {kind: loading, message: Nothing to show yet; the user must still see that content is coming.}
  - {kind: offline, message: "There is no network; show saved data, and say which actions are unavailable and why."}
  - {kind: success, message: What the user did worked; say what changed.}
copy_keys: [lib.common.error, lib.common.loading, lib.common.offline, lib.common.success, lib.settings.domain.save_failed, scr_s006.change_preference, scr_s006.go_account_deletion, scr_s006.title]
acceptance:
  - GIVEN the user has typical data and a working network WHEN the user uses Settings for its main purpose THEN the main purpose is achieved and the result is confirmed in plain words
  - GIVEN the server fails when Settings asks for data or sends a change WHEN the failure is received THEN the user is told in plain words that something went wrong, nothing is lost, and a retry is offered
  - GIVEN the device has no network while the user is on Settings WHEN the user opens Settings or acts on it THEN saved data is shown as possibly out of date, actions that need the network say so, and queued actions are sent when the network returns
  - GIVEN the user's session ended while Settings was open WHEN the user does something that needs the session THEN the user is asked to sign in again, comes back to Settings afterwards, and keeps any unsent input
  - GIVEN the user changes the app language while Settings is open WHEN the change is applied THEN all text on Settings follows the new language, numbers and dates follow its formats, and the user's place and input are kept

## closure
route SCR-028: language_settings /language_settings () guard: signed_in, role_has_access
route SCR-S007: help_and_support /help_and_support () guard: signed_in, role_has_access
route SCR-S008: delete_account /delete_account () guard: signed_in, role_has_access
copy (en): lib.common.error 'Something went wrong. Please try again.'; lib.common.loading 'Loading, one moment.'; lib.common.offline 'You are offline. Showing what was saved last.'; lib.common.success 'Done.'; lib.settings.domain.save_failed 'That setting could not be changed. Try again.'; scr_s006.change_preference 'Change preference'; scr_s006.go_account_deletion 'Go account deletion'; scr_s006.title 'Settings'
tokens: names only; none used by value
