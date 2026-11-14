---
id: SCR-S004   kind: screen   name: Help and support
app: APP-001   module: MOD-001   screen_type: detail   surface: phone
roles: [ROL-002, ROL-003, ROL-004, ROL-005]
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
copy_keys: [lib.common.error, lib.common.forbidden, lib.common.loading, lib.common.not_found, lib.common.offline, lib.common.stale, scr_s004.contact_owner, scr_s004.title]
acceptance:
  - GIVEN the user has typical data and a working network WHEN the user uses Help and support for its main purpose THEN the main purpose is achieved and the result is confirmed in plain words
  - GIVEN the server fails when Help and support asks for data or sends a change WHEN the failure is received THEN the user is told in plain words that something went wrong, nothing is lost, and a retry is offered
  - GIVEN the device has no network while the user is on Help and support WHEN the user opens Help and support or acts on it THEN saved data is shown as possibly out of date, actions that need the network say so, and queued actions are sent when the network returns

## closure
copy (en): lib.common.error 'Something went wrong. Please try again.'; lib.common.forbidden 'You do not have access to this.'; lib.common.loading 'Loading, one moment.'; lib.common.not_found 'We could not find this. It may have been removed.'; lib.common.offline 'You are offline. Showing what was saved last.'; lib.common.stale 'This may be out of date.'; scr_s004.contact_owner 'Contact owner'; scr_s004.title 'Help and support'
tokens: names only; none used by value
