---
id: SCR-S002   kind: screen   name: FPO form
app: APP-001   module: MOD-002   screen_type: form   surface: phone
roles: [ROL-004, ROL-008]
tiers: {purpose: default, regions: default, actions: default, states: default}
provenance: ["assumed:a role can create or edit 'FPO' and no screen does: one form with a create and edit mode is added", "assumed:entity ENT-005 has no form", "model:spec.scrn", "user:FTR-001", "user:FTR-004"]
stories: [US-S001, US-S004]
---
purpose: The user works with fpos on this screen.
route: {name: fpo_form, path: /fpo_form, params: [], guard: [signed_in, role_has_access]}
regions:   # alphabetical by key; the order carries no meaning
  - {key: details, shape: inputs, what: the details the user gives about the fpo, bind: [ENT-005.fpo_name, ENT-005.listing_eligible], importance: primary}
actions:
  - {key: cancel, importance: secondary, intent: cancel, what: leave without saving, effect: [stay]}
  - {key: save, importance: primary, intent: confirm, what: save the new fpo, precondition: the required details are given; the form is in create mode, effect: [call OP-S022], errors: [network, forbidden, validation_failed, conflict], flags: [idempotent]}
  - {key: save_changes, importance: secondary, intent: confirm, what: save the changes to the fpo, precondition: an existing record is being edited, effect: [call OP-S023], errors: [network, forbidden, validation_failed, conflict], flags: [idempotent]}
inputs:
  - {key: fpo_name, type: text, required: True, source: ENT-005.fpo_name}
  - {key: listing_eligible, type: toggle, required: True, source: ENT-005.listing_eligible}
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
copy_keys: [lib.common.error, lib.common.forbidden, lib.common.loading, lib.common.offline, lib.common.success, lib.form.domain.conflict, lib.form.domain.submitting, lib.form.domain.validating, lib.form.domain.validation_failed, scr_s002.cancel, scr_s002.details_label, scr_s002.fpo_name, scr_s002.listing_eligible, scr_s002.save, scr_s002.save_changes, scr_s002.title]
analytics: [fpo_confirmed]
accessibility: the result of save the new fpo is announced to assistive technology
acceptance:
  - GIVEN the user has typical data and a working network WHEN the user uses FPO form for its main purpose THEN the main purpose is achieved and the result is confirmed in plain words
  - GIVEN the user gave a value that breaks a rule on FPO form WHEN the user submits or leaves the input THEN the problem is named next to the input in plain words, the input is kept, and nothing is sent
  - GIVEN the server fails when FPO form asks for data or sends a change WHEN the failure is received THEN the user is told in plain words that something went wrong, nothing is lost, and a retry is offered
  - GIVEN the device has no network while the user is on FPO form WHEN the user opens FPO form or acts on it THEN saved data is shown as possibly out of date, actions that need the network say so, and queued actions are sent when the network returns
  - GIVEN the user sends the same thing twice, by repeating it or by a slow connection WHEN the second request arrives THEN the thing is created once, and the user sees one clear result
  - GIVEN a signed-in farmer groups (fpos) on FPO form WHEN they save the new fpo THEN the change is saved and the person stays here and sees the result
  - GIVEN a signed-in farmer groups (fpos) on FPO form and the request fails with validation failed WHEN they save the new fpo THEN they stay here, see the reason in plain words and can try again or leave
mock: {profiles: [broken, typical], hero: []}

## closure
ENT-005 FPO, fields used: fpo_name (text); listing_eligible (toggle)
OP-S022 create_fpo POST /fpos(fpo_name (text), listing_eligible (toggle)) -> (id (id), fpo_name (text), listing_eligible (toggle)); errors: network, forbidden, validation_failed, conflict idempotent
OP-S023 update_fpo PATCH /fpos/{id}(fpo_name (text), listing_eligible (toggle)) -> (id (id), fpo_name (text), listing_eligible (toggle)); errors: network, forbidden, validation_failed, conflict idempotent
error recovery: conflict -> retry; forbidden -> stay; network -> offline; validation_failed -> stay
access (this screen): ROL-004 -> FPO: view/create/edit/delete/manage; ROL-008 -> FPO: view/create/edit/delete/manage
copy (en): lib.common.error 'Something went wrong. Please try again.'; lib.common.forbidden 'You do not have access to this.'; lib.common.loading 'Loading, one moment.'; lib.common.offline 'You are offline. Showing what was saved last.'; lib.common.success 'Done.'; lib.form.domain.conflict 'This was changed by someone else. Review the latest version.'; lib.form.domain.submitting 'Sending.'; lib.form.domain.validating 'Checking.'; lib.form.domain.validation_failed 'Please check the highlighted input.'; scr_s002.cancel 'Cancel'; scr_s002.details_label 'Details'; scr_s002.fpo_name 'Fpo name'; scr_s002.listing_eligible 'Listing eligible'; scr_s002.save 'Save'; scr_s002.save_changes 'Save changes'; scr_s002.title 'FPO form'
tokens: names only; none used by value
