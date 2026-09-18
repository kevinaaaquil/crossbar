"""Evidence types and their storage format."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

MAX_CONTENT_CHARS = 400_000
"""Ceiling per captured item. Generous, because truncating the thing the judge
has to read defeats the point, but not unbounded: a database dump from a large
system would otherwise be written in full for every Attempt."""


@dataclass(frozen=True)
class EvidenceRequest:
    """One thing the Check Plan says must be looked at."""

    label: str
    """What this is, in the plan's own words — shown to the user and given to
    the judge so it knows what it is reading."""

    connector: str
    probe: str
    args: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "connector": self.connector,
            "probe": self.probe,
            "args": dict(self.args),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "EvidenceRequest":
        return cls(
            label=str(data.get("label", "")),
            connector=str(data.get("connector", "")),
            probe=str(data.get("probe", "")),
            args=dict(data.get("args") or {}),
        )


@dataclass(frozen=True)
class EvidenceItem:
    """One captured observation, or the reason there is none."""

    request: EvidenceRequest
    content: str | None = None
    error: str | None = None

    @property
    def available(self) -> bool:
        return self.content is not None

    def to_dict(self, truncate: bool = True) -> dict[str, Any]:
        content = self.content
        if truncate and content is not None and len(content) > MAX_CONTENT_CHARS:
            dropped = len(content) - MAX_CONTENT_CHARS
            content = content[:MAX_CONTENT_CHARS] + f"\n... [truncated {dropped} characters]"
        return {"request": self.request.to_dict(), "content": content, "error": self.error}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "EvidenceItem":
        return cls(
            request=EvidenceRequest.from_dict(data.get("request") or {}),
            content=data.get("content"),
            error=data.get("error"),
        )


@dataclass(frozen=True)
class Evidence:
    """Everything captured from one Attempt.

    Deliberately carries **no model, agent or role identity**. The judge is
    blinded, and the cleanest way to guarantee that is a payload with nothing to
    leak rather than a rule someone has to remember.
    """

    final_answer: str
    items: tuple[EvidenceItem, ...] = ()

    @property
    def missing(self) -> tuple[EvidenceItem, ...]:
        return tuple(i for i in self.items if not i.available)

    @property
    def is_complete(self) -> bool:
        return not self.missing

    def item(self, label: str) -> EvidenceItem | None:
        return next((i for i in self.items if i.request.label == label), None)

    def to_dict(self) -> dict[str, Any]:
        return {
            "final_answer": self.final_answer,
            "items": [i.to_dict() for i in self.items],
        }

    def write(self, path: str | os.PathLike[str]) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(self.to_dict(), indent=2))
        return target

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Evidence":
        return cls(
            final_answer=str(data.get("final_answer", "")),
            items=tuple(EvidenceItem.from_dict(i) for i in data.get("items") or []),
        )


def load_evidence(path: str | os.PathLike[str]) -> Evidence:
    return Evidence.from_dict(json.loads(Path(path).read_text()))
