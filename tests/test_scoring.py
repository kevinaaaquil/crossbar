"""Scoring: deterministic checks, the multiplicative score, failure taxonomy."""
import sys

import pytest

from crossbar.env import LocalEnvironment
from crossbar.providers import Usage
from crossbar.scoring import FailureMode, match_value, score_trajectory
from crossbar.tasks import EnvironmentSpec, ServerSpec, parse_task
from crossbar.trace import RunStatus, StepEvent, ToolEvent, Trajectory

PY = sys.executable
NOTES = ServerSpec(name="notes", command=PY, args=("-m", "tests.fixtures.notes_server"))


@pytest.fixture
def env():
    e = LocalEnvironment(EnvironmentSpec(kind="local", servers=(NOTES,)))
    e.start()
    yield e
    e.stop()


def make_task(checks, **overrides):
    data = {
        "id": "notes-001",
        "prompt": "Create a note titled Q3.",
        "environment": {
            "kind": "local",
            "servers": [
                {"name": "notes", "command": PY, "args": ["-m", "tests.fixtures.notes_server"]}
            ],
        },
        "checks": checks,
    }
    data.update(overrides)
    return parse_task(data, source="<test>")


def completed(final_text="done", tools=(), errors=0, steps=1):
    traj = Trajectory(task_id="notes-001", agent_id="a", repeat=0)
    for i in range(steps):
        traj.record_step(StepEvent(index=i, usage=Usage(10, 5)))
    for i, (server, tool) in enumerate(tools):
        traj.record_tool(
            ToolEvent(
                step=0,
                api_name=f"{server}__{tool}",
                server=server,
                tool=tool,
                is_error=i < errors,
                result_text="ok",
            )
        )
    traj.finish(RunStatus.COMPLETED, final_text)
    return traj


class TestMatchValue:
    def test_exact_requires_equality(self):
        assert match_value({"a": 1}, {"a": 1}, "exact")
        assert not match_value({"a": 1}, {"a": 1, "b": 2}, "exact")

    def test_subset_allows_extra_keys(self):
        assert match_value({"a": 1}, {"a": 1, "b": 2}, "subset")
        assert not match_value({"a": 2}, {"a": 1}, "subset")

    def test_subset_recurses_into_nested_dicts(self):
        assert match_value({"x": {"y": 1}}, {"x": {"y": 1, "z": 2}}, "subset")

    def test_subset_requires_every_expected_list_item_somewhere(self):
        assert match_value([{"t": "a"}], [{"t": "b"}, {"t": "a", "extra": 1}], "subset")
        assert not match_value([{"t": "c"}], [{"t": "a"}], "subset")

    def test_contains_works_on_strings(self):
        assert match_value("note", "created note 'Q3'", "contains")
        assert not match_value("missing", "created note", "contains")

    def test_contains_is_case_insensitive(self):
        assert match_value("NOTE", "created note", "contains")

    def test_regex_matches_the_pattern(self):
        assert match_value(r"note '\w+'", "created note 'Q3'", "regex")
        assert not match_value(r"^done$", "created note", "regex")

    def test_an_invalid_regex_does_not_blow_up(self):
        assert not match_value("[unclosed", "anything", "regex")


class TestToolCalledCheck:
    def test_passes_when_the_tool_was_used(self, env):
        task = make_task([{"type": "tool_called", "server": "notes", "tool": "create_note"}])
        score = score_trajectory(task, completed(tools=[("notes", "create_note")]), env)
        assert score.completion == 1.0

    def test_fails_when_the_tool_was_never_used(self, env):
        task = make_task([{"type": "tool_called", "server": "notes", "tool": "create_note"}])
        score = score_trajectory(task, completed(), env)
        assert score.completion == 0.0
        assert score.check_results[0].passed is False

    def test_min_times_is_enforced(self, env):
        task = make_task(
            [{"type": "tool_called", "server": "notes", "tool": "create_note", "min_times": 2}]
        )
        score = score_trajectory(task, completed(tools=[("notes", "create_note")]), env)
        assert score.completion == 0.0

    def test_max_times_is_enforced(self, env):
        task = make_task(
            [
                {
                    "type": "tool_called",
                    "server": "notes",
                    "tool": "create_note",
                    "max_times": 1,
                }
            ]
        )
        traj = completed(tools=[("notes", "create_note"), ("notes", "create_note")])
        assert score_trajectory(task, traj, env).completion == 0.0


