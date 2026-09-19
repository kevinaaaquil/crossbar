"""Running the matrix.

Per Test: derive every Check Plan first, then run the Candidate's Attempts, then
the Baseline's. Every Attempt gets a fresh Environment. Nothing that goes wrong
inside an Attempt escapes — a run that dies on Task 7 of 80 is worse than
useless.
"""

from __future__ import annotations

import time
import traceback
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Sequence

from crossbar.agents import ModelAgent
from crossbar.connectors import build_connector
from crossbar.domain import Role, Task, Test
from crossbar.environment import build_environment
from crossbar.evidence import Evidence, capture
from crossbar.judging import (
    CheckPlan,
    Judgement,
    Outcome,
    ProbeCatalogue,
    load_plan,
)
from crossbar.orchestrator.queue import QueueItem
from crossbar.orchestrator.results import Attempt, RunResult, load_run
from crossbar.roster import Roster, RosterError, build_provider, cost_usd
from crossbar.trace import RunStatus, Trajectory

AgentFactory = Callable[[str, Role, Task, int], Any]

JUDGE_TESTS_BY_DEFAULT = 1
"""Judging is the expensive part of an eval bill, so the default must not
quietly spend the user's money on every Test they scheduled."""


@dataclass(frozen=True)
class RunEvent:
    """Progress, for whatever is watching."""

    kind: str
    item: QueueItem | None = None
    queue: tuple[QueueItem, ...] = ()
    completed: int = 0
    total: int = 0
    message: str = ""


