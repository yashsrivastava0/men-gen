---
id: SCR-S003   kind: screen   name: Transport partner form
app: APP-004   module: MOD-004   screen_type: form   surface: phone
roles: [ROL-005]
tiers: {purpose: default, regions: default, actions: default, states: default}
provenance: ["assumed:a role can create or edit 'Transport partner' and no screen does: one form with a create and edit mode is added", "assumed:entity ENT-009 has no form", "model:spec.scrn", "user:FTR-007", "user:FTR-009"]
stories: [US-S006, US-S007]
---
purpose: The user works with transport partners on this screen.
route: {name: transport_partner_form, path: /transport_partner_form, params: [], guard: [signed_in, role_has_access]}
regions:   # alphabetical by key; the order carries no meaning
  - {key: details, shape: inputs, what: the details the user gives about the transport partner, bind: [ENT-009.name, ENT-009.contact], importance: primary}
actions:
  - {key: cancel, importance: secondary, intent: cancel, what: leave without saving, effect: [stay]}
  - {key: save, importance: primary, intent: confirm, what: save the new transport partner, precondition: the required details are given; the form is in create mode, effect: [call OP-S038], errors: [network, forbidden, validation_failed, conflict], flags: [idempotent]}
  - {key: save_changes, importance: secondary, intent: confirm, what: save the changes to the transport partner, precondition: an existing record is being edited, effect: [call OP-S039], errors: [network, forbidden, validation_failed, conflict], flags: [idempotent]}
inputs:
  - {key: contact, type: text, required: True, source: ENT-009.contact}
  - {key: name, type: text, required: True, source: ENT-009.name}
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
copy_keys: [lib.common.error, lib.common.forbidden, lib.common.loading, lib.common.offline, lib.common.success, lib.form.domain.conflict, lib.form.domain.submitting, lib.form.domain.validating, lib.form.domain.validation_failed, scr_s003.cancel, scr_s003.contact, scr_s003.details_label, scr_s003.name, scr_s003.save, scr_s003.save_changes, scr_s003.title]
analytics: [transport_partner_confirmed]
accessibility: the result of save the new transport partner is announced to assistive technology
acceptance:
  - GIVEN the user has typical data and a working network WHEN the user uses Transport partner form for its main purpose THEN the main purpose is achieved and the result is confirmed in plain words
  - GIVEN the user gave a value that breaks a rule on Transport partner form WHEN the user submits or leaves the input THEN the problem is named next to the input in plain words, the input is kept, and nothing is sent
  - GIVEN the server fails when Transport partner form asks for data or sends a change WHEN the failure is received THEN the user is told in plain words that something went wrong, nothing is lost, and a retry is offered
  - GIVEN the device has no network while the user is on Transport partner form WHEN the user opens Transport partner form or acts on it THEN saved data is shown as possibly out of date, actions that need the network say so, and queued actions are sent when the network returns
  - GIVEN the user sends the same thing twice, by repeating it or by a slow connection WHEN the second request arrives THEN the thing is created once, and the user sees one clear result
  - GIVEN a signed-in manager on Transport partner form WHEN they save the new transport partner THEN the change is saved and the person stays here and sees the result
  - GIVEN a signed-in manager on Transport partner form and the request fails with validation failed WHEN they save the new transport partner THEN they stay here, see the reason in plain words and can try again or leave
mock: {profiles: [broken, typical], hero: []}

## closure
ENT-009 Transport partner, fields used: name (text); contact (text)
OP-S038 create_transport_partner POST /transport_partners(name (text), contact (text)) -> (id (id), name (text), contact (text)); errors: network, forbidden, validation_failed, conflict idempotent
OP-S039 update_transport_partner PATCH /transport_partners/{id}(name (text), contact (text)) -> (id (id), name (text), contact (text)); errors: network, forbidden, validation_failed, conflict idempotent
error recovery: conflict -> retry; forbidden -> stay; network -> offline; validation_failed -> stay
access (this screen): ROL-005 -> Transport partner: view/create/edit/delete/manage
copy (en): lib.common.error 'Something went wrong. Please try again.'; lib.common.forbidden 'You do not have access to this.'; lib.common.loading 'Loading, one moment.'; lib.common.offline 'You are offline. Showing what was saved last.'; lib.common.success 'Done.'; lib.form.domain.conflict 'This was changed by someone else. Review the latest version.'; lib.form.domain.submitting 'Sending.'; lib.form.domain.validating 'Checking.'; lib.form.domain.validation_failed 'Please check the highlighted input.'; scr_s003.cancel 'Cancel'; scr_s003.contact 'Contact'; scr_s003.details_label 'Details'; scr_s003.name 'Name'; scr_s003.save 'Save'; scr_s003.save_changes 'Save changes'; scr_s003.title 'Transport partner form'
tokens: names only; none used by value
