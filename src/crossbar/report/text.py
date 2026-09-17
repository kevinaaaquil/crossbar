"""Plain-text rendering, shared by the CLI and the TUI.

The verdict card is the product's actual output: one screen a decision-maker
can read, stating a money number, saying plainly when a difference is not real,
and pointing at the harness fix suggested by the failure taxonomy.
"""

from __future__ import annotations

import os
from pathlib import Path

from crossbar.analysis import Analysis, CellSummary, Comparison

WIDTH = 78


def render_verdict(analysis: Analysis) -> str:
    verdict = analysis.verdict
    pack = analysis.pack_name or "task pack"
    lines = [
        f"CROSSBAR VERDICT{' ' * max(1, WIDTH - 16 - len(pack) - 18)}"
        f"{pack}, {len(analysis.task_ids)} tasks",
        "",
        _agent_block("BASELINE", verdict.baseline),
        "",
        _agent_block("WINNER  ", verdict.winner),
        "",
    ]

    comparison = verdict.comparison
    if comparison is not None:
        delta_pp = comparison.delta * 100
        lines.append(f"  QUALITY DELTA   {comparison.agent_id} vs baseline")
        lines.append(
            f"                  {delta_pp:+.1f}pp   "
            f"[{comparison.ci_low * 100:+.1f} to {comparison.ci_high * 100:+.1f}]"
        )
        verdict_word = "SIGNIFICANT" if comparison.significant else "NOT SIGNIFICANT"
        lines.append(
            f"  {verdict_word} at 95% (paired bootstrap, p={comparison.p_adjusted:.3f} "
            "Holm-corrected)"
        )
        lines.append("")

    if verdict.recommend_switch:
        if comparison is not None and comparison.indistinguishable:
            lines.append("  >> You cannot distinguish these at your task count.")
        else:
            lines.append("  >> The cheaper configuration is at least as good here.")
        lines.append(
            f"     Savings: ${verdict.savings_usd:,.2f} over this sweep "
            f"({verdict.savings_pct:.0f}%)."
        )
    else:
        lines.append("  >> Stay on the baseline. No cheaper cell held its quality.")
    lines.append("")
    lines.extend(
        _wrap(f"{verdict.confidence.title()}. {verdict.confidence_note}", "  CONFIDENCE  ")
    )

    if verdict.watch_outs:
        lines.append("")
        for index, note in enumerate(verdict.watch_outs):
            label = "  WATCH OUT  " if index == 0 else "              "
            lines.extend(_wrap(note, label))

    if all(cell.pass_rate == 0.0 for cell in analysis.cells):
        lines.append("")
        lines.extend(
            _wrap(
                "Every cell failed every task, which usually means the task pack "
                "rather than the models. Check that your checks describe the state "
                "a correct run leaves behind, open a trace in runs/traces/ to see "
                "what the agent actually did, and remember the built-in mock models "
                "only act on a task's demo script - a task without one is a task "
                "they will not attempt.",
                "  NOTHING PASSED  ",
            )
        )
    return "\n".join(lines)


def render_matrix(analysis: Analysis) -> str:
    baseline_id = analysis.verdict.baseline.agent_id
    by_id = {c.agent_id: c for c in analysis.comparisons}

    header = (
        f"{'CELL':<26}{'PASS':>7}  {'95% CI':^16}"
        f"{'COST':>9}{'$/SUCCESS':>11}  NOTE"
    )
    lines = ["MATRIX", "", header, "-" * WIDTH]
    for cell in analysis.cells:
        comparison = by_id.get(cell.agent_id)
        note = _row_note(cell.agent_id, baseline_id, comparison)
        per_success = (
            f"${cell.cost_per_success:,.2f}" if cell.cost_per_success is not None else "n/a"
        )
        lines.append(
            f"{cell.agent_id[:25]:<26}"
            f"{cell.pass_rate * 100:>6.1f}%  "
            f"[{cell.ci_low * 100:>5.1f} - {cell.ci_high * 100:>5.1f}]"
            f"{cell.total_cost:>8,.2f}"
            f"{per_success:>11}  {note}"
        )
    lines.append("")
    lines.append("  ~ marks a cell whose interval overlaps the baseline: not separable.")
    return "\n".join(lines)


def render_failures(analysis: Analysis) -> str:
    lines = ["FAILURE TAXONOMY", ""]
    any_failures = False
    for cell in analysis.cells:
        tally = dict(cell.failure_tally)
        if not tally:
            continue
        any_failures = True
        lines.append(f"  {cell.agent_id}  ({cell.n} rollouts)")
        for mode, count in sorted(tally.items(), key=lambda kv: -kv[1]):
            share = count / cell.n * 100 if cell.n else 0.0
            lines.append(f"    {_mode_label(mode):<24}{count:>4}  ({share:.0f}% of rollouts)")
        lines.append("")
    if not any_failures:
        lines.append("  No failures recorded: every rollout passed every check.")
        lines.append("")

    for cell in analysis.cells:
        if cell.noise_share > 0.5 and cell.n > cell.n_tasks:
            lines.extend(
                _wrap(
                    f"{cell.agent_id}: {cell.noise_share * 100:.0f}% of score variance is "
                    "run-to-run noise rather than task difficulty. Add repeats before "
                    "trusting this cell's ranking.",
                    "  NOISE  ",
                )
            )
    if analysis.uninformative_tasks:
        lines.append("")
        lines.extend(
            _wrap(
                "Tasks that every cell passed or every cell failed, so they separate "
                "nothing and cost money: " + ", ".join(analysis.uninformative_tasks),
                "  PRUNE  ",
            )
        )
    return "\n".join(lines)


def render_report(analysis: Analysis) -> str:
    return "\n\n".join(
        [render_verdict(analysis), render_matrix(analysis), render_failures(analysis)]
    )


def write_markdown(analysis: Analysis, path: str | os.PathLike[str]) -> Path:
    """Same report, fenced so it can be pasted into a PR or a doc."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    body = render_report(analysis)
    target.write_text(f"# Crossbar report: {analysis.pack_name or 'sweep'}\n\n```\n{body}\n```\n")
    return target


# -- helpers ---------------------------------------------------------------


def _agent_block(label: str, cell: CellSummary) -> str:
    head = (
        f"  {label}   {cell.agent_id}\n"
        f"             {cell.pass_rate * 100:.1f}% pass  "
        f"[{cell.ci_low * 100:.1f} - {cell.ci_high * 100:.1f}]    "
        f"${cell.total_cost:,.2f} over this sweep"
    )
    return head


def _row_note(agent_id: str, baseline_id: str, comparison: Comparison | None) -> str:
    if agent_id == baseline_id:
        return "baseline"
    if comparison is None:
        return ""
    if comparison.indistinguishable:
        return "~ tied with baseline"
    return "better" if comparison.delta > 0 else "worse"


def _mode_label(mode: str) -> str:
    return {
        "security": "Security violation",
        "contract_format": "Contract / format",
        "tool_recovery": "Tool / recovery",
        "evidence_grounding": "Evidence / grounding",
        "artifact_commitment": "Artifact commitment",
        "state_continuation": "State / continuation",
    }.get(mode, mode)


def _wrap(text: str, label: str) -> list[str]:
    """Wrap a note under a label column, keeping every line inside the width."""
    indent = " " * len(label)
    words = text.split()
    lines: list[str] = []
    current = label
    for word in words:
        if len(current) + len(word) + 1 > WIDTH:
            lines.append(current.rstrip())
            current = indent
        current += word + " "
    if current.strip():
        lines.append(current.rstrip())
    return lines
