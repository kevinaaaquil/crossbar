"""Analysis: judged attempts to a defensible recommendation."""
import pytest

from crossbar.analysis import analyze
from crossbar.domain import Role
from crossbar.judging import CheckOutcome, CheckStatus, Judgement, Outcome
from crossbar.orchestrator import Attempt, RunResult
from crossbar.providers import Usage


def attempt(model, task, repeat, outcome=Outcome.GRADED, score=1.0, cost=1.0, error=""):
    judgement = None
    if outcome is not None:
        judgement = Judgement(
            outcome=outcome,
            score=score,
            checks=(CheckOutcome("c", CheckStatus.PASS if score >= 1 else CheckStatus.FAIL),)
            if outcome is Outcome.GRADED
            else (CheckOutcome("c", CheckStatus.UNCHECKED, "no dump available"),),
            error=error,
        )
    return Attempt(
        id=f"{model}-{task}-{repeat}",
        test_name="T",
        task_id=task,
        model_id=model,
        role=Role.CANDIDATE if model == "local" else Role.BASELINE,
        repeat=repeat,
        judgement=judgement,
        usage=Usage(1000, 500),
        cost_usd=cost,
        error=error,
    )


def run(attempts, judged=("T",), judge_is_baseline=False, roles=None):
    return RunResult(
        run_id="r",
        attempts=tuple(attempts),
        roles=roles or {"candidate": "local", "baseline": "frontier"},
        judged_tests=tuple(judged),
        judge_is_baseline=judge_is_baseline,
    )


def paired(local_scores, frontier_scores, **kwargs):
    attempts = []
    for i, score in enumerate(local_scores):
        attempts.append(attempt("local", f"t{i}", 0, score=score, **kwargs))
    for i, score in enumerate(frontier_scores):
        attempts.append(attempt("frontier", f"t{i}", 0, score=score, cost=10.0))
    return run(attempts)


class TestModelSummaries:
    def test_each_model_is_summarised(self):
        result = analyze(paired([1, 1, 0], [1, 1, 1]))
        assert {m.model_id for m in result.models} == {"local", "frontier"}

    def test_the_pass_rate_is_over_graded_attempts(self):
        summary = analyze(paired([1, 1, 0], [1, 1, 1])).model("local")
        assert summary.pass_rate == pytest.approx(2 / 3)

    def test_an_interval_is_reported(self):
        summary = analyze(paired([1, 1, 0], [1, 1, 1])).model("local")
        assert summary.ci_low <= summary.pass_rate <= summary.ci_high

    def test_roles_are_carried_through(self):
        result = analyze(paired([1], [1]))
        assert result.model("local").role is Role.CANDIDATE
        assert result.model("frontier").role is Role.BASELINE

    def test_cost_and_cost_per_success_are_reported(self):
        summary = analyze(paired([1, 1, 0], [1, 1, 1])).model("local")
        assert summary.total_cost == pytest.approx(3.0)
        assert summary.cost_per_success == pytest.approx(1.5)

    def test_a_model_that_never_passes_has_no_cost_per_success(self):
        assert analyze(paired([0, 0], [1, 1])).model("local").cost_per_success is None


