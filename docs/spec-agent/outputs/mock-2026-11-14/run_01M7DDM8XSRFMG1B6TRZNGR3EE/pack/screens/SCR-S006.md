---
id: SCR-S006   kind: screen   name: Help and support
app: APP-001   module: MOD-001   screen_type: detail   surface: phone
roles: [ROL-001, ROL-002, ROL-003, ROL-004, ROL-005, ROL-006, ROL-007, ROL-008, ROL-010, ROL-012, ROL-013, ROL-014, ROL-015, ROL-016, ROL-017]
tiers: {purpose: default, regions: default, actions: default, states: default}
provenance: ["library:system_screens.help_support"]
---
purpose: The user finds answers and a way to reach the owner.
route: {name: help_and_support, path: /help_and_support, params: [], guard: [signed_in, role_has_access]}
regions:   # alphabetical by key; the order carries no meaning
  - {key: answers, shape: collection, what: common questions and their answers, importance: primary, cardinality: many}
  - {key: contact, shape: key_values, what: how to reach the owner, importance: supporting}
actions:
  - {key: contact_owner, importance: primary, intent: call_contact, what: reach the owner by the offered channel, effect: [stay]}
states:   # what each must communicate, not how
  - {kind: error, message: Something failed; say what happened in plain words and offer a retry.}
  - {kind: forbidden, message: The user's role may not see or do this; say so and where to go instead.}
  - {kind: loading, message: Nothing to show yet; the user must still see that content is coming.}
  - {kind: not_found, message: The item does not exist or was removed; offer a way back.}
  - {kind: offline, message: "There is no network; show saved data, and say which actions are unavailable and why."}
  - {kind: stale, message: The data shown may be out of date; the user can ask for the latest.}
copy_keys: [lib.common.error, lib.common.forbidden, lib.common.loading, lib.common.not_found, lib.common.offline, lib.common.stale, scr_s006.contact_owner, scr_s006.title]
acceptance:
  - GIVEN the user has typical data and a working network WHEN the user uses Help and support for its main purpose THEN the main purpose is achieved and the result is confirmed in plain words
  - GIVEN the server fails when Help and support asks for data or sends a change WHEN the failure is received THEN the user is told in plain words that something went wrong, nothing is lost, and a retry is offered
  - GIVEN the device has no network while the user is on Help and support WHEN the user opens Help and support or acts on it THEN saved data is shown as possibly out of date, actions that need the network say so, and queued actions are sent when the network returns
  - GIVEN the network is slow while Help and support loads WHEN the wait passes the moment the user expects THEN the user can see that content is coming, and a wait past the timeout becomes an error with a retry
  - GIVEN the user opens a link to Help and support and is signed out WHEN the app starts from the link THEN the user signs in first and then arrives at Help and support, not at the home screen
  - GIVEN the user's role may not see or do what Help and support offers WHEN the user reaches Help and support or tries the action THEN the user sees the forbidden state with the reason and a way to somewhere allowed, and no restricted data is shown
  - GIVEN the data on Help and support was changed elsewhere after it was loaded WHEN the user acts on the older data THEN the user is told it changed, sees the newer data, and nothing is overwritten without the user's choice
  - GIVEN the data on Help and support has very long text, very large numbers, emoji and right-to-left text WHEN Help and support shows it THEN nothing is cut off in a way that hides its meaning, and the screen stays usable

## closure
copy (en): lib.common.error 'Something went wrong. Please try again.'; lib.common.forbidden 'You do not have access to this.'; lib.common.loading 'Loading, one moment.'; lib.common.not_found 'We could not find this. It may have been removed.'; lib.common.offline 'You are offline. Showing what was saved last.'; lib.common.stale 'This may be out of date.'; scr_s006.contact_owner 'Contact owner'; scr_s006.title 'Help and support'
tokens: names only; none used by value
