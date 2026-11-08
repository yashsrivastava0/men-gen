"""Validator 6, ``flow_endpoints``: every flow starts and ends on a real screen."""

from __future__ import annotations

from mde_contracts.calls import ValidatorFinding
from mde_contracts.spec import AppSpec
from mde_spec_builder.validators._common import duplicates, finding

NAME = "flow_endpoints"


def flow_endpoints(spec: AppSpec) -> list[ValidatorFinding]:
    """Validator 6: a flow's start, end and screens exist; start and end belong to it."""
    out: list[ValidatorFinding] = []
    screens = {s.id for s in spec.screens}
    flow_ids = {f.id for f in spec.flows}

    for flow in spec.flows:
        if not flow.screens:
            out.append(finding(NAME, f"Flow {flow.id} ({flow.name}) lists no screens", flow.id))
        for screen_id in flow.screens:
            if screen_id not in screens:
                out.append(
                    finding(
                        NAME,
                        f"Flow {flow.id} lists screen {screen_id}, which does not exist",
                        flow.id,
                        screen_id,
                    )
                )
        for label, end_id in (("starts", flow.start), ("ends", flow.end)):
            if end_id not in screens:
                out.append(
                    finding(
                        NAME,
                        f"Flow {flow.id} {label} on {end_id}, which does not exist",
                        flow.id,
                        end_id,
                    )
                )
            elif end_id not in flow.screens:
                out.append(
                    finding(
                        NAME,
                        f"Flow {flow.id} {label} on {end_id}, which is not one of its screens",
                        flow.id,
                        end_id,
                    )
                )
        if flow.screens and flow.start in flow.screens and flow.start != flow.screens[0]:
            out.append(
                finding(
                    NAME,
                    f"Flow {flow.id} starts on {flow.start} but its first screen is {flow.screens[0]}",
                    flow.id,
                    severity="warn",
                )
            )
        if flow.screens and flow.end in flow.screens and flow.end != flow.screens[-1]:
            out.append(
                finding(
                    NAME,
                    f"Flow {flow.id} ends on {flow.end} but its last screen is {flow.screens[-1]}",
                    flow.id,
                    severity="warn",
                )
            )
        for dup in duplicates(flow.screens):
            out.append(
                finding(
                    NAME,
                    f"Flow {flow.id} lists {dup} more than once",
                    flow.id,
                    dup,
                    severity="warn",
                )
            )

    for screen in spec.screens:
        if screen.flow is not None and screen.flow not in flow_ids:
            out.append(
                finding(
                    NAME,
                    f"Screen {screen.id} belongs to flow {screen.flow}, which does not exist",
                    screen.id,
                )
            )

    primaries = [f.id for f in spec.flows if f.primary]
    if spec.flows and not primaries:
        out.append(
            finding(
                NAME,
                "No flow is marked primary; the vertical slice will use the flow with most screens",
                severity="warn",
            )
        )
    elif len(primaries) > 1:
        out.append(
            finding(
                NAME,
                f"Flows {', '.join(primaries)} are all marked primary; the first one is used",
                *primaries,
                severity="warn",
            )
        )
    return out
