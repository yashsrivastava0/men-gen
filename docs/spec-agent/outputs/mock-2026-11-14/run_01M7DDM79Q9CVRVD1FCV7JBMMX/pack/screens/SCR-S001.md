---
id: SCR-S001   kind: screen   name: Profile
app: APP-001   module: MOD-001   screen_type: profile   surface: phone
roles: [ROL-002, ROL-003, ROL-004, ROL-005]
tiers: {purpose: default, regions: default, actions: default, states: default}
provenance: ["library:system_screens.profile"]
---
purpose: The user sees and edits the details of their own account.
route: {name: profile, path: /profile, params: [], guard: [signed_in, role_has_access]}
leads_to: [SCR-S002]
regions:   # alphabetical by key; the order carries no meaning
  - {key: account_links, shape: collection, what: ways to the account's other screens, importance: supporting, cardinality: many}
  - {key: identity, shape: key_values, what: "the user's name, picture and contact details", importance: primary}
actions:
  - {key: edit_details, importance: primary, intent: enter_text, what: change the user's own details, effect: [stay]}
  - {key: sign_out, importance: secondary, intent: sign_out, what: end the session on this device, effect: [go SCR-001]}
states:   # what each must communicate, not how
  - {kind: "domain:save_failed", message: The change was not saved; the user's entry is kept and a retry is offered.}
  - {kind: error, message: Something failed; say what happened in plain words and offer a retry.}
  - {kind: forbidden, message: The user's role may not see or do this; say so and where to go instead.}
  - {kind: loading, message: Nothing to show yet; the user must still see that content is coming.}
  - {kind: offline, message: "There is no network; show saved data, and say which actions are unavailable and why."}
  - {kind: success, message: What the user did worked; say what changed.}
copy_keys: [lib.common.error, lib.common.forbidden, lib.common.loading, lib.common.offline, lib.common.success, lib.profile.domain.save_failed, scr_s001.edit_details, scr_s001.sign_out, scr_s001.title]
acceptance:
  - GIVEN the user has typical data and a working network WHEN the user uses Profile for its main purpose THEN the main purpose is achieved and the result is confirmed in plain words
  - GIVEN the user gave a value that breaks a rule on Profile WHEN the user submits or leaves the input THEN the problem is named next to the input in plain words, the input is kept, and nothing is sent
  - GIVEN the server fails when Profile asks for data or sends a change WHEN the failure is received THEN the user is told in plain words that something went wrong, nothing is lost, and a retry is offered

## closure
route SCR-001: phone_sign_in /phone_sign_in ()
route SCR-S002: settings /settings () guard: signed_in, role_has_access
copy (en): lib.common.error 'Something went wrong. Please try again.'; lib.common.forbidden 'You do not have access to this.'; lib.common.loading 'Loading, one moment.'; lib.common.offline 'You are offline. Showing what was saved last.'; lib.common.success 'Done.'; lib.profile.domain.save_failed 'Your changes were not saved. Try again.'; scr_s001.edit_details 'Edit details'; scr_s001.sign_out 'Sign out'; scr_s001.title 'Profile'
tokens: names only; none used by value
