"""Pre-flight checks, run before a sweep is allowed to spend anything.

A run costs money, and the expensive failures are cheap to predict: a key that
is not set, an MCP server that will not launch, an Environment that can show
nothing back. All of it is knowable in a few seconds.

`validate`, `doctor` and `run` all go through here, so the three cannot drift
into disagreeing about whether a setup is sound.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from enum import Enum
from typing import Sequence

from crossbar.connectors import build_connector
from crossbar.domain import Role, Test
from crossbar.environment import build_environment
from crossbar.roster import Roster


class Status(Enum):
    OK = "ok"
    WARN = "warn"
    FAIL = "fail"


@dataclass(frozen=True)
class Check:
    name: str
    status: Status
    detail: str

    @property
    def failed(self) -> bool:
        return self.status is Status.FAIL


@dataclass(frozen=True)
class PreflightReport:
    checks: tuple[Check, ...] = ()
    planned_attempts: int = 0

    @property
    def failures(self) -> tuple[Check, ...]:
        return tuple(c for c in self.checks if c.failed)

    @property
    def warnings(self) -> tuple[Check, ...]:
        return tuple(c for c in self.checks if c.status is Status.WARN)

    @property
    def ok(self) -> bool:
        """Whether a run should be allowed to start.

        Warnings never block. A conflict of interest or a Test that can check
        nothing is worth knowing about, but it is the user's call.
        """
        return not self.failures


def preflight(
    roster: Roster,
    tests: Sequence[Test],
    start_environments: bool = True,
) -> PreflightReport:
    """Check everything that can be checked without running the agents."""
    checks: list[Check] = []
    checks.append(_check_roles(roster))
    checks.append(_check_keys(roster))
    checks.append(_check_judge(roster))
    checks.append(_check_docker(tests))
    cli_check = _check_agent_clis(roster)
    if cli_check is not None:
        checks.append(cli_check)

    for test in tests:
        if start_environments:
            checks.extend(_check_environment(test))

    planned = sum(
        len(test.tasks) * test.repeats * len(roster.execution_roles) for test in tests
    )
    return PreflightReport(checks=tuple(checks), planned_attempts=planned)


# -- individual checks -----------------------------------------------------


def _check_roles(roster: Roster) -> Check:
    try:
        candidate = roster.assigned(Role.CANDIDATE).id
        if roster.is_single_model:
            return Check(
                "roles",
                Status.OK,
                f"{candidate} assessed on its own, judged by "
                f"{roster.assigned(Role.JUDGE).id}",
            )
        baseline = roster.assigned(Role.BASELINE).id
    except Exception as exc:
        return Check("roles", Status.FAIL, str(exc))
    return Check("roles", Status.OK, f"candidate {candidate}, baseline {baseline}")


def _check_keys(roster: Roster) -> Check:
    missing = [m for m in roster.models if m.api_key_env and not m.api_key()]
    if missing:
        detail = "; ".join(f"{m.id} needs {m.api_key_env} to be set" for m in missing)
        return Check("model-keys", Status.FAIL, detail)

    unkeyed = [m.id for m in roster.models if not m.api_key_env and not m.api_key_inline]
    if unkeyed:
        # Local endpoints commonly need no key, so this is worth saying and not
        # worth blocking on.
        return Check(
            "model-keys",
            Status.WARN,
            f"no key configured for {', '.join(unkeyed)} — fine if the endpoint needs none",
        )
    return Check("model-keys", Status.OK, "every model has a key")


def _check_judge(roster: Roster) -> Check:
    if roster.judge_is_baseline:
        return Check(
            "judge",
            Status.WARN,
            f"no judge assigned, so the baseline ({roster.assigned(Role.BASELINE).id}) "
            "will grade its own attempts — blinded, but a conflict of interest",
        )
    return Check("judge", Status.OK, f"judged by {roster.assigned(Role.JUDGE).id}")


def _check_agent_clis(roster: Roster) -> Check | None:
    """Agent CLIs have to exist, and to be able to authenticate.

    crossbar runs them with `--bare` so the operator's own hooks, skills and
    memory do not join the measurement — and `--bare` reads Anthropic
    credentials only from ANTHROPIC_API_KEY, never from an OAuth session. A
    subscription login is not enough, and without the key every Attempt fails
    identically with "Not logged in".
    """
    cli_models = [m for m in roster.models if m.drives_itself]
    if not cli_models:
        return None

    problems = []
    for model in cli_models:
        if shutil.which(model.command) is None and "/" not in model.command:
            problems.append(f"{model.id}: {model.command!r} is not on PATH")
    if not os.environ.get("ANTHROPIC_API_KEY"):
        problems.append(
            "ANTHROPIC_API_KEY is not set, and crossbar runs the CLI with --bare, "
            "which ignores an OAuth login"
        )
    if problems:
        return Check("agent-cli", Status.FAIL, "; ".join(problems))
    return Check(
        "agent-cli",
        Status.OK,
        ", ".join(f"{m.id} via {m.command}" for m in cli_models),
    )


def _check_docker(tests: Sequence[Test]) -> Check:
    needs_docker = [
        test.name
        for test in tests
        if any((t.environment or test.environment).kind == "docker" for t in test.tasks)
    ]
    if not needs_docker:
        return Check("docker", Status.OK, "no test needs docker")
    if shutil.which("docker") is None:
        return Check(
            "docker",
            Status.FAIL,
            f"docker is not on PATH, but these tests need it: {', '.join(needs_docker)}",
        )
    return Check("docker", Status.OK, f"docker available for {', '.join(needs_docker)}")


def _check_environment(test: Test) -> list[Check]:
    """Actually start the Environment and see whether it comes up.

    The single most valuable check here: an MCP server that will not launch
    fails every Attempt, and finding that out after paying for a sweep is the
    expensive way to learn it.
    """
    spec = test.environment
    environment = build_environment(spec)
    connectors: list = []
    try:
        handle = environment.start()
        for config in spec.connectors:
            connector = build_connector(config)
            connector.setup(handle)
            connectors.append(connector)
        tools = [t for c in connectors for t in c.tools()]
        probes = [p for c in connectors for p in c.probes()]
    except Exception as exc:
        for connector in connectors:
            _quietly(connector.teardown)
        environment.stop()
        return [Check(f"environment · {test.name}", Status.FAIL, f"{test.name}: {exc}")]

    for connector in connectors:
        _quietly(connector.teardown)
    environment.stop()

    checks = [
        Check(
            f"environment · {test.name}",
            Status.OK,
            f"{len(connectors)} connector(s), {len(tools)} tools",
        )
    ]
    if probes:
        checks.append(
            Check(f"probes · {test.name}", Status.OK, f"{len(probes)} read-only probe(s)")
        )
    else:
        checks.append(
            Check(
                f"probes · {test.name}",
                Status.WARN,
                "nothing in this environment can be read back, so every attempt "
                "would come back unchecked — add a read-only tool, or declare one "
                "under read_only_tools",
            )
        )
    return checks


def _quietly(action) -> None:
    try:
        action()
    except Exception:
        pass


# -- rendering -------------------------------------------------------------

MARK = {Status.OK: "PASS", Status.WARN: "WARN", Status.FAIL: "FAIL"}


WIDTH = 78


def render_preflight(report: PreflightReport) -> str:
    lines = ["PRE-FLIGHT", ""]
    for check in report.checks:
        lines.append(f"  [{MARK[check.status]}] {check.name}")
        lines.extend(_wrap(check.detail, " " * 9))
    lines.append("")
    lines.append(f"  {report.planned_attempts} attempts would run.")
    if report.failures:
        lines.append("")
        lines.append("  Not starting: fix the failures above, or pass --skip-preflight.")
    return "\n".join(lines)


def _wrap(text: str, indent: str) -> list[str]:
    lines: list[str] = []
    current = indent
    for word in text.split():
        if len(current) + len(word) + 1 > WIDTH:
            lines.append(current.rstrip())
            current = indent
        current += word + " "
    if current.strip():
        lines.append(current.rstrip())
    return lines
