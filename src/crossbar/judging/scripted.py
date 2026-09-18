"""A judge that does not call a model. For tests and deterministic runs."""

from __future__ import annotations

from typing import Callable, Mapping

from crossbar.domain import Task
from crossbar.evidence import Evidence
from crossbar.judging.grading import (
    all_unchecked,
    assemble_judgement,
    undecidable_checks,
)
from crossbar.judging.model import (
    CheckOutcome,
    CheckPlan,
    CheckStatus,
    Judgement,
    ProbeCatalogue,
)


class ScriptedJudge:
    """Returns a fixed plan, and grades by a supplied rule.

    It deliberately keeps the real judge's handling of missing evidence, so a
    test using it still exercises the Unchecked path rather than pretending
    everything was observable.
    """

    def __init__(
        self,
        plan: CheckPlan,
        grader: Callable[[CheckPlan, Evidence], Mapping[str, str]] | None = None,
        reasoning: str = "scripted",
    ) -> None:
        self.plan = plan
        self.grader = grader or (lambda p, e: {})
        self.reasoning = reasoning
        self.plans_made = 0
        self.gradings_made = 0

    def make_plan(self, task: Task, catalogue: ProbeCatalogue) -> CheckPlan:
        self.plans_made += 1
        return self.plan

    def grade(self, plan: CheckPlan, evidence: Evidence) -> Judgement:
        self.gradings_made += 1
        verdicts = dict(self.grader(plan, evidence))
        undecidable = undecidable_checks(plan, evidence)

        if len(undecidable) == len(plan.items):
            return all_unchecked(plan, undecidable)

        outcomes = []
        for item in plan.items:
            if item.id in undecidable:
                outcomes.append(
                    CheckOutcome(item.id, CheckStatus.UNCHECKED, undecidable[item.id])
                )
                continue
            raw = verdicts.get(item.id, "unchecked")
            outcomes.append(CheckOutcome(item.id, CheckStatus(raw), "scripted"))
        return assemble_judgement(tuple(outcomes), self.reasoning)
