"""The whole pipeline, wired together.

Roster and Test from disk, through planning, execution, evidence capture,
judging, analysis, report and dump. Only the model and the judge are scripted;
the MCP servers, the environment, the connectors and every file written are
real.
"""
import json
import zipfile

import pytest

from crossbar.agents import ModelAgent
from crossbar.analysis import analyze
from crossbar.connectors import known_connectors
from crossbar.domain import Role, load_test
from crossbar.dump import create_dump
from crossbar.judging import CheckPlan, Outcome, ScriptedJudge
from crossbar.orchestrator import Orchestrator, load_run
from crossbar.providers import ScriptedProvider, Usage, scripted_step
from crossbar.report import render_report
from crossbar.roster import parse_roster

from crossbar.demo.scripted import example_test_path

EXAMPLE = str(example_test_path())

ROSTER = parse_roster(
    {
        "models": [
            {"id": "my-model", "provider": "openai", "model": "qwen",
             "base_url": "http://localhost:1/v1",
             "price": {"input_per_mtok": 0.2, "output_per_mtok": 0.6}},
            {"id": "frontier", "provider": "anthropic", "model": "big",
             "price": {"input_per_mtok": 15.0, "output_per_mtok": 75.0}},
        ],
        "roles": {"candidate": "my-model", "baseline": "frontier"},
    },
    source="<test>",
)

