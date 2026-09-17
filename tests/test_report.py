"""The report: what a person actually reads at the end of a sweep."""
import pytest

from crossbar.analysis import analyze
from crossbar.providers import Usage
from crossbar.report import render_failures, render_matrix, render_report, render_verdict
from crossbar.runner import RunRecord, SweepResult
from crossbar.scoring import FailureMode, TaskScore


def record(agent, task, repeat, passed, cost=1.0, failure=None, model=None, harness="react"):
    return RunRecord(
        agent_id=agent,
        model_id=model or agent.split("+")[0],
        harness_id=harness,
        task_id=task,
        repeat=repeat,
        score=TaskScore(
            security=1.0,
            completion=1.0 if passed else 0.0,
            process=1.0,
            failure_mode=failure if not passed else None,
        ),
        usage=Usage(1000, 500),
        cost_usd=cost,
        wall_time_s=2.5,
    )


def analysis(pattern, costs=None, baseline="opus+react", failure=None, tasks=None):
    costs = costs or {}
    tasks = tasks or [f"t{i}" for i in range(len(next(iter(pattern.values()))))]
    records = []
    for agent, passes in pattern.items():
        for task, passed in zip(tasks, passes):
            records.append(
                record(agent, task, 0, passed, cost=costs.get(agent, 1.0), failure=failure)
            )
    return analyze(
        SweepResult(
            records=tuple(records),
            agent_ids=tuple(pattern),
            task_ids=tuple(tasks),
            baseline=baseline,
            seed=5,
            repeats=1,
            pack_name="claims-triage",
        )
    )


TIE = {"opus+react": [1, 1, 1, 1, 1, 1], "qwen+react": [1, 1, 1, 1, 1, 0]}
COSTS = {"opus+react": 10.0, "qwen+react": 1.0}


class TestVerdictCard:
    def test_names_the_baseline_and_the_winner(self):
        text = render_verdict(analysis(TIE, COSTS))
        assert "BASELINE" in text and "opus+react" in text
        assert "WINNER" in text and "qwen+react" in text

    def test_shows_pass_rates_with_intervals(self):
        text = render_verdict(analysis(TIE, COSTS))
        assert "%" in text
        assert "[" in text and "]" in text

    def test_states_the_money(self):
        text = render_verdict(analysis(TIE, COSTS))
        assert "$" in text
        assert "83" in text or "savings" in text.lower()

    def test_reports_non_significance_as_a_finding(self):
        text = render_verdict(analysis(TIE, COSTS))
        assert "NOT SIGNIFICANT" in text.upper()

    def test_reports_a_real_difference_as_significant(self):
        pattern = {"opus+react": [1] * 20, "qwen+react": [0] * 20}
        text = render_verdict(analysis(pattern, {"opus+react": 10.0, "qwen+react": 1.0}))
        assert "SIGNIFICANT" in text.upper()
        assert "NOT SIGNIFICANT" not in text.upper()

    def test_includes_the_confidence_line(self):
        text = render_verdict(analysis(TIE, COSTS))
        assert "CONFIDENCE" in text.upper()
        assert "task" in text.lower()

    def test_recommends_staying_when_the_baseline_wins(self):
        pattern = {"opus+react": [1] * 12, "qwen+react": [0] * 12}
        text = render_verdict(analysis(pattern, {"opus+react": 10.0, "qwen+react": 1.0}))
        assert "stay" in text.lower() or "keep" in text.lower()

    def test_lists_watch_outs_when_there_are_any(self):
        pattern = {"opus+react": [1, 1, 1, 1, 1, 1], "qwen+react": [1, 0, 0, 0, 1, 1]}
        text = render_verdict(analysis(pattern, COSTS, failure=FailureMode.CONTRACT_FORMAT))
        assert "WATCH" in text.upper()
        assert "contract" in text.lower()

    def test_names_the_task_pack(self):
        assert "claims-triage" in render_verdict(analysis(TIE, COSTS))

    def test_fits_in_a_terminal(self):
        for line in render_verdict(analysis(TIE, COSTS)).splitlines():
            assert len(line) <= 100


class TestMatrixTable:
    def test_every_cell_gets_a_row(self):
        text = render_matrix(analysis(TIE, COSTS))
        assert "opus+react" in text and "qwen+react" in text

    def test_columns_include_pass_rate_interval_and_cost(self):
        text = render_matrix(analysis(TIE, COSTS)).lower()
        assert "pass" in text and "95%" in text and "cost" in text

    def test_cells_that_cannot_be_separated_are_marked(self):
        text = render_matrix(analysis(TIE, COSTS))
        assert "~" in text or "tied" in text.lower()

    def test_a_significantly_worse_cell_is_not_marked_as_tied(self):
        pattern = {"opus+react": [1] * 20, "qwen+react": [0] * 20}
        rows = [
            line
            for line in render_matrix(analysis(pattern, COSTS)).splitlines()
            if "qwen+react" in line
        ]
        assert rows and "~" not in rows[0]

    def test_the_baseline_row_is_labelled(self):
        text = render_matrix(analysis(TIE, COSTS))
        assert "baseline" in text.lower()

    def test_cost_per_success_is_shown(self):
        assert "success" in render_matrix(analysis(TIE, COSTS)).lower()


class TestFailureReport:
    def test_failure_modes_are_tallied_per_cell(self):
        pattern = {"opus+react": [1, 1, 1, 1], "qwen+react": [0, 0, 1, 1]}
        text = render_failures(analysis(pattern, COSTS, failure=FailureMode.TOOL_RECOVERY))
        assert "Tool / recovery" in text
        assert "qwen+react" in text

    def test_a_clean_sweep_says_so(self):
        text = render_failures(analysis({"opus+react": [1, 1], "qwen+react": [1, 1]}, COSTS))
        assert "no failures" in text.lower()

    def test_uninformative_tasks_are_called_out(self):
        pattern = {"opus+react": [1, 1, 0], "qwen+react": [1, 0, 0]}
        text = render_failures(analysis(pattern, COSTS))
        assert "t0" in text and "t2" in text


class TestFullReport:
    def test_contains_all_three_sections(self):
        text = render_report(analysis(TIE, COSTS))
        assert "VERDICT" in text.upper()
        assert "opus+react" in text
        assert "FAILURE" in text.upper()

    def test_is_plain_text_without_ansi_codes(self):
        assert "\x1b[" not in render_report(analysis(TIE, COSTS))

    def test_handles_a_single_cell_sweep(self):
        text = render_report(analysis({"opus+react": [1, 1, 1]}))
        assert "opus+react" in text


class TestAllZeroSweep:
    """When nothing passes anywhere, the likely cause is the task pack, not the
    models - and the report has to say so or the user is stranded."""

    def test_it_says_every_cell_failed(self):
        text = render_verdict(analysis({"opus+react": [0, 0, 0], "qwen+react": [0, 0, 0]}))
        assert "every cell failed" in text.lower()

    def test_it_points_at_the_likely_cause(self):
        text = render_verdict(analysis({"opus+react": [0, 0, 0], "qwen+react": [0, 0, 0]}))
        assert "check" in text.lower()
        assert "demo" in text.lower() or "mock" in text.lower()

    def test_a_normal_sweep_gets_no_such_warning(self):
        text = render_verdict(analysis(TIE, COSTS))
        assert "every cell failed" not in text.lower()

    def test_a_partial_failure_gets_no_such_warning(self):
        text = render_verdict(analysis({"opus+react": [1, 0, 0], "qwen+react": [0, 0, 0]}))
        assert "every cell failed" not in text.lower()
