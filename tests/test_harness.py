"""Harness behaviour: the loop that turns model output into MCP tool calls."""
import sys

import pytest

from crossbar.env import LocalEnvironment
from crossbar.harness import ReactHarness, SingleShotHarness
from crossbar.providers import ProviderError, ScriptedProvider, Usage, scripted_step
from crossbar.tasks import EnvironmentSpec, SecuritySpec, ServerSpec, parse_task
from crossbar.trace import RunStatus

PY = sys.executable
NOTES = ServerSpec(name="notes", command=PY, args=("-m", "tests.fixtures.notes_server"))


def make_task(**overrides):
    data = {
        "id": "notes-001",
        "prompt": "Create a note titled Q3.",
        "environment": {
            "kind": "local",
            "servers": [{"name": "notes", "command": PY, "args": ["-m", "tests.fixtures.notes_server"]}],
        },
        "checks": [{"type": "tool_called", "server": "notes", "tool": "create_note"}],
    }
    data.update(overrides)
    return parse_task(data, source="<test>")


@pytest.fixture
def env():
    e = LocalEnvironment(EnvironmentSpec(kind="local", servers=(NOTES,)))
    e.start()
    yield e
    e.stop()


def run(provider, task=None, env=None, harness=None, **kwargs):
    harness = harness or ReactHarness(**kwargs)
    return harness.run(task or make_task(), env, provider, repeat=0)


class TestReactLoop:
    def test_a_single_tool_call_then_an_answer_completes(self, env):
        provider = ScriptedProvider(
            [
                scripted_step(tool_calls=[("notes__create_note", {"title": "Q3"})]),
                scripted_step(text="Created the note."),
            ]
        )
        traj = run(provider, env=env)
        assert traj.status is RunStatus.COMPLETED
        assert traj.final_text == "Created the note."
        assert traj.tool_call_counts() == {"notes.create_note": 1}

    def test_tool_calls_actually_reach_the_mcp_server(self, env):
        provider = ScriptedProvider(
            [
                scripted_step(tool_calls=[("notes__create_note", {"title": "real"})]),
                scripted_step(text="done"),
            ]
        )
        run(provider, env=env)
        assert env.call("notes__list_notes", {}).structured == {
            "notes": [{"title": "real", "body": "", "tags": []}]
        }

    def test_tool_results_are_fed_back_to_the_model(self, env):
        provider = ScriptedProvider(
            [
                scripted_step(tool_calls=[("notes__create_note", {"title": "x"})]),
                scripted_step(text="done"),
            ]
        )
        run(provider, env=env)
        second_request = provider.requests[1]
        tool_messages = [m for m in second_request.messages if m.role == "tool"]
        assert "created note" in tool_messages[0].content

    def test_the_task_prompt_is_the_first_user_message(self, env):
        provider = ScriptedProvider([scripted_step(text="ok")])
        run(provider, env=env)
        user = [m for m in provider.requests[0].messages if m.role == "user"]
        assert user[0].content == "Create a note titled Q3."

    def test_tools_are_offered_to_the_model(self, env):
        provider = ScriptedProvider([scripted_step(text="ok")])
        run(provider, env=env)
        names = {t.name for t in provider.requests[0].tools}
        assert "notes__create_note" in names

    def test_several_tool_calls_in_one_step_all_execute(self, env):
        provider = ScriptedProvider(
            [
                scripted_step(
                    tool_calls=[
                        ("notes__create_note", {"title": "a"}),
                        ("notes__create_note", {"title": "b"}),
                    ]
                ),
                scripted_step(text="done"),
            ]
        )
        traj = run(provider, env=env)
        assert traj.tool_call_count == 2

    def test_a_tool_error_is_reported_back_and_the_run_continues(self, env):
        provider = ScriptedProvider(
            [
                scripted_step(tool_calls=[("notes__explode", {})]),
                scripted_step(text="recovered"),
            ]
        )
        traj = run(provider, env=env)
        assert traj.status is RunStatus.COMPLETED
        assert traj.error_count == 1
        assert traj.final_text == "recovered"

    def test_calling_a_tool_that_does_not_exist_is_reported_not_fatal(self, env):
        provider = ScriptedProvider(
            [
                scripted_step(tool_calls=[("notes__imaginary", {})]),
                scripted_step(text="oh well"),
            ]
        )
        traj = run(provider, env=env)
        assert traj.status is RunStatus.COMPLETED
        assert traj.tool_events[0].is_error is True
        assert "unknown tool" in traj.tool_events[0].result_text

    def test_usage_accumulates_across_steps(self, env):
        provider = ScriptedProvider(
            [
                scripted_step(tool_calls=[("notes__list_notes", {})], usage=Usage(100, 20)),
                scripted_step(text="done", usage=Usage(50, 10)),
            ]
        )
        traj = run(provider, env=env)
        assert traj.usage == Usage(150, 30)

    def test_every_step_is_recorded(self, env):
        provider = ScriptedProvider(
            [
                scripted_step(tool_calls=[("notes__list_notes", {})]),
                scripted_step(text="done"),
            ]
        )
        traj = run(provider, env=env)
        assert traj.step_count == 2

    def test_trajectory_is_labelled_with_task_agent_and_repeat(self, env):
        provider = ScriptedProvider([scripted_step(text="ok")])
        harness = ReactHarness()
        traj = harness.run(make_task(), env, provider, repeat=3, agent_id="qwen+react")
        assert (traj.task_id, traj.agent_id, traj.repeat) == ("notes-001", "qwen+react", 3)


