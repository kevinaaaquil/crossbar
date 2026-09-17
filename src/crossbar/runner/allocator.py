"""Sequential budget allocation: spend repeats only where they change the answer.

Running every cell the same number of times wastes most of the budget on cells
that were settled after three rollouts. Treat it as best-arm identification:
eliminate a cell as soon as its interval sits clear of the leader's, and keep
buying evidence only for the ones still in contention.
"""

from __future__ import annotations

from typing import Mapping, Sequence

from crossbar.stats import bootstrap_ci


def plan_additional_repeats(
    outcomes_by_cell: Mapping[str, Sequence[float]],
    max_repeats: int,
    seed: int = 0,
    n_resamples: int = 2000,
) -> list[str]:
    """Cells that still deserve more rollouts.

    A cell qualifies when its confidence interval overlaps the leader's - that
    is, the data so far cannot tell them apart - and it has not hit the ceiling.
    """
    if len(outcomes_by_cell) < 2:
        return []

    intervals: dict[str, tuple[float, float]] = {}
    means: dict[str, float] = {}
    for cell, outcomes in outcomes_by_cell.items():
        if not outcomes:
            continue
        means[cell] = sum(outcomes) / len(outcomes)
        intervals[cell] = bootstrap_ci(list(outcomes), n_resamples=n_resamples, seed=seed)
    if not means:
        return []

    leader = max(means, key=lambda c: (means[c], c))
    leader_low = intervals[leader][0]

    contested = []
    for cell, (_, high) in intervals.items():
        if cell == leader:
            continue
        if len(outcomes_by_cell[cell]) >= max_repeats:
            continue
        if high >= leader_low:  # intervals touch: the difference is not established
            contested.append(cell)
    return sorted(contested)
