---
id: SCR-S004   kind: screen   name: Notification settings
app: APP-001   module: MOD-001   screen_type: settings   surface: phone
roles: [ROL-001, ROL-002, ROL-003, ROL-004, ROL-005, ROL-006, ROL-007, ROL-008, ROL-010, ROL-012, ROL-013, ROL-014, ROL-015, ROL-016, ROL-017]
tiers: {purpose: default, regions: default, actions: default, states: default}
provenance: ["library:system_screens.notification_settings"]
---
purpose: The user chooses which notifications they receive.
route: {name: notification_settings, path: /notification_settings, params: [], guard: [signed_in, role_has_access]}
regions:   # alphabetical by key; the order carries no meaning
  - {key: kinds, shape: key_values, what: the kinds of notification and whether each is on, importance: primary}
  - {key: system_state, shape: notice, what: whether the device allows notifications at all, importance: supporting}
actions:
  - {key: change_kind, importance: primary, intent: toggle, what: turn one kind of notification on or off, effect: [stay]}
  - {key: open_system_settings, importance: secondary, intent: grant_permission, what: go to the system settings when the device blocks notifications, effect: [stay]}
states:   # what each must communicate, not how
  - {kind: "domain:save_failed", message: The change was not saved; the previous value is restored and the reason is given.}
  - {kind: error, message: Something failed; say what happened in plain words and offer a retry.}
  - {kind: loading, message: Nothing to show yet; the user must still see that content is coming.}
  - {kind: offline, message: "There is no network; show saved data, and say which actions are unavailable and why."}
  - {kind: success, message: What the user did worked; say what changed.}
copy_keys: [lib.common.error, lib.common.loading, lib.common.offline, lib.common.success, lib.settings.domain.save_failed, scr_s004.change_kind, scr_s004.open_system_settings, scr_s004.title]
acceptance:
  - GIVEN the user has typical data and a working network WHEN the user uses Notification settings for its main purpose THEN the main purpose is achieved and the result is confirmed in plain words
  - GIVEN the server fails when Notification settings asks for data or sends a change WHEN the failure is received THEN the user is told in plain words that something went wrong, nothing is lost, and a retry is offered
  - GIVEN the device has no network while the user is on Notification settings WHEN the user opens Notification settings or acts on it THEN saved data is shown as possibly out of date, actions that need the network say so, and queued actions are sent when the network returns
  - GIVEN the user's session ended while Notification settings was open WHEN the user does something that needs the session THEN the user is asked to sign in again, comes back to Notification settings afterwards, and keeps any unsent input
  - GIVEN the user changes the app language while Notification settings is open WHEN the change is applied THEN all text on Notification settings follows the new language, numbers and dates follow its formats, and the user's place and input are kept
  - GIVEN the user's role may not see or do what Notification settings offers WHEN the user reaches Notification settings or tries the action THEN the user sees the forbidden state with the reason and a way to somewhere allowed, and no restricted data is shown
  - GIVEN the data on Notification settings was changed elsewhere after it was loaded WHEN the user acts on the older data THEN the user is told it changed, sees the newer data, and nothing is overwritten without the user's choice
  - GIVEN the user asks to delete their account from Notification settings WHEN the user confirms THEN the user is told what will be removed and when, the account is deleted, the session ends, and the user cannot sign in with it again

## closure
copy (en): lib.common.error 'Something went wrong. Please try again.'; lib.common.loading 'Loading, one moment.'; lib.common.offline 'You are offline. Showing what was saved last.'; lib.common.success 'Done.'; lib.settings.domain.save_failed 'That setting could not be changed. Try again.'; scr_s004.change_kind 'Change kind'; scr_s004.open_system_settings 'Open system settings'; scr_s004.title 'Notification settings'
tokens: names only; none used by value
