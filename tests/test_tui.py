"""The TUI: four tabs, a live queue view, and a results browser.

Everything here runs offline. The sweep is driven by the same scripted agents
and scripted judge the orchestrator suite uses, so the app is exercised against
a real run rather than a mocked one.
"""

import asyncio
import threading

import pytest
from textual.widgets import ListView, ProgressBar, Static, TabbedContent

from crossbar.analysis import analyze
from crossbar.domain import Role, load_test
from crossbar.evidence import Evidence, EvidenceItem, EvidenceRequest
from crossbar.judging import CheckOutcome, CheckStatus, Judgement, Outcome, ScriptedJudge
from crossbar.orchestrator import Attempt
from crossbar.roster import parse_roster
from crossbar.tui import CrossbarApp, build_app, run_app
from crossbar.tui.formatting import (
    render_attempt,
    render_now_running,
    render_queue,
    render_roster,
    render_tests,
)
from tests.test_orchestrator import (
    FIXTURE,
    PLAN,
    ROSTER,
    judge,
    single_task_test,
    solving_agent,
)

INDEPENDENT_ROSTER = parse_roster(
    {
        "models": [
            {"id": "local", "provider": "openai", "model": "qwen",
             "base_url": "http://localhost:1/v1"},
            {"id": "frontier", "provider": "anthropic", "model": "big"},
            {"id": "arbiter", "provider": "anthropic", "model": "referee"},
        ],
        "roles": {"candidate": "local", "baseline": "frontier", "judge": "arbiter"},
    },
    source="<test>",
)

FREE_TEXT_PANELS = (
    "models-body",
    "tests-body",
    "run-current",
    "run-error",
    "run-queue",
    "run-log",
    "results-report",
    "results-detail",
)

ROSTER_YAML = """
models:
  - id: local
    provider: openai
    model: qwen
    base_url: http://localhost:1/v1
  - id: frontier
    provider: anthropic
    model: big
roles:
  candidate: local
  baseline: frontier
"""


# -- helpers ---------------------------------------------------------------


def make_app(tmp_path, agent_factory=solving_agent, tests=None, **kwargs):
    return CrossbarApp(
        roster=kwargs.pop("roster", ROSTER),
        tests=tests or [single_task_test()],
        results_dir=str(kwargs.pop("results_dir", tmp_path)),
        judge=kwargs.pop("judge", judge()),
        agent_factory=agent_factory,
        **kwargs,
    )


def two_tests():
    test = single_task_test()
    second = type(test)(
        name="Second", tasks=test.tasks, environment=test.environment,
        repeats=1, path=test.path,
    )
    return [test, second]


def text_of(app, selector):
    return str(app.query_one(selector, Static).content)


async def finish_sweep(app, pilot, timeout_s=30.0):
    """Wait for the worker thread to report the sweep is over."""
    waited = 0.0
    while waited < timeout_s:
        if app.sweep_done:
            await pilot.pause()
            return
        await pilot.pause(0.02)
        waited += 0.02
    raise AssertionError(f"the sweep did not finish: error={app.run_error!r}")


async def wait_until(pilot, predicate, timeout_s=10.0):
    waited = 0.0
    while waited < timeout_s:
        if predicate():
            return
        await pilot.pause(0.02)
        waited += 0.02
    raise AssertionError("condition never became true")


def an_attempt(judgement=None, evidence=None, **kwargs):
    return Attempt(
        id="local-support-triage-escalate-outage-r0",
        test_name="Support triage",
        task_id="escalate-outage",
        model_id="local",
        role=Role.CANDIDATE,
        repeat=0,
        judgement=judgement,
        evidence=evidence,
        **kwargs,
    )


# -- the models panel ------------------------------------------------------


