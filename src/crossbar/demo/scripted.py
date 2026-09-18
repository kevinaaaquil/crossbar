"""A fully scripted run of the shipped example, for seeing the shape of the output.

Nothing here talks to a model. Two scripted agents stand in: one that performs
each Task through the MCP server, and one that only describes what it would do.
The judge reads the captured evidence and decides from actual state.

It exists so the pipeline can be watched end to end before anything is
connected. It is a demonstration, not a measurement — the "models" are fixed
scripts, and the report says so.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from crossbar.agents import ModelAgent
from crossbar.connectors import known_connectors
from crossbar.domain import Role, load_test
from crossbar.judging import (
    CheckOutcome,
    CheckPlan,
    CheckStatus,
    assemble_judgement,
    undecidable_checks,
)
from crossbar.orchestrator import Orchestrator, RunResult
from crossbar.providers import ScriptedProvider, Usage, scripted_step
from crossbar.roster import parse_roster

EXAMPLE = Path(__file__).resolve().parent.parent.parent.parent / "examples" / "support-triage"

ROSTER = parse_roster(
    {
        "models": [
            {"id": "diligent-demo", "provider": "openai", "model": "diligent",
             "base_url": "http://demo.invalid/v1",
             "price": {"input_per_mtok": 0.20, "output_per_mtok": 0.60}},
            {"id": "idle-demo", "provider": "anthropic", "model": "idle",
             "price": {"input_per_mtok": 15.0, "output_per_mtok": 75.0}},
        ],
        "roles": {"candidate": "diligent-demo", "baseline": "idle-demo"},
    },
    source="<demo>",
)

SOLUTIONS: dict[str, list[tuple[str, dict]]] = {
    "escalate-outage": [
        ("tickets__set_priority", {"id": "T-1001", "priority": "urgent"}),
        ("tickets__set_priority", {"id": "T-1004", "priority": "urgent"}),
    ],
    "route-billing": [
        ("tickets__assign", {"id": "T-1002", "assignee": "alice"}),
        ("tickets__add_tag", {"id": "T-1002", "tag": "billing"}),
        ("tickets__assign", {"id": "T-1005", "assignee": "alice"}),
        ("tickets__add_tag", {"id": "T-1005", "tag": "billing"}),
    ],
    "close-resolved": [
        ("tickets__close_ticket", {"id": "T-1003"}),
        ("tickets__close_ticket", {"id": "T-1006"}),
    ],
    "tag-enterprise": [
        ("tickets__add_tag", {"id": "T-1001", "tag": "enterprise"}),
        ("tickets__set_priority", {"id": "T-1001", "priority": "high"}),
        ("tickets__add_tag", {"id": "T-1004", "tag": "enterprise"}),
        ("tickets__set_priority", {"id": "T-1004", "priority": "high"}),
    ],
}

EXPECTATIONS = {
    "escalate-outage": lambda t: t["T-1001"]["priority"] == "urgent"
    and t["T-1004"]["priority"] == "urgent"
    and t["T-1002"]["priority"] == "normal",
    "route-billing": lambda t: t["T-1002"]["assignee"] == "alice"
    and "billing" in t["T-1002"]["tags"]
    and t["T-1005"]["assignee"] == "alice",
    "close-resolved": lambda t: t["T-1003"]["status"] == "closed"
    and t["T-1006"]["status"] == "closed"
    and t["T-1001"]["status"] == "open",
    "tag-enterprise": lambda t: "enterprise" in t["T-1001"]["tags"]
    and t["T-1001"]["priority"] in ("high", "urgent")
    and "enterprise" in t["T-1004"]["tags"],
}


def _agent(model_id: str, role: Role, task, repeat: int):
    """The candidate performs the work; the baseline only talks about it."""
    if role is Role.BASELINE:
        return ModelAgent(model_id, ScriptedProvider([
            scripted_step(text="I would go through the queue and handle those.",
                          usage=Usage(300, 80)),
        ]))
    return ModelAgent(model_id, ScriptedProvider([
        scripted_step(tool_calls=[("tickets__list_tickets", {})], usage=Usage(500, 100)),
        scripted_step(tool_calls=SOLUTIONS[task.id], usage=Usage(900, 200)),
        scripted_step(text="Done.", usage=Usage(200, 60)),
    ]))


class _StateJudge:
    """Decides from the captured evidence rather than from a model.

    It still honours the real rule that missing evidence means Unchecked, so the
    demo shows the genuine behaviour rather than a happy path.
    """

    def make_plan(self, task, catalogue) -> CheckPlan:
        return CheckPlan.from_dict({
            "task_id": task.id,
            "items": [{
                "id": "state-matches-golden",
                "criterion": "the ticket store matches what the golden describes",
                "evidence": [{"label": "every ticket after the run", "connector": "mcp",
                              "probe": "tickets__dump_db", "args": {}}],
            }],
            "unsatisfiable": [],
        })

    def grade(self, plan: CheckPlan, evidence) -> Any:
        undecidable = undecidable_checks(plan, evidence)
        if undecidable:
            return assemble_judgement(
                tuple(
                    CheckOutcome(i.id, CheckStatus.UNCHECKED, undecidable.get(i.id, ""))
                    for i in plan.items
                ),
                "",
            )
        tickets = {
            t["id"]: t for t in json.loads(evidence.items[0].content)["tickets"]
        }
        ok = EXPECTATIONS[plan.task_id](tickets)
        return assemble_judgement(
            (
                CheckOutcome(
                    "state-matches-golden",
                    CheckStatus.PASS if ok else CheckStatus.FAIL,
                    "read the ticket store and compared it with the golden",
                ),
            ),
            "compared the store against the golden",
        )


def run_demo(results_dir: str, repeats: int = 1, on_event=None) -> RunResult:
    """Run the shipped example with scripted stand-ins for both models."""
    test = load_test(EXAMPLE, known_connectors=known_connectors())
    sized = type(test)(
        name=test.name, tasks=test.tasks, environment=test.environment,
        description=test.description, repeats=repeats, path=test.path,
    )
    return Orchestrator(
        roster=ROSTER,
        tests=[sized],
        results_dir=results_dir,
        agent_factory=_agent,
        judge=_StateJudge(),
        on_event=on_event,
    ).run()
