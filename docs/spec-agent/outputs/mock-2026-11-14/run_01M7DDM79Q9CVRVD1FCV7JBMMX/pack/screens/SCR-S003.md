---
id: SCR-S003   kind: screen   name: Notification settings
app: APP-001   module: MOD-001   screen_type: settings   surface: phone
roles: [ROL-002, ROL-003, ROL-004, ROL-005]
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
copy_keys: [lib.common.error, lib.common.loading, lib.common.offline, lib.common.success, lib.settings.domain.save_failed, scr_s003.change_kind, scr_s003.open_system_settings, scr_s003.title]
acceptance:
  - GIVEN the user has typical data and a working network WHEN the user uses Notification settings for its main purpose THEN the main purpose is achieved and the result is confirmed in plain words
  - GIVEN the server fails when Notification settings asks for data or sends a change WHEN the failure is received THEN the user is told in plain words that something went wrong, nothing is lost, and a retry is offered
  - GIVEN the device has no network while the user is on Notification settings WHEN the user opens Notification settings or acts on it THEN saved data is shown as possibly out of date, actions that need the network say so, and queued actions are sent when the network returns

## closure
copy (en): lib.common.error 'Something went wrong. Please try again.'; lib.common.loading 'Loading, one moment.'; lib.common.offline 'You are offline. Showing what was saved last.'; lib.common.success 'Done.'; lib.settings.domain.save_failed 'That setting could not be changed. Try again.'; scr_s003.change_kind 'Change kind'; scr_s003.open_system_settings 'Open system settings'; scr_s003.title 'Notification settings'
tokens: names only; none used by value
