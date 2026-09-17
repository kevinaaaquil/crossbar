"""Analysis: turning a pile of rollouts into a defensible recommendation."""
import pytest

from crossbar.analysis import analyze
from crossbar.providers import Usage
from crossbar.runner import RunRecord, SweepResult
from crossbar.scoring import FailureMode, TaskScore


def record(agent, task, repeat, passed, cost=1.0, failure=None, model="m", harness="h"):
    score = TaskScore(
        security=1.0,
        completion=1.0 if passed else 0.0,
        process=1.0,
        failure_mode=failure if not passed else None,
    )
    return RunRecord(
        agent_id=agent,
        model_id=model,
        harness_id=harness,
        task_id=task,
        repeat=repeat,
        score=score,
        usage=Usage(1000, 500),
        cost_usd=cost,
        wall_time_s=1.0,
    )


def sweep(pattern, tasks=None, costs=None, baseline="baseline", repeats=2):
    """pattern: {agent_id: [pass?] per task} replicated over repeats."""
    tasks = tasks or [f"t{i}" for i in range(len(next(iter(pattern.values()))))]
    costs = costs or {}
    records = []
    for agent, passes in pattern.items():
        for task, passed in zip(tasks, passes):
            for repeat in range(repeats):
                records.append(
                    record(agent, task, repeat, passed, cost=costs.get(agent, 1.0))
                )
    return SweepResult(
        records=tuple(records),
        agent_ids=tuple(pattern),
        task_ids=tuple(tasks),
        baseline=baseline,
        seed=7,
        repeats=repeats,
    )


class TestCellSummaries:
    def test_every_cell_is_summarised(self):
        result = analyze(sweep({"baseline": [1, 1, 0], "rival": [1, 0, 0]}))
        assert {c.agent_id for c in result.cells} == {"baseline", "rival"}

    def test_pass_rate_and_interval_are_reported(self):
        result = analyze(sweep({"baseline": [1, 1, 0], "rival": [1, 0, 0]}))
        baseline = result.cell("baseline")
        assert baseline.pass_rate == pytest.approx(2 / 3)
        assert baseline.ci_low < baseline.pass_rate < baseline.ci_high

    def test_cells_are_ranked_best_first(self):
        result = analyze(sweep({"weak": [0, 0, 0], "strong": [1, 1, 1]}, baseline="weak"))
        assert result.cells[0].agent_id == "strong"

    def test_cost_per_success_is_reported(self):
        result = analyze(sweep({"baseline": [1, 1, 0], "rival": [1, 0, 0]}, costs={"baseline": 2.0}))
        baseline = result.cell("baseline")
        assert baseline.cost_per_success == pytest.approx(baseline.total_cost / 4)

    def test_a_cell_that_never_passes_has_no_cost_per_success(self):
        result = analyze(sweep({"baseline": [1, 1], "dud": [0, 0]}))
        assert result.cell("dud").cost_per_success is None


class TestComparisons:
    def test_each_non_baseline_cell_is_compared_with_the_baseline(self):
        result = analyze(sweep({"baseline": [1, 1, 0], "a": [1, 0, 0], "b": [0, 0, 0]}))
        assert {c.agent_id for c in result.comparisons} == {"a", "b"}

    def test_the_delta_is_measured_against_the_baseline(self):
        result = analyze(sweep({"baseline": [1, 1, 1, 1], "rival": [1, 1, 0, 0]}))
        assert result.comparison("rival").delta == pytest.approx(-0.5)

    def test_a_large_consistent_gap_is_significant(self):
        pattern = {"baseline": [1] * 20, "rival": [0] * 20}
        assert analyze(sweep(pattern)).comparison("rival").significant is True

    def test_a_small_noisy_gap_is_not_significant(self):
        baseline = [1, 0, 1, 1, 0, 1, 0, 1, 1, 0]
        rival = [1, 0, 1, 0, 0, 1, 0, 1, 1, 0]
        result = analyze(sweep({"baseline": baseline, "rival": rival}))
        assert result.comparison("rival").significant is False

    def test_p_values_are_corrected_for_multiple_comparisons(self):
        pattern = {
            "baseline": [1, 1, 1, 1, 1, 1, 1, 1],
            "a": [1, 1, 1, 1, 0, 0, 0, 0],
            "b": [1, 1, 1, 0, 0, 0, 0, 0],
            "c": [1, 1, 0, 0, 0, 0, 0, 0],
        }
        result = analyze(sweep(pattern))
        for comparison in result.comparisons:
            assert comparison.p_adjusted >= comparison.p_value

    def test_mcnemar_is_reported_as_a_cross_check(self):
        result = analyze(sweep({"baseline": [1, 1, 1, 0], "rival": [0, 0, 1, 0]}))
        assert 0.0 <= result.comparison("rival").mcnemar_p <= 1.0

    def test_an_unknown_comparison_raises(self):
        with pytest.raises(KeyError):
            analyze(sweep({"baseline": [1], "rival": [0]})).comparison("ghost")


