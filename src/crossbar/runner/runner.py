"""Executing the matrix.

Every rollout gets its own environment, so no repeat can inherit state from the
one before it. Failures are recorded as zero-scoring records rather than raised:
a sweep that dies on task 7 of 80 is worse than useless.
"""

from __future__ import annotations

import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence

from crossbar.config import AgentSpec, Config, HarnessSpec, ModelSpec, build_harness, build_provider, cost_usd
from crossbar.env import Environment, EnvironmentError_, build_environment
from crossbar.runner.allocator import plan_additional_repeats
from crossbar.runner.results import RunRecord, SweepResult
from crossbar.scoring import TaskScore, score_trajectory
from crossbar.tasks import Task, TaskPack
from crossbar.trace import RunStatus, Trajectory

AgentFactory = Callable[[AgentSpec, ModelSpec, HarnessSpec, Task, int], tuple[Any, Any]]


@dataclass(frozen=True)
class RunEvent:
    """Progress, for whatever is watching (the TUI, a log, a CI job)."""

    kind: str
    agent_id: str = ""
    task_id: str = ""
    repeat: int = 0
    completed: int = 0
    total: int = 0
    record: RunRecord | None = None
    message: str = ""


class Runner:
    def __init__(
        self,
        config: Config,
        pack: TaskPack,
        results_dir: str | None = None,
        agent_factory: AgentFactory | None = None,
        env_factory: Callable[[Task, str], Environment] | None = None,
        on_event: Callable[[RunEvent], None] | None = None,
    ) -> None:
        self.config = config
        self.pack = pack
        self.results_dir = Path(results_dir or config.run.results_dir)
        self.agent_factory = agent_factory or self._default_agent_factory
        self.env_factory = env_factory or (lambda task, workspace: build_environment(task.environment))
        self.on_event = on_event or (lambda event: None)
        self._completed = 0
        self._total = 0

    # -- public API --------------------------------------------------------

    def run(self, adaptive: bool = False, max_repeats: int | None = None) -> SweepResult:
        """Run the full matrix, optionally buying extra repeats where it matters."""
        started = time.time()
        agents = self.config.agents
        tasks = list(self.pack.tasks)
        repeats = self.config.run.repeats
        ceiling = max_repeats or repeats

        self._completed = 0
        self._total = len(agents) * len(tasks) * repeats
        self._emit(RunEvent(kind="sweep_started", total=self._total))

        jobs = [
            (agent, task, repeat)
            for agent in agents
            for task in tasks
            for repeat in range(repeats)
        ]
        records = list(self._execute(jobs))

        extra = 0
        if adaptive:
            extra = self._buy_extra_repeats(records, agents, tasks, repeats, ceiling)

        result = SweepResult(
            records=tuple(records),
            agent_ids=tuple(a.id for a in agents),
            task_ids=tuple(t.id for t in tasks),
            baseline=self.config.run.baseline,
            seed=self.config.run.seed,
            repeats=repeats,
            extra_repeats=extra,
            pack_name=self.pack.name or self.pack.path,
            started_at=started,
            finished_at=time.time(),
        )
        result.write(self.results_dir / "sweep.json")
        self._emit(RunEvent(kind="sweep_finished", completed=self._completed, total=self._total))
        return result

    # -- internals ---------------------------------------------------------

    def _execute(self, jobs: Sequence[tuple[AgentSpec, Task, int]]) -> list[RunRecord]:
        concurrency = max(1, self.config.run.concurrency)
        if concurrency == 1:
            return [self._rollout(*job) for job in jobs]
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            return list(pool.map(lambda job: self._rollout(*job), jobs))

    def _buy_extra_repeats(
        self,
        records: list[RunRecord],
        agents: Sequence[AgentSpec],
        tasks: Sequence[Task],
        repeats: int,
        ceiling: int,
    ) -> int:
        """Keep adding rollouts to contested cells until they separate or run out."""
        extra = 0
        current = repeats
        while current < ceiling:
            outcomes = {
                agent.id: [
                    1.0 if r.score.passed else 0.0 for r in records if r.agent_id == agent.id
                ]
                for agent in agents
            }
            contested = plan_additional_repeats(
                outcomes, max_repeats=ceiling * len(tasks), seed=self.config.run.seed
            )
            if not contested:
                break
            jobs = [
                (agent, task, current)
                for agent in agents
                if agent.id in contested
                for task in tasks
            ]
            if not jobs:
                break
            self._total += len(jobs)
            records.extend(self._execute(jobs))
            extra += len(jobs)
            current += 1
        return extra

    def _rollout(self, agent: AgentSpec, task: Task, repeat: int) -> RunRecord:
        model = self.config.model(agent.model_id)
        harness_spec = self.config.harness(agent.harness_id)
        record = self._run_one(agent, model, harness_spec, task, repeat)
        self._completed += 1
        self._emit(
            RunEvent(
                kind="rollout_finished",
                agent_id=agent.id,
                task_id=task.id,
                repeat=repeat,
                completed=self._completed,
                total=self._total,
                record=record,
            )
        )
        return record

    def _run_one(
        self,
        agent: AgentSpec,
        model: ModelSpec,
        harness_spec: HarnessSpec,
        task: Task,
        repeat: int,
    ) -> RunRecord:
        # Threading task and repeat through keeps seeded backends reproducible:
        # the same sweep seed replays the same rollouts.
        harness, provider = self.agent_factory(agent, model, harness_spec, task, repeat)
        env = self.env_factory(task, "")
        try:
            env.start()
        except EnvironmentError_ as exc:
            return _failed_record(agent, model, harness_spec, task, repeat, f"environment: {exc}")

        workspace = env.workspace
        try:
            traj = harness.run(task, env, provider, repeat=repeat, agent_id=agent.id)
        except Exception as exc:  # a broken harness must not kill the sweep
            env.stop()
            return _failed_record(
                agent, model, harness_spec, task, repeat,
                f"harness {harness_spec.id!r} raised: {exc}\n{traceback.format_exc(limit=3)}",
            )

        try:
            score = self._score(task, traj, env, harness, workspace)
        finally:
            env.stop()

        path = self._write_trajectory(traj, agent, task, repeat)
        return RunRecord(
            agent_id=agent.id,
            model_id=model.id,
            harness_id=harness_spec.id,
            task_id=task.id,
            repeat=repeat,
            score=score,
            usage=traj.usage,
            cost_usd=traj.reported_cost_usd or cost_usd(traj.usage, model.price),
            wall_time_s=traj.wall_time_s,
            tool_calls=traj.tool_call_count,
            trajectory_path=str(path.relative_to(self.results_dir)) if path else "",
            error=traj.error,
        )

    def _score(
        self,
        task: Task,
        traj: Trajectory,
        env: Environment,
        harness: Any,
        workspace: str,
    ) -> TaskScore:
        """Score against the live environment, reconnecting first if the harness
        ran its own copies of the servers (Claude Code does)."""
        if not getattr(harness, "owns_servers", False):
            return score_trajectory(task, traj, env)
        env.stop()
        verifier = build_environment(task.environment)
        verifier.workspace = workspace
        verifier._owns_workspace = False  # the rollout's environment owns it
        try:
            verifier.start()
            return score_trajectory(task, traj, verifier)
        except EnvironmentError_ as exc:
            traj.error = traj.error or f"verification environment failed: {exc}"
            return score_trajectory(task, traj, _NullEnvironment())
        finally:
            verifier.stop()

    def _write_trajectory(
        self, traj: Trajectory, agent: AgentSpec, task: Task, repeat: int
    ) -> Path | None:
        safe_agent = agent.id.replace("/", "_")
        path = self.results_dir / "traces" / safe_agent / f"{task.id}-r{repeat}.json"
        try:
            return traj.write(path)
        except OSError:
            return None

    def _default_agent_factory(
        self,
        agent: AgentSpec,
        model: ModelSpec,
        harness_spec: HarnessSpec,
        task: Task,
        repeat: int,
    ) -> tuple[Any, Any]:
        seed = self.config.run.seed * 10_000 + repeat
        return build_harness(harness_spec, model), build_provider(model, task=task, seed=seed)

    def _emit(self, event: RunEvent) -> None:
        try:
            self.on_event(event)
        except Exception:  # a noisy observer must not break the sweep
            pass


class _NullEnvironment:
    """Stand-in when verification cannot connect: every state check fails."""

    def call(self, name, arguments=None):
        raise EnvironmentError_("verification environment unavailable")

    def resolve(self, name):
        return None

    def tools(self):
        return ()


def _failed_record(
    agent: AgentSpec,
    model: ModelSpec,
    harness_spec: HarnessSpec,
    task: Task,
    repeat: int,
    error: str,
) -> RunRecord:
    return RunRecord(
        agent_id=agent.id,
        model_id=model.id,
        harness_id=harness_spec.id,
        task_id=task.id,
        repeat=repeat,
        score=TaskScore(security=1.0, completion=0.0, process=0.0, status=RunStatus.ERROR),
        error=error,
    )



