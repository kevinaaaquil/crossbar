"""Check Plan and Judgement types."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, Sequence

from crossbar.evidence import EvidenceRequest


class CheckStatus(Enum):
    PASS = "pass"
    FAIL = "fail"
    UNCHECKED = "unchecked"


class Outcome(Enum):
    """How a Task ended for one Attempt."""

    GRADED = "graded"
    UNCHECKED = "unchecked"
    """Required evidence was unavailable, so there is no score. Carries a reason."""
    FAILED = "failed"
    """The Attempt itself errored before there was anything to judge."""


@dataclass(frozen=True)
class ProbeCatalogue:
    """The read-only probes available for a Task, shown to the judge when it
    plans. It can only ask for evidence something can actually supply."""

    probes: tuple[Mapping[str, Any], ...] = ()

    @classmethod
    def from_connectors(cls, connectors: Sequence) -> "ProbeCatalogue":
        entries = []
        for connector in connectors:
            for spec in connector.probes():
                entries.append(
                    {
                        "connector": connector.name,
                        "name": spec.name,
                        "description": spec.description,
                        "input_schema": dict(spec.input_schema),
                    }
                )
        return cls(probes=tuple(entries))

    def names(self) -> set[str]:
        return {str(p["name"]) for p in self.probes}

    def describe(self) -> str:
        if not self.probes:
            return "(none available)"
        lines = []
        for probe in self.probes:
            schema = json.dumps(probe.get("input_schema") or {}, separators=(",", ":"))
            lines.append(
                f"- {probe['name']} (connector: {probe['connector']})\n"
                f"    {probe['description']}\n"
                f"    arguments: {schema}"
            )
        return "\n".join(lines)


@dataclass(frozen=True)
class CheckItem:
    """One thing that must be true, and what to look at to decide it."""

    id: str
    criterion: str
    evidence: tuple[EvidenceRequest, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "criterion": self.criterion,
            "evidence": [e.to_dict() for e in self.evidence],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "CheckItem":
        return cls(
            id=str(data.get("id", "")),
            criterion=str(data.get("criterion", "")),
            evidence=tuple(EvidenceRequest.from_dict(e) for e in data.get("evidence") or []),
        )


@dataclass(frozen=True)
class CheckPlan:
    """Derived once per Task, from Task + Golden, before any Attempt runs.

    Fixing the criteria before any model output has been seen is an integrity
    property: they cannot be tailored to whichever output the judge is looking
    at. It also tells the orchestrator what evidence to capture.
    """

    task_id: str
    items: tuple[CheckItem, ...] = ()
    unsatisfiable: tuple[str, ...] = ()
    """Parts of the Golden nothing available can decide. Surfaced before a run,
    so the user can fix their setup instead of collecting Unchecked results."""

    @property
    def is_empty(self) -> bool:
        return not self.items

    def item(self, check_id: str) -> CheckItem | None:
        return next((i for i in self.items if i.id == check_id), None)

    def evidence_requests(self) -> tuple[EvidenceRequest, ...]:
        """Everything the orchestrator must capture, de-duplicated."""
        seen: dict[tuple, EvidenceRequest] = {}
        for item in self.items:
            for request in item.evidence:
                key = (request.connector, request.probe, json.dumps(request.args, sort_keys=True))
                seen.setdefault(key, request)
        return tuple(seen.values())

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "items": [i.to_dict() for i in self.items],
            "unsatisfiable": list(self.unsatisfiable),
        }

    def write(self, path: str | os.PathLike[str]) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(self.to_dict(), indent=2))
        return target

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "CheckPlan":
        return cls(
            task_id=str(data.get("task_id", "")),
            items=tuple(CheckItem.from_dict(i) for i in data.get("items") or []),
            unsatisfiable=tuple(str(u) for u in data.get("unsatisfiable") or []),
        )


def load_plan(path: str | os.PathLike[str]) -> CheckPlan:
    return CheckPlan.from_dict(json.loads(Path(path).read_text()))


@dataclass(frozen=True)
class CheckOutcome:
    check_id: str
    status: CheckStatus
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"check_id": self.check_id, "status": self.status.value, "reason": self.reason}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "CheckOutcome":
        return cls(
            check_id=str(data.get("check_id", "")),
            status=CheckStatus(str(data.get("status", "unchecked"))),
            reason=str(data.get("reason", "")),
        )


@dataclass(frozen=True)
class Judgement:
    """The verdict on one Attempt."""

    outcome: Outcome
    checks: tuple[CheckOutcome, ...] = ()
    score: float = 0.0
    reasoning: str = ""
    error: str = ""

    @property
    def passed(self) -> bool:
        """Binary success: graded, and every check passed."""
        return self.outcome is Outcome.GRADED and self.score >= 1.0

    def check(self, check_id: str) -> CheckOutcome | None:
        return next((c for c in self.checks if c.check_id == check_id), None)

    @property
    def unchecked(self) -> tuple[CheckOutcome, ...]:
        return tuple(c for c in self.checks if c.status is CheckStatus.UNCHECKED)

    @property
    def unchecked_reason(self) -> str:
        """Why this Attempt has no score, in terms the user can act on."""
        if self.outcome is not Outcome.UNCHECKED:
            return ""
        reasons = [c.reason for c in self.unchecked if c.reason]
        return "; ".join(reasons) or self.error or "required evidence was unavailable"

    def to_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome.value,
            "checks": [c.to_dict() for c in self.checks],
            "score": self.score,
            "passed": self.passed,
            "reasoning": self.reasoning,
            "error": self.error,
        }

    def write(self, path: str | os.PathLike[str]) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(self.to_dict(), indent=2))
        return target

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Judgement":
        return cls(
            outcome=Outcome(str(data.get("outcome", "failed"))),
            checks=tuple(CheckOutcome.from_dict(c) for c in data.get("checks") or []),
            score=float(data.get("score", 0.0)),
            reasoning=str(data.get("reasoning", "")),
            error=str(data.get("error", "")),
        )


def load_judgement(path: str | os.PathLike[str]) -> Judgement:
    return Judgement.from_dict(json.loads(Path(path).read_text()))
