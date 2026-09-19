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


from tests.test_analysis import single as single_run


def single_analysis(scores, **kwargs):
    return analyze(single_run(scores, **kwargs))


class TestSingleModelReport:
    """With one model there is no decision between options, so the card states
    how it did rather than whether to switch."""

    def test_it_is_headed_as_an_assessment_not_a_verdict(self):
        text = render_verdict(single_analysis([1, 1, 0]))
        assert "ASSESSMENT" in text.upper()

    def test_it_names_the_model_and_its_pass_rate(self):
        text = render_verdict(single_analysis([1, 1, 0]))
        assert "local" in text
        assert "66.7%" in text or "67" in text

    def test_the_interval_is_shown(self):
        text = render_verdict(single_analysis([1, 1, 0, 1]))
        assert "[" in text and "]" in text

    def test_no_comparison_language_appears(self):
        text = render_verdict(single_analysis([1, 1, 0])).lower()
        for word in ("baseline", "switch", "savings", "difference", "vs "):
            assert word not in text, f"{word!r} has no meaning without a second model"

    def test_the_cost_per_success_is_stated(self):
        text = render_verdict(single_analysis([1, 1, 0, 0], cost=2.0))
        assert "success" in text.lower()

    def test_confidence_is_still_reported(self):
        assert "CONFIDENCE" in render_verdict(single_analysis([1, 1])).upper()

    def test_it_says_a_baseline_would_make_it_a_comparison(self):
        text = render_verdict(single_analysis([1, 1])).lower()
        assert "baseline" not in text
        assert "compare" in text or "on its own" in text

    def test_the_model_table_still_renders(self):
        assert "local" in render_models(single_analysis([1, 1, 0]))

    def test_the_full_report_renders(self):
        text = render_report(single_analysis([1, 1, 0]))
        assert "ASSESSMENT" in text.upper()
        assert "local" in text

    def test_every_line_fits_a_terminal(self):
        for line in render_verdict(single_analysis([1, 1, 0])).splitlines():
            assert len(line) <= 100

    def test_a_two_model_report_is_unaffected(self):
        assert "VERDICT" in render_verdict(analysis(*TIED)).upper()


class TestProductComparisonIsLabelled:
    def test_the_verdict_says_so(self):
        from tests.test_analysis import TestExternalHarnessCaveat

        result = analyze(TestExternalHarnessCaveat().run_with_cli())
        assert "product comparison" in render_verdict(result).lower()

    def test_an_ordinary_verdict_does_not(self):
        assert "product comparison" not in render_verdict(analysis(*TIED)).lower()
