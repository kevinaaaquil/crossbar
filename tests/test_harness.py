"""The harness: the loop that turns model output into connector calls."""
import sys

import pytest

from crossbar.connectors import McpConnector
from crossbar.domain import ConnectorConfig, Limits, Task
from crossbar.environment import EnvironmentHandle, LocalEnvironment
from crossbar.domain import EnvironmentSpec
from crossbar.harness import Harness
from crossbar.providers import ProviderError, ScriptedProvider, Usage, scripted_step
from crossbar.trace import RunStatus
from tests.fixtures.echo_connector import EchoConnector

PY = sys.executable

MCP = ConnectorConfig(
    name="mcp",
    options={"servers": [{"name": "tickets", "command": "${CROSSBAR_PYTHON}",
                          "args": ["-m", "tests.fixtures.tickets_server"]}]},
)


def make_task(**overrides) -> Task:
    data = {
        "id": "escalate",
        "prompt": "Escalate T-1001 to urgent.",
        "golden": "T-1001 has priority urgent.",
        "limits": Limits(),
    }
    data.update(overrides)
    return Task(**data)


@pytest.fixture
def env():
    e = LocalEnvironment(EnvironmentSpec(kind="local", connectors=(MCP,)))
    yield e
    e.stop()


@pytest.fixture
def connectors(env):
    handle = env.start()
    connector = McpConnector(MCP)
    connector.setup(handle)
    yield [connector]
    connector.teardown()


def run(provider, connectors, task=None, harness=None, **kwargs):
    harness = harness or Harness(**kwargs)
    return harness.run(task or make_task(), connectors, provider)


class TestTheLoop:
    def test_a_tool_call_then_an_answer_completes(self, connectors):
        provider = ScriptedProvider([
            scripted_step(tool_calls=[("tickets__set_priority",
                                       {"id": "T-1001", "priority": "urgent"})]),
            scripted_step(text="Escalated T-1001."),
        ])
        traj = run(provider, connectors)
        assert traj.status is RunStatus.COMPLETED
        assert traj.final_text == "Escalated T-1001."

    def test_calls_reach_the_real_server(self, connectors):
        provider = ScriptedProvider([
            scripted_step(tool_calls=[("tickets__set_priority",
                                       {"id": "T-1001", "priority": "urgent"})]),
            scripted_step(text="done"),
        ])
        run(provider, connectors)
        ticket = connectors[0].probe("tickets__get_ticket", {"id": "T-1001"}).structured["ticket"]
        assert ticket["priority"] == "urgent"

    def test_results_are_fed_back_to_the_model(self, connectors):
        provider = ScriptedProvider([
            scripted_step(tool_calls=[("tickets__get_ticket", {"id": "T-1001"})]),
            scripted_step(text="done"),
        ])
        run(provider, connectors)
        tool_messages = [m for m in provider.requests[1].messages if m.role == "tool"]
        assert "T-1001" in tool_messages[0].content

    def test_the_task_prompt_is_the_first_user_message(self, connectors):
        provider = ScriptedProvider([scripted_step(text="ok")])
        run(provider, connectors)
        user = [m for m in provider.requests[0].messages if m.role == "user"]
        assert user[0].content == "Escalate T-1001 to urgent."

    def test_every_connector_tool_is_offered(self, connectors):
        provider = ScriptedProvider([scripted_step(text="ok")])
        run(provider, connectors)
        names = {t.name for t in provider.requests[0].tools}
        assert "tickets__set_priority" in names
        assert "tickets__dump_db" in names

    def test_several_calls_in_one_step_all_execute(self, connectors):
        provider = ScriptedProvider([
            scripted_step(tool_calls=[
                ("tickets__set_priority", {"id": "T-1001", "priority": "urgent"}),
                ("tickets__set_priority", {"id": "T-1004", "priority": "urgent"}),
            ]),
            scripted_step(text="done"),
        ])
        traj = run(provider, connectors)
        assert traj.tool_call_count == 2

    def test_a_tool_error_is_reported_and_the_run_continues(self, connectors):
        provider = ScriptedProvider([
            scripted_step(tool_calls=[("tickets__explode", {})]),
            scripted_step(text="recovered"),
        ])
        traj = run(provider, connectors)
        assert traj.status is RunStatus.COMPLETED
        assert traj.error_count == 1

    def test_an_unknown_tool_is_reported_not_fatal(self, connectors):
        provider = ScriptedProvider([
            scripted_step(tool_calls=[("tickets__imaginary", {})]),
            scripted_step(text="oh well"),
        ])
        traj = run(provider, connectors)
        assert traj.status is RunStatus.COMPLETED
        assert traj.tool_events[0].is_error is True
        assert "unknown tool" in traj.tool_events[0].result_text

    def test_steps_and_usage_are_recorded(self, connectors):
        provider = ScriptedProvider([
            scripted_step(tool_calls=[("tickets__list_tickets", {})], usage=Usage(100, 20)),
            scripted_step(text="done", usage=Usage(50, 10)),
        ])
        traj = run(provider, connectors)
        assert traj.step_count == 2
        assert traj.usage == Usage(150, 30)


