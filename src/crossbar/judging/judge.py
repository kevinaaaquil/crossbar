"""The judge: plan once, then grade each Attempt against that plan.

Two rules shape everything here. Planning sees only the Task and its Golden,
never an Attempt, so criteria cannot be tailored to the output being graded.
And grading sees evidence with no identity attached, so the judge cannot favour
its own work — which matters most when the Baseline is doing the judging.
"""

from __future__ import annotations

import json
import re
from typing import Any, Mapping, Sequence

from crossbar.domain import Task
from crossbar.evidence import Evidence, EvidenceRequest
from crossbar.judging.model import (
    CheckItem,
    CheckOutcome,
    CheckPlan,
    CheckStatus,
    Judgement,
    Outcome,
    ProbeCatalogue,
)
from crossbar.providers import CompletionRequest, Message, ProviderError

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)

PLAN_SYSTEM = (
    "You design verification plans. You are given a task, a statement of what a "
    "correct result looks like, and a list of read-only probes that can observe "
    "the system afterwards.\n\n"
    "Break the correct-result statement into independent checks. For each check, "
    "say what must be true and which probes would show it. Use only the probes "
    "listed; if part of the statement cannot be verified with them, put it in "
    "'unsatisfiable' instead of inventing a probe.\n\n"
    "Reply with JSON only:\n"
    '{"items": [{"id": "short-slug", "criterion": "what must be true", '
    '"evidence": [{"label": "what this shows", "connector": "<connector>", '
    '"probe": "<probe name>", "args": {}}]}], "unsatisfiable": ["..."]}'
)

GRADE_SYSTEM = (
    "You verify whether a result meets a set of criteria, using only the "
    "observations provided. Decide each check independently and strictly on the "
    "evidence. If the evidence does not settle a check, say so rather than "
    "guessing.\n\n"
    "Reply with JSON only:\n"
    '{"checks": [{"check_id": "...", "status": "pass" | "fail" | "unchecked", '
    '"reason": "..."}], "reasoning": "how you decided"}'
)


class JudgingError(RuntimeError):
    """The judge could not be reached, or answered with something unusable."""


class Judge:
    def __init__(self, provider, max_tokens: int = 4096) -> None:
        self.provider = provider
        self.max_tokens = max_tokens
        self.plans_made = 0
        self.gradings_made = 0

    # -- planning ----------------------------------------------------------

    def make_plan(self, task: Task, catalogue: ProbeCatalogue) -> CheckPlan:
        """Derive the Check Plan for a Task. Once, before any Attempt."""
        prompt = (
            f"TASK\n{task.prompt}\n\n"
            f"CORRECT RESULT\n{task.golden}\n\n"
            f"AVAILABLE PROBES\n{catalogue.describe()}"
        )
        payload = self._ask(PLAN_SYSTEM, prompt)
        self.plans_made += 1

        items: list[CheckItem] = []
        unsatisfiable = [str(u) for u in payload.get("unsatisfiable") or []]
        available = catalogue.names()

        for raw in payload.get("items") or []:
            item = CheckItem.from_dict(raw)
            unknown = [e.probe for e in item.evidence if e.probe not in available]
            if unknown:
                # The judge asked for something nothing can supply. Better to say
                # so before the run than to collect Unchecked results afterwards.
                unsatisfiable.append(
                    f"{item.criterion or item.id}: no probe named "
                    f"{', '.join(repr(u) for u in unknown)} is available"
                )
                continue
            items.append(item)

        return CheckPlan(
            task_id=task.id, items=tuple(items), unsatisfiable=tuple(unsatisfiable)
        )

    # -- grading -----------------------------------------------------------

    def grade(self, plan: CheckPlan, evidence: Evidence) -> Judgement:
        """Grade one Attempt against the plan.

        Checks whose evidence was never captured are settled here, without
        spending a judge call: there is nothing for a model to read.
        """
        missing = _missing_by_request(evidence)
        undecidable: dict[str, str] = {}
        decidable: list[CheckItem] = []

        for item in plan.items:
            reasons = [
                missing[_key(request)]
                for request in item.evidence
                if _key(request) in missing
            ]
            if reasons or not item.evidence:
                undecidable[item.id] = "; ".join(reasons) or "no evidence was requested"
            else:
                decidable.append(item)

        if not decidable:
            return _all_unchecked(plan, undecidable)

        payload = self._ask(GRADE_SYSTEM, _grading_prompt(plan, decidable, evidence))
        self.gradings_made += 1

        graded = {
            str(c.get("check_id")): c for c in payload.get("checks") or [] if c.get("check_id")
        }
        outcomes: list[CheckOutcome] = []
        for item in plan.items:
            if item.id in undecidable:
                outcomes.append(
                    CheckOutcome(item.id, CheckStatus.UNCHECKED, undecidable[item.id])
                )
                continue
            raw = graded.get(item.id)
            if raw is None:
                # Silence is not a pass. A check the judge did not answer is
                # unchecked, so it cannot inflate a score by omission.
                outcomes.append(
                    CheckOutcome(
                        item.id, CheckStatus.UNCHECKED, "the judge did not return a verdict"
                    )
                )
                continue
            try:
                status = CheckStatus(str(raw.get("status", "unchecked")).lower())
            except ValueError:
                status = CheckStatus.UNCHECKED
            outcomes.append(CheckOutcome(item.id, status, str(raw.get("reason", ""))))

        return _assemble(tuple(outcomes), str(payload.get("reasoning", "")))

    # -- transport ---------------------------------------------------------

    def _ask(self, system: str, prompt: str) -> dict[str, Any]:
        try:
            response = self.provider.complete(
                CompletionRequest(
                    messages=[
                        Message(role="system", content=system),
                        Message(role="user", content=prompt),
                    ],
                    tools=[],
                    max_tokens=self.max_tokens,
                    temperature=0.0,
                )
            )
        except ProviderError as exc:
            raise JudgingError(f"the judge could not be reached: {exc}") from exc
        return _parse_json(response.text)


