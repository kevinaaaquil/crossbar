"""Turning domain objects into the plain text the TUI paints.

Kept separate from the app so every panel's wording is testable without a
terminal, and so the app itself stays a thin layer of wiring. Everything here
returns plain text: the widgets that show it are built with ``markup=False``,
because a Golden, a judge's reasoning or a database dump will contain square
brackets sooner or later.
"""

from __future__ import annotations

from typing import Sequence

from crossbar.domain import Role, Test
from crossbar.orchestrator import Attempt, QueueItem
from crossbar.roster import Roster

WIDTH = 78


def _role_value(role) -> str:
    return getattr(role, "value", str(role))


# -- models ----------------------------------------------------------------


def roles_of(roster: Roster, model_id: str) -> tuple[str, ...]:
    """Every role a model holds, including the Judge it inherits by fallback.

    The fallback matters here: a roster with no judge assigned still has one,
    and hiding that would hide the conflict of interest it creates.
    """
    held = [role.value for role in Role if roster.roles.get(role.value) == model_id]
    if roster.assigned(Role.JUDGE).id == model_id and Role.JUDGE.value not in held:
        held.append("judge (fallback)")
    return tuple(held)


def render_roster(roster: Roster) -> str:
    lines = ["MODELS", "", f"  {'MODEL':<20}{'ROLE':<30}CONNECTION", "  " + "-" * (WIDTH - 2)]
    for model in roster.models:
        held = roles_of(roster, model.id)
        label = " · ".join(held) if held else "unassigned"
        connection = f"{model.provider}/{model.model}"
        lines.append(f"  {model.id[:19]:<20}{label:<30}{connection}")

    lines.append("")
    if roster.judge_is_baseline:
        # Fixed lines rather than a wrapper: this warning is the one piece of
        # text on the screen that must never be quietly reflowed out of shape.
        baseline = roster.assigned(Role.BASELINE)
        lines.extend(
            [
                f"  !! No judge is assigned, so the baseline judges. {baseline.id} will",
                "     grade its own Attempts. That is a conflict of interest, and it is",
                "     structural: blinding is applied — the judge is never told whose",
                "     Attempt it is reading — but models still show self-preference.",
                "     Connect an independent judge before making a decision that matters.",
            ]
        )
    else:
        judge = roster.assigned(Role.JUDGE)
        lines.append(f"  The judge ({judge.id}) is independent of the baseline.")
    return "\n".join(lines)


# -- tests -----------------------------------------------------------------


def judged_names(tests: Sequence[Test], judge_tests: int) -> tuple[str, ...]:
    """The Tests that will be graded: the first ``judge_tests`` of them."""
    return tuple(test.name for test in tests[: max(0, judge_tests)])


def render_tests(tests: Sequence[Test], judge_tests: int) -> str:
    judged = judged_names(tests, judge_tests)
    total_tasks = sum(len(test.tasks) for test in tests)
    lines = [
        "TESTS",
        "",
        f"  {_plural(len(tests), 'test')} · {_plural(total_tasks, 'task')} · "
        f"{len(judged)} of them judged",
        "",
    ]

    for test in tests:
        mark = "JUDGED" if test.name in judged else "NOT JUDGED"
        heading = f"  {test.name}"
        lines.append(f"{heading:<{WIDTH - 12}}{mark:>10}")
        lines.append(
            f"    {_plural(len(test.tasks), 'task')} · "
            f"{_plural(test.repeats, 'repeat')} each"
        )
        if test.description:
            lines.append(f"    {test.description}")
        for task in test.tasks:
            lines.append(f"      - {task.id:<24}{task.name}")
        lines.append("")

    lines.extend(
        [
            "  Judging is opt-in per Test and defaults to the first one only.",
            "  Judging is the expensive part of an eval bill: every extra Test",
            "  judged costs more.",
            "",
            "  A Test that is executed but not judged produces no score, so no",
            "  statistics and no verdict for it. Its Attempts are stored with their",
            "  evidence, and can be judged later without re-running anything.",
        ]
    )
    return "\n".join(lines)


# -- the queue -------------------------------------------------------------


def render_now_running(item: QueueItem | None) -> str:
    if item is None:
        return "  Nothing is running. The queue is idle."
    return (
        f"  RUNNING  {item.model_id} ({_role_value(item.role)})\n"
        f"           {item.test_name} · {item.task_id} · repeat {item.repeat}"
    )


def render_queue(queue: Sequence[QueueItem]) -> str:
    if not queue:
        return "  The queue is empty: no Tests are loaded."
    header = f"  {'#':>3}  {'STATE':<9}{'MODEL':<16}{'ROLE':<11}{'TEST':<20}{'TASK':<20}REPEAT"
    lines = [header, "  " + "-" * (len(header) - 2)]
    for index, item in enumerate(queue, start=1):
        lines.append(
            f"  {index:>3}  {item.state:<9}{item.model_id[:15]:<16}"
            f"{_role_value(item.role)[:10]:<11}{item.test_name[:19]:<20}"
            f"{item.task_id[:19]:<20}{item.repeat}"
        )
    return "\n".join(lines)


def queue_event_line(item: QueueItem | None) -> str:
    """One log line for a finished Attempt."""
    if item is None:
        return ""
    return (
        f"{item.state:<7} {item.model_id} · {item.test_name} · "
        f"{item.task_id} · repeat {item.repeat}"
    )


# -- attempts --------------------------------------------------------------


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


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


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
