"""The orchestrator: ordering, the queue, opt-in judging, storage, containment."""
import json

import pytest

from crossbar.agents import ModelAgent
from crossbar.domain import Role, load_test
from crossbar.judging import CheckPlan, Outcome, ScriptedJudge
from crossbar.orchestrator import Orchestrator, RunResult, load_run
from crossbar.providers import ScriptedProvider, Usage, scripted_step
from crossbar.roster import parse_roster

FIXTURE = "tests/fixtures/tests/support-triage"

ROSTER = parse_roster(
    {
        "models": [
            {"id": "local", "provider": "openai", "model": "qwen",
             "base_url": "http://localhost:1/v1",
             "price": {"input_per_mtok": 1.0, "output_per_mtok": 2.0}},
            {"id": "frontier", "provider": "anthropic", "model": "big",
             "price": {"input_per_mtok": 10.0, "output_per_mtok": 20.0}},
        ],
        "roles": {"candidate": "local", "baseline": "frontier"},
    },
    source="<test>",
)

PLAN = CheckPlan.from_dict(
    {
        "task_id": "escalate-outage",
        "items": [
            {"id": "urgent", "criterion": "T-1001 and T-1004 are urgent",
             "evidence": [{"label": "the store", "connector": "mcp",
                           "probe": "tickets__dump_db", "args": {}}]}
        ],
        "unsatisfiable": [],
    }
)


def solving_agent(model_id, role, task, repeat):
    """An agent that does the task properly."""
    provider = ScriptedProvider([
        scripted_step(
            tool_calls=[("tickets__set_priority", {"id": "T-1001", "priority": "urgent"}),
                        ("tickets__set_priority", {"id": "T-1004", "priority": "urgent"})],
            usage=Usage(1000, 200),
        ),
        scripted_step(text="Escalated both.", usage=Usage(100, 50)),
    ])
    return ModelAgent(model_id, provider)


def idle_agent(model_id, role, task, repeat):
    """An agent that talks and does nothing."""
    return ModelAgent(model_id, ScriptedProvider([scripted_step(text="I would escalate them.")]))


def by_role(model_id, role, task, repeat):
    """Candidate solves; baseline does not."""
    if role is Role.CANDIDATE:
        return solving_agent(model_id, role, task, repeat)
    return idle_agent(model_id, role, task, repeat)


def judge(pass_all=True):
    return ScriptedJudge(
        plan=PLAN,
        grader=lambda p, e: {i.id: ("pass" if pass_all else "fail") for i in p.items},
    )


def single_task_test():
    """The fixture Test narrowed to one Task, to keep the suite quick."""
    test = load_test(FIXTURE)
    return type(test)(
        name=test.name, tasks=(test.task("escalate-outage"),),
        environment=test.environment, repeats=1, path=test.path,
    )


def orchestrate(tmp_path, tests=None, agent_factory=solving_agent, judge_obj=None, **kwargs):
    return Orchestrator(
        roster=ROSTER,
        tests=tests or [single_task_test()],
        results_dir=str(tmp_path),
        agent_factory=agent_factory,
        judge=judge_obj if judge_obj is not None else judge(),
        **kwargs,
    )


class TestOrdering:
    def test_the_candidate_runs_before_the_baseline(self, tmp_path):
        result = orchestrate(tmp_path).run()
        roles = [a.role for a in result.attempts]
        assert roles == [Role.CANDIDATE, Role.BASELINE]

    def test_a_test_finishes_both_roles_before_the_next_test_starts(self, tmp_path):
        test = single_task_test()
        second = type(test)(name="Second", tasks=test.tasks, environment=test.environment,
                            repeats=1, path=test.path)
        result = orchestrate(tmp_path, tests=[test, second]).run()
        names = [(a.test_name, a.role.value) for a in result.attempts]
        assert names == [
            ("Support triage", "candidate"), ("Support triage", "baseline"),
            ("Second", "candidate"), ("Second", "baseline"),
        ]

    def test_every_repeat_runs(self, tmp_path):
        test = single_task_test()
        repeated = type(test)(name=test.name, tasks=test.tasks,
                              environment=test.environment, repeats=3, path=test.path)
        result = orchestrate(tmp_path, tests=[repeated]).run()
        assert len(result.attempts) == 6
        assert sorted(a.repeat for a in result.attempts if a.role is Role.CANDIDATE) == [0, 1, 2]