# -- helpers ---------------------------------------------------------------


def _grading_prompt(plan: CheckPlan, decidable: Sequence[CheckItem], evidence: Evidence) -> str:
    """Reference-based, one Attempt at a time.

    Never "which of these is better": pairwise comparison is more sensitive and
    more biased, and bias is the thing we are trying to keep out.
    """
    checks = "\n".join(f"- {item.id}: {item.criterion}" for item in decidable)
    observations = []
    for item in evidence.items:
        if item.available:
            observations.append(f"### {item.request.label}\n{item.content}")
    final = evidence.final_answer or "(no closing summary was given)"
    return (
        f"CHECKS\n{checks}\n\n"
        f"CLOSING SUMMARY OF THE WORK\n{final}\n\n"
        f"OBSERVATIONS\n" + ("\n\n".join(observations) or "(none)")
    )


def _missing_by_request(evidence: Evidence) -> dict[tuple, str]:
    return {
        _key(item.request): item.error or "evidence was not captured"
        for item in evidence.items
        if not item.available
    }


def _key(request: EvidenceRequest) -> tuple:
    return (request.connector, request.probe, json.dumps(dict(request.args), sort_keys=True))


def _all_unchecked(plan: CheckPlan, undecidable: Mapping[str, str]) -> Judgement:
    if plan.is_empty:
        reason = "; ".join(plan.unsatisfiable) or "the plan contains no checks"
        return Judgement(outcome=Outcome.UNCHECKED, error=reason)
    outcomes = tuple(
        CheckOutcome(item.id, CheckStatus.UNCHECKED, undecidable.get(item.id, "no evidence"))
        for item in plan.items
    )
    return _assemble(outcomes, "")


def _assemble(outcomes: tuple[CheckOutcome, ...], reasoning: str) -> Judgement:
    """A Golden is one statement. Verifying part of it does not establish that
    the task was done, so any unchecked check leaves the whole Attempt unscored.
    """
    if any(c.status is CheckStatus.UNCHECKED for c in outcomes) or not outcomes:
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


def _parse_json(text: str) -> dict[str, Any]:
    """Models wrap JSON in prose and fences. Dig it out, or say so clearly."""
    candidates = [text]
    fenced = _FENCE.search(text or "")
    if fenced:
        candidates.insert(0, fenced.group(1))
    start, end = (text or "").find("{"), (text or "").rfind("}")
    if start != -1 and end > start:
        candidates.append(text[start : end + 1])

    for candidate in candidates:
        try:
            parsed = json.loads(candidate.strip())
        except (json.JSONDecodeError, AttributeError):
            continue
        if isinstance(parsed, dict):
            return parsed
    raise JudgingError(f"the judge did not return JSON: {(text or '')[:200]!r}")
