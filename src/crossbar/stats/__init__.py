"""Statistics engine.

Agent runs are stochastic. Every number this package produces carries an
interval, because a point estimate from a handful of rollouts is mostly noise.
Everything is pure Python and seeded, so results are reproducible.
"""

from crossbar.stats.core import (
    DEFAULT_ALPHA,
    DEFAULT_RESAMPLES,
    DiffResult,
    VarianceDecomposition,
    bootstrap_ci,
    holm_bonferroni,
    mcnemar_exact,
    paired_bootstrap,
    variance_decomposition,
    wilson_interval,
)

__all__ = [
    "DEFAULT_ALPHA",
    "DEFAULT_RESAMPLES",
    "DiffResult",
    "VarianceDecomposition",
    "bootstrap_ci",
    "holm_bonferroni",
    "mcnemar_exact",
    "paired_bootstrap",
    "variance_decomposition",
    "wilson_interval",
]