class TestRosterPanel:
    def test_every_connected_model_is_listed_with_its_role(self):
        rendered = render_roster(INDEPENDENT_ROSTER)
        assert "local" in rendered and "candidate" in rendered
        assert "frontier" in rendered and "baseline" in rendered
        assert "arbiter" in rendered and "judge" in rendered

    def test_a_model_with_no_role_is_shown_as_unassigned(self):
        roster = parse_roster(
            {
                "models": [
                    {"id": "local", "provider": "anthropic", "model": "q"},
                    {"id": "frontier", "provider": "anthropic", "model": "b"},
                    {"id": "spare", "provider": "anthropic", "model": "s"},
                ],
                "roles": {"candidate": "local", "baseline": "frontier"},
            },
            source="<test>",
        )
        assert "unassigned" in render_roster(roster)

    def test_the_judge_doubling_as_the_baseline_is_stated_plainly(self):
        rendered = render_roster(ROSTER).lower()
        assert "conflict of interest" in rendered
        assert "frontier" in rendered

    def test_an_independent_judge_raises_no_conflict(self):
        assert "conflict of interest" not in render_roster(INDEPENDENT_ROSTER).lower()


# -- the tests panel -------------------------------------------------------


class TestTestsPanel:
    def test_each_test_lists_its_tasks_and_their_count(self):
        rendered = render_tests([load_test(FIXTURE)], judge_tests=1)
        assert "Support triage" in rendered
        assert "escalate-outage" in rendered
        assert "route-billing" in rendered
        assert "2 tasks" in rendered

    def test_the_tests_that_will_be_judged_are_marked(self):
        rendered = render_tests(two_tests(), judge_tests=1)
        judged, unjudged = rendered.split("Second")
        assert "JUDGED" in judged
        assert "NOT JUDGED" in unjudged

    def test_judging_more_tests_is_flagged_as_costing_more(self):
        assert "costs more" in render_tests(two_tests(), judge_tests=1)

    def test_an_unjudged_test_is_said_to_produce_no_score(self):
        assert "no score" in render_tests(two_tests(), judge_tests=1)


# -- the queue -------------------------------------------------------------


class TestQueueRendering:
    def test_each_queued_item_shows_its_model_test_task_and_state(self, tmp_path):
        app = make_app(tmp_path)
        rendered = render_queue(app.orchestrator.queue)
        assert "local" in rendered
        assert "Support triage" in rendered
        assert "escalate-outage" in rendered
        assert rendered.count("pending") == len(app.orchestrator.queue)

    def test_the_running_item_names_the_model_test_and_task(self, tmp_path):
        item = make_app(tmp_path).orchestrator.queue[0]
        item.state = "running"
        line = render_now_running(item)
        assert "local" in line and "Support triage" in line and "escalate-outage" in line

    def test_nothing_running_says_so(self):
        assert "idle" in render_now_running(None).lower()


# -- the attempt detail ----------------------------------------------------


class TestAttemptDetail:
    def test_a_graded_attempt_shows_its_checks_and_the_judges_reasoning(self):
        attempt = an_attempt(
            judgement=Judgement(
                outcome=Outcome.GRADED,
                checks=(CheckOutcome("urgent", CheckStatus.PASS, "both were urgent"),),
                score=1.0,
                reasoning="The store showed T-1001 and T-1004 at urgent.",
            )
        )
        rendered = render_attempt(attempt)
        assert "graded" in rendered
        assert "urgent" in rendered and "pass" in rendered
        assert "The store showed T-1001 and T-1004 at urgent." in rendered

    def test_the_captured_evidence_is_shown(self):
        attempt = an_attempt(
            evidence=Evidence(
                final_answer="Escalated T-1001 and T-1004.",
                items=(
                    EvidenceItem(
                        request=EvidenceRequest("the store", "mcp", "tickets__dump_db"),
                        content='{"T-1001": "urgent"}',
                    ),
                ),
            )
        )
        rendered = render_attempt(attempt)
        assert "Escalated T-1001 and T-1004." in rendered
        assert "the store" in rendered
        assert '{"T-1001": "urgent"}' in rendered

    def test_an_unchecked_attempt_says_why_it_has_no_score(self):
        attempt = an_attempt(
            judgement=Judgement(
                outcome=Outcome.UNCHECKED,
                checks=(
                    CheckOutcome(
                        "urgent",
                        CheckStatus.UNCHECKED,
                        "the golden requires a database dump and nothing exposes one",
                    ),
                ),
            )
        )
        rendered = render_attempt(attempt)
        assert "unchecked" in rendered
        assert "the golden requires a database dump and nothing exposes one" in rendered

    def test_evidence_that_could_not_be_captured_shows_its_reason(self):
        attempt = an_attempt(
            evidence=Evidence(
                final_answer="",
                items=(
                    EvidenceItem(
                        request=EvidenceRequest("the store", "mcp", "tickets__dump_db"),
                        error="no connector named 'mcp' was enabled",
                    ),
                ),
            )
        )
        assert "no connector named 'mcp' was enabled" in render_attempt(attempt)

    def test_a_failed_attempt_shows_the_error(self):
        attempt = an_attempt(
            error="the environment would not start",
            judgement=Judgement(outcome=Outcome.FAILED, error="the environment would not start"),
        )
        rendered = render_attempt(attempt)
        assert "failed" in rendered
        assert "the environment would not start" in rendered

    def test_an_unjudged_attempt_says_it_was_not_judged(self):
        rendered = render_attempt(an_attempt())
        assert "not judged" in rendered