class TestRouting:
    """The harness must not know what kind of connector it is talking to."""

    def test_calls_route_to_the_owning_connector(self, connectors):
        echo = EchoConnector()
        echo.setup(EnvironmentHandle(workspace=""))
        provider = ScriptedProvider([
            scripted_step(tool_calls=[("echo__say", {"message": "hello"})]),
            scripted_step(tool_calls=[("tickets__list_tickets", {})]),
            scripted_step(text="done"),
        ])
        traj = run(provider, [*connectors, echo])
        assert echo.seen == ["hello"]
        assert traj.tool_call_count == 2

    def test_tools_from_every_connector_are_offered(self, connectors):
        echo = EchoConnector()
        provider = ScriptedProvider([scripted_step(text="ok")])
        run(provider, [*connectors, echo])
        names = {t.name for t in provider.requests[0].tools}
        assert {"tickets__list_tickets", "echo__say"} <= names

    def test_a_connector_error_becomes_a_tool_error(self, connectors):
        echo = EchoConnector()
        provider = ScriptedProvider([
            scripted_step(tool_calls=[("echo__nope", {})]),
            scripted_step(text="done"),
        ])
        traj = run(provider, [*connectors, echo])
        assert traj.tool_events[0].is_error is True

    def test_the_harness_imports_nothing_connector_specific(self):
        """If this fails, the seam has leaked and a new connector means a rewrite."""
        import inspect

        import crossbar.harness.loop as loop

        source = inspect.getsource(loop)
        assert "mcp" not in source.lower()
        assert "docker" not in source.lower()


class TestLimits:
    def test_max_steps_stops_the_run(self, connectors):
        provider = ScriptedProvider(
            [scripted_step(tool_calls=[("tickets__list_tickets", {})]) for _ in range(10)]
        )
        traj = run(provider, connectors, task=make_task(limits=Limits(max_steps=3)))
        assert traj.status is RunStatus.MAX_STEPS
        assert traj.step_count == 3

    def test_the_token_budget_stops_the_run(self, connectors):
        provider = ScriptedProvider([
            scripted_step(tool_calls=[("tickets__list_tickets", {})], usage=Usage(400, 100))
            for _ in range(10)
        ])
        traj = run(provider, connectors, task=make_task(limits=Limits(max_tokens=1000)))
        assert traj.status is RunStatus.BUDGET_EXCEEDED

    def test_the_clock_stops_the_run(self, connectors):
        ticks = iter([0, 1, 2, 500, 501, 502, 503, 504])
        provider = ScriptedProvider(
            [scripted_step(tool_calls=[("tickets__list_tickets", {})]) for _ in range(10)]
        )
        traj = run(provider, connectors, task=make_task(limits=Limits(timeout_s=60)),
                   clock=lambda: next(ticks))
        assert traj.status is RunStatus.TIMEOUT

    def test_a_provider_failure_ends_the_run_with_an_error(self, connectors):
        traj = run(ScriptedProvider([scripted_step(error="backend down")]), connectors)
        assert traj.status is RunStatus.ERROR
        assert "backend down" in traj.error

    def test_running_off_the_script_is_an_error_not_a_crash(self, connectors):
        provider = ScriptedProvider([scripted_step(tool_calls=[("tickets__list_tickets", {})])])
        assert run(provider, connectors).status is RunStatus.ERROR


class TestIntegrationAgainstARealTask:
    def test_an_agent_completes_a_fixture_task_and_leaves_checkable_state(self, connectors):
        from crossbar.domain import load_test

        task = load_test("tests/fixtures/tests/support-triage").task("escalate-outage")
        provider = ScriptedProvider([
            scripted_step(tool_calls=[("tickets__search_tickets", {"query": "outage"})]),
            scripted_step(tool_calls=[
                ("tickets__set_priority", {"id": "T-1001", "priority": "urgent"}),
                ("tickets__set_priority", {"id": "T-1004", "priority": "urgent"}),
            ]),
            scripted_step(text="Escalated T-1001 and T-1004."),
        ])
        traj = Harness().run(task, connectors, provider)

        assert traj.status is RunStatus.COMPLETED
        dump = connectors[0].probe("tickets__dump_db", {}).structured["tickets"]
        urgent = {t["id"] for t in dump if t["priority"] == "urgent"}
        assert urgent == {"T-1001", "T-1004"}
        assert next(t for t in dump if t["id"] == "T-1002")["priority"] == "normal"
