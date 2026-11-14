# INDEX

## global
- global/assets.json
- global/behaviors.md
- global/blocks.md
- global/design-direction.md
- global/glossary.md
- global/integrations.md
- global/navigation.md
- global/needs.json
- global/roles-and-access.md
- global/routes.json
- global/rules.md
- global/scope-ledger.json
- global/tokens.dtcg.json

## contract
- contract/openapi.yaml and contract/components.yaml: the program's API (one document, per-app tags)
- contract/resources/<entity>.yaml: the operations of one entity; contract/schemas/<entity>.json: its schema
- contract/auth-matrix.json, contract/events.json

## data-model
- data-model/<module>-<n>.md: the entities owned by a module (each module file names its entities)

## apps
- APP-001 One app for everybody: apps/APP-001/app.md
  - MOD-001 Account: modules/MOD-001/module.md
  - MOD-002 Classes: modules/MOD-002/module.md
  - MOD-003 Class Packs: modules/MOD-003/module.md
  - MOD-004 Studio Schedule: modules/MOD-004/module.md