# -- the app ---------------------------------------------------------------


class TestTabs:
    async def test_the_app_has_the_four_tabs(self, tmp_path):
        app = make_app(tmp_path)
        async with app.run_test():
            panes = app.query_one(TabbedContent).query("TabPane")
            assert [p.id for p in panes] == ["models", "tests", "run", "results"]

    async def test_the_number_keys_switch_tabs(self, tmp_path):
        app = make_app(tmp_path)
        async with app.run_test() as pilot:
            tabs = app.query_one(TabbedContent)
            for key, expected in (("2", "tests"), ("3", "run"), ("4", "results"), ("1", "models")):
                await pilot.press(key)
                await pilot.pause()
                assert tabs.active == expected

    async def test_every_documented_key_is_bound(self, tmp_path):
        bound = {b.key for b in make_app(tmp_path).BINDINGS}
        assert {"r", "1", "2", "3", "4", "d", "q"} <= bound


class TestModelsTab:
    async def test_it_names_every_connected_model(self, tmp_path):
        app = make_app(tmp_path)
        async with app.run_test():
            body = text_of(app, "#models-body")
            assert "local" in body and "frontier" in body

    async def test_it_shows_the_judge_baseline_conflict(self, tmp_path):
        app = make_app(tmp_path)
        async with app.run_test():
            assert "conflict of interest" in text_of(app, "#models-body").lower()


class TestTestsTab:
    async def test_it_shows_the_tasks_and_which_tests_are_judged(self, tmp_path):
        app = make_app(tmp_path, tests=two_tests())
        async with app.run_test():
            body = text_of(app, "#tests-body")
            assert "escalate-outage" in body
            assert "NOT JUDGED" in body


