"""From judged Attempts to a recommendation, with the uncertainty attached.

Two rules govern everything here.

Tasks are the resampling unit, because both models ran the same Tasks and the
pairing cancels per-Task difficulty.

And `Unchecked` is not a failure. It means we could not verify the result, which
is a different statement from the model getting it wrong, so those Attempts are
excluded from the score and reported separately. A `Failed` Attempt *is* counted
as a failure: the agent erroring is the agent's problem.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Sequence

from crossbar.domain import Role
from crossbar.judging import Outcome
from crossbar.orchestrator import Attempt, RunResult
from crossbar.stats import (
    DEFAULT_ALPHA,
    bootstrap_ci,
    mcnemar_exact,
    paired_bootstrap,
)

N_RESAMPLES = 4000
HIGH_CONFIDENCE_TASKS = 150
MEDIUM_CONFIDENCE_TASKS = 50
UNCHECKED_WARNING_SHARE = 0.2


@dataclass(frozen=True)
class ModelSummary:
    model_id: str
    role: Role
    n_attempts: int
    n_graded: int
    n_unchecked: int
    n_failed: int
    pass_rate: float | None
    ci_low: float
    ci_high: float
    mean_score: float
    total_cost: float
    cost_per_success: float | None
    n_tasks: int

    @property
    def unchecked_share(self) -> float:
        return self.n_unchecked / self.n_attempts if self.n_attempts else 0.0


@dataclass(frozen=True)
class Comparison:
    candidate_id: str
    baseline_id: str
    delta: float
    ci_low: float
    ci_high: float
    p_value: float
    mcnemar_p: float
    n_tasks: int

    @property
    def significant(self) -> bool:
        return self.p_value < DEFAULT_ALPHA and not (self.ci_low <= 0.0 <= self.ci_high)

    @property
    def indistinguishable(self) -> bool:
        return not self.significant


@dataclass(frozen=True)
class Verdict:
    recommend_switch: bool
    savings_usd: float
    savings_pct: float
    confidence: str
    confidence_note: str
    caveats: tuple[str, ...] = ()


@dataclass(frozen=True)
class Analysis:
    models: tuple[ModelSummary, ...]
    comparison: Comparison | None
    verdict: Verdict
    unchecked_reasons: tuple[str, ...] = ()
    task_ids: tuple[str, ...] = ()
    is_single_model: bool = False
    is_product_comparison: bool = False
    """At least one model brought its own harness. The number still means
    something, but something other than a controlled model comparison."""
    """One model assessed on its own. There is nothing to compare against, so
    the report states how it did rather than whether to switch."""

    def model(self, model_id: str) -> ModelSummary:
        for summary in self.models:
            if summary.model_id == model_id:
                return summary
        raise KeyError(f"no results for model {model_id!r}")


def analyze(result: RunResult, n_resamples: int = N_RESAMPLES, seed: int = 0) -> Analysis:
    judged = [a for a in result.attempts if a.judged]
    task_ids = sorted({a.task_id for a in judged})
    by_model: dict[str, list[Attempt]] = {}
    for attempt in judged:
        by_model.setdefault(attempt.model_id, []).append(attempt)

    summaries = tuple(
        _summarise(model_id, attempts, task_ids, seed, n_resamples)
        for model_id, attempts in sorted(by_model.items())
    )

    candidate_id = result.roles.get(Role.CANDIDATE.value, "")
    baseline_id = result.roles.get(Role.BASELINE.value, "")
    comparison = _compare(
        by_model.get(candidate_id, []),
        by_model.get(baseline_id, []),
        candidate_id,
        baseline_id,
        seed,
        n_resamples,
    )

    verdict = _decide(summaries, comparison, result, candidate_id, baseline_id)
    return Analysis(
        models=summaries,
        comparison=comparison,
        verdict=verdict,
        unchecked_reasons=tuple(_unchecked_reasons(judged)),
        task_ids=tuple(task_ids),
        is_single_model=not baseline_id,
        is_product_comparison=bool(result.external_harness_models),
    )


# -- pieces ----------------------------------------------------------------


def _scored(attempts: Sequence[Attempt]) -> list[Attempt]:
    """Attempts that carry a usable outcome.

    Graded ones obviously. Failed ones too — the agent errored, which is a
    failure. Unchecked ones are excluded: we do not know how they went.
    """
    return [
        a
        for a in attempts
        if a.judgement is not None and a.judgement.outcome in (Outcome.GRADED, Outcome.FAILED)
    ]


def _outcome_value(attempt: Attempt) -> float:
    judgement = attempt.judgement
    if judgement is None or judgement.outcome is Outcome.FAILED:
        return 0.0
    return 1.0 if judgement.passed else 0.0


def _task_rates(attempts: Sequence[Attempt], task_ids: Sequence[str]) -> dict[str, float]:
    """One number per Task: the share of its scored Attempts that passed."""
    grouped: dict[str, list[float]] = {}
    for attempt in _scored(attempts):
        grouped.setdefault(attempt.task_id, []).append(_outcome_value(attempt))
    return {task: sum(v) / len(v) for task, v in grouped.items() if v}


def _summarise(
    model_id: str,
    attempts: Sequence[Attempt],
    task_ids: Sequence[str],
    seed: int,
    n_resamples: int,
) -> ModelSummary:
    scored = _scored(attempts)
    rates = _task_rates(attempts, task_ids)
    values = list(rates.values())

    pass_rate = sum(values) / len(values) if values else None
    ci_low, ci_high = (
        bootstrap_ci(values, n_resamples=n_resamples, seed=seed) if values else (0.0, 0.0)
    )
    successes = sum(1 for a in scored if _outcome_value(a) >= 1.0)
    total_cost = sum(a.cost_usd for a in attempts)

    return ModelSummary(
        model_id=model_id,
        role=attempts[0].role if attempts else Role.CANDIDATE,
        n_attempts=len(attempts),
        n_graded=sum(1 for a in attempts if a.judgement and a.judgement.outcome is Outcome.GRADED),
        n_unchecked=sum(
            1 for a in attempts if a.judgement and a.judgement.outcome is Outcome.UNCHECKED
        ),
        n_failed=sum(
            1 for a in attempts if a.judgement and a.judgement.outcome is Outcome.FAILED
        ),
        pass_rate=pass_rate,
        ci_low=ci_low,
        ci_high=ci_high,
        mean_score=(
            sum(a.judgement.score for a in scored if a.judgement) / len(scored) if scored else 0.0
        ),
        total_cost=total_cost,
        cost_per_success=total_cost / successes if successes else None,
        n_tasks=len(rates),
    )


def _compare(
    candidate: Sequence[Attempt],
    baseline: Sequence[Attempt],
    candidate_id: str,
    baseline_id: str,
    seed: int,
    n_resamples: int,
) -> Comparison | None:
    if not candidate or not baseline:
        return None
    candidate_rates = _task_rates(candidate, ())
    baseline_rates = _task_rates(baseline, ())

    # Pair only on Tasks both models actually had graded. An Unchecked result on
    # one side would otherwise be silently compared against a real score.
    shared = sorted(set(candidate_rates) & set(baseline_rates))
    if not shared:
        return None

    arm = [candidate_rates[t] for t in shared]
    base = [baseline_rates[t] for t in shared]
    diff = paired_bootstrap(arm, base, n_resamples=n_resamples, seed=seed)
    wins = sum(1 for a, b in zip(arm, base) if a > b)
    losses = sum(1 for a, b in zip(arm, base) if a < b)

    return Comparison(
        candidate_id=candidate_id,
        baseline_id=baseline_id,
        delta=diff.delta,
        ci_low=diff.ci_low,
        ci_high=diff.ci_high,
        p_value=diff.p_value,
        mcnemar_p=mcnemar_exact(wins, losses),
        n_tasks=len(shared),
    )


def _decide(
    summaries: Sequence[ModelSummary],
    comparison: Comparison | None,
    result: RunResult,
    candidate_id: str,
    baseline_id: str,
) -> Verdict:
    """Switch when the candidate is cheaper and cannot be shown to be worse."""
    by_id = {s.model_id: s for s in summaries}
    candidate = by_id.get(candidate_id)
    baseline = by_id.get(baseline_id)

    switch = False
    savings_usd = savings_pct = 0.0
    if candidate and baseline and comparison is not None:
        not_worse = comparison.indistinguishable or comparison.delta > 0
        cheaper = candidate.total_cost < baseline.total_cost
        switch = bool(not_worse and cheaper)
        if switch:
            savings_usd = baseline.total_cost - candidate.total_cost
            savings_pct = savings_usd / baseline.total_cost * 100 if baseline.total_cost else 0.0

    n_tasks = comparison.n_tasks if comparison else (candidate.n_tasks if candidate else 0)
    confidence, note = _confidence(n_tasks)
    return Verdict(
        recommend_switch=switch,
        savings_usd=savings_usd,
        savings_pct=savings_pct,
        confidence=confidence,
        confidence_note=note,
        caveats=tuple(_caveats(summaries, result)),
    )


def _confidence(n_tasks: int) -> tuple[str, str]:
    if n_tasks >= HIGH_CONFIDENCE_TASKS:
        return "high", f"{n_tasks} tasks is enough to resolve a few points of difference."
    if n_tasks >= MEDIUM_CONFIDENCE_TASKS:
        return (
            "medium",
            f"{n_tasks} tasks resolves a large gap but not a small one; about "
            f"{HIGH_CONFIDENCE_TASKS} would settle a 3-point difference.",
        )
    return (
        "low",
        f"{n_tasks} tasks is well below the ~{HIGH_CONFIDENCE_TASKS} needed to detect a "
        "3-point gap. Add tasks, or treat this as directional only.",
    )


def _caveats(summaries: Sequence[ModelSummary], result: RunResult) -> list[str]:
    caveats: list[str] = []

    if result.judge_is_baseline:
        caveats.append(
            "The baseline model is also the judge, so it graded its own attempts. "
            "Blinding is applied, but the conflict of interest is structural — "
            "connect an independent judge before relying on this for a decision."
        )

    for summary in summaries:
        if summary.unchecked_share > UNCHECKED_WARNING_SHARE:
            caveats.append(
                f"{summary.model_id}: {summary.unchecked_share * 100:.0f}% of attempts came "
                "back unchecked, so they are excluded from its score rather than counted as "
                "failures. Supply the evidence that was missing and re-judge before relying "
                "on this."
            )

    external = result.external_harness_models
    if external:
        caveats.append(
            f"{', '.join(external)} brought its own harness rather than running in "
            "crossbar's, so this is a product comparison, not a controlled model "
            "comparison. A shipped agent carries years of engineering around the "
            "model; if it wins, that is part of why."
        )

    unjudged = result.unjudged_tests
    if unjudged:
        caveats.append(
            "These tests ran but were not judged, so nothing here covers them: "
            + ", ".join(unjudged)
            + ". They can be judged without re-running."
        )
    return caveats


def _unchecked_reasons(attempts: Sequence[Attempt]) -> list[str]:
    reasons: list[str] = []
    for attempt in attempts:
        judgement = attempt.judgement
        if judgement is None or judgement.outcome is not Outcome.UNCHECKED:
            continue
        reason = judgement.unchecked_reason
        if reason and reason not in reasons:
            reasons.append(reason)
    return reasons
