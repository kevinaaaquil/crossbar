"""Statistics engine: bootstrap CIs, paired tests, multiplicity correction."""
import math

import pytest

from crossbar.stats import (
    bootstrap_ci,
    holm_bonferroni,
    mcnemar_exact,
    paired_bootstrap,
    variance_decomposition,
    wilson_interval,
)


class TestWilsonInterval:
    def test_half_successes_is_centred_near_half(self):
        lo, hi = wilson_interval(5, 10)
        assert lo < 0.5 < hi
        assert math.isclose((lo + hi) / 2, 0.5, abs_tol=1e-9)

    def test_known_value_for_eight_of_ten(self):
        lo, hi = wilson_interval(8, 10)
        assert math.isclose(lo, 0.4901, abs_tol=5e-4)
        assert math.isclose(hi, 0.9433, abs_tol=5e-4)

    def test_zero_trials_gives_full_interval(self):
        assert wilson_interval(0, 0) == (0.0, 1.0)

    def test_all_successes_upper_bound_is_one(self):
        lo, hi = wilson_interval(10, 10)
        assert hi == 1.0
        assert 0.6 < lo < 1.0

    def test_more_trials_narrows_the_interval(self):
        narrow = wilson_interval(80, 100)
        wide = wilson_interval(8, 10)
        assert (narrow[1] - narrow[0]) < (wide[1] - wide[0])


class TestBootstrapCI:
    def test_constant_sample_has_zero_width_interval(self):
        lo, hi = bootstrap_ci([0.7] * 20, n_resamples=200, seed=1)
        assert lo == pytest.approx(0.7)
        assert hi == pytest.approx(0.7)

    def test_interval_brackets_the_point_estimate(self):
        values = [0, 1, 1, 0, 1, 1, 1, 0, 1, 1]
        lo, hi = bootstrap_ci(values, n_resamples=2000, seed=7)
        assert lo < sum(values) / len(values) < hi

    def test_is_deterministic_for_a_fixed_seed(self):
        values = [0, 1, 1, 0, 1, 0, 1, 1, 0, 1]
        assert bootstrap_ci(values, n_resamples=500, seed=42) == bootstrap_ci(
            values, n_resamples=500, seed=42
        )

    def test_different_seeds_give_different_intervals(self):
        values = [0.13, 0.91, 0.42, 0.77, 0.05, 0.68, 0.34, 0.59, 0.22, 0.86]
        assert bootstrap_ci(values, n_resamples=500, seed=1) != bootstrap_ci(
            values, n_resamples=500, seed=2
        )

    def test_larger_sample_narrows_the_interval(self):
        small = bootstrap_ci([0, 1] * 5, n_resamples=2000, seed=3)
        large = bootstrap_ci([0, 1] * 100, n_resamples=2000, seed=3)
        assert (large[1] - large[0]) < (small[1] - small[0])

    def test_empty_sample_raises(self):
        with pytest.raises(ValueError):
            bootstrap_ci([], n_resamples=10, seed=1)


class TestPairedBootstrap:
    def test_identical_arms_have_zero_delta_and_insignificant_p(self):
        a = [1, 0, 1, 1, 0, 1, 0, 1]
        res = paired_bootstrap(a, list(a), n_resamples=1000, seed=5)
        assert res.delta == pytest.approx(0.0)
        assert res.p_value == pytest.approx(1.0)
        assert res.significant is False

    def test_uniformly_better_arm_is_significant(self):
        a = [1] * 30
        b = [0] * 30
        res = paired_bootstrap(a, b, n_resamples=2000, seed=5)
        assert res.delta == pytest.approx(1.0)
        assert res.p_value < 0.05
        assert res.significant is True

    def test_small_noisy_difference_is_not_significant(self):
        a = [1, 1, 0, 1, 0, 1, 0, 0, 1, 1]
        b = [1, 0, 0, 1, 0, 1, 0, 1, 1, 1]
        res = paired_bootstrap(a, b, n_resamples=3000, seed=11)
        assert res.significant is False
        assert res.ci_low < res.delta < res.ci_high

    def test_mismatched_lengths_raise(self):
        with pytest.raises(ValueError):
            paired_bootstrap([1, 0], [1], n_resamples=10, seed=1)

    def test_resampling_is_paired_not_independent(self):
        """Pairing must be preserved: a perfectly correlated pair has no spread."""
        a = [1, 0, 1, 0, 1, 0]
        res = paired_bootstrap(a, list(a), n_resamples=500, seed=2)
        assert res.ci_low == pytest.approx(0.0)
        assert res.ci_high == pytest.approx(0.0)


class TestMcNemar:
    def test_known_exact_p_value(self):
        # b=10 wins for A, c=2 wins for B -> two-sided binomial p
        assert mcnemar_exact(10, 2) == pytest.approx(0.03857, abs=1e-5)

    def test_symmetric_counts_give_p_of_one(self):
        assert mcnemar_exact(5, 5) == pytest.approx(1.0)

    def test_no_discordant_pairs_gives_p_of_one(self):
        assert mcnemar_exact(0, 0) == 1.0

    def test_p_value_is_symmetric_in_its_arguments(self):
        assert mcnemar_exact(9, 3) == pytest.approx(mcnemar_exact(3, 9))

    def test_result_is_a_probability(self):
        for b, c in [(1, 0), (20, 4), (3, 17), (0, 7)]:
            assert 0.0 <= mcnemar_exact(b, c) <= 1.0


class TestHolmBonferroni:
    def test_known_adjustment_preserves_input_order(self):
        assert holm_bonferroni([0.01, 0.04, 0.03]) == pytest.approx([0.03, 0.06, 0.06])

    def test_adjusted_values_are_monotone_in_rank(self):
        adj = holm_bonferroni([0.001, 0.002, 0.3, 0.4])
        ordered = sorted(adj)
        assert ordered == adj  # input already sorted ascending

    def test_values_are_clamped_to_one(self):
        assert holm_bonferroni([0.5, 0.6, 0.7]) == pytest.approx([1.0, 1.0, 1.0])

    def test_single_p_value_is_unchanged(self):
        assert holm_bonferroni([0.02]) == pytest.approx([0.02])

    def test_empty_input_returns_empty(self):
        assert holm_bonferroni([]) == []


class TestVarianceDecomposition:
    def test_identical_repeats_mean_all_variance_is_between_task(self):
        # each task always gives the same score, tasks differ
        scores = {"t1": [1.0, 1.0, 1.0], "t2": [0.0, 0.0, 0.0]}
        vd = variance_decomposition(scores)
        assert vd.within_task == pytest.approx(0.0)
        assert vd.between_task > 0.0
        assert vd.signal_share == pytest.approx(1.0)

    def test_identical_tasks_with_noisy_repeats_is_all_seed_noise(self):
        scores = {"t1": [1.0, 0.0, 1.0, 0.0], "t2": [1.0, 0.0, 1.0, 0.0]}
        vd = variance_decomposition(scores)
        assert vd.between_task == pytest.approx(0.0)
        assert vd.within_task > 0.0
        assert vd.noise_share == pytest.approx(1.0)

    def test_shares_sum_to_one(self):
        scores = {"t1": [1.0, 0.0, 1.0], "t2": [1.0, 1.0, 0.0], "t3": [0.0, 0.0, 0.0]}
        vd = variance_decomposition(scores)
        assert vd.signal_share + vd.noise_share == pytest.approx(1.0)

    def test_zero_total_variance_reports_no_signal(self):
        vd = variance_decomposition({"t1": [1.0, 1.0], "t2": [1.0, 1.0]})
        assert vd.signal_share == 0.0
        assert vd.noise_share == 0.0
