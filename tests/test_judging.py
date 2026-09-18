"""Judging: the Check Plan, grading, and the blinding that keeps it honest."""
import json

import pytest

from crossbar.domain import Limits, Task
from crossbar.evidence import Evidence, EvidenceItem, EvidenceRequest
from crossbar.judging import (
    CheckItem,
    CheckPlan,
    CheckStatus,
    Judge,
    JudgingError,
    Outcome,
    ProbeCatalogue,
    ScriptedJudge,
    load_plan,
)
from crossbar.providers import ProviderError, ScriptedProvider, scripted_step

TASK = Task(
    id="escalate-outage",
    prompt="Escalate every open ticket mentioning an outage to urgent.",
    golden='Tickets T-1001 and T-1004 both have priority "urgent". '
           'No other ticket changed; T-1002 is still "normal".',
    limits=Limits(),
)

CATALOGUE = ProbeCatalogue(
    probes=(
        {"connector": "mcp", "name": "tickets__dump_db",
         "description": "Dump the whole ticket store as JSON.", "input_schema": {}},
        {"connector": "mcp", "name": "tickets__get_ticket",
         "description": "Fetch one ticket by id.",
         "input_schema": {"type": "object", "properties": {"id": {"type": "string"}}}},
    )
)

PLAN_JSON = {
    "items": [
        {
            "id": "outage-tickets-urgent",
            "criterion": 'T-1001 and T-1004 both have priority "urgent"',
            "evidence": [
                {"label": "the ticket store after the run", "connector": "mcp",
                 "probe": "tickets__dump_db", "args": {}}
            ],
        },
        {
            "id": "others-untouched",
            "criterion": 'T-1002 still has priority "normal"',
            "evidence": [
                {"label": "ticket T-1002", "connector": "mcp",
                 "probe": "tickets__get_ticket", "args": {"id": "T-1002"}}
            ],
        },
    ],
    "unsatisfiable": [],
}


def plan_provider(payload=None):
    return ScriptedProvider([scripted_step(text=json.dumps(payload or PLAN_JSON))])


def a_plan() -> CheckPlan:
    return CheckPlan.from_dict({"task_id": TASK.id, **PLAN_JSON})


def evidence_for(plan: CheckPlan, tickets=None, final="done") -> Evidence:
    tickets = tickets if tickets is not None else [
        {"id": "T-1001", "priority": "urgent"},
        {"id": "T-1002", "priority": "normal"},
        {"id": "T-1004", "priority": "urgent"},
    ]
    items = []
    for item in plan.items:
        for req in item.evidence:
            items.append(EvidenceItem(request=req, content=json.dumps({"tickets": tickets})))
    return Evidence(final_answer=final, items=tuple(items))