class TestRunTab:
    async def test_the_whole_queue_is_rendered_before_anything_runs(self, tmp_path):
        app = make_app(tmp_path)
        async with app.run_test():
            queue = text_of(app, "#run-queue")
            assert queue.count("pending") == len(app.orchestrator.queue)

    async def test_running_shows_the_live_model_test_and_task(self, tmp_path):
        gate = threading.Event()

        def gated(model_id, role, task, repeat):
            gate.wait(timeout=20)
            return solving_agent(model_id, role, task, repeat)

        app = make_app(tmp_path, agent_factory=gated)
        async with app.run_test() as pilot:
            await pilot.press("r")
            # The UI keeps painting while the worker thread sits on the gate.
            await wait_until(pilot, lambda: "local" in text_of(app, "#run-current"))
            current = text_of(app, "#run-current")
            assert "Support triage" in current and "escalate-outage" in current
            assert "running" in text_of(app, "#run-queue")
            gate.set()
            await finish_sweep(app, pilot)

    async def test_a_finished_run_leaves_every_queue_item_done(self, tmp_path):
        app = make_app(tmp_path)
        async with app.run_test() as pilot:
            await pilot.press("r")
            await finish_sweep(app, pilot)
            assert [i.state for i in app.orchestrator.queue] == ["done", "done"]
            assert "pending" not in text_of(app, "#run-queue")

    async def test_the_progress_bar_reaches_the_total(self, tmp_path):
        app = make_app(tmp_path)
        async with app.run_test() as pilot:
            await pilot.press("r")
            await finish_sweep(app, pilot)
            bar = app.query_one("#run-progress", ProgressBar)
            assert bar.total == 2 and bar.progress == 2

    async def test_a_line_is_logged_for_every_finished_attempt(self, tmp_path):
        app = make_app(tmp_path)
        async with app.run_test() as pilot:
            await pilot.press("r")
            await finish_sweep(app, pilot)
            attempt_lines = [line for line in app.log_lines if "escalate-outage" in line]
            assert len(attempt_lines) == 2
            assert "local" in "\n".join(attempt_lines)
            assert text_of(app, "#run-log").count("escalate-outage") == 2

    async def test_pressing_run_twice_does_not_start_two_runs(self, tmp_path):
        gate = threading.Event()

        def gated(model_id, role, task, repeat):
            gate.wait(timeout=20)
            return solving_agent(model_id, role, task, repeat)

        app = make_app(tmp_path, agent_factory=gated)
        async with app.run_test() as pilot:
            await pilot.press("r")
            await wait_until(pilot, lambda: app.sweep_running)
            await pilot.press("r")
            await pilot.pause()
            assert app.runs_started == 1
            gate.set()
            await finish_sweep(app, pilot)
            assert app.runs_started == 1

    async def test_an_attempt_that_errors_is_marked_failed_but_the_run_finishes(self, tmp_path):
        def broken(model_id, role, task, repeat):
            raise RuntimeError("the provider is unreachable")

        app = make_app(tmp_path, agent_factory=broken)
        async with app.run_test() as pilot:
            await pilot.press("r")
            await finish_sweep(app, pilot)
            assert [i.state for i in app.orchestrator.queue] == ["failed", "failed"]
            assert app.run_error == ""

    async def test_a_run_that_fails_outright_shows_the_error_instead_of_crashing(self, tmp_path):
        blocked = tmp_path / "not-a-directory"
        blocked.write_text("in the way")
        app = make_app(tmp_path, results_dir=blocked)
        async with app.run_test() as pilot:
            await pilot.press("r")
            await finish_sweep(app, pilot)
            assert app.run_error
            assert app.run_error in text_of(app, "#run-error")
            assert app.is_running  # the app survived it


class TestResultsTab:
    async def test_the_report_is_rendered_after_a_run(self, tmp_path):
        app = make_app(tmp_path)
        async with app.run_test() as pilot:
            await pilot.press("r")
            await finish_sweep(app, pilot)
            report = text_of(app, "#results-report")
            assert "CROSSBAR VERDICT" in report
            assert "local" in report and "frontier" in report

    async def test_the_report_matches_what_the_cli_would_print(self, tmp_path):
        from crossbar.report import render_report

        app = make_app(tmp_path)
        async with app.run_test() as pilot:
            await pilot.press("r")
            await finish_sweep(app, pilot)
            assert text_of(app, "#results-report") == render_report(analyze(app.result))

    async def test_every_attempt_is_listed(self, tmp_path):
        app = make_app(tmp_path)
        async with app.run_test() as pilot:
            await pilot.press("r")
            await finish_sweep(app, pilot)
            assert len(app.query_one("#results-list", ListView)) == 2

    async def test_selecting_an_attempt_shows_its_detail(self, tmp_path):
        app = make_app(tmp_path)
        async with app.run_test() as pilot:
            await pilot.press("r")
            await finish_sweep(app, pilot)
            listing = app.query_one("#results-list", ListView)
            listing.index = 1
            await pilot.pause()
            detail = text_of(app, "#results-detail")
            assert app.result.attempts[1].model_id in detail
            assert "graded" in detail

    async def test_before_a_run_the_detail_panel_says_there_is_nothing_yet(self, tmp_path):
        app = make_app(tmp_path)
        async with app.run_test():
            assert "no attempt" in text_of(app, "#results-detail").lower()


