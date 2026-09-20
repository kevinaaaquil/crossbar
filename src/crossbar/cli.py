"""The crossbar command line.

Seven verbs: check what you wrote, run it, judge a stored run, read the report,
zip it up, check the machine, and open the terminal app.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

from crossbar.analysis import analyze
from crossbar.connectors import known_connectors
from crossbar.domain import DomainError, Role, load_test
from crossbar.dump import DumpError, create_dump
from crossbar.judging import Judge
from crossbar.orchestrator import Orchestrator, RunEvent, load_run
from crossbar.preflight import Status, preflight, render_preflight
from crossbar.project import (
    CONFIG_NAME as PROJECT_CONFIG,
    PROJECT_DIR,
    ProjectError,
    init_project,
    load_project,
)
from crossbar.report import attempt_line, render_attempt, render_report, write_markdown
from crossbar.roster import Roster, RosterError, build_provider, load_roster

DEFAULT_ROSTER = "roster.yaml"


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])
    if not getattr(args, "command", None):
        parser.print_usage()
        print("\nStart with:  crossbar init      then:  crossbar validate")
        return 2

    try:
        return args.handler(args)
    except Exception as exc:
        # A stack trace is a bug report, not a user interface. The trace is
        # still one environment variable away when it is actually wanted.
        if os.environ.get("CROSSBAR_TRACEBACK"):
            raise
        print(f"error: {exc}")
        print("\nSet CROSSBAR_TRACEBACK=1 to see the full trace.")
        return 1


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="crossbar",
        description="Compare your own model against a frontier model on your own tasks.",
    )
    sub = parser.add_subparsers(dest="command")

    validate = sub.add_parser("validate", help="check the roster and tests before spending anything")
    _common(validate)
    validate.add_argument("--single", action="store_true",
                          help="report what a single-model run would do")
    validate.set_defaults(handler=_cmd_validate)

    run = sub.add_parser("run", help="run the tests and judge the results")
    _common(run)
    run.add_argument("--out", help="where to write results (default: .crossbar/runs)")
    run.add_argument("--repeats", type=int, help="override each Test's repeat count")
    run.add_argument("--judge-tests", type=int,
                     help="how many Tests to judge (judging is the expensive part)")
    run.add_argument("--no-judge", action="store_true", help="execute without judging")
    run.add_argument("--quiet", action="store_true")
    run.add_argument("--skip-preflight", action="store_true",
                     help="start without checking the setup first")
    run.add_argument("--single", action="store_true",
                     help="assess one model on its own instead of comparing two")
    run.set_defaults(handler=_cmd_run)

    judge = sub.add_parser("judge", help="judge a stored run without re-running it")
    judge.add_argument("run_dir")
    judge.add_argument("--roster", default=DEFAULT_ROSTER)
    judge.add_argument("--test", action="append", dest="tests",
                       help="only judge this Test (repeatable)")
    judge.add_argument("--scripted-pass", action="store_true",
                       help="grade everything as passing, for checking the plumbing")
    judge.set_defaults(handler=_cmd_judge)

    report = sub.add_parser("report", help="render a stored run")
    report.add_argument("run_dir", nargs="?", default="runs")
    report.add_argument("--markdown", help="also write the report here")
    report.add_argument("--json", action="store_true", help="machine-readable output")
    report.set_defaults(handler=_cmd_report)

    attempts = sub.add_parser("attempts", help="list every attempt in a run")
    attempts.add_argument("run_dir", nargs="?", default="runs")
    attempts.add_argument("--failed", action="store_true",
                          help="only attempts that failed or could not be checked")
    attempts.set_defaults(handler=_cmd_attempts)

    attempt = sub.add_parser(
        "attempt", help="show one attempt: its checks, the judge's reasoning, the evidence"
    )
    attempt.add_argument("run_dir")
    attempt.add_argument("attempt_id")
    attempt.set_defaults(handler=_cmd_attempt)

    dump = sub.add_parser("dump", help="zip a run for inspection or review")
    dump.add_argument("run_dir", nargs="?", default="runs")
    dump.add_argument("--out", help="where to write the zip")
    dump.set_defaults(handler=_cmd_dump)

    doctor = sub.add_parser("doctor", help="check this machine can run what you configured")
    doctor.add_argument("--roster", default=DEFAULT_ROSTER)
    doctor.set_defaults(handler=_cmd_doctor)

    demo = sub.add_parser(
        "demo", help="run the shipped example with scripted models, to see the output"
    )
    demo.add_argument("--out", default="runs/demo", help="where to write results")
    demo.add_argument("--repeats", type=int, default=1)
    demo.add_argument("--quiet", action="store_true")
    demo.set_defaults(handler=_cmd_demo)

    init = sub.add_parser("init", help="create a .crossbar project folder here")
    init.add_argument("directory", nargs="?", default=".")
    init.set_defaults(handler=_cmd_init)

    setup = sub.add_parser("setup", help="build the config by being asked, one thing at a time")
    setup.add_argument("--config", help="which config to write (default: this project's)")
    setup.set_defaults(handler=_cmd_setup)

    tui = sub.add_parser("tui", help="the interactive terminal app")
    _common(tui)
    tui.set_defaults(handler=_cmd_tui)
    return parser


def _common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--roster", help="override the project's models")
    parser.add_argument("--test", action="append", dest="tests",
                        help="override the project's Tests (repeatable)")


# -- commands --------------------------------------------------------------


def _cmd_validate(args) -> int:
    loaded = _load(args)
    if loaded is None:
        return 1
    roster, tests = loaded

    project = getattr(args, "project", None)
    source = str(project.root / "config.yaml") if project else args.roster
    print(f"Config   {source}")
    roles = (Role.CANDIDATE, Role.JUDGE) if roster.is_single_model else (
        Role.CANDIDATE, Role.BASELINE, Role.JUDGE
    )
    for role in roles:
        print(f"  {role.value:<10} {roster.assigned(role).id}")
    if roster.is_single_model:
        print("\n  This model is assessed on its own. Assign a baseline to compare")
        print("  it against something.")
    if roster.judge_is_baseline:
        print("\n  Note: the baseline is also the judge, so it will grade its own")
        print("  attempts. They are blinded, but connect a separate judge before")
        print("  relying on this for a decision.")

    roles = (roster.execution_roles[0],) if args.single else roster.execution_roles
    if args.single:
        print(f"\n  Single mode: {roster.assigned(roles[0]).id} assessed on its own.")

    print()
    for test in tests:
        attempts = len(test.tasks) * test.repeats * len(roles)
        print(f"Test     {test.name}: {len(test.tasks)} tasks x {test.repeats} repeats "
              f"= {attempts} attempts")
        for task in test.tasks:
            print(f"  - {task.id}")

    print()
    report = preflight(roster, tests)
    print(render_preflight(report))
    return 0 if report.ok else 1


def _cmd_run(args) -> int:
    loaded = _load(args)
    if loaded is None:
        return 1
    roster, tests = loaded

    if args.repeats:
        tests = [_with_repeats(t, args.repeats) for t in tests]

    project = getattr(args, "project", None)
    roles = (roster.execution_roles[0],) if args.single else None
    results_dir = args.out or (str(project.results_dir) if project else "runs")
    judge_tests = args.judge_tests if args.judge_tests is not None else (
        project.judge_tests if project else 1
    )

    # A run costs money. Check what is cheap to check before committing to it,
    # even if the user has already run validate themselves.
    if not args.skip_preflight:
        report = preflight(roster, tests)
        print(render_preflight(report))
        print()
        if not report.ok:
            return 1

    # Even with --no-judge we still build the judge, because the Check Plan is
    # what tells us which evidence to capture. Skip it and the run cannot be
    # judged later without being re-run, which defeats the point of deferring.
    project = getattr(args, "project", None)
    roles = (roster.execution_roles[0],) if args.single else None
    results_dir = args.out or (str(project.results_dir) if project else "runs")
    judge_tests = args.judge_tests if args.judge_tests is not None else (
        project.judge_tests if project else 1
    )

    judge_model = roster.assigned(Role.JUDGE)
    judge = Judge(build_provider(judge_model), model_id=judge_model.id)
    orchestrator = Orchestrator(
        roster=roster,
        tests=tests,
        results_dir=results_dir,
        judge=judge,
        judge_tests=0 if args.no_judge else judge_tests,
        roles=roles,
        on_event=None if args.quiet else _progress,
    )
    result = orchestrator.run()
    if not args.quiet:
        print()

    analysis = analyze(result)
    write_markdown(analysis, Path(results_dir) / "report.md")
    print(render_report(analysis))
    print(f"\nResults  {Path(results_dir) / 'run.json'}")
    print(f"Report   {Path(results_dir) / 'report.md'}")
    return 0


def _cmd_judge(args) -> int:
    run_dir = Path(args.run_dir)
    if not (run_dir / "run.json").exists():
        print(f"error: no run found at {run_dir}")
        return 1

    if args.scripted_pass:
        judge = _PassEverything()
    else:
        try:
            roster = load_roster(args.roster)
        except RosterError as exc:
            print(f"error: {exc}")
            return 1
        judge_model = roster.assigned(Role.JUDGE)
        judge = Judge(build_provider(judge_model), model_id=judge_model.id)

    result = Orchestrator.judge_stored(run_dir, judge=judge, tests=args.tests)
    print(render_report(analyze(result)))
    return 0


def _cmd_report(args) -> int:
    run_dir = Path(args.run_dir)
    if not (run_dir / "run.json").exists():
        print(f"error: no run found at {run_dir}")
        return 1
    analysis = analyze(load_run(run_dir / "run.json"))
    if args.json:
        print(json.dumps(_as_json(analysis), indent=2))
        return 0
    print(render_report(analysis))
    if args.markdown:
        print(f"\nWrote {write_markdown(analysis, args.markdown)}")
    return 0


def _cmd_attempts(args) -> int:
    result = _stored_run(args.run_dir)
    if result is None:
        return 1
    shown = [
        a for a in result.attempts
        if not args.failed or a.error or (a.judgement and not a.judgement.passed)
    ]
    print(f"ATTEMPTS  {len(shown)} of {len(result.attempts)}\n")
    print(f"  {'ID':<46}{'OUTCOME':<12}{'MODEL':<16}TASK")
    print("  " + "-" * 76)
    for a in shown:
        outcome = "not judged" if a.judgement is None else a.judgement.outcome.value
        print(f"  {a.id[:45]:<46}{outcome:<12}{a.model_id[:15]:<16}{a.task_id}")
    if not shown:
        print("  none")
    print(f"\n  Inspect one with:  crossbar attempt {args.run_dir} <id>")
    return 0


def _cmd_attempt(args) -> int:
    result = _stored_run(args.run_dir)
    if result is None:
        return 1
    found = next((a for a in result.attempts if a.id == args.attempt_id), None)
    if found is None:
        print(f"error: no attempt {args.attempt_id!r} in {args.run_dir}")
        print("\nThe attempts in this run:")
        for a in result.attempts:
            print(f"  {a.id}")
        return 1
    print(render_attempt(found))
    return 0


def _stored_run(run_dir: str):
    path = Path(run_dir) / "run.json"
    if not path.exists():
        print(f"error: no run found at {run_dir}")
        return None
    return load_run(path)


def _cmd_dump(args) -> int:
    try:
        path = create_dump(args.run_dir, args.out)
    except DumpError as exc:
        print(f"error: {exc}")
        return 1
    print(f"Wrote {path}")
    return 0


def _cmd_doctor(args) -> int:
    print("crossbar doctor\n")
    print(f"  python       {sys.version.split()[0]}  ({sys.executable})")
    print(f"  docker       {shutil.which('docker') or 'not found — only kind: local will work'}")
    print(f"  connectors   {', '.join(sorted(known_connectors()))}")
    print()

    try:
        roster = load_roster(args.roster)
    except RosterError as exc:
        print(f"  roster       {exc}")
        return 0

    print(f"  roster       {args.roster}")
    for model in roster.models:
        if model.api_key():
            state = "ready"
        elif model.api_key_env:
            state = f"no key: set {model.api_key_env}"
        else:
            state = "no api_key_env set (fine if the endpoint needs no key)"
        print(f"    - {model.id:<20} {state}")

    # The same checks a run would make, minus anything needing a Test.
    print()
    report = preflight(roster, [], start_environments=False)
    for check in report.checks:
        if check.status is not Status.OK:
            print(f"  [{check.status.value.upper()}] {check.name}: {check.detail}")
    return 0


def _cmd_demo(args) -> int:
    from crossbar.demo import run_demo

    print("Running the shipped example with scripted stand-in models.")
    print("Nothing is connected: this shows the shape of the output, not a measurement.\n")
    result = run_demo(
        args.out, repeats=args.repeats, on_event=None if args.quiet else _progress
    )
    print()
    analysis = analyze(result)
    write_markdown(analysis, Path(args.out) / "report.md")
    print(render_report(analysis))
    print(f"\nResults  {Path(args.out) / 'run.json'}")
    print("\nBoth models here are fixed scripts. Connect real ones in roster.yaml,")
    print("then:  crossbar run --test examples/support-triage")
    return 0


def _cmd_init(args) -> int:
    try:
        created = init_project(args.directory)
    except ProjectError as exc:
        print(f"error: {exc}")
        return 1
    for path in created:
        print(f"Wrote {path}")
    print()
    print(f"Next: edit {PROJECT_DIR}/config.yaml to point at your own models,")
    print("then run:  crossbar validate")
    return 0


def _cmd_setup(args) -> int:
    from crossbar.cli_setup import run_setup
    from crossbar.project import find_project

    if args.config:
        target = Path(args.config)
    else:
        root = find_project()
        target = (root or Path.cwd() / PROJECT_DIR) / PROJECT_CONFIG

    wrote = run_setup(target)
    if wrote:
        print("\nNext:  crossbar validate")
    return 0


def _cmd_tui(args) -> int:
    from crossbar.tui import CrossbarApp

    loaded = _load(args)
    if loaded is None:
        return 1
    roster, tests = loaded

    project = getattr(args, "project", None)
    judge_model = roster.assigned(Role.JUDGE)
    CrossbarApp(
        roster=roster,
        tests=tests,
        results_dir=str(project.results_dir) if project else "runs",
        judge=Judge(build_provider(judge_model), model_id=judge_model.id),
        judge_tests=project.judge_tests if project else 1,
        # Setup writes a file, so it must target the project this app was
        # opened with -- not whichever .crossbar happens to sit above the
        # working directory.
        config_path=(project.root / PROJECT_CONFIG) if project else None,
    ).run()
    return 0


# -- helpers ---------------------------------------------------------------


def _load(args):
    """Explicit flags win; otherwise fall back to the .crossbar project."""
    explicit_roster = getattr(args, "roster", None)
    explicit_tests = getattr(args, "tests", None)

    if not explicit_roster and not explicit_tests:
        try:
            project = load_project()
        except ProjectError as exc:
            print(f"error: {exc}")
            return None
        args.project = project
        return project.roster, list(project.tests)

    try:
        roster = load_roster(explicit_roster or DEFAULT_ROSTER)
    except RosterError as exc:
        print(f"error: {exc}")
        return None
    if not explicit_tests:
        print("error: no tests given; pass --test <directory>")
        return None
    tests = []
    for path in explicit_tests:
        try:
            tests.append(load_test(path, known_connectors=known_connectors()))
        except DomainError as exc:
            print(f"error: {exc}")
            return None
    return roster, tests


def _with_repeats(test, repeats: int):
    return type(test)(
        name=test.name, tasks=test.tasks, environment=test.environment,
        description=test.description, repeats=repeats, path=test.path,
    )


def _progress(event: RunEvent) -> None:
    if event.kind != "attempt_finished" or event.item is None:
        return
    mark = "ok" if event.item.state == "done" else "failed"
    print(
        f"  [{event.completed:>3}/{event.total:<3}] {event.item.model_id:<18} "
        f"{event.item.task_id:<20} {mark}",
        flush=True,
    )


def _as_json(analysis) -> dict:
    verdict = analysis.verdict
    comparison = analysis.comparison
    return {
        "models": [
            {
                "model_id": m.model_id,
                "role": m.role.value,
                "pass_rate": m.pass_rate,
                "ci_low": m.ci_low,
                "ci_high": m.ci_high,
                "attempts": m.n_attempts,
                "graded": m.n_graded,
                "unchecked": m.n_unchecked,
                "failed": m.n_failed,
                "total_cost": m.total_cost,
                "cost_per_success": m.cost_per_success,
            }
            for m in analysis.models
        ],
        "comparison": None if comparison is None else {
            "candidate": comparison.candidate_id,
            "baseline": comparison.baseline_id,
            "delta": comparison.delta,
            "ci_low": comparison.ci_low,
            "ci_high": comparison.ci_high,
            "p_value": comparison.p_value,
            "significant": comparison.significant,
            "n_tasks": comparison.n_tasks,
        },
        "verdict": {
            "recommend_switch": verdict.recommend_switch,
            "savings_usd": verdict.savings_usd,
            "savings_pct": verdict.savings_pct,
            "confidence": verdict.confidence,
            "caveats": list(verdict.caveats),
        },
        "unchecked_reasons": list(analysis.unchecked_reasons),
    }


class _PassEverything:
    """A judge that grades everything as passing, for checking the plumbing.

    It never talks to a model, so it is safe to point at a stored run when you
    only want to know that judging and storage are wired up correctly.
    """

    def make_plan(self, task, catalogue):
        raise NotImplementedError("re-judging always reuses the stored plan")

    def grade(self, plan, evidence):
        from crossbar.judging import (
            CheckOutcome,
            CheckStatus,
            assemble_judgement,
            undecidable_checks,
        )

        undecidable = undecidable_checks(plan, evidence)
        outcomes = [
            CheckOutcome(
                item.id,
                CheckStatus.UNCHECKED if item.id in undecidable else CheckStatus.PASS,
                undecidable.get(item.id, "scripted pass"),
            )
            for item in plan.items
        ]
        return assemble_judgement(tuple(outcomes), "scripted: everything passes")


if __name__ == "__main__":
    raise SystemExit(main())