class TestCheckPlanGeneration:
    def test_a_plan_is_derived_from_the_task_and_golden(self):
        plan = Judge(plan_provider()).make_plan(TASK, CATALOGUE)
        assert isinstance(plan, CheckPlan)
        assert plan.task_id == "escalate-outage"
        assert [i.id for i in plan.items] == ["outage-tickets-urgent", "others-untouched"]

    def test_each_item_carries_a_criterion_and_its_evidence(self):
        item = Judge(plan_provider()).make_plan(TASK, CATALOGUE).items[0]
        assert isinstance(item, CheckItem)
        assert "urgent" in item.criterion
        assert item.evidence[0].probe == "tickets__dump_db"

    def test_the_prompt_contains_the_task_and_the_golden(self):
        provider = plan_provider()
        Judge(provider).make_plan(TASK, CATALOGUE)
        prompt = " ".join(m.content for m in provider.requests[0].messages)
        assert TASK.prompt in prompt
        assert TASK.golden in prompt

    def test_the_prompt_lists_the_available_probes(self):
        provider = plan_provider()
        Judge(provider).make_plan(TASK, CATALOGUE)
        prompt = " ".join(m.content for m in provider.requests[0].messages)
        assert "tickets__dump_db" in prompt
        assert "Dump the whole ticket store" in prompt

    def test_the_plan_never_sees_an_attempt(self):
        """Criteria are fixed before any model output exists. That is what stops
        them being tailored to whichever output the judge is looking at."""
        provider = plan_provider()
        Judge(provider).make_plan(TASK, CATALOGUE)
        prompt = " ".join(m.content for m in provider.requests[0].messages).lower()
        for leak in ("attempt", "final_answer", "trajectory"):
            assert leak not in prompt

    def test_a_probe_that_does_not_exist_becomes_unsatisfiable(self):
        payload = {
            "items": [
                {"id": "x", "criterion": "the DB has rows",
                 "evidence": [{"label": "db", "connector": "mcp",
                               "probe": "tickets__no_such_probe", "args": {}}]}
            ],
            "unsatisfiable": [],
        }
        plan = Judge(plan_provider(payload)).make_plan(TASK, CATALOGUE)
        assert plan.items == ()
        assert any("no_such_probe" in u for u in plan.unsatisfiable)

    def test_the_judge_may_declare_something_unsatisfiable_itself(self):
        payload = {"items": [], "unsatisfiable": ["the golden needs a screenshot"]}
        plan = Judge(plan_provider(payload)).make_plan(TASK, CATALOGUE)
        assert plan.unsatisfiable == ("the golden needs a screenshot",)

    def test_a_plan_with_nothing_satisfiable_is_flagged(self):
        payload = {"items": [], "unsatisfiable": ["no way to see the database"]}
        plan = Judge(plan_provider(payload)).make_plan(TASK, CATALOGUE)
        assert plan.is_empty is True

    def test_json_wrapped_in_a_code_fence_is_accepted(self):
        fenced = f"Here is the plan:\n```json\n{json.dumps(PLAN_JSON)}\n```\n"
        provider = ScriptedProvider([scripted_step(text=fenced)])
        assert Judge(provider).make_plan(TASK, CATALOGUE).items

    def test_unparseable_output_raises(self):
        provider = ScriptedProvider([scripted_step(text="I am not JSON")])
        with pytest.raises(JudgingError, match="JSON"):
            Judge(provider).make_plan(TASK, CATALOGUE)

    def test_a_provider_failure_raises(self):
        provider = ScriptedProvider([scripted_step(error="judge endpoint down")])
        with pytest.raises(JudgingError, match="down"):
            Judge(provider).make_plan(TASK, CATALOGUE)

    def test_every_evidence_request_the_plan_needs_is_exposed(self):
        plan = a_plan()
        assert len(plan.evidence_requests()) == 2
        assert {r.probe for r in plan.evidence_requests()} == {
            "tickets__dump_db", "tickets__get_ticket"
        }


class TestPlanPersistence:
    def test_a_plan_round_trips_through_disk(self, tmp_path):
        plan = a_plan()
        restored = load_plan(plan.write(tmp_path / "plan.json"))
        assert restored.task_id == plan.task_id
        assert [i.id for i in restored.items] == [i.id for i in plan.items]
        assert restored.items[1].evidence[0].args == {"id": "T-1002"}

    def test_the_file_is_plain_json(self, tmp_path):
        path = a_plan().write(tmp_path / "plan.json")
        assert json.loads(path.read_text())["task_id"] == "escalate-outage"

    def test_a_stored_plan_is_what_gets_reused(self, tmp_path):
        """Re-judging reuses the plan, so old and new scores stay comparable."""
        restored = load_plan(a_plan().write(tmp_path / "plan.json"))
        assert restored == a_plan()