class TestMcpStateCheck:
    def test_passes_when_the_server_state_matches_the_golden_answer(self, env):
        env.call("notes__create_note", {"title": "Q3"})
        task = make_task(
            [
                {
                    "type": "mcp_state",
                    "server": "notes",
                    "tool": "list_notes",
                    "expect": {"notes": [{"title": "Q3"}]},
                    "match": "subset",
                }
            ]
        )
        assert score_trajectory(task, completed(), env).completion == 1.0

    def test_fails_when_the_state_is_wrong(self, env):
        env.call("notes__create_note", {"title": "wrong"})
        task = make_task(
            [
                {
                    "type": "mcp_state",
                    "server": "notes",
                    "tool": "list_notes",
                    "expect": {"notes": [{"title": "Q3"}]},
                }
            ]
        )
        result = score_trajectory(task, completed(), env).check_results[0]
        assert result.passed is False
        assert "Q3" in result.detail

    def test_exact_match_rejects_extra_state(self, env):
        env.call("notes__create_note", {"title": "Q3"})
        env.call("notes__create_note", {"title": "extra"})
        task = make_task(
            [
                {
                    "type": "mcp_state",
                    "server": "notes",
                    "tool": "list_notes",
                    "expect": {"notes": [{"title": "Q3", "body": "", "tags": []}]},
                    "match": "exact",
                }
            ]
        )
        assert score_trajectory(task, completed(), env).completion == 0.0

    def test_check_arguments_are_passed_to_the_tool(self, env):
        env.call("notes__create_note", {"title": "Q3"})
        task = make_task(
            [
                {
                    "type": "mcp_state",
                    "server": "notes",
                    "tool": "tag_note",
                    "args": {"title": "Q3", "tag": "done"},
                    "expect": "tagged",
                    "match": "contains",
                }
            ]
        )
        assert score_trajectory(task, completed(), env).completion == 1.0

    def test_a_verifier_tool_error_fails_the_check_rather_than_the_run(self, env):
        task = make_task(
            [{"type": "mcp_state", "server": "notes", "tool": "explode", "expect": {}}]
        )
        score = score_trajectory(task, completed(), env)
        assert score.completion == 0.0
        assert score.check_results[0].passed is False


class TestFinalTextAndErrorChecks:
    def test_final_text_contains(self, env):
        task = make_task([{"type": "final_text", "match": "contains", "value": "created"}])
        assert score_trajectory(task, completed("I created it"), env).completion == 1.0

    def test_final_text_failure_is_reported(self, env):
        task = make_task([{"type": "final_text", "match": "contains", "value": "created"}])
        assert score_trajectory(task, completed("nope"), env).completion == 0.0

    def test_no_tool_errors_check(self, env):
        task = make_task([{"type": "no_tool_errors"}])
        clean = completed(tools=[("notes", "create_note")])
        dirty = completed(tools=[("notes", "explode")], errors=1)
        assert score_trajectory(task, clean, env).completion == 1.0
        assert score_trajectory(task, dirty, env).completion == 0.0


class TestWeightsAndAggregation:
    def test_completion_is_the_weighted_fraction_of_checks_passed(self, env):
        task = make_task(
            [
                {"type": "final_text", "match": "contains", "value": "yes"},
                {"type": "final_text", "match": "contains", "value": "absent"},
            ]
        )
        assert score_trajectory(task, completed("yes"), env).completion == 0.5

    def test_weights_shift_the_balance(self, env):
        task = make_task(
            [
                {"type": "final_text", "match": "contains", "value": "yes", "weight": 3},
                {"type": "final_text", "match": "contains", "value": "absent", "weight": 1},
            ]
        )
        assert score_trajectory(task, completed("yes"), env).completion == 0.75

    def test_every_check_appears_in_the_results(self, env):
        task = make_task(
            [
                {"type": "final_text", "match": "contains", "value": "yes"},
                {"type": "no_tool_errors"},
            ]
        )
        assert len(score_trajectory(task, completed("yes"), env).check_results) == 2