class TestUncheckedHandling:
    """Unchecked means we could not verify, not that the model failed."""

    def test_unchecked_attempts_are_excluded_from_the_score(self):
        attempts = [
            attempt("local", "t0", 0, score=1.0),
            attempt("local", "t1", 0, outcome=Outcome.UNCHECKED, score=0.0),
            attempt("frontier", "t0", 0, score=1.0),
            attempt("frontier", "t1", 0, score=1.0),
        ]
        summary = analyze(run(attempts)).model("local")
        assert summary.pass_rate == 1.0, "an unchecked attempt must not count as a failure"
        assert summary.n_unchecked == 1

    def test_failed_attempts_do_count_as_failures(self):
        """The agent erroring is the agent's failure, unlike our inability to check."""
        attempts = [
            attempt("local", "t0", 0, score=1.0),
            attempt("local", "t1", 0, outcome=Outcome.FAILED, score=0.0, error="crashed"),
            attempt("frontier", "t0", 0, score=1.0),
            attempt("frontier", "t1", 0, score=1.0),
        ]
        summary = analyze(run(attempts)).model("local")
        assert summary.pass_rate == 0.5
        assert summary.n_failed == 1

    def test_the_unchecked_share_is_reported(self):
        attempts = [
            attempt("local", "t0", 0, outcome=Outcome.UNCHECKED),
            attempt("local", "t1", 0, score=1.0),
            attempt("frontier", "t0", 0, score=1.0),
            attempt("frontier", "t1", 0, score=1.0),
        ]
        assert analyze(run(attempts)).model("local").unchecked_share == pytest.approx(0.5)

    def test_a_heavily_unchecked_run_is_flagged(self):
        attempts = [attempt("local", f"t{i}", 0, outcome=Outcome.UNCHECKED) for i in range(4)]
        attempts += [attempt("frontier", f"t{i}", 0, score=1.0) for i in range(4)]
        caveats = " ".join(analyze(run(attempts)).verdict.caveats).lower()
        assert "unchecked" in caveats

    def test_the_reasons_for_unchecked_results_are_collected(self):
        attempts = [
            attempt("local", "t0", 0, outcome=Outcome.UNCHECKED),
            attempt("frontier", "t0", 0, score=1.0),
        ]
        assert "no dump available" in " ".join(analyze(run(attempts)).unchecked_reasons)

    def test_a_model_with_nothing_graded_has_no_pass_rate(self):
        attempts = [attempt("local", "t0", 0, outcome=Outcome.UNCHECKED),
                    attempt("frontier", "t0", 0, score=1.0)]
        assert analyze(run(attempts)).model("local").pass_rate is None


class TestComparison:
    def test_the_candidate_is_compared_with_the_baseline(self):
        comparison = analyze(paired([1, 1, 1, 1], [1, 1, 0, 0])).comparison
        assert comparison.candidate_id == "local"
        assert comparison.baseline_id == "frontier"
        assert comparison.delta == pytest.approx(0.5)

    def test_a_large_consistent_gap_is_significant(self):
        assert analyze(paired([1] * 20, [0] * 20)).comparison.significant is True

    def test_a_small_noisy_gap_is_not(self):
        local = [1, 0, 1, 1, 0, 1, 0, 1, 1, 0]
        frontier = [1, 0, 1, 0, 0, 1, 0, 1, 1, 0]
        assert analyze(paired(local, frontier)).comparison.significant is False

    def test_the_interval_brackets_the_delta(self):
        comparison = analyze(paired([1, 1, 0, 1], [1, 0, 0, 1])).comparison
        assert comparison.ci_low <= comparison.delta <= comparison.ci_high

    def test_mcnemar_is_reported_as_a_cross_check(self):
        assert 0.0 <= analyze(paired([1, 1, 0], [0, 1, 0])).comparison.mcnemar_p <= 1.0

    def test_only_tasks_both_models_had_graded_are_paired(self):
        attempts = [
            attempt("local", "t0", 0, score=1.0),
            attempt("local", "t1", 0, outcome=Outcome.UNCHECKED),
            attempt("frontier", "t0", 0, score=1.0),
            attempt("frontier", "t1", 0, score=1.0),
        ]
        assert analyze(run(attempts)).comparison.n_tasks == 1