class Orchestrator:
    def __init__(
        self,
        roster: Roster,
        tests: Sequence[Test],
        results_dir: str,
        judge: Any = None,
        agent_factory: AgentFactory | None = None,
        on_event: Callable[[RunEvent], None] | None = None,
        judge_tests: int = JUDGE_TESTS_BY_DEFAULT,
        roles: Sequence[Role] | None = None,
    ) -> None:
        self.roster = roster
        self.roles = tuple(roles) if roles is not None else roster.execution_roles
        """Which roles execute. Defaults to everything the roster assigns;
        narrowing it is how a single-model run is chosen without editing the
        roster. Validated now rather than mid-run."""
        unrunnable = [r for r in self.roles if r not in roster.execution_roles]
        if unrunnable:
            raise RosterError(
                "cannot run "
                + ", ".join(r.value for r in unrunnable)
                + "; only roles the roster assigns a model to can execute ("
                + ", ".join(r.value for r in roster.execution_roles)
                + ")"
            )
        self.tests = list(tests)
        self.results_dir = Path(results_dir)
        self.judge = judge
        self.agent_factory = agent_factory or self._default_agent
        self.on_event = on_event or (lambda event: None)
        self.judge_tests = judge_tests
        self.run_id = uuid.uuid4().hex[:12]
        self.queue: list[QueueItem] = self._build_queue()

    # -- planning ----------------------------------------------------------

    def _build_queue(self) -> list[QueueItem]:
        """Everything that will run, in the order it will run."""
        items: list[QueueItem] = []
        for test in self.tests:
            for role in self.roles:
                model = self.roster.assigned(role)
                for task in test.tasks:
                    for repeat in range(test.repeats):
                        items.append(
                            QueueItem(
                                test_name=test.name,
                                task_id=task.id,
                                model_id=model.id,
                                role=role,
                                repeat=repeat,
                            )
                        )
        return items

    def progress(self) -> tuple[int, int]:
        done = sum(1 for i in self.queue if i.state in ("done", "failed"))
        return done, len(self.queue)

    @property
    def judged_test_names(self) -> tuple[str, ...]:
        if self.judge is None:
            return ()
        return tuple(t.name for t in self.tests[: max(0, self.judge_tests)])

    # -- running -----------------------------------------------------------

    def run(self) -> RunResult:
        started = time.time()
        self._emit("run_started")
        attempts: list[Attempt] = []
        judged = self.judged_test_names

        for test in self.tests:
            plans = self._make_plans(test)
            for role in self.roles:
                model = self.roster.assigned(role)
                for task in test.tasks:
                    for repeat in range(test.repeats):
                        attempt = self._run_attempt(test, task, model, role, repeat, plans)
                        if test.name in judged and self.judge is not None:
                            self._judge_attempt(attempt, plans.get(task.id))
                        self._store(attempt)
                        attempts.append(attempt)

        result = RunResult(
            run_id=self.run_id,
            attempts=tuple(attempts),
            roles={
                role.value: self.roster.assigned(role).id
                for role in (*self.roles, Role.JUDGE)
                if self.roster.roles.get(role.value)
                or (role is Role.JUDGE and not self.roster.is_single_model)
            },
            judged_tests=judged,
            judge_is_baseline=self._judge_is_baseline(),
            started_at=started,
            finished_at=time.time(),
            results_dir=str(self.results_dir),
        )
        result.write(self.results_dir / "run.json")
        self._emit("run_finished")
        return result

    def _judge_is_baseline(self) -> bool:
        """Whether the model that judged is also the Baseline.

        Established from the judge actually used, not from what the roster would
        have fallen back to — an injected judge may be something else entirely,
        and claiming a conflict that does not exist is as misleading as hiding
        one that does.
        """
        judging_model = getattr(self.judge, "model_id", None)
        if judging_model is None:
            return False
        return judging_model == self.roster.assigned(Role.BASELINE).id

    def _make_plans(self, test: Test) -> dict[str, CheckPlan]:
        """One plan per Task, before any Attempt of that Test runs.

        Derived whenever a judge is available, **even when grading is switched
        off**: the plan is what says which evidence to capture, so a run without
        one can never be judged later without being re-run. Planning is one call
        per Task; grading is one per Attempt, which is where the cost lives.

        The Environment is brought up once here purely to read the probe
        catalogue, so the judge can only ask for evidence something can supply.
        """
        if self.judge is None:
            return {}
        plans: dict[str, CheckPlan] = {}
        for task in test.tasks:
            environment = build_environment(task.environment or test.environment)
            connectors: list[Any] = []
            try:
                handle = environment.start()
                connectors = self._connectors(task, test, handle)
                catalogue = ProbeCatalogue.from_connectors(connectors)
            except Exception:
                catalogue = ProbeCatalogue()
            finally:
                for connector in connectors:
                    connector.teardown()
                environment.stop()

            try:
                plan = self.judge.make_plan(task, catalogue)
            except Exception as exc:
                plan = CheckPlan(task_id=task.id, unsatisfiable=(f"planning failed: {exc}",))
            plans[task.id] = plan
            plan.write(self.results_dir / "plans" / f"{task.id}.json")
        return plans

    def _run_attempt(
        self, test: Test, task: Task, model, role: Role, repeat: int, plans
    ) -> Attempt:
        item = self._queue_item(test, task, model.id, role, repeat)
        if item is not None:
            item.state = "running"
        self._emit("attempt_started", item)

        attempt = Attempt(
            id=f"{_slug(model.id)}-{_slug(test.name)}-{task.id}-r{repeat}",
            test_name=test.name,
            task_id=task.id,
            model_id=model.id,
            role=role,
            repeat=repeat,
        )
        plan = plans.get(task.id)
        started = time.monotonic()

        environment = build_environment(task.environment or test.environment)
        connectors: list[Any] = []
        try:
            handle = environment.start()
            connectors = self._connectors(task, test, handle)
            agent = self.agent_factory(model.id, role, task, repeat)
            attempt.trajectory = agent.run(task, connectors, repeat=repeat)
            attempt.evidence = capture(
                plan.evidence_requests() if plan else (),
                connectors,
                final_answer=attempt.trajectory.final_text,
            )
            if attempt.trajectory.status is not RunStatus.COMPLETED:
                attempt.error = attempt.trajectory.error or attempt.trajectory.status.value
        except Exception as exc:
            attempt.error = f"{exc}\n{traceback.format_exc(limit=3)}"
            attempt.evidence = attempt.evidence or Evidence(final_answer="")
        finally:
            for connector in connectors:
                try:
                    connector.teardown()
                except Exception:
                    pass
            environment.stop()

        attempt.wall_time_s = time.monotonic() - started
        if attempt.trajectory is not None:
            attempt.usage = attempt.trajectory.usage
            attempt.cost_usd = attempt.trajectory.reported_cost_usd or cost_usd(
                attempt.usage, model.price
            )

        if item is not None:
            item.state = "failed" if attempt.error else "done"
        done, total = self.progress()
        self._emit("attempt_finished", item, completed=done, total=total)
        return attempt

    def _judge_attempt(self, attempt: Attempt, plan: CheckPlan | None) -> None:
        if attempt.error and attempt.trajectory is None:
            attempt.judgement = Judgement(outcome=Outcome.FAILED, error=attempt.error)
            return
        if plan is None:
            attempt.judgement = Judgement(
                outcome=Outcome.UNCHECKED, error="no check plan was produced for this task"
            )
            return
        if attempt.error:
            attempt.judgement = Judgement(outcome=Outcome.FAILED, error=attempt.error)
            return
        try:
            attempt.judgement = self.judge.grade(plan, attempt.evidence or Evidence(""))
        except Exception as exc:
            attempt.judgement = Judgement(outcome=Outcome.FAILED, error=f"judging failed: {exc}")

    # -- storage -----------------------------------------------------------

    def _store(self, attempt: Attempt) -> None:
        directory = self.results_dir / "attempts" / attempt.id
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "attempt.json").write_text(_json(attempt.to_dict()))
        if attempt.trajectory is not None:
            attempt.trajectory.write(directory / "trajectory.json")
        if attempt.evidence is not None:
            attempt.evidence.write(directory / "evidence.json")
        if attempt.judgement is not None:
            attempt.judgement.write(directory / "judgement.json")

    # -- re-judging --------------------------------------------------------

    @staticmethod
    def judge_stored(
        results_dir: str | Path,
        judge: Any,
        tests: Sequence[str] | None = None,
    ) -> RunResult:
        """Judge a stored run without re-running it.

        Reuses each Task's stored Check Plan. Regenerating it would silently
        change the criteria and make old and new scores incomparable.
        """
        root = Path(results_dir)
        result = load_run(root / "run.json")
        wanted = set(tests) if tests is not None else {a.test_name for a in result.attempts}
        plans: dict[str, CheckPlan] = {}

        judged: set[str] = set(result.judged_tests)
        for attempt in result.attempts:
            if attempt.test_name not in wanted:
                continue
            if attempt.task_id not in plans:
                plan_path = root / "plans" / f"{attempt.task_id}.json"
                if not plan_path.exists():
                    continue
                plans[attempt.task_id] = load_plan(plan_path)

            if attempt.error:
                attempt.judgement = Judgement(outcome=Outcome.FAILED, error=attempt.error)
            else:
                try:
                    attempt.judgement = judge.grade(
                        plans[attempt.task_id], attempt.evidence or Evidence("")
                    )
                except Exception as exc:
                    attempt.judgement = Judgement(
                        outcome=Outcome.FAILED, error=f"judging failed: {exc}"
                    )
            attempt.judgement.write(root / "attempts" / attempt.id / "judgement.json")
            judged.add(attempt.test_name)

        result.judged_tests = tuple(sorted(judged))
        result.write(root / "run.json")
        return result

    # -- helpers -----------------------------------------------------------

    def _connectors(self, task: Task, test: Test, handle) -> list[Any]:
        spec = task.environment or test.environment
        connectors = []
        for config in spec.connectors:
            connector = build_connector(config)
            connector.setup(handle)
            connectors.append(connector)
        return connectors

    def _default_agent(self, model_id: str, role: Role, task: Task, repeat: int):
        model = self.roster.model(model_id)
        return ModelAgent(model.id, build_provider(model))

    def _queue_item(self, test, task, model_id, role, repeat) -> QueueItem | None:
        for item in self.queue:
            if (
                item.test_name == test.name
                and item.task_id == task.id
                and item.model_id == model_id
                and item.role is role
                and item.repeat == repeat
            ):
                return item
        return None

    def _emit(self, kind: str, item: QueueItem | None = None, **counts) -> None:
        done, total = self.progress()
        event = RunEvent(
            kind=kind,
            item=item,
            queue=tuple(self.queue),
            completed=counts.get("completed", done),
            total=counts.get("total", total),
        )
        try:
            self.on_event(event)
        except Exception:
            pass  # a noisy observer must not break a run


def _json(data) -> str:
    import json

    return json.dumps(data, indent=2)


def _slug(text: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "-" for c in text).strip("-").lower()