class TestGrading:
    def verdict(self, checks, reasoning="looked at the store"):
        return json.dumps({"checks": checks, "reasoning": reasoning})

    def test_a_passing_attempt_is_graded(self):
        plan = a_plan()
        provider = ScriptedProvider([scripted_step(text=self.verdict([
            {"check_id": "outage-tickets-urgent", "status": "pass", "reason": "both urgent"},
            {"check_id": "others-untouched", "status": "pass", "reason": "T-1002 normal"},
        ]))])
        judgement = Judge(provider).grade(plan, evidence_for(plan))
        assert judgement.outcome is Outcome.GRADED
        assert judgement.score == 1.0

    def test_a_failing_check_lowers_the_score(self):
        plan = a_plan()
        provider = ScriptedProvider([scripted_step(text=self.verdict([
            {"check_id": "outage-tickets-urgent", "status": "pass", "reason": "ok"},
            {"check_id": "others-untouched", "status": "fail", "reason": "T-1002 was changed"},
        ]))])
        judgement = Judge(provider).grade(plan, evidence_for(plan))
        assert judgement.outcome is Outcome.GRADED
        assert judgement.score == 0.5
        assert judgement.passed is False

    def test_the_judges_reasoning_is_kept(self):
        plan = a_plan()
        provider = ScriptedProvider([scripted_step(text=self.verdict(
            [{"check_id": i.id, "status": "pass", "reason": "ok"} for i in plan.items],
            reasoning="I dumped the store and compared every priority.",
        ))])
        judgement = Judge(provider).grade(plan, evidence_for(plan))
        assert "dumped the store" in judgement.reasoning

    def test_per_check_reasons_are_kept(self):
        plan = a_plan()
        provider = ScriptedProvider([scripted_step(text=self.verdict([
            {"check_id": "outage-tickets-urgent", "status": "pass", "reason": "both urgent"},
            {"check_id": "others-untouched", "status": "fail", "reason": "T-1002 is high"},
        ]))])
        judgement = Judge(provider).grade(plan, evidence_for(plan))
        assert judgement.check("others-untouched").reason == "T-1002 is high"

    def test_the_evidence_is_put_in_front_of_the_judge(self):
        plan = a_plan()
        provider = ScriptedProvider([scripted_step(text=self.verdict(
            [{"check_id": i.id, "status": "pass", "reason": "ok"} for i in plan.items]))])
        Judge(provider).grade(plan, evidence_for(plan))
        prompt = " ".join(m.content for m in provider.requests[0].messages)
        assert "the ticket store after the run" in prompt
        assert "T-1001" in prompt

    def test_a_check_the_model_forgot_is_unchecked_not_silently_passed(self):
        plan = a_plan()
        provider = ScriptedProvider([scripted_step(text=self.verdict([
            {"check_id": "outage-tickets-urgent", "status": "pass", "reason": "ok"},
        ]))])
        judgement = Judge(provider).grade(plan, evidence_for(plan))
        assert judgement.check("others-untouched").status is CheckStatus.UNCHECKED
        assert judgement.outcome is Outcome.UNCHECKED

    def test_unparseable_grading_output_raises(self):
        provider = ScriptedProvider([scripted_step(text="not json at all")])
        with pytest.raises(JudgingError, match="JSON"):
            Judge(provider).grade(a_plan(), evidence_for(a_plan()))


class TestUncheckedOutcomes:
    """Missing evidence is decided without spending a judge call."""

    def test_a_check_whose_evidence_is_missing_is_unchecked(self):
        plan = a_plan()
        evidence = Evidence(
            final_answer="done",
            items=(
                EvidenceItem(
                    request=plan.items[0].evidence[0],
                    error="no connector named 'mcp' is enabled",
                ),
                EvidenceItem(request=plan.items[1].evidence[0], content='{"tickets": []}'),
            ),
        )
        provider = ScriptedProvider([scripted_step(text=json.dumps({
            "checks": [{"check_id": "others-untouched", "status": "pass", "reason": "ok"}],
            "reasoning": "only one was checkable",
        }))])
        judgement = Judge(provider).grade(plan, evidence)
        assert judgement.check("outage-tickets-urgent").status is CheckStatus.UNCHECKED
        assert "no connector" in judgement.check("outage-tickets-urgent").reason

    def test_any_unchecked_check_makes_the_whole_task_unchecked(self):
        """A Golden is one statement. Verifying half of it does not mean the
        task was done, so there is no score."""
        plan = a_plan()
        evidence = Evidence(
            final_answer="done",
            items=(EvidenceItem(request=plan.items[0].evidence[0], error="unavailable"),),
        )
        provider = ScriptedProvider([scripted_step(text=json.dumps({"checks": [], "reasoning": ""}))])
        judgement = Judge(provider).grade(plan, evidence)
        assert judgement.outcome is Outcome.UNCHECKED
        assert judgement.score == 0.0

    def test_an_unchecked_task_says_why(self):
        plan = a_plan()
        evidence = Evidence(
            final_answer="",
            items=(EvidenceItem(request=plan.items[0].evidence[0],
                                error="the probe reported an error: boom"),),
        )
        provider = ScriptedProvider([scripted_step(text=json.dumps({"checks": [], "reasoning": ""}))])
        judgement = Judge(provider).grade(plan, evidence)
        assert "boom" in judgement.unchecked_reason

    def test_no_judge_call_is_made_when_nothing_is_checkable(self):
        plan = a_plan()
        evidence = Evidence(
            final_answer="",
            items=tuple(
                EvidenceItem(request=r, error="unavailable") for r in plan.evidence_requests()
            ),
        )
        provider = ScriptedProvider([])  # any call would run off the end and raise
        judgement = Judge(provider).grade(plan, evidence)
        assert judgement.outcome is Outcome.UNCHECKED
        assert provider.steps_used == 0

    def test_an_empty_plan_is_unchecked_without_a_call(self):
        plan = CheckPlan(task_id="t", items=(), unsatisfiable=("no probe can see the DB",))
        judgement = Judge(ScriptedProvider([])).grade(plan, Evidence(final_answer=""))
        assert judgement.outcome is Outcome.UNCHECKED
        assert "no probe" in judgement.unchecked_reason