class TestVerdict:
    def test_the_cheapest_statistically_tied_cell_wins(self):
        pattern = {"baseline": [1, 1, 1, 1, 1, 1], "cheap": [1, 1, 1, 1, 1, 0]}
        result = analyze(sweep(pattern, costs={"baseline": 10.0, "cheap": 1.0}))
        assert result.verdict.winner.agent_id == "cheap"
        assert result.verdict.recommend_switch is True

    def test_a_significantly_worse_cell_never_wins_on_price(self):
        pattern = {"baseline": [1] * 20, "cheap": [0] * 20}
        result = analyze(sweep(pattern, costs={"baseline": 10.0, "cheap": 0.1}))
        assert result.verdict.winner.agent_id == "baseline"
        assert result.verdict.recommend_switch is False

    def test_savings_are_stated_in_money(self):
        pattern = {"baseline": [1, 1, 1, 1], "cheap": [1, 1, 1, 1]}
        result = analyze(sweep(pattern, costs={"baseline": 10.0, "cheap": 1.0}))
        assert result.verdict.savings_pct == pytest.approx(90.0)
        assert result.verdict.savings_usd > 0

    def test_staying_on_the_baseline_reports_no_savings(self):
        pattern = {"baseline": [1, 1, 1, 1], "worse": [0, 0, 0, 0]}
        result = analyze(sweep(pattern))
        assert result.verdict.savings_usd == 0.0

    def test_a_better_and_cheaper_cell_is_recommended(self):
        pattern = {"baseline": [1, 0, 0, 0], "better": [1, 1, 1, 1]}
        result = analyze(sweep(pattern, costs={"baseline": 5.0, "better": 1.0}))
        assert result.verdict.winner.agent_id == "better"

    def test_confidence_is_low_with_very_few_tasks(self):
        result = analyze(sweep({"baseline": [1, 1], "rival": [1, 0]}))
        assert result.verdict.confidence == "low"

    def test_confidence_improves_with_more_tasks(self):
        few = analyze(sweep({"baseline": [1] * 4, "rival": [1] * 4}))
        many = analyze(sweep({"baseline": [1] * 200, "rival": [1] * 200}, repeats=1))
        assert few.verdict.confidence != "high"
        assert many.verdict.confidence == "high"

    def test_the_verdict_names_the_baseline(self):
        result = analyze(sweep({"baseline": [1, 1], "rival": [1, 0]}))
        assert result.verdict.baseline.agent_id == "baseline"

    def test_a_missing_baseline_falls_back_to_the_first_cell(self):
        result = analyze(sweep({"a": [1, 1], "b": [1, 0]}, baseline="not-there"))
        assert result.verdict.baseline.agent_id in {"a", "b"}


class TestWatchOuts:
    def test_an_over_represented_failure_mode_is_flagged(self):
        records = []
        for task in range(6):
            records.append(record("baseline", f"t{task}", 0, task < 5))
            records.append(
                record("rival", f"t{task}", 0, False, failure=FailureMode.CONTRACT_FORMAT)
            )
        result = analyze(
            SweepResult(
                records=tuple(records),
                agent_ids=("baseline", "rival"),
                task_ids=tuple(f"t{i}" for i in range(6)),
                baseline="baseline",
                seed=1,
                repeats=1,
            )
        )
        assert any("contract" in w.lower() for w in result.verdict.watch_outs)

    def test_a_clean_winner_has_no_watch_outs(self):
        result = analyze(sweep({"baseline": [1, 1, 1, 1], "rival": [1, 1, 1, 1]}))
        assert result.verdict.watch_outs == ()


class TestVarianceReporting:
    def test_seed_noise_share_is_reported_per_cell(self):
        records = []
        for repeat, outcome in enumerate([True, False, True, False]):
            records.append(record("baseline", "t1", repeat, outcome))
            records.append(record("baseline", "t2", repeat, outcome))
        result = analyze(
            SweepResult(
                records=tuple(records),
                agent_ids=("baseline",),
                task_ids=("t1", "t2"),
                baseline="baseline",
                seed=3,
                repeats=4,
            )
        )
        assert result.cell("baseline").noise_share == pytest.approx(1.0)

    def test_tasks_that_discriminate_nothing_are_listed(self):
        pattern = {"baseline": [1, 1, 0], "rival": [1, 0, 0]}
        result = analyze(sweep(pattern))
        # t0 is passed by everyone, t2 failed by everyone: neither separates
        assert set(result.uninformative_tasks) == {"t0", "t2"}
