---
id: SCR-S004   kind: screen   name: Forgot password
app: APP-001   module: MOD-001   screen_type: auth   surface: phone
roles: [ROL-001, ROL-002, ROL-003, ROL-004, ROL-005, ROL-006, ROL-007, ROL-008, ROL-009]
tiers: {purpose: default, regions: default, actions: default, states: default}
provenance: ["library:system_screens.forgot_password"]
---
purpose: The user asks for a way to set a new password.
route: {name: forgot_password, path: /forgot_password, params: []}
reached_from: [SCR-001.go_forgot_password]   leads_to: [SCR-S005]
regions:   # alphabetical by key; the order carries no meaning
  - {key: identity, shape: inputs, what: the email address of the account, importance: primary}
  - {key: outcome, shape: notice, what: "what happens next, without revealing whether the account exists", importance: supporting}
actions:
  - {key: back_to_sign_in, importance: secondary, intent: navigate, what: return to sign in, effect: [go SCR-001], errors: [network]}
  - {key: request_reset, importance: primary, intent: confirm, what: send the recovery message, effect: [go SCR-S005], errors: [network]}
inputs:
  - {key: email, type: email, required: True}
states:   # what each must communicate, not how
  - {kind: "domain:code_expired", message: The code is no longer valid; the user can request a new one.}
  - {kind: "domain:code_sent", message: "A code was sent; say where, and when another can be requested."}
  - {kind: "domain:locked_out", message: Too many attempts; say when the user may try again and how to recover.}
  - {kind: "domain:wrong_credentials", message: The details do not match; do not reveal which part was wrong.}
  - {kind: error, message: Something failed; say what happened in plain words and offer a retry.}
  - {kind: loading, message: Nothing to show yet; the user must still see that content is coming.}
  - {kind: offline, message: "There is no network; show saved data, and say which actions are unavailable and why."}
  - {kind: success, message: What the user did worked; say what changed.}
copy_keys: [lib.auth.domain.code_expired, lib.auth.domain.code_sent, lib.auth.domain.locked_out, lib.auth.domain.wrong_credentials, lib.common.error, lib.common.loading, lib.common.offline, lib.common.success, scr_s004.back_to_sign_in, scr_s004.email, scr_s004.request_reset, scr_s004.title]
acceptance:
  - GIVEN the user has an account and is signed out WHEN the user gives valid credentials THEN the user is signed in and lands on their home screen, and the credentials are not kept anywhere visible
  - GIVEN the user gave a value that breaks a rule on Forgot password WHEN the user submits or leaves the input THEN the problem is named next to the input in plain words, the input is kept, and nothing is sent
  - GIVEN the server fails when Forgot password asks for data or sends a change WHEN the failure is received THEN the user is told in plain words that something went wrong, nothing is lost, and a retry is offered
  - GIVEN the device has no network while the user is on Forgot password WHEN the user opens Forgot password or acts on it THEN saved data is shown as possibly out of date, actions that need the network say so, and queued actions are sent when the network returns
  - GIVEN the user's session ended while Forgot password was open WHEN the user does something that needs the session THEN the user is asked to sign in again, comes back to Forgot password afterwards, and keeps any unsent input

## closure
route SCR-001: sign_in /sign_in ()
route SCR-S005: reset_password /reset_password ()
copy (en): lib.auth.domain.code_expired 'That code has expired. Request a new one.'; lib.auth.domain.code_sent 'We sent you a code.'; lib.auth.domain.locked_out 'Too many attempts. Try again later or reset your access.'; lib.auth.domain.wrong_credentials 'Those details did not match. Please check them and try again.'; lib.common.error 'Something went wrong. Please try again.'; lib.common.loading 'Loading, one moment.'; lib.common.offline 'You are offline. Showing what was saved last.'; lib.common.success 'Done.'; scr_s004.back_to_sign_in 'Back to sign in'; scr_s004.email 'Email'; scr_s004.request_reset 'Request reset'; scr_s004.title 'Forgot password'
tokens: names only; none used by value
