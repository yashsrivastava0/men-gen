# verify-agent

Package `mde_verify_agent` (`agents/verify/`). Checks deep Spec packs: a structure critic at the Freeze, a canary per task card (the weakest configured consumer reads exactly the card's reads), a final critic; returns structured patches `{node, field, change, reason, class}` that Spec applies. It only checks and never writes into the graph or the pack. Imports `mde_spec_core`, `mde_core`, `mde_contracts` and the gateway's call types only; never the Spec agent. Spec loads it through `mde_spec_core.verify_port.load_verify_port` (entry point group `mde.spec.verify`) and gives it a `ModelCall` built around Spec's own runner, so the money cap holds (D-182). At most 2 LLM rounds; `critic_vendor` is stated honestly; a bigger route is not assumed to be a better critic.

Test: `uv run pytest agents/verify`.