class TestBlinding:
    def test_no_identity_reaches_the_grading_prompt(self):
        plan = a_plan()
        provider = ScriptedProvider([scripted_step(text=json.dumps({
            "checks": [{"check_id": i.id, "status": "pass", "reason": "ok"} for i in plan.items],
            "reasoning": "",
        }))])
        Judge(provider).grade(plan, evidence_for(plan))
        prompt = " ".join(m.content for m in provider.requests[0].messages).lower()
        for leak in ("candidate", "baseline", "gpt", "claude", "qwen", "model id", "role"):
            assert leak not in prompt, f"{leak!r} leaked into the grading prompt"

    def test_the_judge_is_not_told_it_may_be_grading_itself(self):
        plan = a_plan()
        provider = ScriptedProvider([scripted_step(text=json.dumps({
            "checks": [{"check_id": i.id, "status": "pass", "reason": "ok"} for i in plan.items],
            "reasoning": "",
        }))])
        Judge(provider).grade(plan, evidence_for(plan))
        prompt = " ".join(m.content for m in provider.requests[0].messages).lower()
        assert "your own" not in prompt
        assert "you produced" not in prompt

    def test_grading_is_reference_based_not_pairwise(self):
        """One Attempt against the Golden, never two Attempts against each other:
        less room for comparative bias."""
        plan = a_plan()
        provider = ScriptedProvider([scripted_step(text=json.dumps({
            "checks": [{"check_id": i.id, "status": "pass", "reason": "ok"} for i in plan.items],
            "reasoning": "",
        }))])
        Judge(provider).grade(plan, evidence_for(plan))
        prompt = " ".join(m.content for m in provider.requests[0].messages).lower()
        assert "which of these" not in prompt
        assert "attempt b" not in prompt


class TestScriptedJudge:
    def test_it_returns_the_plan_it_was_given(self):
        judge = ScriptedJudge(plan=a_plan())
        assert judge.make_plan(TASK, CATALOGUE) is judge.plan

    def test_it_grades_by_a_supplied_rule(self):
        plan = a_plan()
        judge = ScriptedJudge(plan=plan, grader=lambda p, e: {i.id: "pass" for i in p.items})
        assert judge.grade(plan, evidence_for(plan)).score == 1.0

    def test_it_still_marks_missing_evidence_unchecked(self):
        plan = a_plan()
        judge = ScriptedJudge(plan=plan, grader=lambda p, e: {i.id: "pass" for i in p.items})
        evidence = Evidence(
            final_answer="",
            items=(EvidenceItem(request=plan.items[0].evidence[0], error="gone"),),
        )
        assert judge.grade(plan, evidence).outcome is Outcome.UNCHECKED

    def test_it_records_every_call(self):
        judge = ScriptedJudge(plan=a_plan())
        judge.make_plan(TASK, CATALOGUE)
        assert judge.plans_made == 1


class TestJudgementPersistence:
    def test_a_judgement_round_trips(self, tmp_path):
        from crossbar.judging import load_judgement

        plan = a_plan()
        judge = ScriptedJudge(plan=plan, grader=lambda p, e: {
            "outage-tickets-urgent": "pass", "others-untouched": "fail"
        })
        original = judge.grade(plan, evidence_for(plan))
        restored = load_judgement(original.write(tmp_path / "j.json"))
        assert restored.outcome is original.outcome
        assert restored.score == original.score
        assert restored.check("others-untouched").status is CheckStatus.FAIL
