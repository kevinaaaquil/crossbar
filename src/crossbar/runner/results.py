"""What came out of a sweep: one record per rollout, grouped into cells."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from crossbar.providers import Usage
from crossbar.scoring import CheckResult, FailureMode, TaskScore
from crossbar.stats import bootstrap_ci, variance_decomposition
from crossbar.trace import RunStatus


@dataclass(frozen=True)
class RunRecord:
    """One rollout: one (agent, task, repeat) triple and how it went."""

    agent_id: str
    model_id: str
    harness_id: str
    task_id: str
    repeat: int
    score: TaskScore
    usage: Usage = field(default_factory=Usage)
    cost_usd: float = 0.0
    wall_time_s: float = 0.0
    tool_calls: int = 0
    trajectory_path: str = ""
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "model_id": self.model_id,
            "harness_id": self.harness_id,
            "task_id": self.task_id,
            "repeat": self.repeat,
            "score": self.score.to_dict(),
            "usage": {
                "input_tokens": self.usage.input_tokens,
                "output_tokens": self.usage.output_tokens,
            },
            "cost_usd": self.cost_usd,
            "wall_time_s": self.wall_time_s,
            "tool_calls": self.tool_calls,
            "trajectory_path": self.trajectory_path,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "RunRecord":
        score_data = data.get("score") or {}
        usage = data.get("usage") or {}
        failure = score_data.get("failure_mode")
        return cls(
            agent_id=str(data.get("agent_id", "")),
            model_id=str(data.get("model_id", "")),
            harness_id=str(data.get("harness_id", "")),
            task_id=str(data.get("task_id", "")),
            repeat=int(data.get("repeat", 0)),
            score=TaskScore(
                security=float(score_data.get("security", 0.0)),
                completion=float(score_data.get("completion", 0.0)),
                process=float(score_data.get("process", 0.0)),
                check_results=tuple(
                    CheckResult(
                        label=str(c.get("label", "")),
                        passed=bool(c.get("passed", False)),
                        detail=str(c.get("detail", "")),
                        kind=str(c.get("kind", "")),
                    )
                    for c in score_data.get("checks") or []
                ),
                failure_mode=FailureMode(failure) if failure else None,
                status=RunStatus(str(score_data.get("status", "completed"))),
            ),
            usage=Usage(
                int(usage.get("input_tokens", 0)),
                int(usage.get("output_tokens", 0)),
            ),
            cost_usd=float(data.get("cost_usd", 0.0)),
            wall_time_s=float(data.get("wall_time_s", 0.0)),
            tool_calls=int(data.get("tool_calls", 0)),
            trajectory_path=str(data.get("trajectory_path", "")),
            error=str(data.get("error", "")),
        )


@dataclass(frozen=True)
class CellResult:
    """Every rollout for one (model, harness) pair."""

    agent_id: str
    records: tuple[RunRecord, ...]
    seed: int = 0

    @property
    def n(self) -> int:
        return len(self.records)

    @property
    def outcomes(self) -> list[float]:
        """Binary pass/fail per rollout; the unit the statistics resample."""
        return [1.0 if r.score.passed else 0.0 for r in self.records]

    @property
    def pass_rate(self) -> float:
        return sum(self.outcomes) / self.n if self.n else 0.0

    @property
    def mean_score(self) -> float:
        return sum(r.score.value for r in self.records) / self.n if self.n else 0.0

    @property
    def total_cost(self) -> float:
        return sum(r.cost_usd for r in self.records)

    @property
    def mean_cost(self) -> float:
        return self.total_cost / self.n if self.n else 0.0

    @property
    def mean_wall_time(self) -> float:
        return sum(r.wall_time_s for r in self.records) / self.n if self.n else 0.0

    @property
    def total_usage(self) -> Usage:
        total = Usage()
        for record in self.records:
            total = total + record.usage
        return total

    @property
    def cost_per_success(self) -> float | None:
        """Total cost divided by successes. None when nothing passed."""
        successes = sum(self.outcomes)
        return self.total_cost / successes if successes else None

    def interval(self, n_resamples: int = 2000) -> tuple[float, float]:
        if not self.records:
            return (0.0, 0.0)
        return bootstrap_ci(self.outcomes, n_resamples=n_resamples, seed=self.seed)

    @property
    def ci_low(self) -> float:
        return self.interval()[0]

    @property
    def ci_high(self) -> float:
        return self.interval()[1]

    def scores_by_task(self) -> dict[str, list[float]]:
        grouped: dict[str, list[float]] = {}
        for record in self.records:
            grouped.setdefault(record.task_id, []).append(record.score.value)
        return grouped

    def outcomes_by_task(self) -> dict[str, list[float]]:
        grouped: dict[str, list[float]] = {}
        for record in self.records:
            grouped.setdefault(record.task_id, []).append(1.0 if record.score.passed else 0.0)
        return grouped

    def variance(self):
        return variance_decomposition(self.scores_by_task())

    def failure_tally(self) -> dict[str, int]:
        tally: dict[str, int] = {}
        for record in self.records:
            mode = record.score.failure_mode
            if mode is not None:
                tally[mode.value] = tally.get(mode.value, 0) + 1
        return tally


@dataclass(frozen=True)
class SweepResult:
    """The whole matrix, with enough context to reproduce it."""

    records: tuple[RunRecord, ...]
    agent_ids: tuple[str, ...]
    task_ids: tuple[str, ...]
    baseline: str = ""
    seed: int = 0
    repeats: int = 0
    extra_repeats: int = 0
    pack_name: str = ""
    started_at: float = 0.0
    finished_at: float = 0.0

    def cell(self, agent_id: str) -> CellResult:
        records = tuple(r for r in self.records if r.agent_id == agent_id)
        if not records:
            raise KeyError(f"no results for agent {agent_id!r}")
        return CellResult(agent_id=agent_id, records=records, seed=self.seed)

    def cells(self) -> list[CellResult]:
        return [self.cell(agent_id) for agent_id in self.agent_ids if self._has(agent_id)]

    def _has(self, agent_id: str) -> bool:
        return any(r.agent_id == agent_id for r in self.records)

    @property
    def total_cost(self) -> float:
        return sum(r.cost_usd for r in self.records)

    @property
    def duration_s(self) -> float:
        return max(0.0, self.finished_at - self.started_at)

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_ids": list(self.agent_ids),
            "task_ids": list(self.task_ids),
            "baseline": self.baseline,
            "seed": self.seed,
            "repeats": self.repeats,
            "extra_repeats": self.extra_repeats,
            "pack_name": self.pack_name,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "records": [r.to_dict() for r in self.records],
        }

    def write(self, path: str | os.PathLike[str]) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(self.to_dict(), indent=2))
        return target

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SweepResult":
        return cls(
            records=tuple(RunRecord.from_dict(r) for r in data.get("records") or []),
            agent_ids=tuple(data.get("agent_ids") or []),
            task_ids=tuple(data.get("task_ids") or []),
            baseline=str(data.get("baseline", "")),
            seed=int(data.get("seed", 0)),
            repeats=int(data.get("repeats", 0)),
            extra_repeats=int(data.get("extra_repeats", 0)),
            pack_name=str(data.get("pack_name", "")),
            started_at=float(data.get("started_at", 0.0)),
            finished_at=float(data.get("finished_at", 0.0)),
        )


def load_sweep(path: str | os.PathLike[str]) -> SweepResult:
    return SweepResult.from_dict(json.loads(Path(path).read_text()))


def paired_task_means(
    cell: CellResult, task_ids: Sequence[str]
) -> list[float]:
    """Mean score per task, in a fixed task order, for paired comparisons."""
    grouped = cell.scores_by_task()
    return [
        (sum(grouped[t]) / len(grouped[t])) if grouped.get(t) else 0.0 for t in task_ids
    ]
