"""From rollouts to a recommendation, with the uncertainty attached.

Two rules govern everything here. Tasks are the resampling unit, because the
same tasks ran in every cell and pairing is what makes small task packs usable.
And a difference whose interval covers zero is not a difference, however large
the point estimate looks - so the verdict is allowed to say "you cannot tell
these apart, take the cheaper one".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Sequence

from crossbar.runner.results import CellResult, SweepResult
from crossbar.stats import (
    DEFAULT_ALPHA,
    bootstrap_ci,
    holm_bonferroni,
    mcnemar_exact,
    paired_bootstrap,
    variance_decomposition,
)

N_RESAMPLES = 4000
"""Resamples per interval. The literature standard is 10k; 4k keeps an
interactive sweep responsive and moves the third decimal place at most."""

HIGH_CONFIDENCE_TASKS = 150
"""Roughly the task count needed to resolve a 3-point gap at 95%."""

MEDIUM_CONFIDENCE_TASKS = 50


@dataclass(frozen=True)
class CellSummary:
    agent_id: str
    model_id: str
    harness_id: str
    n: int
    n_tasks: int
    pass_rate: float
    ci_low: float
    ci_high: float
    mean_score: float
    total_cost: float
    mean_cost: float
    cost_per_success: float | None
    mean_wall_time: float
    noise_share: float
    failure_tally: Mapping[str, int] = field(default_factory=dict)

    @property
    def ci_width(self) -> float:
        return self.ci_high - self.ci_low


@dataclass(frozen=True)
class Comparison:
    """One cell measured against the baseline on the same tasks."""

    agent_id: str
    baseline_id: str
    delta: float
    ci_low: float
    ci_high: float
    p_value: float
    p_adjusted: float
    mcnemar_p: float
    n_tasks: int

    @property
    def significant(self) -> bool:
        """Significant only after the multiplicity correction, and only when the
        interval clears zero."""
        return self.p_adjusted < DEFAULT_ALPHA and not (self.ci_low <= 0.0 <= self.ci_high)

    @property
    def indistinguishable(self) -> bool:
        return not self.significant


@dataclass(frozen=True)
class Verdict:
    baseline: CellSummary
    winner: CellSummary
    comparison: Comparison | None
    recommend_switch: bool
    savings_usd: float
    savings_pct: float
    confidence: str
    confidence_note: str
    watch_outs: tuple[str, ...] = ()


@dataclass(frozen=True)
class Analysis:
    cells: tuple[CellSummary, ...]
    comparisons: tuple[Comparison, ...]
    verdict: Verdict
    task_ids: tuple[str, ...]
    uninformative_tasks: tuple[str, ...]
    seed: int
    pack_name: str = ""

    def cell(self, agent_id: str) -> CellSummary:
        for cell in self.cells:
            if cell.agent_id == agent_id:
                return cell
        raise KeyError(f"no cell {agent_id!r}")

    def comparison(self, agent_id: str) -> Comparison:
        for comparison in self.comparisons:
            if comparison.agent_id == agent_id:
                return comparison
        raise KeyError(f"no comparison for {agent_id!r}")


def analyze(sweep: SweepResult, n_resamples: int = N_RESAMPLES) -> Analysis:
    cells = sweep.cells()
    if not cells:
        raise ValueError("sweep contains no results")
    task_ids = list(sweep.task_ids) or sorted({r.task_id for r in sweep.records})

    baseline_cell = next((c for c in cells if c.agent_id == sweep.baseline), cells[0])
    summaries = {c.agent_id: _summarise(c, task_ids, sweep.seed, n_resamples) for c in cells}

    raw = [
        _compare(cell, baseline_cell, task_ids, sweep.seed, n_resamples)
        for cell in cells
        if cell.agent_id != baseline_cell.agent_id
    ]
    adjusted = holm_bonferroni([c[1] for c in raw])
    comparisons = tuple(
        Comparison(
            agent_id=partial.agent_id,
            baseline_id=partial.baseline_id,
            delta=partial.delta,
            ci_low=partial.ci_low,
            ci_high=partial.ci_high,
            p_value=partial.p_value,
            p_adjusted=p_adj,
            mcnemar_p=partial.mcnemar_p,
            n_tasks=partial.n_tasks,
        )
        for (partial, _), p_adj in zip(raw, adjusted)
    )

    ranked = tuple(
        sorted(summaries.values(), key=lambda s: (-s.pass_rate, s.mean_cost, s.agent_id))
    )
    verdict = _decide(summaries[baseline_cell.agent_id], ranked, comparisons, len(task_ids))

    return Analysis(
        cells=ranked,
        comparisons=comparisons,
        verdict=verdict,
        task_ids=tuple(task_ids),
        uninformative_tasks=tuple(_uninformative(cells, task_ids)),
        seed=sweep.seed,
        pack_name=sweep.pack_name,
    )


# -- pieces ----------------------------------------------------------------


def _summarise(cell: CellResult, task_ids: Sequence[str], seed: int, n_resamples: int) -> CellSummary:
    per_task = _task_pass_rates(cell, task_ids)
    ci_low, ci_high = bootstrap_ci(per_task, n_resamples=n_resamples, seed=seed)
    record = cell.records[0]
    return CellSummary(
        agent_id=cell.agent_id,
        model_id=record.model_id,
        harness_id=record.harness_id,
        n=cell.n,
        n_tasks=len(task_ids),
        pass_rate=cell.pass_rate,
        ci_low=ci_low,
        ci_high=ci_high,
        mean_score=cell.mean_score,
        total_cost=cell.total_cost,
        mean_cost=cell.mean_cost,
        cost_per_success=cell.cost_per_success,
        mean_wall_time=cell.mean_wall_time,
        noise_share=variance_decomposition(cell.scores_by_task()).noise_share,
        failure_tally=cell.failure_tally(),
    )


def _compare(
    cell: CellResult,
    baseline: CellResult,
    task_ids: Sequence[str],
    seed: int,
    n_resamples: int,
) -> tuple[Comparison, float]:
    arm = _task_pass_rates(cell, task_ids)
    base = _task_pass_rates(baseline, task_ids)
    diff = paired_bootstrap(arm, base, n_resamples=n_resamples, seed=seed)
    wins = sum(1 for a, b in zip(arm, base) if a > b)
    losses = sum(1 for a, b in zip(arm, base) if a < b)
    partial = Comparison(
        agent_id=cell.agent_id,
        baseline_id=baseline.agent_id,
        delta=diff.delta,
        ci_low=diff.ci_low,
        ci_high=diff.ci_high,
        p_value=diff.p_value,
        p_adjusted=diff.p_value,
        mcnemar_p=mcnemar_exact(wins, losses),
        n_tasks=len(task_ids),
    )
    return partial, diff.p_value


def _task_pass_rates(cell: CellResult, task_ids: Sequence[str]) -> list[float]:
    """One number per task: the share of that task's repeats which passed."""
    grouped = cell.outcomes_by_task()
    return [
        (sum(grouped[t]) / len(grouped[t])) if grouped.get(t) else 0.0 for t in task_ids
    ]


