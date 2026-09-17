"""Bootstrap intervals, paired significance tests, multiplicity correction."""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Mapping, Sequence

DEFAULT_RESAMPLES = 10_000
DEFAULT_ALPHA = 0.05


@dataclass(frozen=True)
class DiffResult:
    """Outcome of comparing two arms on the same tasks."""

    delta: float
    ci_low: float
    ci_high: float
    p_value: float
    n: int

    @property
    def significant(self) -> bool:
        """True only when the interval excludes zero and the p-value clears 5%."""
        return self.p_value < DEFAULT_ALPHA and not (self.ci_low <= 0.0 <= self.ci_high)


@dataclass(frozen=True)
class VarianceDecomposition:
    """How much of the observed spread is real difficulty vs. run-to-run noise."""

    between_task: float
    within_task: float

    @property
    def total(self) -> float:
        return self.between_task + self.within_task

    @property
    def signal_share(self) -> float:
        return self.between_task / self.total if self.total > 0 else 0.0

    @property
    def noise_share(self) -> float:
        return self.within_task / self.total if self.total > 0 else 0.0


def wilson_interval(successes: int, trials: int, alpha: float = DEFAULT_ALPHA) -> tuple[float, float]:
    """Wilson score interval for a binomial proportion.

    Preferred over the normal approximation because it stays inside [0, 1] and
    behaves sanely at the extremes, which is where agent pass rates often sit.
    """
    if trials <= 0:
        return (0.0, 1.0)
    z = _z_for(alpha)
    p = successes / trials
    denom = 1 + z * z / trials
    centre = (p + z * z / (2 * trials)) / denom
    half = (z / denom) * math.sqrt(p * (1 - p) / trials + z * z / (4 * trials * trials))
    return (max(0.0, centre - half), min(1.0, centre + half))


def bootstrap_ci(
    values: Sequence[float],
    n_resamples: int = DEFAULT_RESAMPLES,
    seed: int = 0,
    alpha: float = DEFAULT_ALPHA,
) -> tuple[float, float]:
    """Percentile bootstrap interval for the mean, resampling tasks with replacement."""
    if not values:
        raise ValueError("bootstrap_ci needs at least one value")
    rng = random.Random(seed)
    n = len(values)
    means = []
    for _ in range(n_resamples):
        total = 0.0
        for _ in range(n):
            total += values[rng.randrange(n)]
        means.append(total / n)
    means.sort()
    return (_percentile(means, alpha / 2), _percentile(means, 1 - alpha / 2))


def paired_bootstrap(
    arm_a: Sequence[float],
    arm_b: Sequence[float],
    n_resamples: int = DEFAULT_RESAMPLES,
    seed: int = 0,
    alpha: float = DEFAULT_ALPHA,
) -> DiffResult:
    """Paired bootstrap of ``mean(a) - mean(b)`` over the shared task list.

    Resampling indices rather than each arm independently keeps the pairing,
    which is the whole point: both arms ran the same tasks, so the per-task
    difficulty cancels out and the interval tightens.
    """
    if len(arm_a) != len(arm_b):
        raise ValueError("paired_bootstrap needs arms of equal length")
    if not arm_a:
        raise ValueError("paired_bootstrap needs at least one pair")
    diffs = [a - b for a, b in zip(arm_a, arm_b)]
    observed = sum(diffs) / len(diffs)

    rng = random.Random(seed)
    n = len(diffs)
    resampled = []
    for _ in range(n_resamples):
        total = 0.0
        for _ in range(n):
            total += diffs[rng.randrange(n)]
        resampled.append(total / n)
    resampled.sort()
    ci_low = _percentile(resampled, alpha / 2)
    ci_high = _percentile(resampled, 1 - alpha / 2)

    # Two-sided p-value: how often a centred resample is at least as extreme.
    centred_extreme = sum(1 for m in resampled if abs(m - observed) >= abs(observed))
    p_value = min(1.0, (centred_extreme + 1) / (n_resamples + 1))
    if observed == 0.0:
        p_value = 1.0
    return DiffResult(observed, ci_low, ci_high, p_value, n)


def mcnemar_exact(b: int, c: int) -> float:
    """Exact (binomial) McNemar test on discordant pair counts.

    ``b`` is the number of tasks the first arm solved and the second did not;
    ``c`` is the reverse. Concordant pairs carry no information and are ignored.
    """
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / (2**n)
    return min(1.0, 2 * tail)


def holm_bonferroni(p_values: Sequence[float]) -> list[float]:
    """Holm step-down adjusted p-values, returned in the caller's input order.

    A matrix of cells means a pile of comparisons; uncorrected p-values from a
    40-cell sweep will show a "winner" that is pure multiplicity.
    """
    m = len(p_values)
    if m == 0:
        return []
    order = sorted(range(m), key=lambda i: p_values[i])
    adjusted = [0.0] * m
    running = 0.0
    for rank, idx in enumerate(order):
        scaled = min(1.0, p_values[idx] * (m - rank))
        running = max(running, scaled)
        adjusted[idx] = running
    return adjusted


def variance_decomposition(scores_by_task: Mapping[str, Sequence[float]]) -> VarianceDecomposition:
    """Split total score variance into between-task and within-task components.

    Between-task variance is real difficulty signal; within-task variance is the
    same agent behaving differently on the same task, i.e. seed noise.
    """
    groups = [list(v) for v in scores_by_task.values() if v]
    if not groups:
        return VarianceDecomposition(0.0, 0.0)
    all_values = [v for g in groups for v in g]
    grand_mean = sum(all_values) / len(all_values)

    within = 0.0
    between = 0.0
    for g in groups:
        mean_g = sum(g) / len(g)
        within += sum((v - mean_g) ** 2 for v in g)
        between += len(g) * (mean_g - grand_mean) ** 2
    n = len(all_values)
    return VarianceDecomposition(between_task=between / n, within_task=within / n)


def _percentile(sorted_values: Sequence[float], q: float) -> float:
    """Linear-interpolated percentile of an already sorted sequence."""
    if not sorted_values:
        raise ValueError("no values")
    if len(sorted_values) == 1:
        return sorted_values[0]
    pos = q * (len(sorted_values) - 1)
    low = math.floor(pos)
    high = math.ceil(pos)
    if low == high:
        return sorted_values[int(pos)]
    frac = pos - low
    return sorted_values[low] * (1 - frac) + sorted_values[high] * frac


def _z_for(alpha: float) -> float:
    """Two-sided normal critical value via the inverse error function."""
    return math.sqrt(2) * _erfinv(1 - alpha)


def _erfinv(x: float) -> float:
    """Inverse error function (Giles' rational approximation, ~1e-9 accurate)."""
    w = -math.log((1.0 - x) * (1.0 + x))
    if w < 5.0:
        w -= 2.5
        coeffs = [
            2.81022636e-08, 3.43273939e-07, -3.5233877e-06, -4.39150654e-06,
            0.00021858087, -0.00125372503, -0.00417768164, 0.246640727, 1.50140941,
        ]
    else:
        w = math.sqrt(w) - 3.0
        coeffs = [
            -0.000200214257, 0.000100950558, 0.00134934322, -0.00367342844,
            0.00573950773, -0.0076224613, 0.00943887047, 1.00167406, 2.83297682,
        ]
    p = coeffs[0]
    for c in coeffs[1:]:
        p = p * w + c
    return p * x
