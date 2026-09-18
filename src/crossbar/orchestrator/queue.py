"""The queue: every planned unit of work and where it has got to.

Built before anything runs, so the TUI can render the whole shape of a run at
any moment rather than reconstructing it from a stream of events.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Mapping

State = Literal["pending", "running", "done", "failed"]


@dataclass
class QueueItem:
    """One Attempt, planned or in flight."""

    test_name: str
    task_id: str
    model_id: str
    role: Any
    repeat: int
    state: State = "pending"

    @property
    def label(self) -> str:
        return f"{self.model_id} · {self.test_name} · {self.task_id} · repeat {self.repeat}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "test_name": self.test_name,
            "task_id": self.task_id,
            "model_id": self.model_id,
            "role": getattr(self.role, "value", str(self.role)),
            "repeat": self.repeat,
            "state": self.state,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "QueueItem":
        from crossbar.domain import Role

        return cls(
            test_name=str(data.get("test_name", "")),
            task_id=str(data.get("task_id", "")),
            model_id=str(data.get("model_id", "")),
            role=Role(str(data.get("role", "candidate"))),
            repeat=int(data.get("repeat", 0)),
            state=str(data.get("state", "pending")),  # type: ignore[arg-type]
        )
