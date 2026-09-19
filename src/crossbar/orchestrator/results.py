"""What a run produced."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from crossbar.domain import Role
from crossbar.evidence import Evidence
from crossbar.judging import Judgement
from crossbar.providers import Usage
from crossbar.trace import Trajectory


@dataclass
class Attempt:
    """One execution of one Task by one model."""

    id: str
    test_name: str
    task_id: str
    model_id: str
    role: Role
    repeat: int
    trajectory: Trajectory | None = None
    evidence: Evidence | None = None
    judgement: Judgement | None = None
    usage: Usage = field(default_factory=Usage)
    cost_usd: float = 0.0
    wall_time_s: float = 0.0
    error: str = ""

    @property
    def judged(self) -> bool:
        return self.judgement is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "test_name": self.test_name,
            "task_id": self.task_id,
            "model_id": self.model_id,
            "role": self.role.value,
            "repeat": self.repeat,
            "usage": {
                "input_tokens": self.usage.input_tokens,
                "output_tokens": self.usage.output_tokens,
            },
            "cost_usd": self.cost_usd,
            "wall_time_s": self.wall_time_s,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Attempt":
        usage = data.get("usage") or {}
        return cls(
            id=str(data.get("id", "")),
            test_name=str(data.get("test_name", "")),
            task_id=str(data.get("task_id", "")),
            model_id=str(data.get("model_id", "")),
            role=Role(str(data.get("role", "candidate"))),
            repeat=int(data.get("repeat", 0)),
            usage=Usage(int(usage.get("input_tokens", 0)), int(usage.get("output_tokens", 0))),
            cost_usd=float(data.get("cost_usd", 0.0)),
            wall_time_s=float(data.get("wall_time_s", 0.0)),
            error=str(data.get("error", "")),
        )


@dataclass
class RunResult:
    """Everything one run produced, and enough context to reproduce it."""

    run_id: str
    attempts: tuple[Attempt, ...] = ()
    roles: Mapping[str, str] = field(default_factory=dict)
    judged_tests: tuple[str, ...] = ()
    judge_is_baseline: bool = False
    external_harness_models: tuple[str, ...] = ()
    """Models that brought their own harness — an agent CLI rather than a model
    driven through ours. Comparing one of these against a model in our harness
    is a product comparison, not a controlled one, and the report says so."""
    started_at: float = 0.0
    finished_at: float = 0.0
    results_dir: str = ""

    def is_judged(self, test_name: str) -> bool:
        return test_name in self.judged_tests

    @property
    def unjudged_tests(self) -> tuple[str, ...]:
        seen = {a.test_name for a in self.attempts}
        return tuple(sorted(seen - set(self.judged_tests)))

    @property
    def total_cost(self) -> float:
        return sum(a.cost_usd for a in self.attempts)

    def for_model(self, model_id: str) -> tuple[Attempt, ...]:
        return tuple(a for a in self.attempts if a.model_id == model_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "roles": dict(self.roles),
            "judged_tests": list(self.judged_tests),
            "judge_is_baseline": self.judge_is_baseline,
            "external_harness_models": list(self.external_harness_models),
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "attempts": [a.to_dict() for a in self.attempts],
        }

    def write(self, path: str | os.PathLike[str]) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(self.to_dict(), indent=2))
        return target


def load_run(path: str | os.PathLike[str]) -> RunResult:
    """Reload a run, including each Attempt's trajectory, evidence and verdict."""
    from crossbar.evidence import load_evidence
    from crossbar.judging import load_judgement
    from crossbar.trace import load_trajectory

    run_path = Path(path)
    data = json.loads(run_path.read_text())
    root = run_path.parent

    attempts = []
    for raw in data.get("attempts") or []:
        attempt = Attempt.from_dict(raw)
        directory = root / "attempts" / attempt.id
        for name, loader, field_name in (
            ("trajectory.json", load_trajectory, "trajectory"),
            ("evidence.json", load_evidence, "evidence"),
            ("judgement.json", load_judgement, "judgement"),
        ):
            target = directory / name
            if target.exists():
                setattr(attempt, field_name, loader(target))
        attempts.append(attempt)

    return RunResult(
        run_id=str(data.get("run_id", "")),
        attempts=tuple(attempts),
        roles=dict(data.get("roles") or {}),
        judged_tests=tuple(data.get("judged_tests") or []),
        judge_is_baseline=bool(data.get("judge_is_baseline", False)),
        external_harness_models=tuple(data.get("external_harness_models") or []),
        started_at=float(data.get("started_at", 0.0)),
        finished_at=float(data.get("finished_at", 0.0)),
        results_dir=str(root),
    )
