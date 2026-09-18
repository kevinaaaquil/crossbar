"""The report: what a person reads at the end of a run."""
import pytest

from crossbar.analysis import analyze
from crossbar.report import render_models, render_report, render_verdict, write_markdown
from tests.test_analysis import attempt, paired, run
from crossbar.judging import Outcome


def analysis(local, frontier, **kwargs):
    return analyze(paired(local, frontier, **kwargs))


TIED = ([1, 1, 1, 1, 1, 1], [1, 1, 1, 1, 1, 1])


class TestVerdictCard:
    def test_it_names_both_models(self):
        text = render_verdict(analysis(*TIED))
        assert "local" in text and "frontier" in text

    def test_it_shows_pass_rates_with_intervals(self):
        text = render_verdict(analysis(*TIED))
        assert "%" in text and "[" in text and "]" in text

    def test_it_states_the_money(self):
        assert "$" in render_verdict(analysis(*TIED))

    def test_a_tied_result_is_reported_as_not_significant(self):
        assert "NOT SIGNIFICANT" in render_verdict(analysis(*TIED)).upper()

    def test_a_real_difference_is_reported_as_significant(self):
        text = render_verdict(analysis([0] * 20, [1] * 20)).upper()
        assert "SIGNIFICANT" in text and "NOT SIGNIFICANT" not in text

    def test_it_recommends_switching_when_the_candidate_holds_up(self):
        text = render_verdict(analysis(*TIED)).lower()
        assert "switch" in text or "savings" in text

    def test_it_recommends_staying_when_the_candidate_is_worse(self):
        text = render_verdict(analysis([0] * 20, [1] * 20)).lower()
        assert "stay" in text or "keep" in text

    def test_it_includes_the_confidence_line(self):
        assert "CONFIDENCE" in render_verdict(analysis(*TIED)).upper()

    def test_caveats_are_printed(self):
        result = analyze(run(
            [attempt("local", "t0", 0), attempt("frontier", "t0", 0)],
            judge_is_baseline=True,
        ))
        assert "judge" in render_verdict(result).lower()

    def test_every_line_fits_a_terminal(self):
        for line in render_verdict(analysis(*TIED)).splitlines():
            assert len(line) <= 100


class TestModelTable:
    def test_each_model_gets_a_row(self):
        text = render_models(analysis(*TIED))
        assert "local" in text and "frontier" in text

    def test_the_columns_cover_what_matters(self):
        text = render_models(analysis(*TIED)).lower()
        for column in ("pass", "95%", "cost", "success"):
            assert column in text

    def test_roles_are_labelled(self):
        text = render_models(analysis(*TIED)).lower()
        assert "candidate" in text and "baseline" in text

    def test_unchecked_counts_are_visible(self):
        attempts = [
            attempt("local", "t0", 0, outcome=Outcome.UNCHECKED),
            attempt("local", "t1", 0, score=1.0),
            attempt("frontier", "t0", 0, score=1.0),
            attempt("frontier", "t1", 0, score=1.0),
        ]
        assert "unchecked" in render_models(analyze(run(attempts))).lower()

    def test_a_model_with_no_graded_attempts_shows_no_rate(self):
        attempts = [
            attempt("local", "t0", 0, outcome=Outcome.UNCHECKED),
            attempt("frontier", "t0", 0, score=1.0),
        ]
        assert "n/a" in render_models(analyze(run(attempts))).lower()


class TestFullReport:
    def test_it_contains_the_verdict_and_the_table(self):
        text = render_report(analysis(*TIED))
        assert "VERDICT" in text.upper()
        assert "local" in text

    def test_unchecked_reasons_are_listed(self):
        attempts = [
            attempt("local", "t0", 0, outcome=Outcome.UNCHECKED),
            attempt("frontier", "t0", 0, score=1.0),
        ]
        assert "no dump available" in render_report(analyze(run(attempts)))

    def test_it_is_plain_text(self):
        assert "\x1b[" not in render_report(analysis(*TIED))

    def test_markdown_can_be_written(self, tmp_path):
        path = write_markdown(analysis(*TIED), tmp_path / "report.md")
        assert "Crossbar" in path.read_text()

    def test_a_run_with_nothing_judged_says_so(self):
        result = analyze(run(
            [attempt("local", "t0", 0, outcome=None), attempt("frontier", "t0", 0, outcome=None)],
            judged=(),
        ))
        assert "nothing" in render_report(result).lower() or "no judged" in render_report(result).lower()
