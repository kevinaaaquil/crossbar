"""Running the Check Plan's evidence requests against the live Environment."""

from __future__ import annotations

from typing import Sequence

from crossbar.evidence.model import Evidence, EvidenceItem, EvidenceRequest


def capture(
    requests: Sequence[EvidenceRequest],
    connectors: Sequence,
    final_answer: str,
) -> Evidence:
    """Collect what the plan asked for, while the Environment is still up.

    Nothing here raises. An item that cannot be captured carries the reason,
    because "we could not check this, and here is why" is a useful answer for
    the user and a silent failure is not.
    """
    by_name = {c.name: c for c in connectors}
    items: list[EvidenceItem] = []

    for request in requests:
        connector = by_name.get(request.connector)
        if connector is None:
            items.append(
                EvidenceItem(
                    request=request,
                    error=f"no connector named {request.connector!r} is enabled for this task",
                )
            )
            continue
        try:
            result = connector.probe(request.probe, request.args)
        except Exception as exc:
            items.append(EvidenceItem(request=request, error=str(exc)))
            continue
        if result.is_error:
            items.append(
                EvidenceItem(request=request, error=f"the probe reported an error: {result.text}")
            )
            continue
        items.append(EvidenceItem(request=request, content=result.text))

    return Evidence(final_answer=final_answer, items=tuple(items))