class TestMarkup:
    """A Golden, a judge's reasoning or a database dump will contain square
    brackets sooner or later. With markup left on, a bare '[/]' raises
    MarkupError while the panel is painting and takes the app down mid-run."""

    async def test_every_free_text_panel_has_markup_turned_off(self, tmp_path):
        app = make_app(tmp_path)
        async with app.run_test():
            panels = [app.query_one(f"#{name}", Static) for name in FREE_TEXT_PANELS]
            # Private, but it is the only place the flag survives construction,
            # and this is the invariant worth pinning.
            assert [w.id for w in panels if w._render_markup] == []

    async def test_brackets_in_the_judges_words_do_not_break_the_detail_panel(self, tmp_path):
        reasoning = "The dump showed [T-1001, T-1004] at urgent. [/] Nothing else moved."
        bracketed = ScriptedJudge(
            plan=PLAN,
            grader=lambda p, e: {i.id: "pass" for i in p.items},
            reasoning=reasoning,
        )
        app = make_app(tmp_path, judge=bracketed)
        async with app.run_test() as pilot:
            await pilot.press("r")
            await finish_sweep(app, pilot)
            await pilot.pause()
            assert reasoning in text_of(app, "#results-detail")


class TestDump:
    async def test_pressing_d_after_a_run_writes_a_dump(self, tmp_path):
        app = make_app(tmp_path)
        async with app.run_test() as pilot:
            await pilot.press("r")
            await finish_sweep(app, pilot)
            await pilot.press("d")
            await wait_until(pilot, lambda: bool(app.dump_path))
            assert (tmp_path / "dump.zip").exists()
            assert "dump.zip" in "\n".join(app.log_lines)

    async def test_pressing_d_before_a_run_reports_there_is_nothing_to_dump(self, tmp_path):
        app = make_app(tmp_path)
        async with app.run_test() as pilot:
            await pilot.press("d")
            await pilot.pause()
            assert not app.dump_path
            assert "no run" in "\n".join(app.log_lines).lower()


class TestEntryPoint:
    def test_build_app_loads_the_roster_and_the_tests_from_disk(self, tmp_path):
        roster_path = tmp_path / "roster.yaml"
        roster_path.write_text(ROSTER_YAML)
        app = build_app(str(roster_path), [FIXTURE], results_dir=str(tmp_path / "runs"))
        assert [m.id for m in app.roster.models] == ["local", "frontier"]
        assert [t.name for t in app.tests] == ["Support triage"]
        assert len(app.tests[0].tasks) == 2

    def test_run_app_is_exported_for_the_cli(self):
        assert callable(run_app)


