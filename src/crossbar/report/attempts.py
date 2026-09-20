"""Rendering one Attempt: its verdict, its checks, and what the judge read.

Lives here rather than in the TUI because the terminal app is one front-end,
not the product. The CLI shows the same text, so neither can drift into
describing a run differently from the other.
"""

from __future__ import annotations

from crossbar.orchestrator import Attempt

WIDTH = 78


def _role_value(role) -> str:
    return getattr(role, "value", str(role))


def attempt_line(attempt: Attempt) -> str:
    """The one-line summary used in the results list."""
    return f"{_outcome_word(attempt):<11}{attempt.model_id} · {attempt.task_id} · r{attempt.repeat}"


def _outcome_word(attempt: Attempt) -> str:
    if attempt.judgement is None:
        return "not judged"
    return attempt.judgement.outcome.value


def render_attempt(attempt: Attempt | None) -> str:
    if attempt is None:
        return "  No attempt selected. Run a sweep, then pick one from the list."

    judgement = attempt.judgement
    lines = [
        f"{attempt.model_id} · {_role_value(attempt.role)} · {attempt.test_name} · "
        f"{attempt.task_id} · repeat {attempt.repeat}",
        "",
    ]

    if judgement is None:
        lines.append("  OUTCOME     not judged")
        lines.extend(
            _wrap(
                "This Test was executed but not judged, so it carries no score. Its "
                "evidence is stored and it can be judged without re-running.",
                "              ",
            )
        )
    else:
        score = "" if judgement.outcome.value != "graded" else f"    score {judgement.score:.2f}"
        lines.append(f"  OUTCOME     {judgement.outcome.value}{score}")

    if attempt.error:
        lines.append("")
        lines.extend(_wrap(attempt.error.strip().splitlines()[0], "  ERROR       "))

    # Why an Unchecked Attempt has no score is the thing the user must act on,
    # so it goes above the per-check detail rather than being buried in it.
    if judgement is not None and judgement.unchecked_reason:
        lines.append("")
        lines.extend(_wrap(judgement.unchecked_reason, "  UNCHECKED   "))

    if judgement is not None and judgement.checks:
        lines.append("")
        lines.append("  CHECKS")
        for check in judgement.checks:
            lines.append(f"    {check.check_id:<22}{check.status.value:<11}{check.reason}")

    if judgement is not None and judgement.reasoning:
        lines.append("")
        lines.append("  THE JUDGE'S REASONING")
        for line in judgement.reasoning.splitlines():
            lines.append(f"    {line}")

    evidence = attempt.evidence
    if evidence is not None:
        lines.append("")
        lines.append("  EVIDENCE")
        lines.append("    final answer:")
        for line in (evidence.final_answer or "(nothing was returned)").splitlines():
            lines.append(f"      {line}")
        for item in evidence.items:
            request = item.request
            lines.append("")
            lines.append(f"    {request.label} ({request.connector} · {request.probe})")
            body = item.content if item.content is not None else f"not captured: {item.error}"
            for line in body.splitlines():
                lines.append(f"      {line}")

    lines.append("")
    lines.append(
        f"  {attempt.usage.input_tokens} in / {attempt.usage.output_tokens} out tokens · "
        f"${attempt.cost_usd:,.4f} · {attempt.wall_time_s:.1f}s"
    )
    return "\n".join(lines)


# -- shared ----------------------------------------------------------------


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