def _decide(
    baseline: CellSummary,
    ranked: Sequence[CellSummary],
    comparisons: Sequence[Comparison],
    n_tasks: int,
) -> Verdict:
    """Cheapest cell you cannot prove is worse than the baseline.

    That is the migration question, not "which cell scored highest": a cell that
    is one point behind and ninety percent cheaper is the answer most of the time.
    """
    by_id = {c.agent_id: c for c in comparisons}
    eligible = [baseline]
    for cell in ranked:
        if cell.agent_id == baseline.agent_id:
            continue
        comparison = by_id.get(cell.agent_id)
        if comparison is None:
            continue
        not_worse = comparison.indistinguishable or comparison.delta > 0
        if not_worse:
            eligible.append(cell)

    winner = min(eligible, key=lambda c: (c.mean_cost, -c.pass_rate, c.agent_id))
    switch = winner.agent_id != baseline.agent_id

    savings_usd = max(0.0, baseline.total_cost - winner.total_cost) if switch else 0.0
    savings_pct = (savings_usd / baseline.total_cost * 100) if switch and baseline.total_cost else 0.0

    # The card reports on whichever challenger the reader is weighing: the
    # winner when we recommend switching, otherwise the best rival on offer -
    # "we tested the alternative and it lost" is a finding worth printing.
    subject = winner
    if not switch:
        subject = next((c for c in ranked if c.agent_id != baseline.agent_id), winner)
    comparison = by_id.get(subject.agent_id)

    confidence, note = _confidence(n_tasks, winner)
    return Verdict(
        baseline=baseline,
        winner=winner,
        comparison=comparison,
        recommend_switch=switch,
        savings_usd=savings_usd,
        savings_pct=savings_pct,
        confidence=confidence,
        confidence_note=note,
        watch_outs=tuple(_watch_outs(baseline, subject)),
    )


def _confidence(n_tasks: int, winner: CellSummary) -> tuple[str, str]:
    if n_tasks >= HIGH_CONFIDENCE_TASKS and winner.ci_width <= 0.15:
        return "high", f"{n_tasks} tasks is enough to resolve a few points of difference."
    if n_tasks >= MEDIUM_CONFIDENCE_TASKS:
        return (
            "medium",
            f"{n_tasks} tasks resolves a large gap but not a small one; "
            f"{HIGH_CONFIDENCE_TASKS} would settle a 3-point difference.",
        )
    return (
        "low",
        f"{n_tasks} tasks is well below the ~{HIGH_CONFIDENCE_TASKS} needed to detect a "
        "3-point gap. Add tasks, or treat this as directional only.",
    )


def _watch_outs(baseline: CellSummary, subject: CellSummary) -> list[str]:
    """Failure modes the challenger hits materially more often than the baseline."""
    if subject.agent_id == baseline.agent_id:
        return []
    notes = []
    for mode, count in sorted(subject.failure_tally.items()):
        if count < 2:
            continue
        base_count = baseline.failure_tally.get(mode, 0)
        ratio = count / base_count if base_count else float("inf")
        if ratio >= 2.0:
            label = mode.replace("_", "/")
            times = "never seen on the baseline" if not base_count else f"{ratio:.1f}x the baseline rate"
            notes.append(
                f"{label} failures: {count} of {subject.n} rollouts, {times}. "
                "Tighten the harness here before switching."
            )
    return notes


def _uninformative(cells: Sequence[CellResult], task_ids: Sequence[str]) -> list[str]:
    """Tasks every cell passes, or every cell fails: they cost money and say nothing."""
    dead = []
    for index, task_id in enumerate(task_ids):
        rates = [_task_pass_rates(cell, task_ids)[index] for cell in cells]
        if all(r == 1.0 for r in rates) or all(r == 0.0 for r in rates):
            dead.append(task_id)
    return dead