class TestCheckPlans:
    def test_a_plan_is_made_once_per_task_not_per_attempt(self, tmp_path):
        test = single_task_test()
        repeated = type(test)(name=test.name, tasks=test.tasks,
                              environment=test.environment, repeats=3, path=test.path)
        scripted = judge()
        orchestrate(tmp_path, tests=[repeated], judge_obj=scripted).run()
        assert scripted.plans_made == 1

    def test_the_plan_is_stored(self, tmp_path):
        orchestrate(tmp_path).run()
        stored = json.loads((tmp_path / "plans" / "escalate-outage.json").read_text())
        assert stored["items"][0]["id"] == "urgent"

    def test_the_same_plan_grades_every_attempt(self, tmp_path):
        result = orchestrate(tmp_path).run()
        assert {a.judgement.checks[0].check_id for a in result.attempts} == {"urgent"}


class TestIsolation:
    def test_each_attempt_starts_from_a_clean_environment(self, tmp_path):
        """The baseline does nothing, so it must not inherit the candidate's work."""
        result = orchestrate(tmp_path, agent_factory=by_role, judge_obj=None).run()
        candidate = next(a for a in result.attempts if a.role is Role.CANDIDATE)
        baseline = next(a for a in result.attempts if a.role is Role.BASELINE)

        def urgent(attempt):
            content = attempt.evidence.items[0].content
            return {t["id"] for t in json.loads(content)["tickets"] if t["priority"] == "urgent"}

        assert urgent(candidate) == {"T-1001", "T-1004"}
        assert urgent(baseline) == set()


class TestJudging:
    def test_attempts_are_graded(self, tmp_path):
        result = orchestrate(tmp_path).run()
        assert all(a.judgement.outcome is Outcome.GRADED for a in result.attempts)
        assert all(a.judgement.score == 1.0 for a in result.attempts)

    def test_only_the_first_test_is_judged_by_default(self, tmp_path):
        test = single_task_test()
        second = type(test)(name="Second", tasks=test.tasks, environment=test.environment,
                            repeats=1, path=test.path)
        result = orchestrate(tmp_path, tests=[test, second]).run()
        judged = {a.test_name for a in result.attempts if a.judgement is not None}
        assert judged == {"Support triage"}

    def test_unjudged_attempts_are_marked_unjudged(self, tmp_path):
        test = single_task_test()
        second = type(test)(name="Second", tasks=test.tasks, environment=test.environment,
                            repeats=1, path=test.path)
        result = orchestrate(tmp_path, tests=[test, second]).run()
        assert result.is_judged("Support triage") is True
        assert result.is_judged("Second") is False

    def test_more_tests_can_be_judged_on_request(self, tmp_path):
        test = single_task_test()
        second = type(test)(name="Second", tasks=test.tasks, environment=test.environment,
                            repeats=1, path=test.path)
        result = orchestrate(tmp_path, tests=[test, second], judge_tests=2).run()
        assert all(a.judgement is not None for a in result.attempts)

    def test_judging_can_be_turned_off_entirely(self, tmp_path):
        result = orchestrate(tmp_path, judge_tests=0).run()
        assert all(a.judgement is None for a in result.attempts)

    def test_a_run_without_a_judge_still_executes(self, tmp_path):
        result = Orchestrator(
            roster=ROSTER, tests=[single_task_test()], results_dir=str(tmp_path),
            agent_factory=solving_agent, judge=None,
        ).run()
        assert len(result.attempts) == 2
        assert all(a.judgement is None for a in result.attempts)

    def test_evidence_is_captured_even_when_judging_is_off(self, tmp_path):
        """So an unjudged Test can be judged later without re-running."""
        result = orchestrate(tmp_path, judge_tests=0).run()
        assert all(a.evidence.is_complete for a in result.attempts)


class TestQueue:
    def test_the_queue_is_built_before_anything_runs(self, tmp_path):
        orchestrator = orchestrate(tmp_path)
        assert len(orchestrator.queue) == 2
        assert all(item.state == "pending" for item in orchestrator.queue)

    def test_the_queue_describes_each_unit_of_work(self, tmp_path):
        item = orchestrate(tmp_path).queue[0]
        assert item.test_name == "Support triage"
        assert item.task_id == "escalate-outage"
        assert item.model_id == "local"
        assert item.role is Role.CANDIDATE
        assert item.repeat == 0

    def test_the_queue_is_in_execution_order(self, tmp_path):
        queue = orchestrate(tmp_path).queue
        assert [i.role for i in queue] == [Role.CANDIDATE, Role.BASELINE]

    def test_items_are_marked_done_as_the_run_proceeds(self, tmp_path):
        orchestrator = orchestrate(tmp_path)
        orchestrator.run()
        assert all(item.state == "done" for item in orchestrator.queue)

    def test_the_running_item_is_visible_while_it_runs(self, tmp_path):
        seen = []

        def watcher(event):
            if event.kind == "attempt_started":
                running = [i for i in event.queue if i.state == "running"]
                seen.append(len(running))

        orchestrate(tmp_path, on_event=watcher).run()
        assert seen == [1, 1], "exactly one item runs at a time"

    def test_a_failed_item_is_marked_failed(self, tmp_path):
        def broken(model_id, role, task, repeat):
            return ModelAgent(model_id, ScriptedProvider([scripted_step(error="endpoint down")]))

        orchestrator = orchestrate(tmp_path, agent_factory=broken)
        orchestrator.run()
        assert all(item.state == "failed" for item in orchestrator.queue)

    def test_progress_counts_are_available(self, tmp_path):
        orchestrator = orchestrate(tmp_path)
        assert orchestrator.progress() == (0, 2)
        orchestrator.run()
        assert orchestrator.progress() == (2, 2)


