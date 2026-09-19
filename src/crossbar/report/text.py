"""Plain-text rendering.

The verdict card is the product's actual output: one screen someone can act on,
stating a money number, saying plainly when a difference is not established, and
never hiding what could not be checked.
"""

from __future__ import annotations

import os
from pathlib import Path

from crossbar.analysis import Analysis, ModelSummary

WIDTH = 78


def render_verdict(analysis: Analysis) -> str:
    if analysis.is_single_model:
        return _render_assessment(analysis)

    verdict = analysis.verdict
    comparison = analysis.comparison
    lines = [
        f"CROSSBAR VERDICT{' ' * 30}{len(analysis.task_ids)} tasks judged",
        "",
    ]

    for summary in sorted(analysis.models, key=lambda m: m.role.value):
        lines.append(_model_block(summary))
        lines.append("")

    if comparison is not None:
        lines.append(
            f"  DIFFERENCE   {comparison.candidate_id} vs {comparison.baseline_id}"
        )
        lines.append(
            f"               {comparison.delta * 100:+.1f}pp   "
            f"[{comparison.ci_low * 100:+.1f} to {comparison.ci_high * 100:+.1f}]"
        )
        word = "SIGNIFICANT" if comparison.significant else "NOT SIGNIFICANT"
        lines.append(
            f"  {word} at 95% (paired bootstrap, p={comparison.p_value:.3f}, "
            f"{comparison.n_tasks} paired tasks)"
        )
        lines.append("")

    if verdict.recommend_switch:
        if comparison is not None and comparison.indistinguishable:
            lines.append("  >> You cannot distinguish these at this task count, and the")
            lines.append("     candidate is cheaper.")
        else:
            lines.append("  >> The candidate is at least as good here, and cheaper.")
        lines.append(
            f"     Switching saves ${verdict.savings_usd:,.2f} over this run "
            f"({verdict.savings_pct:.0f}%)."
        )
    else:
        lines.append("  >> Stay on the baseline. The candidate did not hold its quality.")
    lines.append("")
    lines.extend(_wrap(f"{verdict.confidence.title()}. {verdict.confidence_note}", "  CONFIDENCE  "))

    for index, caveat in enumerate(verdict.caveats):
        lines.append("")
        lines.extend(_wrap(caveat, "  CAVEAT      " if index == 0 else "              "))
    return "\n".join(lines)


def _render_assessment(analysis: Analysis) -> str:
    """One model, assessed on its own.

    No comparison exists, so none of the comparison language belongs here — a
    "difference" or a "saving" against nothing is meaningless, and printing it
    anyway would invite the reader to infer one.
    """
    verdict = analysis.verdict
    lines = [
        f"CROSSBAR ASSESSMENT{' ' * 27}{len(analysis.task_ids)} tasks judged",
        "",
    ]
    for summary in analysis.models:
        rate = "n/a" if summary.pass_rate is None else f"{summary.pass_rate * 100:.1f}% pass"
        interval = (
            ""
            if summary.pass_rate is None
            else f"  [{summary.ci_low * 100:.1f} - {summary.ci_high * 100:.1f}]"
        )
        lines.append(f"  MODEL      {summary.model_id}")
        lines.append(f"             {rate}{interval}")
        lines.append(
            f"             {summary.n_graded} graded · {summary.n_unchecked} unchecked · "
            f"{summary.n_failed} failed"
        )
        per_success = (
            f"${summary.cost_per_success:,.2f} per success"
            if summary.cost_per_success is not None
            else "no successes, so no cost per success"
        )
        lines.append(f"             ${summary.total_cost:,.2f} over this run, {per_success}")
        lines.append("")

    lines.extend(
        _wrap(f"{verdict.confidence.title()}. {verdict.confidence_note}", "  CONFIDENCE  ")
    )
    lines.append("")
    lines.extend(
        _wrap(
            "This model was assessed on its own. Assign a second model to compare "
            "against, and the report will say whether the gap between them is real "
            "or just noise.",
            "  NOTE        ",
        )
    )
    for index, caveat in enumerate(verdict.caveats):
        lines.append("")
        lines.extend(_wrap(caveat, "  CAVEAT      " if index == 0 else "              "))
    return "\n".join(lines)


def render_models(analysis: Analysis) -> str:
    header = (
        f"{'MODEL':<20}{'ROLE':<12}{'PASS':>7}  {'95% CI':^16}"
        f"{'COST':>9}{'$/SUCCESS':>11}  UNCHECKED"
    )
    lines = ["MODELS", "", header, "-" * WIDTH]
    for summary in sorted(analysis.models, key=lambda m: m.role.value):
        rate = "n/a" if summary.pass_rate is None else f"{summary.pass_rate * 100:.1f}%"
        interval = (
            "       n/a      "
            if summary.pass_rate is None
            else f"[{summary.ci_low * 100:>5.1f} - {summary.ci_high * 100:>5.1f}]"
        )
        per_success = (
            f"${summary.cost_per_success:,.2f}" if summary.cost_per_success is not None else "n/a"
        )
        lines.append(
            f"{summary.model_id[:19]:<20}{summary.role.value:<12}{rate:>7}  {interval}"
            f"{summary.total_cost:>8,.2f}{per_success:>11}  "
            f"{summary.n_unchecked}/{summary.n_attempts}"
        )
    lines.append("")
    lines.append("  Unchecked attempts are excluded from the score, not counted as failures.")
    return "\n".join(lines)


def render_report(analysis: Analysis) -> str:
    if not analysis.models:
        return (
            "CROSSBAR REPORT\n\n"
            "  Nothing was judged in this run, so there is no verdict.\n"
            "  Judge a test to get one — the attempts are stored and can be judged\n"
            "  without re-running them."
        )
    sections = [render_verdict(analysis), render_models(analysis)]
    if analysis.unchecked_reasons:
        lines = ["WHY SOME ATTEMPTS COULD NOT BE CHECKED", ""]
        for reason in analysis.unchecked_reasons:
            lines.extend(_wrap(reason, "  - "))
        sections.append("\n".join(lines))
    return "\n\n".join(sections)


def write_markdown(analysis: Analysis, path: str | os.PathLike[str]) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(f"# Crossbar report\n\n```\n{render_report(analysis)}\n```\n")
    return target


def _model_block(summary: ModelSummary) -> str:
    rate = "n/a" if summary.pass_rate is None else f"{summary.pass_rate * 100:.1f}% pass"
    interval = (
        ""
        if summary.pass_rate is None
        else f"  [{summary.ci_low * 100:.1f} - {summary.ci_high * 100:.1f}]"
    )
    return (
        f"  {summary.role.value.upper():<10} {summary.model_id}\n"
        f"             {rate}{interval}    ${summary.total_cost:,.2f} over this run"
    )


def _wrap(text: str, label: str) -> list[str]:
    indent = " " * len(label)
    lines: list[str] = []
    current = label
    for word in text.split():
        if len(current) + len(word) + 1 > WIDTH:
            lines.append(current.rstrip())
            current = indent
        current += word + " "
    if current.strip():
        lines.append(current.rstrip())
    return lines
