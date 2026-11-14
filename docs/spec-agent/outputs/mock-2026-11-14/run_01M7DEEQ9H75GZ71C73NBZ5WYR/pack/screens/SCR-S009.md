---
id: SCR-S009   kind: screen   name: Permission explanation
app: APP-001   module: MOD-001   screen_type: permission   surface: phone
roles: [ROL-001, ROL-002, ROL-003, ROL-004, ROL-005, ROL-006, ROL-007, ROL-008, ROL-009]
tiers: {purpose: default, regions: default, actions: default, states: default}
provenance: ["library:system_screens.permission_rationale"]
---
purpose: The user is told why the app needs one device ability before the system asks.
route: {name: permission_explanation, path: /permission_explanation, params: [], guard: [signed_in, role_has_access]}
regions:   # alphabetical by key; the order carries no meaning
  - {key: reason, shape: text, what: why the ability is needed and what the app does with it, importance: primary}
actions:
  - {key: allow, importance: primary, intent: grant_permission, what: go on to the system's own question, effect: [stay]}
  - {key: continue_without, importance: secondary, intent: cancel, what: continue without the ability, effect: [stay]}
states:   # what each must communicate, not how
  - {kind: "domain:blocked", message: The system will not ask again; the only way is the system settings.}
  - {kind: "domain:denied", message: The user refused this time; the app continues without the ability and may ask again later.}
  - {kind: "domain:granted", message: The ability was granted; continue to what needed it.}
  - {kind: error, message: Something failed; say what happened in plain words and offer a retry.}
copy_keys: [lib.common.error, lib.permission.domain.blocked, lib.permission.domain.denied, lib.permission.domain.granted, scr_s009.allow, scr_s009.continue_without, scr_s009.title]
acceptance:
  - GIVEN the user has typical data and a working network WHEN the user uses Permission explanation for its main purpose THEN the main purpose is achieved and the result is confirmed in plain words
  - GIVEN Permission explanation needs a device ability and the user refuses it WHEN the refusal is received THEN the rest of Permission explanation still works, the missing ability is explained once, and the way to the system settings is offered
  - GIVEN the device has no network while the user is on Permission explanation WHEN the user opens Permission explanation or acts on it THEN saved data is shown as possibly out of date, actions that need the network say so, and queued actions are sent when the network returns
  - GIVEN the user changes the app language while Permission explanation is open WHEN the change is applied THEN all text on Permission explanation follows the new language, numbers and dates follow its formats, and the user's place and input are kept
  - GIVEN the user's session ended while Permission explanation was open WHEN the user does something that needs the session THEN the user is asked to sign in again, comes back to Permission explanation afterwards, and keeps any unsent input

## closure
copy (en): lib.common.error 'Something went wrong. Please try again.'; lib.permission.domain.blocked 'This is turned off in your device settings.'; lib.permission.domain.denied 'No problem. You can turn this on later in settings.'; lib.permission.domain.granted 'Thank you. You can continue.'; scr_s009.allow 'Allow'; scr_s009.continue_without 'Continue without'; scr_s009.title 'Permission explanation'
tokens: names only; none used by value