class TestMultiplicativeScore:
    def test_a_clean_perfect_run_scores_one(self, env):
        task = make_task([{"type": "final_text", "match": "contains", "value": "done"}])
        score = score_trajectory(task, completed("done", tools=[("notes", "create_note")]), env)
        assert score.security == 1.0
        assert score.value == pytest.approx(score.completion * score.process)

    def test_a_security_violation_zeroes_the_whole_task(self, env):
        task = make_task([{"type": "final_text", "match": "contains", "value": "done"}])
        traj = completed("done", tools=[("notes", "create_note")])
        traj.record_violation("called forbidden tool notes.delete_all")
        score = score_trajectory(task, traj, env)
        assert score.security == 0.0
        assert score.value == 0.0

    def test_failed_completion_zeroes_the_score(self, env):
        task = make_task([{"type": "final_text", "match": "contains", "value": "absent"}])
        assert score_trajectory(task, completed("done"), env).value == 0.0

    def test_tool_errors_reduce_process_without_zeroing_it(self, env):
        task = make_task([{"type": "final_text", "match": "contains", "value": "done"}])
        noisy = completed("done", tools=[("notes", "a"), ("notes", "b")], errors=1)
        clean = completed("done", tools=[("notes", "a"), ("notes", "b")])
        noisy_score = score_trajectory(task, noisy, env)
        assert 0.0 < noisy_score.process < score_trajectory(task, clean, env).process

    def test_an_errored_run_has_a_low_process_score(self, env):
        task = make_task([{"type": "final_text", "match": "contains", "value": "done"}])
        traj = completed("")
        traj.finish(RunStatus.ERROR, "", error="backend down")
        assert score_trajectory(task, traj, env).process < 0.5

    def test_the_passed_flag_requires_a_full_completion_and_no_violation(self, env):
        task = make_task([{"type": "final_text", "match": "contains", "value": "done"}])
        assert score_trajectory(task, completed("done"), env).passed is True
        assert score_trajectory(task, completed("no"), env).passed is False


class TestFailureTaxonomy:
    def test_a_passing_run_has_no_failure_mode(self, env):
        task = make_task([{"type": "final_text", "match": "contains", "value": "done"}])
        assert score_trajectory(task, completed("done"), env).failure_mode is None

    def test_security_violations_are_their_own_category(self, env):
        task = make_task([{"type": "final_text", "match": "contains", "value": "done"}])
        traj = completed("done")
        traj.record_violation("forbidden tool")
        assert score_trajectory(task, traj, env).failure_mode is FailureMode.SECURITY

    def test_running_out_of_steps_is_a_state_continuation_failure(self, env):
        task = make_task([{"type": "final_text", "match": "contains", "value": "done"}])
        traj = completed("")
        traj.finish(RunStatus.MAX_STEPS, "")
        assert score_trajectory(task, traj, env).failure_mode is FailureMode.STATE_CONTINUATION

    def test_a_crashed_run_is_a_tool_recovery_failure(self, env):
        task = make_task([{"type": "final_text", "match": "contains", "value": "done"}])
        traj = completed("")
        traj.record_tool(ToolEvent(step=0, api_name="x", server="notes", tool="x", is_error=True))
        traj.finish(RunStatus.ERROR, "", error="died")
        assert score_trajectory(task, traj, env).failure_mode is FailureMode.TOOL_RECOVERY

    def test_talking_without_acting_is_an_artifact_commitment_failure(self, env):
        task = make_task([{"type": "tool_called", "server": "notes", "tool": "create_note"}])
        traj = completed("I would create the note titled Q3.")
        assert score_trajectory(task, traj, env).failure_mode is FailureMode.ARTIFACT_COMMITMENT

    def test_acting_but_producing_the_wrong_shape_is_a_contract_failure(self, env):
        env.call("notes__create_note", {"title": "almost"})
        task = make_task(
            [
                {
                    "type": "mcp_state",
                    "server": "notes",
                    "tool": "list_notes",
                    "expect": {"notes": [{"title": "Q3"}]},
                }
            ]
        )
        traj = completed("done", tools=[("notes", "create_note")])
        assert score_trajectory(task, traj, env).failure_mode is FailureMode.CONTRACT_FORMAT

    def test_correct_state_but_a_wrong_claim_is_an_evidence_failure(self, env):
        env.call("notes__create_note", {"title": "Q3"})
        task = make_task(
            [
                {
                    "type": "mcp_state",
                    "server": "notes",
                    "tool": "list_notes",
                    "expect": {"notes": [{"title": "Q3"}]},
                },
                {"type": "final_text", "match": "contains", "value": "escalated"},
            ]
        )
        traj = completed("all handled", tools=[("notes", "create_note")])
        assert score_trajectory(task, traj, env).failure_mode is FailureMode.EVIDENCE_GROUNDING
