---
id: SCR-S005   kind: screen   name: Notifications
app: APP-001   module: MOD-001   screen_type: notifications   surface: phone
roles: [ROL-001, ROL-002, ROL-003, ROL-004, ROL-005, ROL-006, ROL-007, ROL-008, ROL-010, ROL-012, ROL-013, ROL-014, ROL-015, ROL-016, ROL-017]
tiers: {purpose: default, regions: default, actions: default, states: default}
provenance: ["library:system_screens.notifications_inbox"]
---
purpose: The user reads the messages the app has sent them.
route: {name: notifications, path: /notifications, params: [], guard: [signed_in, role_has_access]}
regions:   # alphabetical by key; the order carries no meaning
  - {key: messages, shape: collection, what: the notifications the user received, importance: primary, cardinality: many}
actions:
  - {key: mark_all_read, importance: secondary, intent: act_on_item, what: mark every notification as read, effect: [stay]}
  - {key: open_notification, importance: primary, intent: navigate, what: go to what the notification is about, effect: [stay]}
states:   # what each must communicate, not how
  - {kind: "domain:push_disabled", message: Push notifications are off for this app; say what the user misses and how to turn them on.}
  - {kind: empty, message: There are no notifications; say what kinds will appear here.}
  - {kind: error, message: Something failed; say what happened in plain words and offer a retry.}
  - {kind: loading, message: Nothing to show yet; the user must still see that content is coming.}
  - {kind: offline, message: "There is no network; show saved data, and say which actions are unavailable and why."}
copy_keys: [lib.common.error, lib.common.loading, lib.common.offline, lib.notifications.domain.push_disabled, lib.notifications.empty, scr_s005.mark_all_read, scr_s005.open_notification, scr_s005.title]
acceptance:
  - GIVEN the user has typical data and a working network WHEN the user uses Notifications for its main purpose THEN the main purpose is achieved and the result is confirmed in plain words
  - GIVEN the user has no data for Notifications yet WHEN Notifications loads THEN the empty state says why there is nothing and what the user can do next
  - GIVEN the server fails when Notifications asks for data or sends a change WHEN the failure is received THEN the user is told in plain words that something went wrong, nothing is lost, and a retry is offered
  - GIVEN the device has no network while the user is on Notifications WHEN the user opens Notifications or acts on it THEN saved data is shown as possibly out of date, actions that need the network say so, and queued actions are sent when the network returns
  - GIVEN the user receives a notification about something on Notifications and is signed out WHEN the user opens the notification THEN the notification shows no private content before sign-in, and after sign-in the user arrives at Notifications
  - GIVEN the user's role may not see or do what Notifications offers WHEN the user reaches Notifications or tries the action THEN the user sees the forbidden state with the reason and a way to somewhere allowed, and no restricted data is shown
  - GIVEN the data on Notifications was changed elsewhere after it was loaded WHEN the user acts on the older data THEN the user is told it changed, sees the newer data, and nothing is overwritten without the user's choice
  - GIVEN Notifications needs a device ability and the user refuses it WHEN the refusal is received THEN the rest of Notifications still works, the missing ability is explained once, and the way to the system settings is offered

## closure
copy (en): lib.common.error 'Something went wrong. Please try again.'; lib.common.loading 'Loading, one moment.'; lib.common.offline 'You are offline. Showing what was saved last.'; lib.notifications.domain.push_disabled 'Notifications are off. Turn them on to get updates as they happen.'; lib.notifications.empty 'No notifications yet.'; scr_s005.mark_all_read 'Mark all read'; scr_s005.open_notification 'Open notification'; scr_s005.title 'Notifications'
tokens: names only; none used by value