class TestFailureContainment:
    def test_a_provider_failure_does_not_stop_the_run(self, tmp_path):
        def broken(model_id, role, task, repeat):
            return ModelAgent(model_id, ScriptedProvider([scripted_step(error="down")]))

        result = orchestrate(tmp_path, agent_factory=broken).run()
        assert len(result.attempts) == 2
        assert all(a.judgement.outcome is Outcome.FAILED for a in result.attempts)

    def test_an_agent_that_raises_is_contained(self, tmp_path):
        class Exploding:
            id = "boom"
            owns_harness = False

            def run(self, task, connectors, repeat=0):
                raise RuntimeError("the agent exploded")

        result = orchestrate(tmp_path, agent_factory=lambda *a: Exploding()).run()
        assert all("exploded" in a.error for a in result.attempts)

    def test_an_environment_that_will_not_start_is_contained(self, tmp_path):
        from crossbar.domain import ConnectorConfig, EnvironmentSpec

        test = single_task_test()
        broken_env = EnvironmentSpec(
            kind="local",
            connectors=(ConnectorConfig(name="mcp",
                                        options={"servers": [{"name": "x",
                                                              "command": "no-such-binary-xyz"}]}),),
        )
        broken_task = type(test.tasks[0])(
            id=test.tasks[0].id, prompt=test.tasks[0].prompt, golden=test.tasks[0].golden,
            environment=broken_env,
        )
        broken_test = type(test)(name=test.name, tasks=(broken_task,),
                                 environment=broken_env, repeats=1, path=test.path)
        result = orchestrate(tmp_path, tests=[broken_test]).run()
        assert len(result.attempts) == 2
        assert all(a.error for a in result.attempts)


class TestStorage:
    def test_the_layout_matches_the_design(self, tmp_path):
        orchestrate(tmp_path).run()
        assert (tmp_path / "run.json").exists()
        assert (tmp_path / "plans" / "escalate-outage.json").exists()
        attempts = sorted((tmp_path / "attempts").iterdir())
        assert len(attempts) == 2
        for directory in attempts:
            for name in ("attempt.json", "trajectory.json", "evidence.json", "judgement.json"):
                assert (directory / name).exists(), f"{name} missing from {directory}"

    def test_a_run_reloads_from_disk(self, tmp_path):
        original = orchestrate(tmp_path).run()
        restored = load_run(tmp_path / "run.json")
        assert isinstance(restored, RunResult)
        assert len(restored.attempts) == len(original.attempts)
        assert restored.attempts[0].role is Role.CANDIDATE
        assert restored.attempts[0].judgement.score == 1.0

    def test_usage_and_cost_are_recorded(self, tmp_path):
        result = orchestrate(tmp_path).run()
        candidate = next(a for a in result.attempts if a.role is Role.CANDIDATE)
        assert candidate.usage == Usage(1100, 250)
        assert candidate.cost_usd == pytest.approx(1100 / 1e6 * 1.0 + 250 / 1e6 * 2.0)

    def test_the_run_records_which_models_held_which_roles(self, tmp_path):
        result = orchestrate(tmp_path).run()
        assert result.roles["candidate"] == "local"
        assert result.roles["baseline"] == "frontier"

    def test_the_judge_conflict_is_carried_into_storage(self, tmp_path):
        """The report has to be able to say the baseline graded its own work."""
        scripted = judge()
        scripted.model_id = "frontier"
        orchestrate(tmp_path, judge_obj=scripted).run()
        assert load_run(tmp_path / "run.json").judge_is_baseline is True


