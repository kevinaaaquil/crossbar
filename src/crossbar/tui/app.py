"""Crossbar's terminal UI.

Three panes: what you are about to run, what is happening, and what it means.
The sweep itself runs on a worker thread so the matrix updates while it works,
and every number on screen comes from the same analysis the CLI prints.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

from textual import work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    ProgressBar,
    RichLog,
    Static,
    TabbedContent,
    TabPane,
)

from crossbar.analysis import analyze
from crossbar.config import ConfigError, load_config
from crossbar.report import render_report, write_markdown
from crossbar.runner import RunEvent, Runner
from crossbar.tasks import TaskValidationError, load_pack
from crossbar.trace import load_trajectory

CELL_COLUMNS = ("cell", "done", "pass", "95% CI", "cost", "note")
ROLLOUT_COLUMNS = ("#", "cell", "task", "repeat", "result", "tools", "cost")


class CrossbarApp(App):
    """One screen, three tabs, one keystroke to run the sweep."""

    CSS = """
    Screen { layout: vertical; }
    #setup-summary { padding: 1 2; }
    #controls { height: auto; padding: 0 2; }
    #controls Input { width: 12; }
    #controls Label { padding: 1 1 0 0; }
    #cells { height: 1fr; }
    #log { height: 1fr; border: solid $panel; }
    #report { padding: 1 2; }
    #trace { padding: 1 2; height: 1fr; }
    #rollouts { height: 12; }
    .muted { color: $text-muted; }
    """

    BINDINGS = [
        ("r", "run_sweep", "Run sweep"),
        ("1", "show_tab('setup')", "Setup"),
        ("2", "show_tab('run')", "Run"),
        ("3", "show_tab('results')", "Results"),
        ("q", "quit", "Quit"),
    ]

    TITLE = "crossbar"
    SUB_TITLE = "model x harness, on your tasks"

    def __init__(
        self,
        config_path: str = "crossbar.yaml",
        pack_path: str = "taskpacks/support-triage",
        repeats: int | None = None,
        results_dir: str | None = None,
    ) -> None:
        super().__init__()
        self.config_path = config_path
        self.pack_path = pack_path
        self.results_dir = results_dir
        self.load_error = ""
        self.config: Any = None
        self.pack: Any = None
        self.sweep: Any = None
        self.analysis: Any = None
        self.sweep_done = False
        self.sweep_running = False
        self.sweeps_started = 0
        self.rollouts_seen = 0
        self._records: list = []

        try:
            self.config = load_config(config_path)
        except ConfigError as exc:
            self.load_error = str(exc)
        try:
            self.pack = load_pack(pack_path)
        except TaskValidationError as exc:
            self.load_error = self.load_error or str(exc)

        if self.config is not None and repeats:
            self.config = replace(self.config, run=replace(self.config.run, repeats=repeats))

    # -- layout ------------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Header()
        with TabbedContent(initial="setup", id="tabs"):
            with TabPane("Setup", id="setup"):
                yield Static(self._summary(), id="setup-summary", markup=False)
                with Horizontal(id="controls"):
                    yield Label("Repeats")
                    yield Input(value=str(self._default_repeats()), id="repeats")
                    yield Label("Concurrency")
                    yield Input(value=str(self._default_concurrency()), id="concurrency")
                    yield Button("Run sweep  (r)", variant="primary", id="run")
                yield DataTable(id="cells")
            with TabPane("Run", id="run-tab"):
                yield ProgressBar(id="progress", show_eta=False)
                yield RichLog(id="log", highlight=False, markup=False, wrap=True)
            with TabPane("Results", id="results"):
                with Vertical():
                    with VerticalScroll():
                        yield Static(self._placeholder(), id="report", markup=False)
                    yield DataTable(id="rollouts")
                    with VerticalScroll():
                        yield Static("Select a rollout to see its trace.", id="trace", markup=False)
        yield Footer()

    def on_mount(self) -> None:
        cells = self.query_one("#cells", DataTable)
        cells.add_columns(*CELL_COLUMNS)
        cells.cursor_type = "row"
        rollouts = self.query_one("#rollouts", DataTable)
        rollouts.add_columns(*ROLLOUT_COLUMNS)
        rollouts.cursor_type = "row"
        self._fill_cells()

    # -- actions -----------------------------------------------------------

    def action_run_sweep(self) -> None:
        if self.load_error:
            self.query_one("#log", RichLog).write(f"Cannot run: {self.load_error}")
            return
        if self.sweep_running:
            return
        self.sweep_running = True
        self.sweeps_started += 1
        self.sweep_done = False
        self.rollouts_seen = 0
        self.query_one("#tabs", TabbedContent).active = "run-tab"
        log = self.query_one("#log", RichLog)
        log.clear()
        log.write(f"Sweeping {len(self.config.agents)} cells over {len(self.pack)} tasks...")
        self._sweep_worker()

    def action_show_tab(self, tab: str) -> None:
        mapping = {"setup": "setup", "run": "run-tab", "results": "results"}
        self.query_one("#tabs", TabbedContent).active = mapping.get(tab, "setup")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "run":
            self.action_run_sweep()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.data_table.id == "rollouts":
            self.show_trace(event.cursor_row)

    # -- the sweep ---------------------------------------------------------

    @work(thread=True, exclusive=True)
    def _sweep_worker(self) -> None:
        config = replace(
            self.config,
            run=replace(
                self.config.run,
                repeats=self.chosen_repeats(),
                concurrency=self.chosen_concurrency(),
            ),
        )
        results_dir = self.results_dir or config.run.results_dir
        runner = Runner(config, self.pack, results_dir=results_dir, on_event=self._on_event)
        try:
            sweep = runner.run()
            analysis = analyze(sweep)
            write_markdown(analysis, Path(results_dir) / "report.md")
        except Exception as exc:  # surface it rather than dying silently
            self.call_from_thread(self._sweep_failed, str(exc))
            return
        self.call_from_thread(self._sweep_finished, sweep, analysis, results_dir)

    def _on_event(self, event: RunEvent) -> None:
        if event.kind == "sweep_started":
            self.call_from_thread(self._start_progress, event.total)
        elif event.kind == "rollout_finished" and event.record is not None:
            self.call_from_thread(self._rollout_finished, event)

    def _start_progress(self, total: int) -> None:
        self.query_one("#progress", ProgressBar).update(total=max(1, total), progress=0)

    def _rollout_finished(self, event: RunEvent) -> None:
        self.rollouts_seen += 1
        record = event.record
        self._records.append(record)
        mark = "PASS" if record.score.passed else "FAIL"
        detail = record.error.splitlines()[0][:60] if record.error else ""
        self.query_one("#log", RichLog).write(
            f"[{event.completed:>3}/{event.total:<3}] {record.agent_id:<34} "
            f"{record.task_id:<18} {mark}  {detail}"
        )
        progress = self.query_one("#progress", ProgressBar)
        progress.update(total=max(1, event.total), progress=event.completed)

    def _sweep_finished(self, sweep, analysis, results_dir: str) -> None:
        self.sweep = sweep
        self.analysis = analysis
        self.sweep_done = True
        self.sweep_running = False
        self.query_one("#report", Static).update(render_report(analysis))
        self._fill_cells()
        self._fill_rollouts()
        self.query_one("#log", RichLog).write(f"\nDone. Results written to {results_dir}/")
        self.query_one("#tabs", TabbedContent).active = "results"

    def _sweep_failed(self, message: str) -> None:
        self.sweep_done = True
        self.sweep_running = False
        self.query_one("#log", RichLog).write(f"\nSweep failed: {message}")

    # -- tables ------------------------------------------------------------

    def _fill_cells(self) -> None:
        table = self.query_one("#cells", DataTable)
        table.clear()
        if self.config is None:
            return
        baseline = self.config.run.baseline
        summaries = {c.agent_id: c for c in (self.analysis.cells if self.analysis else ())}
        comparisons = {c.agent_id: c for c in (self.analysis.comparisons if self.analysis else ())}
        for agent in self.config.agents:
            summary = summaries.get(agent.id)
            note = "baseline" if agent.id == baseline else ""
            if summary is not None:
                comparison = comparisons.get(agent.id)
                if comparison is not None:
                    note = (
                        "~ tied with baseline"
                        if comparison.indistinguishable
                        else ("better" if comparison.delta > 0 else "worse")
                    )
                table.add_row(
                    agent.id,
                    str(summary.n),
                    f"{summary.pass_rate * 100:.0f}%",
                    f"[{summary.ci_low * 100:.0f} - {summary.ci_high * 100:.0f}]",
                    f"${summary.total_cost:,.2f}",
                    note,
                )
            else:
                table.add_row(agent.id, "-", "-", "-", "-", note)

    def _fill_rollouts(self) -> None:
        table = self.query_one("#rollouts", DataTable)
        table.clear()
        if self.sweep is None:
            return
        self._records = list(self.sweep.records)
        for index, record in enumerate(self._records):
            table.add_row(
                str(index),
                record.agent_id,
                record.task_id,
                str(record.repeat),
                "PASS" if record.score.passed else "FAIL",
                str(record.tool_calls),
                f"${record.cost_usd:,.3f}",
            )

    def show_trace(self, index: int) -> None:
        """Render one rollout: what it did, and which checks it satisfied."""
        if not self._records or index >= len(self._records):
            return
        record = self._records[index]
        lines = [
            f"{record.agent_id}   {record.task_id}   repeat {record.repeat}",
            f"status {record.score.status.value}   score {record.score.value:.2f} "
            f"(security {record.score.security:.0f} x completion {record.score.completion:.2f} "
            f"x process {record.score.process:.2f})",
            "",
        ]
        if record.error:
            lines += ["error:", f"  {record.error.splitlines()[0]}", ""]

        lines.append("checks")
        for check in record.score.check_results:
            mark = "PASS" if check.passed else "FAIL"
            lines.append(f"  [{mark}] {check.label}")
            if check.detail and not check.passed:
                lines.append(f"         {check.detail}")
        lines.append("")

        traj = self._load_trajectory(record)
        if traj is None:
            lines.append("(no trace file for this rollout)")
        else:
            lines.append("trajectory")
            for step in traj.steps:
                if step.text:
                    lines.append(f"  step {step.index}: {step.text[:200]}")
            for event in traj.tool_events:
                mark = "error" if event.is_error else "ok"
                blocked = " BLOCKED" if event.blocked else ""
                lines.append(
                    f"  call {event.server}.{event.tool}({_brief_args(event.arguments)})"
                    f" -> {mark}{blocked}"
                )
                if event.result_text:
                    lines.append(f"       {event.result_text.splitlines()[0][:160]}")
            if traj.final_text:
                lines += ["", f"final answer: {traj.final_text[:400]}"]
        self.query_one("#trace", Static).update("\n".join(lines))

    def _load_trajectory(self, record):
        if not record.trajectory_path:
            return None
        base = Path(self.results_dir or (self.config.run.results_dir if self.config else "runs"))
        path = base / record.trajectory_path
        try:
            return load_trajectory(path)
        except (OSError, ValueError):
            return None

    # -- inputs ------------------------------------------------------------

    def chosen_repeats(self) -> int:
        return _positive(self.query_one("#repeats", Input).value, self._default_repeats())

    def chosen_concurrency(self) -> int:
        return _positive(self.query_one("#concurrency", Input).value, self._default_concurrency())

    def _default_repeats(self) -> int:
        return self.config.run.repeats if self.config else 3

    def _default_concurrency(self) -> int:
        return self.config.run.concurrency if self.config else 4

    def _summary(self) -> str:
        if self.load_error:
            return f"Could not load your setup:\n\n  {self.load_error}"
        models = ", ".join(m.id for m in self.config.models)
        harnesses = ", ".join(h.id for h in self.config.harnesses)
        return (
            f"Roster    {self.config_path}\n"
            f"  models      {models}\n"
            f"  harnesses   {harnesses}\n"
            f"  baseline    {self.config.run.baseline}\n\n"
            f"Tasks     {self.pack.name or self.pack_path} - {len(self.pack)} tasks\n"
            f"  {', '.join(t.id for t in self.pack)}\n\n"
            f"Press r to run the sweep."
        )

    def _placeholder(self) -> str:
        return "No results yet. Press r to run the sweep."


def _positive(value: str, fallback: int) -> int:
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        return 1
    return number if number > 0 else 1


def _brief_args(arguments: dict) -> str:
    text = ", ".join(f"{k}={v!r}" for k, v in list(arguments.items())[:3])
    return text if len(text) <= 60 else text[:57] + "..."


def run_app(config_path: str, pack_path: str) -> int:
    CrossbarApp(config_path=config_path, pack_path=pack_path).run()
    return 0
