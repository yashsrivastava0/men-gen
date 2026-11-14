---
id: SCR-S005   kind: screen   name: Reset password
app: APP-001   module: MOD-001   screen_type: auth   surface: phone
roles: [ROL-001, ROL-002, ROL-003, ROL-004, ROL-005, ROL-006, ROL-007, ROL-008, ROL-009]
tiers: {purpose: default, regions: default, actions: default, states: default}
provenance: ["library:system_screens.reset_password"]
---
purpose: The user chooses a new password after following the recovery message.
route: {name: reset_password, path: /reset_password, params: []}
reached_from: [SCR-S004.request_reset]   leads_to: [SCR-001]
regions:   # alphabetical by key; the order carries no meaning
  - {key: new_secret_entry, shape: inputs, what: the new password and its confirmation, importance: primary}
  - {key: rules, shape: text, what: what a valid password needs, importance: supporting}
actions:
  - {key: save_secret, importance: primary, intent: enter_secret, what: set the new password and end other sessions, effect: [go SCR-001], errors: [network]}
inputs:
  - {key: new_secret, type: secret, required: True}
  - {key: new_secret_again, type: secret, required: True}
states:   # what each must communicate, not how
  - {kind: "domain:code_expired", message: The code is no longer valid; the user can request a new one.}
  - {kind: "domain:code_sent", message: "A code was sent; say where, and when another can be requested."}
  - {kind: "domain:locked_out", message: Too many attempts; say when the user may try again and how to recover.}
  - {kind: "domain:wrong_credentials", message: The details do not match; do not reveal which part was wrong.}
  - {kind: error, message: Something failed; say what happened in plain words and offer a retry.}
  - {kind: loading, message: Nothing to show yet; the user must still see that content is coming.}
  - {kind: offline, message: "There is no network; show saved data, and say which actions are unavailable and why."}
  - {kind: success, message: What the user did worked; say what changed.}
copy_keys: [lib.auth.domain.code_expired, lib.auth.domain.code_sent, lib.auth.domain.locked_out, lib.auth.domain.wrong_credentials, lib.common.error, lib.common.loading, lib.common.offline, lib.common.success, scr_s005.new_secret, scr_s005.new_secret_again, scr_s005.save_secret, scr_s005.title]
acceptance:
  - GIVEN the user has an account and is signed out WHEN the user gives valid credentials THEN the user is signed in and lands on their home screen, and the credentials are not kept anywhere visible
  - GIVEN the user gave a value that breaks a rule on Reset password WHEN the user submits or leaves the input THEN the problem is named next to the input in plain words, the input is kept, and nothing is sent
  - GIVEN the server fails when Reset password asks for data or sends a change WHEN the failure is received THEN the user is told in plain words that something went wrong, nothing is lost, and a retry is offered
  - GIVEN the device has no network while the user is on Reset password WHEN the user opens Reset password or acts on it THEN saved data is shown as possibly out of date, actions that need the network say so, and queued actions are sent when the network returns
  - GIVEN the user's session ended while Reset password was open WHEN the user does something that needs the session THEN the user is asked to sign in again, comes back to Reset password afterwards, and keeps any unsent input

## closure
route SCR-001: sign_in /sign_in ()
copy (en): lib.auth.domain.code_expired 'That code has expired. Request a new one.'; lib.auth.domain.code_sent 'We sent you a code.'; lib.auth.domain.locked_out 'Too many attempts. Try again later or reset your access.'; lib.auth.domain.wrong_credentials 'Those details did not match. Please check them and try again.'; lib.common.error 'Something went wrong. Please try again.'; lib.common.loading 'Loading, one moment.'; lib.common.offline 'You are offline. Showing what was saved last.'; lib.common.success 'Done.'; scr_s005.new_secret 'New secret'; scr_s005.new_secret_again 'New secret again'; scr_s005.save_secret 'Save secret'; scr_s005.title 'Reset password'
tokens: names only; none used by value
