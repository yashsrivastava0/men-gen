---
id: SCR-S007   kind: screen   name: Delete account
app: APP-001   module: MOD-001   screen_type: form   surface: phone
roles: [ROL-001, ROL-002, ROL-003, ROL-004, ROL-005, ROL-006, ROL-007, ROL-008, ROL-010, ROL-012, ROL-013, ROL-014, ROL-015, ROL-016, ROL-017]
tiers: {purpose: default, regions: default, actions: default, states: default}
provenance: ["policy:policy:POL-APPLE-DELETION@2026-11-14"]
---
purpose: The user deletes their account and its data from inside the app, as the stores require.
route: {name: delete_account, path: /delete_account, params: [], guard: [signed_in, role_has_access]}
reached_from: [SCR-S003.go_account_deletion]   leads_to: []
regions:   # alphabetical by key; the order carries no meaning
  - {key: confirmation, shape: inputs, what: the user's confirmation of the request, importance: primary}
  - {key: consequences, shape: text, what: "what will be removed, what is kept by law, and when", importance: primary}
actions:
  - {key: delete_account, importance: primary, intent: confirm, what: delete the account and end every session, effect: [go SCR-001], errors: [network]}
  - {key: keep_account, importance: secondary, intent: cancel, what: leave the account as it is, effect: [go SCR-S003], errors: [network]}
inputs:
  - {key: confirm_secret, type: secret, required: True}
states:   # what each must communicate, not how
  - {kind: "domain:conflict", message: The data changed elsewhere since it was loaded; show what changed and let the user choose.}
  - {kind: "domain:submitting", message: The form is being sent; it cannot be sent again meanwhile.}
  - {kind: "domain:validating", message: The input is being checked; the user can keep typing.}
  - {kind: "domain:validation_failed", message: A value breaks a rule; the message names the input and the rule.}
  - {kind: error, message: Something failed; say what happened in plain words and offer a retry.}
  - {kind: forbidden, message: The user's role may not see or do this; say so and where to go instead.}
  - {kind: loading, message: Nothing to show yet; the user must still see that content is coming.}
  - {kind: offline, message: "There is no network; show saved data, and say which actions are unavailable and why."}
  - {kind: success, message: What the user did worked; say what changed.}
copy_keys: [lib.common.error, lib.common.forbidden, lib.common.loading, lib.common.offline, lib.common.success, lib.form.domain.conflict, lib.form.domain.submitting, lib.form.domain.validating, lib.form.domain.validation_failed, scr_s007.confirm_secret, scr_s007.delete_account, scr_s007.keep_account, scr_s007.title]
acceptance:
  - GIVEN the user has typical data and a working network WHEN the user uses Delete account for its main purpose THEN the main purpose is achieved and the result is confirmed in plain words
  - GIVEN the user gave a value that breaks a rule on Delete account WHEN the user submits or leaves the input THEN the problem is named next to the input in plain words, the input is kept, and nothing is sent
  - GIVEN the server fails when Delete account asks for data or sends a change WHEN the failure is received THEN the user is told in plain words that something went wrong, nothing is lost, and a retry is offered
  - GIVEN the device has no network while the user is on Delete account WHEN the user opens Delete account or acts on it THEN saved data is shown as possibly out of date, actions that need the network say so, and queued actions are sent when the network returns
  - GIVEN the user sends the same thing twice, by repeating it or by a slow connection WHEN the second request arrives THEN the thing is created once, and the user sees one clear result
  - GIVEN the user's role may not see or do what Delete account offers WHEN the user reaches Delete account or tries the action THEN the user sees the forbidden state with the reason and a way to somewhere allowed, and no restricted data is shown
  - GIVEN the data on Delete account was changed elsewhere after it was loaded WHEN the user acts on the older data THEN the user is told it changed, sees the newer data, and nothing is overwritten without the user's choice
  - GIVEN the user's session ended while Delete account was open WHEN the user does something that needs the session THEN the user is asked to sign in again, comes back to Delete account afterwards, and keeps any unsent input

## closure
route SCR-001: sign_in /sign_in ()
route SCR-S003: settings /settings () guard: signed_in, role_has_access
copy (en): lib.common.error 'Something went wrong. Please try again.'; lib.common.forbidden 'You do not have access to this.'; lib.common.loading 'Loading, one moment.'; lib.common.offline 'You are offline. Showing what was saved last.'; lib.common.success 'Done.'; lib.form.domain.conflict 'This was changed by someone else. Review the latest version.'; lib.form.domain.submitting 'Sending.'; lib.form.domain.validating 'Checking.'; lib.form.domain.validation_failed 'Please check the highlighted input.'; scr_s007.confirm_secret 'Confirm secret'; scr_s007.delete_account 'Delete account'; scr_s007.keep_account 'Keep account'; scr_s007.title 'Delete account'
tokens: names only; none used by value