class TestLimits:
    def test_max_steps_stops_the_run(self, env):
        provider = ScriptedProvider(
            [scripted_step(tool_calls=[("notes__list_notes", {})]) for _ in range(10)]
        )
        traj = run(provider, task=make_task(max_steps=3), env=env)
        assert traj.status is RunStatus.MAX_STEPS
        assert traj.step_count == 3

    def test_token_budget_stops_the_run(self, env):
        provider = ScriptedProvider(
            [scripted_step(tool_calls=[("notes__list_notes", {})], usage=Usage(400, 100)) for _ in range(10)]
        )
        traj = run(provider, task=make_task(budget_tokens=1000), env=env)
        assert traj.status is RunStatus.BUDGET_EXCEEDED
        assert traj.usage.total >= 1000

    def test_timeout_stops_the_run(self, env):
        ticks = iter([0, 1, 2, 500, 501, 502, 503])
        provider = ScriptedProvider(
            [scripted_step(tool_calls=[("notes__list_notes", {})]) for _ in range(10)]
        )
        traj = run(provider, task=make_task(timeout_s=60), env=env, clock=lambda: next(ticks))
        assert traj.status is RunStatus.TIMEOUT

    def test_provider_failure_ends_the_run_with_an_error(self, env):
        provider = ScriptedProvider([scripted_step(error="backend is down")])
        traj = run(provider, env=env)
        assert traj.status is RunStatus.ERROR
        assert "backend is down" in traj.error

    def test_running_off_the_script_surfaces_as_an_error(self, env):
        provider = ScriptedProvider([scripted_step(tool_calls=[("notes__list_notes", {})])])
        traj = run(provider, env=env)
        assert traj.status is RunStatus.ERROR


class TestSecurityGate:
    def forbidding_task(self, **kw):
        return make_task(
            security={"forbidden_tools": ["notes.delete_all"]},
            **kw,
        )

    def test_a_forbidden_tool_is_never_executed(self, env):
        env.call("notes__create_note", {"title": "precious"})
        provider = ScriptedProvider(
            [
                scripted_step(tool_calls=[("notes__delete_all", {})]),
                scripted_step(text="done"),
            ]
        )
        run(provider, task=self.forbidding_task(), env=env)
        assert env.call("notes__list_notes", {}).structured["notes"] != []

    def test_a_forbidden_call_is_recorded_as_a_violation(self, env):
        provider = ScriptedProvider(
            [
                scripted_step(tool_calls=[("notes__delete_all", {})]),
                scripted_step(text="done"),
            ]
        )
        traj = run(provider, task=self.forbidding_task(), env=env)
        assert any("delete_all" in v for v in traj.security_violations)

    def test_the_blocked_call_is_reported_to_the_model_as_an_error(self, env):
        provider = ScriptedProvider(
            [
                scripted_step(tool_calls=[("notes__delete_all", {})]),
                scripted_step(text="done"),
            ]
        )
        traj = run(provider, task=self.forbidding_task(), env=env)
        assert traj.tool_events[0].blocked is True
        assert traj.tool_events[0].is_error is True

    def test_exceeding_max_tool_calls_is_a_violation(self, env):
        task = make_task(security={"max_tool_calls": 1}, max_steps=6)
        provider = ScriptedProvider(
            [
                scripted_step(tool_calls=[("notes__list_notes", {})]),
                scripted_step(tool_calls=[("notes__list_notes", {})]),
                scripted_step(text="done"),
            ]
        )
        traj = run(provider, task=task, env=env)
        assert traj.security_violations


class TestVerifyVariant:
    """The react-plus-verify harness: same model, one extra self-check turn."""

    def test_verify_variant_asks_the_model_to_check_its_work_once(self, env):
        provider = ScriptedProvider(
            [
                scripted_step(text="I think I am done."),
                scripted_step(text="Checked, all good."),
            ]
        )
        traj = run(provider, env=env, harness=ReactHarness(verify=True))
        assert traj.status is RunStatus.COMPLETED
        assert traj.final_text == "Checked, all good."
        assert any("verify" in m.content.lower() for m in provider.requests[1].messages)

    def test_verify_only_fires_once(self, env):
        provider = ScriptedProvider(
            [scripted_step(text="done"), scripted_step(text="still done")]
        )
        traj = run(provider, env=env, harness=ReactHarness(verify=True))
        assert traj.step_count == 2
        assert provider.steps_used == 2

    def test_plain_variant_does_not_add_the_extra_turn(self, env):
        provider = ScriptedProvider([scripted_step(text="done")])
        traj = run(provider, env=env, harness=ReactHarness(verify=False))
        assert traj.step_count == 1

    def test_harness_ids_distinguish_the_variants(self):
        assert ReactHarness().id != ReactHarness(verify=True).id


class TestSingleShotHarness:
    def test_makes_exactly_one_call_with_no_tools(self, env):
        provider = ScriptedProvider([scripted_step(text="I would create a note.")])
        traj = run(provider, env=env, harness=SingleShotHarness())
        assert traj.step_count == 1
        assert provider.requests[0].tools == []
        assert traj.final_text == "I would create a note."

    def test_records_no_tool_events(self, env):
        provider = ScriptedProvider([scripted_step(text="nothing to do")])
        traj = run(provider, env=env, harness=SingleShotHarness())
        assert traj.tool_call_count == 0

    def test_provider_error_is_captured(self, env):
        provider = ScriptedProvider([scripted_step(error="nope")])
        traj = run(provider, env=env, harness=SingleShotHarness())
        assert traj.status is RunStatus.ERROR