# What a competent agent does for each Task in the shipped example.
SOLUTIONS = {
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


def agent_factory(model_id, role, task, repeat):
    """The candidate solves every Task; the baseline describes and does nothing."""
    if role is Role.BASELINE:
        return ModelAgent(model_id, ScriptedProvider([
            scripted_step(text="I would handle those tickets.", usage=Usage(300, 80)),
        ]))
    calls = SOLUTIONS[task.id]
    return ModelAgent(model_id, ScriptedProvider([
        scripted_step(tool_calls=[("tickets__list_tickets", {})], usage=Usage(500, 100)),
        scripted_step(tool_calls=calls, usage=Usage(900, 200)),
        scripted_step(text="Done.", usage=Usage(200, 60)),
    ]))


def plan_for(task_id):
    """A plan of the shape the real judge would produce for these Goldens."""
    return CheckPlan.from_dict({
        "task_id": task_id,
        "items": [{
            "id": "state-matches-golden",
            "criterion": "the ticket store matches what the golden describes",
            "evidence": [{"label": "every ticket after the run", "connector": "mcp",
                          "probe": "tickets__dump_db", "args": {}}],
        }],
        "unsatisfiable": [],
    })


class StateCheckingJudge:
    """Grades by actually reading the captured evidence.

    Not a real LLM judge, but it exercises the whole path for real: it reads the
    dump the plan asked for and decides from the state, so a candidate that did
    the work passes and a baseline that did not, fails.
    """

    def __init__(self):
        self.plans_made = 0

    def make_plan(self, task, catalogue):
        self.plans_made += 1
        return plan_for(task.id)

    def grade(self, plan, evidence):
        from crossbar.judging import CheckOutcome, CheckStatus, assemble_judgement

        item = evidence.items[0] if evidence.items else None
        if item is None or not item.available:
            reason = item.error if item else "no evidence was captured"
            return assemble_judgement(
                (CheckOutcome("state-matches-golden", CheckStatus.UNCHECKED, reason),), ""
            )

        tickets = {t["id"]: t for t in json.loads(item.content)["tickets"]}
        expectations = {
            "escalate-outage": lambda: tickets["T-1001"]["priority"] == "urgent"
            and tickets["T-1004"]["priority"] == "urgent",
            "route-billing": lambda: tickets["T-1002"]["assignee"] == "alice"
            and "billing" in tickets["T-1002"]["tags"],
            "close-resolved": lambda: tickets["T-1003"]["status"] == "closed"
            and tickets["T-1006"]["status"] == "closed",
            "tag-enterprise": lambda: "enterprise" in tickets["T-1001"]["tags"]
            and tickets["T-1001"]["priority"] in ("high", "urgent"),
        }
        ok = expectations[plan.task_id]()
        status = CheckStatus.PASS if ok else CheckStatus.FAIL
        return assemble_judgement(
            (CheckOutcome("state-matches-golden", status, "read the ticket store"),),
            "compared the store against the golden",
        )


@pytest.fixture(scope="module")
def finished_run(tmp_path_factory):
    """One full run, shared by every assertion below."""
    results = tmp_path_factory.mktemp("run")
    test = load_test(EXAMPLE, known_connectors=known_connectors())
    trimmed = type(test)(
        name=test.name, tasks=test.tasks, environment=test.environment,
        description=test.description, repeats=1, path=test.path,
    )
    orchestrator = Orchestrator(
        roster=ROSTER, tests=[trimmed], results_dir=str(results),
        agent_factory=agent_factory, judge=StateCheckingJudge(),
    )
    return orchestrator.run(), results


class TestTheWholePipeline:
    def test_every_task_ran_for_both_models(self, finished_run):
        result, _ = finished_run
        assert len(result.attempts) == 8  # 4 tasks x 1 repeat x 2 roles

    def test_the_candidate_ran_before_the_baseline(self, finished_run):
        result, _ = finished_run
        roles = [a.role for a in result.attempts]
        assert roles[:4] == [Role.CANDIDATE] * 4
        assert roles[4:] == [Role.BASELINE] * 4

    def test_a_plan_was_made_for_every_task(self, finished_run):
        _, results = finished_run
        plans = {p.stem for p in (results / "plans").iterdir()}
        assert plans == set(SOLUTIONS)

    def test_the_model_that_did_the_work_passes(self, finished_run):
        result, _ = finished_run
        candidate = [a for a in result.attempts if a.role is Role.CANDIDATE]
        assert all(a.judgement.outcome is Outcome.GRADED for a in candidate)
        assert all(a.judgement.passed for a in candidate)

    def test_the_model_that_only_talked_fails(self, finished_run):
        result, _ = finished_run
        baseline = [a for a in result.attempts if a.role is Role.BASELINE]
        assert all(a.judgement.outcome is Outcome.GRADED for a in baseline)
        assert not any(a.judgement.passed for a in baseline)

    def test_the_analysis_reflects_that(self, finished_run):
        result, _ = finished_run
        analysis = analyze(result)
        assert analysis.model("my-model").pass_rate == 1.0
        assert analysis.model("frontier").pass_rate == 0.0

    def test_the_comparison_finds_the_gap_significant(self, finished_run):
        result, _ = finished_run
        comparison = analyze(result).comparison
        assert comparison.delta == pytest.approx(1.0)
        assert comparison.n_tasks == 4

    def test_the_verdict_recommends_the_cheaper_model_that_worked(self, finished_run):
        result, _ = finished_run
        verdict = analyze(result).verdict
        assert verdict.recommend_switch is True
        assert verdict.savings_usd > 0

    def test_no_conflict_is_claimed_when_the_judge_is_independent(self, finished_run):
        """This run used a judge of its own, so the baseline did not grade
        itself and the report must not say it did."""
        result, _ = finished_run
        assert result.judge_is_baseline is False
        assert not any("graded its own" in c.lower() for c in analyze(result).verdict.caveats)

    def test_the_report_renders(self, finished_run):
        result, _ = finished_run
        report = render_report(analyze(result))
        assert "VERDICT" in report.upper()
        assert "my-model" in report and "frontier" in report


class TestWhatLandedOnDisk:
    def test_the_layout_is_complete(self, finished_run):
        _, results = finished_run
        assert (results / "run.json").exists()
        assert len(list((results / "plans").iterdir())) == 4
        assert len(list((results / "attempts").iterdir())) == 8

    def test_every_attempt_stored_its_evidence_and_verdict(self, finished_run):
        _, results = finished_run
        for directory in (results / "attempts").iterdir():
            for name in ("attempt.json", "trajectory.json", "evidence.json", "judgement.json"):
                assert (directory / name).exists(), f"{name} missing from {directory.name}"

    def test_the_run_reloads_with_everything_attached(self, finished_run):
        _, results = finished_run
        restored = load_run(results / "run.json")
        assert len(restored.attempts) == 8
        assert all(a.evidence is not None for a in restored.attempts)
        assert all(a.judgement is not None for a in restored.attempts)

    def test_a_reloaded_run_analyses_identically(self, finished_run):
        result, results = finished_run
        assert analyze(load_run(results / "run.json")).model("my-model").pass_rate == (
            analyze(result).model("my-model").pass_rate
        )

    def test_the_run_dumps(self, finished_run):
        _, results = finished_run
        with zipfile.ZipFile(create_dump(results)) as archive:
            names = archive.namelist()
        assert "run.json" in names
        assert sum(1 for n in names if n.endswith("judgement.json")) == 8


class TestDeferredJudging:
    def test_a_run_can_be_judged_after_the_fact(self, tmp_path):
        test = load_test(EXAMPLE, known_connectors=known_connectors())
        one_task = type(test)(
            name=test.name, tasks=(test.task("escalate-outage"),),
            environment=test.environment, repeats=1, path=test.path,
        )
        Orchestrator(
            roster=ROSTER, tests=[one_task], results_dir=str(tmp_path),
            agent_factory=agent_factory, judge=StateCheckingJudge(), judge_tests=0,
        ).run()

        stored = load_run(tmp_path / "run.json")
        assert all(a.judgement is None for a in stored.attempts)
        assert all(a.evidence.is_complete for a in stored.attempts)

        judge = StateCheckingJudge()
        rejudged = Orchestrator.judge_stored(tmp_path, judge=judge)
        assert judge.plans_made == 0, "re-judging must reuse the stored plan"
        assert all(a.judgement.outcome is Outcome.GRADED for a in rejudged.attempts)


SINGLE_ROSTER = parse_roster(
    {
        "models": [
            {"id": "my-model", "provider": "openai", "model": "qwen",
             "base_url": "http://localhost:1/v1",
             "price": {"input_per_mtok": 0.2, "output_per_mtok": 0.6}},
            {"id": "grader", "provider": "anthropic", "model": "big"},
        ],
        "roles": {"candidate": "my-model", "judge": "grader"},
    },
    source="<test>",
)


@pytest.fixture(scope="module")
def finished_single(tmp_path_factory):
    """One single-model run, shared across the assertions below."""
    results = tmp_path_factory.mktemp("single")
    test = load_test(EXAMPLE, known_connectors=known_connectors())
    trimmed = type(test)(
        name=test.name, tasks=test.tasks, environment=test.environment,
        description=test.description, repeats=1, path=test.path,
    )
    orchestrator = Orchestrator(
        roster=SINGLE_ROSTER, tests=[trimmed], results_dir=str(results),
        agent_factory=agent_factory, judge=StateCheckingJudge(),
    )
    return orchestrator.run(), results


class TestSingleModelRunEndToEnd:
    """One model assessed on its own, all the way through."""

    def test_only_the_candidate_runs(self, finished_single):
        result, _ = finished_single
        assert len(result.attempts) == 4  # 4 tasks x 1 repeat x 1 role
        assert {a.role for a in result.attempts} == {Role.CANDIDATE}

    def test_every_attempt_is_graded(self, finished_single):
        result, _ = finished_single
        assert all(a.judgement.outcome is Outcome.GRADED for a in result.attempts)
        assert all(a.judgement.passed for a in result.attempts)

    def test_the_analysis_has_no_comparison(self, finished_single):
        result, _ = finished_single
        analysis = analyze(result)
        assert analysis.is_single_model is True
        assert analysis.comparison is None
        assert analysis.model("my-model").pass_rate == 1.0

    def test_the_report_is_an_assessment(self, finished_single):
        result, _ = finished_single
        report = render_report(analyze(result))
        assert "ASSESSMENT" in report.upper()
        assert "my-model" in report

    def test_the_run_reloads(self, finished_single):
        _, results = finished_single
        restored = load_run(results / "run.json")
        assert len(restored.attempts) == 4
        assert analyze(restored).is_single_model is True

    def test_the_queue_only_plans_one_role(self, tmp_path):
        test = load_test(EXAMPLE, known_connectors=known_connectors())
        one = type(test)(
            name=test.name, tasks=(test.task("escalate-outage"),),
            environment=test.environment, repeats=2, path=test.path,
        )
        orchestrator = Orchestrator(
            roster=SINGLE_ROSTER, tests=[one], results_dir=str(tmp_path),
            agent_factory=agent_factory, judge=StateCheckingJudge(),
        )
        assert len(orchestrator.queue) == 2
        assert {i.role for i in orchestrator.queue} == {Role.CANDIDATE}
