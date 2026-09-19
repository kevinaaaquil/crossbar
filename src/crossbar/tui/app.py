"""The terminal app.

Four tabs: the models connected and what role each holds, the Tests that will
run, the live queue while they run, and the results afterwards.

The sweep runs on a worker thread. Everything it wants to say comes back
through ``call_from_thread``, so the UI keeps painting while a model is being
waited on — an Attempt can take minutes, and a frozen terminal makes a working
run look like a hung one.

The queue is rendered from ``Orchestrator.queue``, never reconstructed from the
event stream: the orchestrator already holds that state, and two copies of it
would eventually disagree.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable, Sequence

from textual import work
from textual.app import App, ComposeResult
from textual.app import ScreenStackError
from textual.css.query import NoMatches
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import (
    Footer,
    Header,
    ListItem,
    ListView,
    ProgressBar,
    Static,
    TabbedContent,
    Input,
    Label,
    TabPane,
)

from crossbar.analysis import Analysis, analyze
from crossbar.connectors import known_connectors
from crossbar.domain import Role, Test, load_test
from crossbar.dump import create_dump
from crossbar.judging import Judge
from crossbar.orchestrator import Orchestrator, RunEvent, RunResult
from crossbar.report import render_report
from crossbar.roster import build_provider, Roster, load_roster
from crossbar.tui.formatting import (
    attempt_line,
    queue_event_line,
    render_attempt,
    render_now_running,
    render_queue,
    render_roster,
    render_tests,
)

MAX_LOG_LINES = 500
"""A long sweep would otherwise grow the log without bound."""


class CrossbarApp(App):
    """Connect models, run a sweep, read the verdict."""

    TITLE = "crossbar"

    CSS = """
    #run-current { height: auto; padding: 1 0; }
    #run-error { height: auto; color: $error; }
    #run-progress { height: auto; }
    #run-queue-scroll { height: 1fr; }
    #run-log-scroll { height: 40%; border-top: solid $panel; }
    #results-report-scroll { height: 50%; }
    #results-browser { height: 1fr; border-top: solid $panel; }
    #results-list { width: 46; border-right: solid $panel; }
    #judge-controls { height: 3; padding: 0 1; }
    #judge-controls Label { padding: 1 1 0 0; }
    #judge-count { width: 8; }
    #judge-count-total { width: auto; }
    .pane-body { height: auto; }
    """

    BINDINGS = [
        Binding("r", "run", "Run"),
        Binding("1", "show_tab('models')", "Models"),
        Binding("2", "show_tab('tests')", "Tests"),
        Binding("3", "show_tab('run')", "Run view"),
        Binding("4", "show_tab('results')", "Results"),
        Binding("m", "toggle_mode", "Mode"),
        Binding("d", "dump", "Dump"),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(
        self,
        roster: Roster,
        tests: Sequence[Test],
        results_dir: str,
        judge: Any = None,
        agent_factory: Callable[..., Any] | None = None,
        judge_tests: int = 1,
        mode: str | None = None,
    ) -> None:
        super().__init__()
        self.roster = roster
        self.tests = list(tests)
        self.results_dir = str(results_dir)
        self.judge = judge
        self.agent_factory = agent_factory
        self.judge_tests = judge_tests
        self.mode = mode or ("single" if roster.is_single_model else "comparison")
        """Which models execute. 'single' assesses the model under test on its
        own; 'comparison' runs everything the roster assigns. A roster with one
        executing model can only be in single mode."""

        self.orchestrator: Orchestrator = self._build_orchestrator()
        self.result: RunResult | None = None
        self.analysis: Analysis | None = None

        # Observable state, so a caller — or a test — can wait on the sweep
        # without guessing at timings.
        self.sweep_running = False
        self.sweep_done = False
        self.runs_started = 0
        self.run_error = ""
        self.dump_path = ""
        self.log_lines: list[str] = []

    # -- layout ------------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Header()
        with TabbedContent(initial="models"):
            with TabPane("1 Models", id="models"):
                with VerticalScroll():
                    yield Static(id="models-body", markup=False, classes="pane-body")
            with TabPane("2 Tests", id="tests"):
                with Vertical():
                    # Judging is the expensive part of a run, so when more than
                    # one Test is scheduled the user is asked rather than
                    # having the whole lot judged by default.
                    with Horizontal(id="judge-controls"):
                        yield Label("Tests to judge")
                        yield Input(
                            value=str(self.judge_tests),
                            id="judge-count",
                            classes="narrow",
                        )
                        yield Label(f"of {len(self.tests)}", id="judge-count-total")
                    with VerticalScroll():
                        yield Static(id="tests-body", markup=False, classes="pane-body")
            with TabPane("3 Run", id="run"):
                with Vertical():
                    yield Static(id="run-current", markup=False)
                    yield ProgressBar(id="run-progress", show_eta=False)
                    yield Static(id="run-error", markup=False)
                    with VerticalScroll(id="run-queue-scroll"):
                        yield Static(id="run-queue", markup=False, classes="pane-body")
                    with VerticalScroll(id="run-log-scroll"):
                        yield Static(id="run-log", markup=False, classes="pane-body")
            with TabPane("4 Results", id="results"):
                with VerticalScroll(id="results-report-scroll"):
                    yield Static(id="results-report", markup=False, classes="pane-body")
                with Horizontal(id="results-browser"):
                    yield ListView(id="results-list")
                    with VerticalScroll():
                        yield Static(id="results-detail", markup=False, classes="pane-body")
        yield Footer()

    def on_mount(self) -> None:
        self._set("#models-body", render_roster(self.roster, self.mode, self.active_roles))
        self._set("#tests-body", render_tests(self.tests, self.judge_tests))
        if len(self.tests) < 2:
            self.query_one("#judge-controls").display = False
        self._set("#results-report", "Nothing has been run yet. Press r to start a sweep.")
        self._set("#results-detail", render_attempt(None))
        self._refresh_run_view()

    @property
    def can_compare(self) -> bool:
        """Whether there is a second model to compare against at all."""
        return len(self.roster.execution_roles) > 1

    @property
    def active_roles(self) -> tuple:
        """The roles this mode runs."""
        if self.mode == "single":
            return (self.roster.execution_roles[0],)
        return self.roster.execution_roles

    def action_toggle_mode(self) -> None:
        """Switch between assessing one model and comparing two.

        Refused mid-run: the queue is already built and half-executed, and
        changing what it means partway would make the results incomparable.
        """
        if self.sweep_running:
            return
        if not self.can_compare:
            return
        self.mode = "single" if self.mode == "comparison" else "comparison"
        self.orchestrator = self._build_orchestrator()
        self._set("#models-body", render_roster(self.roster, self.mode, self.active_roles))
        self._refresh_run_view()

    def chosen_judge_tests(self) -> int:
        """How many Tests the user has asked to judge.

        Anything unreadable means none: silently judging everything because a
        field held a typo would spend the user's money without being asked.
        """
        try:
            raw = self.query_one("#judge-count", Input).value
        except (NoMatches, ScreenStackError):
            # Asked before the widget exists: Input.Changed fires during
            # compose. Fall back to what the app was constructed with.
            return self.judge_tests
        try:
            count = int(str(raw).strip())
        except ValueError:
            return 0
        return max(0, min(count, len(self.tests)))

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id != "judge-count" or not self.is_mounted:
            return
        self._set("#tests-body", render_tests(self.tests, self.chosen_judge_tests()))

    # -- actions -----------------------------------------------------------

    def action_show_tab(self, tab: str) -> None:
        self.query_one(TabbedContent).active = tab

    def action_run(self) -> None:
        """Start a sweep, unless one is already going.

        The guard is set here, on the message loop, rather than inside the
        worker: a second keypress must be refused before it can start anything.
        """
        if self.sweep_running:
            self._log("A run is already in progress.")
            return

        self.sweep_running = True
        self.sweep_done = False
        self.run_error = ""
        self.runs_started += 1
        self.orchestrator = self._build_orchestrator()
        self._set("#run-error", "")
        self._log(f"Run {self.orchestrator.run_id} started.")
        self._refresh_run_view()
        self.action_show_tab("run")
        self._sweep(self.orchestrator)

    def action_dump(self) -> None:
        try:
            path = create_dump(self.results_dir)
        except Exception as exc:
            self._log(f"Nothing to dump: {exc}")
            return
        self.dump_path = str(path)
        self._log(f"Dump written to {path}")

    # -- the sweep ---------------------------------------------------------

    @work(thread=True, exit_on_error=False)
    def _sweep(self, orchestrator: Orchestrator) -> None:
        """The whole run, off the message loop."""
        try:
            result = orchestrator.run()
            analysis = analyze(result)
            report = render_report(analysis)
        except Exception as exc:
            # A run that dies must leave the app standing and say what happened.
            self.call_from_thread(self._sweep_failed, f"{type(exc).__name__}: {exc}")
            return
        self.call_from_thread(self._sweep_succeeded, result, analysis, report)

    def _on_run_event(self, event: RunEvent) -> None:
        """Called by the orchestrator, on the worker thread."""
        self.call_from_thread(self._apply_event, event)

    def _apply_event(self, event: RunEvent) -> None:
        if event.kind == "attempt_finished" and event.item is not None:
            self._log(queue_event_line(event.item))
        self._refresh_run_view(event.completed, event.total)

    def _sweep_failed(self, message: str) -> None:
        self.run_error = message
        self._set("#run-error", f"THE RUN FAILED\n\n  {message}")
        self._log(f"Run failed: {message}")
        self.sweep_running = False
        self.sweep_done = True

    async def _sweep_succeeded(
        self, result: RunResult, analysis: Analysis, report: str
    ) -> None:
        self.result = result
        self.analysis = analysis
        self._set("#results-report", report)
        await self._fill_attempt_list(result)
        self._refresh_run_view()
        self._log(
            f"Run finished: {len(result.attempts)} attempts, ${result.total_cost:,.4f}."
        )
        self.sweep_running = False
        self.sweep_done = True  # set last: it is what callers wait on

    # -- results -----------------------------------------------------------

    async def _fill_attempt_list(self, result: RunResult) -> None:
        listing = self.query_one("#results-list", ListView)
        await listing.clear()
        for attempt in result.attempts:
            listing.append(ListItem(Static(attempt_line(attempt), markup=False)))
        listing.index = 0 if result.attempts else None
        self._show_attempt(0 if result.attempts else None)

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        self._show_attempt(event.list_view.index)

    def _show_attempt(self, index: int | None) -> None:
        attempts = self.result.attempts if self.result else ()
        attempt = attempts[index] if index is not None and 0 <= index < len(attempts) else None
        self._set("#results-detail", render_attempt(attempt))

    # -- painting ----------------------------------------------------------

    def _refresh_run_view(self, completed: int | None = None, total: int | None = None) -> None:
        queue = self.orchestrator.queue
        done, planned = self.orchestrator.progress()
        running = next((item for item in queue if item.state == "running"), None)

        self._set("#run-current", render_now_running(running))
        self._set("#run-queue", render_queue(queue))
        bar = self.query_one("#run-progress", ProgressBar)
        bar.update(total=total if total is not None else planned,
                   progress=completed if completed is not None else done)

    def _log(self, line: str) -> None:
        if not line:
            return
        self.log_lines.append(f"{time.strftime('%H:%M:%S')}  {line}")
        del self.log_lines[:-MAX_LOG_LINES]
        self._set("#run-log", "\n".join(self.log_lines))

    def _set(self, selector: str, text: str) -> None:
        self.query_one(selector, Static).update(text)

    # -- wiring ------------------------------------------------------------

    def _build_orchestrator(self) -> Orchestrator:
        return Orchestrator(
            roster=self.roster,
            tests=self.tests,
            results_dir=self.results_dir,
            judge=self.judge,
            agent_factory=self.agent_factory,
            on_event=self._on_run_event,
            judge_tests=self.chosen_judge_tests(),
            roles=self.active_roles,
        )


def build_app(
    roster_path: str,
    test_paths: Sequence[str],
    results_dir: str | None = None,
    judge: Any = None,
    judge_tests: int = 1,
) -> CrossbarApp:
    """Load a roster and some Tests from disk and wire up the app.

    A judge is built from the roster unless one is supplied. Without it a run
    would produce no Check Plan, no evidence and no verdict — which is not what
    anyone opening the app is asking for.
    """
    roster = load_roster(roster_path)
    tests = [load_test(path, known_connectors=known_connectors()) for path in test_paths]
    target = results_dir or str(Path("runs") / time.strftime("%Y%m%d-%H%M%S"))
    if judge is None:
        judge_model = roster.assigned(Role.JUDGE)
        judge = Judge(build_provider(judge_model), model_id=judge_model.id)
    return CrossbarApp(
        roster=roster,
        tests=tests,
        results_dir=target,
        judge=judge,
        judge_tests=judge_tests,
    )


def run_app(
    roster_path: str,
    test_paths: Sequence[str],
    results_dir: str | None = None,
    judge: Any = None,
    judge_tests: int = 1,
) -> None:
    """Open the terminal app on a roster and a set of Tests."""
    build_app(roster_path, test_paths, results_dir, judge, judge_tests).run()