class TestVerdict:
    def test_a_cheaper_model_that_holds_its_quality_is_recommended(self):
        result = analyze(paired([1, 1, 1, 1, 1, 1], [1, 1, 1, 1, 1, 1]))
        assert result.verdict.recommend_switch is True
        assert result.verdict.savings_usd > 0

    def test_a_clearly_worse_model_is_not_recommended(self):
        result = analyze(paired([0] * 20, [1] * 20))
        assert result.verdict.recommend_switch is False

    def test_savings_are_stated_in_money_and_percent(self):
        verdict = analyze(paired([1, 1, 1, 1], [1, 1, 1, 1])).verdict
        assert verdict.savings_usd == pytest.approx(36.0)
        assert verdict.savings_pct == pytest.approx(90.0)

    def test_staying_reports_no_savings(self):
        assert analyze(paired([0] * 20, [1] * 20)).verdict.savings_usd == 0.0

    def test_confidence_is_low_with_few_tasks(self):
        assert analyze(paired([1, 1], [1, 1])).verdict.confidence == "low"

    def test_confidence_improves_with_many_tasks(self):
        assert analyze(paired([1] * 200, [1] * 200)).verdict.confidence == "high"


class TestCaveats:
    def test_a_baseline_judging_itself_is_flagged(self):
        result = analyze(run(
            [attempt("local", "t0", 0), attempt("frontier", "t0", 0)],
            judge_is_baseline=True,
        ))
        assert any("judg" in c.lower() for c in result.verdict.caveats)

    def test_an_independent_judge_is_not_flagged(self):
        result = analyze(paired([1], [1]))
        assert not any("judg" in c.lower() for c in result.verdict.caveats)

    def test_unjudged_tests_are_named(self):
        attempts = [
            attempt("local", "t0", 0),
            attempt("frontier", "t0", 0),
            Attempt(id="x", test_name="Second", task_id="t0", model_id="local",
                    role=Role.CANDIDATE, repeat=0),
        ]
        result = analyze(run(attempts, judged=("T",)))
        assert any("Second" in c for c in result.verdict.caveats)

    def test_a_clean_run_has_no_caveats(self):
        assert analyze(paired([1] * 200, [1] * 200)).verdict.caveats == ()


def single(scores, cost=1.0, **kwargs):
    attempts = [
        attempt("local", f"t{i}", 0, score=s, cost=cost, **kwargs) for i, s in enumerate(scores)
    ]
    return RunResult(
        run_id="r",
        attempts=tuple(attempts),
        roles={"candidate": "local", "judge": "grader"},
        judged_tests=("T",),
    )


class TestSingleModelAssessment:
    """One model on its own: how did it do, not whether to switch."""

    def test_the_one_model_is_summarised(self):
        result = analyze(single([1, 1, 0]))
        assert [m.model_id for m in result.models] == ["local"]
        assert result.model("local").pass_rate == pytest.approx(2 / 3)

    def test_there_is_no_comparison(self):
        assert analyze(single([1, 1, 0])).comparison is None

    def test_it_is_flagged_as_a_single_model_run(self):
        assert analyze(single([1, 1])).is_single_model is True
        assert analyze(paired([1, 1], [1, 1])).is_single_model is False

    def test_no_switch_is_recommended(self):
        verdict = analyze(single([1, 1, 1])).verdict
        assert verdict.recommend_switch is False
        assert verdict.savings_usd == 0.0

    def test_an_interval_is_still_reported(self):
        summary = analyze(single([1, 1, 0, 1])).model("local")
        assert summary.ci_low <= summary.pass_rate <= summary.ci_high

    def test_cost_per_success_is_still_reported(self):
        assert analyze(single([1, 1, 0, 0], cost=2.0)).model("local").cost_per_success == (
            pytest.approx(4.0)
        )

    def test_confidence_still_reflects_the_task_count(self):
        assert analyze(single([1] * 4)).verdict.confidence == "low"
        assert analyze(single([1] * 200)).verdict.confidence == "high"

    def test_unchecked_attempts_are_still_excluded(self):
        result = analyze(single([1, 1], outcome=Outcome.UNCHECKED))
        assert result.model("local").pass_rate is None
        assert result.model("local").n_unchecked == 2

    def test_no_judge_conflict_is_claimed(self):
        caveats = " ".join(analyze(single([1, 1])).verdict.caveats).lower()
        assert "graded its own" not in caveats
