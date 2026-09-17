"""The crossbar command line.

Five verbs: validate what you wrote, run the sweep, read the report, check the
machine, and scaffold a starting point.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from dataclasses import replace
from pathlib import Path

from crossbar.analysis import analyze
from crossbar.config import Config, ConfigError, load_config
from crossbar.report import render_report, render_verdict, write_markdown
from crossbar.runner import RunEvent, Runner, load_sweep
from crossbar.tasks import TaskValidationError, load_pack

DEFAULT_CONFIG = "crossbar.yaml"
DEFAULT_PACK = "taskpacks/support-triage"
PACKAGE_ROOT = Path(__file__).resolve().parent


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])
    if not getattr(args, "command", None):
        parser.print_usage()
        print("\nStart with:  crossbar run        (uses the built-in demo, no API key needed)")
        return 2
    return args.handler(args)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="crossbar",
        description="Sweep models x harnesses over your own MCP tasks, with statistics.",
    )
    sub = parser.add_subparsers(dest="command")

    run = sub.add_parser("run", help="run the sweep and print the verdict")
    _common(run)
    run.add_argument("--repeats", type=int, help="rollouts per cell per task")
    run.add_argument("--concurrency", type=int, help="how many rollouts to run at once")
    run.add_argument("--agent", action="append", help="only run this cell (repeatable)")
    run.add_argument("--out", help="where to write results (default: run.results_dir)")
    run.add_argument("--adaptive", action="store_true", help="spend extra repeats only on contested cells")
    run.add_argument("--max-repeats", type=int, help="ceiling for --adaptive")
    run.add_argument("--json", action="store_true", help="print machine-readable results")
    run.add_argument("--quiet", action="store_true", help="no progress output")
    run.set_defaults(handler=_cmd_run)

    report = sub.add_parser("report", help="re-render a finished sweep")
    report.add_argument("sweep", nargs="?", default="runs/sweep.json")
    report.add_argument("--markdown", help="also write the report to this path")
    report.set_defaults(handler=_cmd_report)

    validate = sub.add_parser("validate", help="check the roster and the task pack")
    _common(validate)
    validate.set_defaults(handler=_cmd_validate)

    doctor = sub.add_parser("doctor", help="check this machine can run what you configured")
    doctor.add_argument("--config", default=DEFAULT_CONFIG)
    doctor.set_defaults(handler=_cmd_doctor)

    tui = sub.add_parser("tui", help="the interactive terminal app")
    _common(tui)
    tui.set_defaults(handler=_cmd_tui)

    init = sub.add_parser("init", help="write a starter config and demo task pack")
    init.add_argument("directory", nargs="?", default=".")
    init.set_defaults(handler=_cmd_init)
    return parser


def _common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", default=DEFAULT_CONFIG, help="roster file")
    parser.add_argument("--pack", default=DEFAULT_PACK, help="task pack directory")


# -- commands --------------------------------------------------------------


def _cmd_validate(args) -> int:
    loaded = _load(args.config, args.pack)
    if loaded is None:
        return 1
    config, pack = loaded
    print(f"Config   {args.config}: {len(config.models)} model(s), {len(config.harnesses)} harness(es)")
    print(f"Pack     {args.pack}: {len(pack)} tasks")
    print(f"Matrix   {len(config.agents)} cells x {len(pack)} tasks x {config.run.repeats} repeats "
          f"= {len(config.agents) * len(pack) * config.run.repeats} rollouts")
    print()
    for agent in config.agents:
        marker = " (baseline)" if agent.id == config.run.baseline else ""
        print(f"  - {agent.id}{marker}")
    print()
    for task in pack:
        print(f"  * {task.id}: {len(task.checks)} check(s), {len(task.environment.servers)} server(s)")
    return 0


def _cmd_run(args) -> int:
    loaded = _load(args.config, args.pack)
    if loaded is None:
        return 1
    config, pack = loaded

    config = _apply_overrides(config, args)
    if args.agent:
        unknown = [a for a in args.agent if a not in {c.id for c in config.agents}]
        if unknown:
            print(f"error: unknown agent(s): {', '.join(unknown)}")
            print(f"       available: {', '.join(c.id for c in config.agents)}")
            return 1
        config = replace(config, agents=tuple(c for c in config.agents if c.id in set(args.agent)))
        if config.run.baseline not in {c.id for c in config.agents}:
            config = replace(config, run=replace(config.run, baseline=config.agents[0].id))

    results_dir = args.out or config.run.results_dir
    quiet = args.quiet or args.json  # machine-readable output must stay parseable
    runner = Runner(
        config,
        pack,
        results_dir=results_dir,
        on_event=None if quiet else _progress,
    )
    sweep = runner.run(adaptive=args.adaptive, max_repeats=args.max_repeats)
    if not quiet:
        print()

    analysis = analyze(sweep)
    write_markdown(analysis, Path(results_dir) / "report.md")

    if args.json:
        print(json.dumps(_as_json(analysis, sweep), indent=2))
    else:
        print(render_report(analysis))
        print()
        print(f"Results   {Path(results_dir) / 'sweep.json'}")
        print(f"Report    {Path(results_dir) / 'report.md'}")
        print(f"Traces    {Path(results_dir) / 'traces'}")
    return 0


def _cmd_report(args) -> int:
    path = Path(args.sweep)
    if not path.exists():
        print(f"error: sweep file not found: {path}")
        print("       run `crossbar run` first, or pass the path to a sweep.json")
        return 1
    analysis = analyze(load_sweep(path))
    print(render_report(analysis))
    if args.markdown:
        written = write_markdown(analysis, args.markdown)
        print(f"\nWrote {written}")
    return 0


def _cmd_doctor(args) -> int:
    print("crossbar doctor\n")
    print(f"  python       {sys.version.split()[0]}  ({sys.executable})")
    docker = shutil.which("docker")
    print(f"  docker       {docker or 'not found - only environments of kind: local will work'}")
    claude = shutil.which("claude")
    print(f"  claude CLI   {claude or 'not found - the claude-code harness will not run'}")
    print()

    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(f"  config       {exc}")
        return 0

    print(f"  config       {args.config}")
    for model in config.models:
        if model.provider == "mock":
            state = "ready (built-in demo backend, no key needed)"
        elif model.provider == "claude-cli":
            state = "ready (uses the claude CLI)" if claude else "needs the claude CLI on PATH"
        elif model.api_key():
            state = "ready"
        elif model.api_key_env:
            state = f"no key: set {model.api_key_env} in your environment"
        else:
            state = "no api_key_env set (fine if your endpoint needs no key)"
        print(f"    - {model.id:<22} {state}")
    return 0


def _cmd_tui(args) -> int:
    from crossbar.tui import run_app

    return run_app(config_path=args.config, pack_path=args.pack)


def _cmd_init(args) -> int:
    target = Path(args.directory)
    target.mkdir(parents=True, exist_ok=True)
    config_path = target / DEFAULT_CONFIG
    if config_path.exists():
        print(f"error: {config_path} already exists; not overwriting it")
        return 1

    source_config = _find_asset(DEFAULT_CONFIG)
    source_pack = _find_asset(DEFAULT_PACK)
    if source_config is None or source_pack is None:
        print("error: could not locate the bundled starter files")
        return 1

    shutil.copyfile(source_config, config_path)
    pack_target = target / DEFAULT_PACK
    pack_target.mkdir(parents=True, exist_ok=True)
    for entry in sorted(Path(source_pack).glob("*.yaml")):
        shutil.copyfile(entry, pack_target / entry.name)

    print(f"Wrote {config_path}")
    print(f"Wrote {pack_target}/ ({len(list(pack_target.glob('*.task.yaml')))} tasks)")
    print("\nNext:  crossbar run")
    return 0


# -- helpers ---------------------------------------------------------------


def _load(config_path: str, pack_path: str):
    try:
        config = load_config(config_path)
    except ConfigError as exc:
        print(f"error: {exc}")
        return None
    try:
        pack = load_pack(pack_path)
    except TaskValidationError as exc:
        print(f"error: {exc}")
        return None
    return config, pack


def _apply_overrides(config: Config, args) -> Config:
    run = config.run
    if getattr(args, "repeats", None):
        run = replace(run, repeats=args.repeats)
    if getattr(args, "concurrency", None):
        run = replace(run, concurrency=args.concurrency)
    return replace(config, run=run)


def _progress(event: RunEvent) -> None:
    if event.kind != "rollout_finished" or event.record is None:
        return
    mark = "pass" if event.record.score.passed else "fail"
    line = (
        f"  [{event.completed:>3}/{event.total:<3}] {event.record.agent_id:<32} "
        f"{event.record.task_id:<20} {mark}"
    )
    print(line, flush=True)


def _as_json(analysis, sweep) -> dict:
    verdict = analysis.verdict
    return {
        "pack": analysis.pack_name,
        "tasks": list(analysis.task_ids),
        "seed": analysis.seed,
        "verdict": {
            "baseline": verdict.baseline.agent_id,
            "winner": verdict.winner.agent_id,
            "recommend_switch": verdict.recommend_switch,
            "savings_usd": verdict.savings_usd,
            "savings_pct": verdict.savings_pct,
            "confidence": verdict.confidence,
            "confidence_note": verdict.confidence_note,
            "watch_outs": list(verdict.watch_outs),
            "delta": None if verdict.comparison is None else {
                "agent_id": verdict.comparison.agent_id,
                "delta": verdict.comparison.delta,
                "ci_low": verdict.comparison.ci_low,
                "ci_high": verdict.comparison.ci_high,
                "p_adjusted": verdict.comparison.p_adjusted,
                "significant": verdict.comparison.significant,
            },
        },
        "cells": [
            {
                "agent_id": cell.agent_id,
                "pass_rate": cell.pass_rate,
                "ci_low": cell.ci_low,
                "ci_high": cell.ci_high,
                "mean_score": cell.mean_score,
                "total_cost": cell.total_cost,
                "cost_per_success": cell.cost_per_success,
                "rollouts": cell.n,
                "failures": dict(cell.failure_tally),
                "noise_share": cell.noise_share,
            }
            for cell in analysis.cells
        ],
        "uninformative_tasks": list(analysis.uninformative_tasks),
        "total_cost": sweep.total_cost,
    }


def _find_asset(relative: str) -> Path | None:
    """Starter files live beside the package in a source checkout."""
    for base in (PACKAGE_ROOT, PACKAGE_ROOT.parent, PACKAGE_ROOT.parent.parent):
        candidate = base / relative
        if candidate.exists():
            return candidate
    return None


if __name__ == "__main__":
    raise SystemExit(main())
