"""Shared grading mechanics.

These rules must hold for every judge — the model-backed one, the scripted one
used in tests, and anything added later. Keeping them here means a new judge
cannot quietly get them wrong.
"""

from __future__ import annotations

import json
from typing import Mapping, Sequence

from crossbar.evidence import Evidence, EvidenceRequest
from crossbar.judging.model import (
    CheckOutcome,
    CheckPlan,
    CheckStatus,
    Judgement,
    Outcome,
)


def request_key(request: EvidenceRequest) -> tuple:
    """Identity of an evidence request, so captured items can be matched to it."""
    return (request.connector, request.probe, json.dumps(dict(request.args), sort_keys=True))


def missing_evidence(evidence: Evidence) -> dict[tuple, str]:
    """Evidence requests that came back empty, mapped to why."""
    return {
        request_key(item.request): item.error or "evidence was not captured"
        for item in evidence.items
        if not item.available
    }


def undecidable_checks(plan: CheckPlan, evidence: Evidence) -> dict[str, str]:
    """Checks that cannot be decided at all, mapped to the reason.

    Settled without a judge call: there is nothing for a model to read.
    """
    missing = missing_evidence(evidence)
    undecidable: dict[str, str] = {}
    for item in plan.items:
        reasons = [missing[request_key(r)] for r in item.evidence if request_key(r) in missing]
        if reasons or not item.evidence:
            undecidable[item.id] = "; ".join(reasons) or "no evidence was requested"
    return undecidable


def assemble_judgement(outcomes: Sequence[CheckOutcome], reasoning: str) -> Judgement:
    """Turn per-check outcomes into a verdict.

    A Golden is one statement. Verifying part of it does not establish that the
    Task was done, so any unchecked check leaves the whole Attempt unscored.
    """
    outcomes = tuple(outcomes)
    if not outcomes or any(c.status is CheckStatus.UNCHECKED for c in outcomes):
        return Judgement(
            outcome=Outcome.UNCHECKED, checks=outcomes, score=0.0, reasoning=reasoning
        )
    passed = sum(1 for c in outcomes if c.status is CheckStatus.PASS)
    return Judgement(
        outcome=Outcome.GRADED,
        checks=outcomes,
        score=passed / len(outcomes),
        reasoning=reasoning,
    )


def all_unchecked(plan: CheckPlan, reasons: Mapping[str, str]) -> Judgement:
    """Nothing in this plan could be decided."""
    if plan.is_empty:
        reason = "; ".join(plan.unsatisfiable) or "the plan contains no checks"
        return Judgement(outcome=Outcome.UNCHECKED, error=reason)
    outcomes = tuple(
        CheckOutcome(item.id, CheckStatus.UNCHECKED, reasons.get(item.id, "no evidence"))
        for item in plan.items
    )
    return assemble_judgement(outcomes, "")