class TestRejudging:
    def test_stored_attempts_can_be_judged_without_re_running(self, tmp_path):
        orchestrate(tmp_path, judge_tests=0).run()

        restored = load_run(tmp_path / "run.json")
        assert all(a.judgement is None for a in restored.attempts)

        rejudged = Orchestrator.judge_stored(
            tmp_path, judge=judge(), tests=["Support triage"]
        )
        assert all(a.judgement.outcome is Outcome.GRADED for a in rejudged.attempts)

    def test_re_judging_reuses_the_stored_plan(self, tmp_path):
        orchestrate(tmp_path, judge_tests=0).run()
        scripted = judge()
        Orchestrator.judge_stored(tmp_path, judge=scripted, tests=["Support triage"])
        assert scripted.plans_made == 0, "a re-judge must not regenerate the plan"

    def test_the_re_judged_verdict_is_written_back(self, tmp_path):
        orchestrate(tmp_path, judge_tests=0).run()
        Orchestrator.judge_stored(tmp_path, judge=judge(), tests=["Support triage"])
        attempt_dir = sorted((tmp_path / "attempts").iterdir())[0]
        stored = json.loads((attempt_dir / "judgement.json").read_text())
        assert stored["outcome"] == "graded"


class TestPlansAreMadeEvenWhenGradingIsOff:
    """Deferring judgement must not make it impossible."""

    def test_plans_are_written_with_judging_switched_off(self, tmp_path):
        orchestrate(tmp_path, judge_tests=0).run()
        assert (tmp_path / "plans" / "escalate-outage.json").exists()

    def test_evidence_is_captured_with_judging_switched_off(self, tmp_path):
        result = orchestrate(tmp_path, judge_tests=0).run()
        assert all(a.evidence.items for a in result.attempts)

    def test_such_a_run_can_be_judged_later(self, tmp_path):
        orchestrate(tmp_path, judge_tests=0).run()
        rejudged = Orchestrator.judge_stored(tmp_path, judge=judge())
        assert all(a.judgement.outcome is Outcome.GRADED for a in rejudged.attempts)


class TestJudgeConflictIsEstablishedNotAssumed:
    """The conflict of interest is a fact about which model judged, not about
    what the roster would have fallen back to."""

    def test_a_judge_that_names_no_model_reports_no_conflict(self, tmp_path):
        result = orchestrate(tmp_path).run()
        assert result.judge_is_baseline is False

    def test_a_judge_that_is_the_baseline_reports_the_conflict(self, tmp_path):
        scripted = judge()
        scripted.model_id = "frontier"
        result = orchestrate(tmp_path, judge_obj=scripted).run()
        assert result.judge_is_baseline is True

    def test_an_independent_judge_reports_no_conflict(self, tmp_path):
        scripted = judge()
        scripted.model_id = "some-third-model"
        result = orchestrate(tmp_path, judge_obj=scripted).run()
        assert result.judge_is_baseline is False


class TestRoleOverride:
    """The mode can be chosen at run time, so the TUI can toggle between
    assessing one model and comparing two without editing the roster."""

    def test_by_default_every_assigned_role_runs(self, tmp_path):
        assert {i.role for i in orchestrate(tmp_path).queue} == {Role.CANDIDATE, Role.BASELINE}

    def test_a_single_role_can_be_selected(self, tmp_path):
        orchestrator = Orchestrator(
            roster=ROSTER, tests=[single_task_test()], results_dir=str(tmp_path),
            agent_factory=solving_agent, judge=judge(), roles=(Role.CANDIDATE,),
        )
        assert {i.role for i in orchestrator.queue} == {Role.CANDIDATE}
        assert len(orchestrator.queue) == 1

    def test_only_the_selected_role_actually_runs(self, tmp_path):
        result = Orchestrator(
            roster=ROSTER, tests=[single_task_test()], results_dir=str(tmp_path),
            agent_factory=solving_agent, judge=judge(), roles=(Role.CANDIDATE,),
        ).run()
        assert {a.role for a in result.attempts} == {Role.CANDIDATE}

    def test_the_baseline_alone_can_be_selected(self, tmp_path):
        result = Orchestrator(
            roster=ROSTER, tests=[single_task_test()], results_dir=str(tmp_path),
            agent_factory=solving_agent, judge=judge(), roles=(Role.BASELINE,),
        ).run()
        assert {a.model_id for a in result.attempts} == {"frontier"}

    def test_a_single_role_run_analyses_as_a_single_model(self, tmp_path):
        from crossbar.analysis import analyze

        result = Orchestrator(
            roster=ROSTER, tests=[single_task_test()], results_dir=str(tmp_path),
            agent_factory=solving_agent, judge=judge(), roles=(Role.CANDIDATE,),
        ).run()
        assert analyze(result).is_single_model is True

    def test_an_unassigned_role_is_rejected(self, tmp_path):
        from crossbar.roster import RosterError

        with pytest.raises(RosterError, match="judge"):
            Orchestrator(
                roster=ROSTER, tests=[single_task_test()], results_dir=str(tmp_path),
                agent_factory=solving_agent, judge=judge(), roles=(Role.JUDGE,),
            )