class TestJudgeCountControl:
    """CLAUDE.md: when more than one Test is scheduled, the user must be asked
    how many to judge, because judging is the expensive part."""

    def two_tests(self):
        from tests.test_orchestrator import single_task_test

        first = single_task_test()
        second = type(first)(
            name="Second", tasks=first.tasks, environment=first.environment,
            repeats=1, path=first.path,
        )
        return [first, second]

    async def test_a_control_appears_when_several_tests_are_scheduled(self, tmp_path):
        from tests.test_orchestrator import ROSTER, judge, solving_agent
        from crossbar.tui import CrossbarApp

        app = CrossbarApp(
            roster=ROSTER, tests=self.two_tests(), results_dir=str(tmp_path),
            judge=judge(), agent_factory=solving_agent,
        )
        async with app.run_test() as pilot:
            await pilot.pause()
            assert app.query_one("#judge-count")

    async def test_it_defaults_to_judging_one_test(self, tmp_path):
        from tests.test_orchestrator import ROSTER, judge, solving_agent
        from crossbar.tui import CrossbarApp

        app = CrossbarApp(
            roster=ROSTER, tests=self.two_tests(), results_dir=str(tmp_path),
            judge=judge(), agent_factory=solving_agent,
        )
        async with app.run_test() as pilot:
            await pilot.pause()
            assert app.query_one("#judge-count").value == "1"
            assert app.chosen_judge_tests() == 1

    async def test_changing_it_changes_what_gets_judged(self, tmp_path):
        from tests.test_orchestrator import ROSTER, judge, solving_agent
        from crossbar.tui import CrossbarApp

        app = CrossbarApp(
            roster=ROSTER, tests=self.two_tests(), results_dir=str(tmp_path),
            judge=judge(), agent_factory=solving_agent,
        )
        async with app.run_test() as pilot:
            await pilot.pause()
            app.query_one("#judge-count").value = "2"
            assert app.chosen_judge_tests() == 2

    async def test_a_nonsense_value_falls_back_to_none_judged(self, tmp_path):
        from tests.test_orchestrator import ROSTER, judge, solving_agent
        from crossbar.tui import CrossbarApp

        app = CrossbarApp(
            roster=ROSTER, tests=self.two_tests(), results_dir=str(tmp_path),
            judge=judge(), agent_factory=solving_agent,
        )
        async with app.run_test() as pilot:
            await pilot.pause()
            app.query_one("#judge-count").value = "banana"
            assert app.chosen_judge_tests() == 0

    async def test_the_cost_warning_is_shown(self, tmp_path):
        from tests.test_orchestrator import ROSTER, judge, solving_agent
        from crossbar.tui import CrossbarApp

        app = CrossbarApp(
            roster=ROSTER, tests=self.two_tests(), results_dir=str(tmp_path),
            judge=judge(), agent_factory=solving_agent,
        )
        async with app.run_test() as pilot:
            await pilot.pause()
            body = str(app.query_one("#tests-body").content).lower()
            assert "judg" in body and ("cost" in body or "expensive" in body)

    async def test_the_choice_reaches_the_orchestrator(self, tmp_path):
        from tests.test_orchestrator import ROSTER, judge, solving_agent
        from crossbar.tui import CrossbarApp

        app = CrossbarApp(
            roster=ROSTER, tests=self.two_tests(), results_dir=str(tmp_path),
            judge=judge(), agent_factory=solving_agent,
        )
        async with app.run_test() as pilot:
            await pilot.pause()
            app.query_one("#judge-count").value = "2"
            await pilot.press("r")
            while not app.sweep_done:
                await pilot.pause()
                await asyncio.sleep(0.05)
            judged = {a.test_name for a in app.result.attempts if a.judgement is not None}
            assert judged == {"Support triage", "Second"}


class TestEntryPointWiring:
    """`crossbar tui` must produce a judged run, like `crossbar run` does."""

    def roster_and_test(self, tmp_path):
        roster = tmp_path / "roster.yaml"
        roster.write_text(
            "models:\n"
            "  - id: local\n    provider: openai\n    model: q\n"
            "    base_url: http://localhost:1/v1\n"
            "  - id: frontier\n    provider: anthropic\n    model: big\n"
            "roles:\n  candidate: local\n  baseline: frontier\n"
        )
        return str(roster), "tests/fixtures/tests/support-triage"

    def test_a_judge_is_wired_up_from_the_roster(self, tmp_path):
        from crossbar.judging import Judge

        roster, test = self.roster_and_test(tmp_path)
        app = build_app(roster, [test], results_dir=str(tmp_path / "runs"))
        assert isinstance(app.judge, Judge), "without a judge, a run produces no verdict"

    def test_the_judging_model_is_named_so_the_conflict_can_be_reported(self, tmp_path):
        roster, test = self.roster_and_test(tmp_path)
        app = build_app(roster, [test], results_dir=str(tmp_path / "runs"))
        assert app.judge.model_id == "frontier"

    def test_an_explicit_judge_is_not_overridden(self, tmp_path):
        roster, test = self.roster_and_test(tmp_path)
        scripted = ScriptedJudge(plan=None)
        app = build_app(roster, [test], results_dir=str(tmp_path / "runs"), judge=scripted)
        assert app.judge is scripted

    def test_unknown_connectors_are_caught_when_loading(self, tmp_path):
        from crossbar.domain import DomainError

        roster, _ = self.roster_and_test(tmp_path)
        bad = tmp_path / "bad"
        bad.mkdir()
        (bad / "test.yaml").write_text("name: X\nenvironment: env.yaml\n")
        (bad / "env.yaml").write_text("kind: local\nconnectors:\n  telepathy: {}\n")
        (bad / "a.task.yaml").write_text("id: a\nprompt: p\ngolden: g\n")
        with pytest.raises(DomainError, match="telepathy"):
            build_app(roster, [str(bad)], results_dir=str(tmp_path / "runs"))
