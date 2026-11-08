# Policy format

A policy is configuration, never a code path ([spec section 10](../product/context-engine-spec.md)). The same engines, schema, file and two gates run for everyone; a policy changes slot levels, research, approvers, limits and model routing. A company never needs special code, only a policy file.

Files live in `packages/content/src/mde_content/data/policies/<id>.yaml` and validate as `mde_contracts.policy.Policy`, plus one file-level key, `version`, that the loader removes before validation. The loader and resolver are `mde_content.policies`.

## Fields

| Field | Meaning | Default |
| --- | --- | --- |
| `version` | file metadata: the N in `id@N` used by `inherits` | 1 |
| `id` | policy id, equals the file name | required |
| `org` | owner (`platform`, an organization or a client) | `platform` |
| `description` | one or two sentences | empty |
| `mode` | `guided` or `enterprise` | `guided` |
| `inherits` | parent as `id@version` | none |
| `slot_policy` | slot id to `must`, `should` or `may`; overrides the slot catalog level for this policy | empty |
| `research.enabled`, `research.depth` | research after Gate 1; depth `light`, `standard` or `deep` | true, `light` |
| `research.web` | `allow_company_name_in_queries`, `allowed_domains` (when set, research uses only these), `blocked_domains` | true, none, none |
| `research.connectors` | internal source connectors (interface only in v1) | none |
| `gates.brief`, `gates.plan` | `approvers` (roles that must all approve the same hash), `full_file_view`, `reminder_hours` | requester only |
| `gates.post_handoff` | `heavy_if` (changes that need a full run: scope, auth, pay, integrations, design, data, roles) and `max_new_screens_light` | scope, auth, pay, integrations; 5 |
| `limits` | `max_screens`, `max_cards`, `max_turns`, `session_budget_usd`, `research_budget_usd` (decimal strings), `max_new_screens_light` | 50, 10, 30, "0.50", "0.30", 5 |
| `model_policy` | `providers` (allowed model providers) and `retention` | openai, none |
| `banned_libraries` | libraries the generated app must not use | none |
| `allowed_regions` | regions where data may live | none (any) |
| `role_precedence` | who wins when approver roles give conflicting answers | none |

Money in a policy (the budgets) is a decimal string, never a float.

## Inheritance

`inherits: "platform-default@1"` names a parent. The chain is at most **three levels**: platform defaults, an organization or client profile, an app. `resolve_policy(policy)` merges parent into child field by field:

- a field the child sets overrides the parent's value;
- dict fields (`slot_policy`, `research`, `research.web`, `gates`, `gates.plan`, `limits`, `model_policy`) merge key by key, recursively;
- lists (`approvers`, `connectors`, `allowed_domains`, `heavy_if`, `providers`, `role_precedence`) are replaced, not merged, so a child can also clear a parent's list with `[]`;
- a field the child leaves out keeps the parent's value.

A cycle, a chain longer than three levels, an unknown parent, a version that does not exist and a malformed reference raise `PolicyInheritanceError` with the chain in the message. The resolved policy keeps the child's `id`, `org` and its own direct `inherits`. `inheritance_chain(policy)` returns the refs from the policy up to the root (for example `["acme@1", "platform-default@1"]`); the Spec Builder copies them into the spec header's `inherits` list, so the generated `APP_SPEC.md` stays self-contained.

```python
from mde_content.policies import resolve_policy, load_policy, list_policies

policy = resolve_policy("enterprise-it-services-firm")   # packaged id
policy = resolve_policy(org_policy, {"acme-base@2": base_policy})  # a Policy object plus extra parents
```

`resolve_policy` takes a packaged id, or a `Policy` object (for example an organization profile loaded from the database), and an optional mapping of extra parents keyed `id@version`; the mapping is looked up before the packaged policies.

## The four profiles

| Policy | Mode | Inherits | What differs |
| --- | --- | --- | --- |
| `platform-default` | guided | none | The baseline: light research, one requester approval per gate, 50 screens, 10 cards, 30 turns, no retention at the model provider |
| `guided-default` | guided | `platform-default@1` | Guided mode for anyone: about seven cards (cap 10), light research after Gate 1, one tap per gate by the requester, at most 50 screens |
| `enterprise-it-services-firm` | enterprise | `platform-default@1` | The spec section 10 example: SSO and data classification must, design direction should; deep research that never puts the client name in a query, connectors docs, wiki and tickets; brief approved by the requester, plan by the client product owner and the delivery lead who both see the full file; 60 screens; approved model providers only |
| `regulated-bank` | enterprise | `platform-default@1` | Compliance, data classification, regions, SSO, payment model, integrations and approvers must-confirm; research limited to named public sources and internal libraries with blocked domains; data only in approved regions; one approved model provider; the plan needs the product owner, the security officer and the compliance officer, with the full file and a 24-hour reminder; more heavy changes (data, roles) need a full run |

The enterprise and bank files are examples with placeholder organizations (`example-services-firm`, `example-regional-bank`) and provider names (`approved-a`, `approved-b`). They show the shape; a real organization copies one and edits values.

## Adding a policy

1. Copy the closest profile to `data/policies/<id>.yaml`; set `id` (equal to the file name), `org`, `version` and `inherits`.
2. Write only what differs from the parent. Do not restate inherited values.
3. Slot ids in `slot_policy` must exist in the slot catalog (`mde_contracts.slots.SLOT_IDS`); gate approvers are roles, not people.
4. Run `uv run pytest packages/content/tests/test_policies.py`. It checks that the file validates, that its chain resolves within three levels, and that every `slot_policy` key is a real slot.
5. An organization policy that lives in the database instead of in the package resolves the same way, through the `available` mapping of `resolve_policy`.
