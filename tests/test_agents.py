"""Agents: anything that can execute a Task and return a Trajectory."""
import sys

import pytest

from crossbar.agents import Agent, ModelAgent
from crossbar.connectors import McpConnector
from crossbar.domain import ConnectorConfig, EnvironmentSpec, Limits, Task
from crossbar.environment import LocalEnvironment
from crossbar.providers import ScriptedProvider, scripted_step
from crossbar.trace import RunStatus

PY = sys.executable

MCP = ConnectorConfig(
    name="mcp",
    options={"servers": [{"name": "tickets", "command": "${CROSSBAR_PYTHON}",
                          "args": ["-m", "tests.fixtures.tickets_server"]}]},
)


@pytest.fixture
def connectors():
    env = LocalEnvironment(EnvironmentSpec(kind="local", connectors=(MCP,)))
    connector = McpConnector(MCP)
    connector.setup(env.start())
    yield [connector]
    connector.teardown()
    env.stop()


def task() -> Task:
    return Task(id="t", prompt="Escalate T-1001.", golden="T-1001 is urgent.", limits=Limits())


class TestModelAgent:
    def test_it_satisfies_the_agent_protocol(self):
        agent = ModelAgent("local", ScriptedProvider([]))
        assert isinstance(agent, Agent)

    def test_it_runs_a_task_and_returns_a_trajectory(self, connectors):
        provider = ScriptedProvider([
            scripted_step(tool_calls=[("tickets__set_priority",
                                       {"id": "T-1001", "priority": "urgent"})]),
            scripted_step(text="done"),
        ])
        traj = ModelAgent("local", provider).run(task(), connectors)
        assert traj.status is RunStatus.COMPLETED
        assert traj.final_text == "done"

    def test_the_agent_id_is_the_model_id(self):
        assert ModelAgent("local-qwen", ScriptedProvider([])).id == "local-qwen"

    def test_it_does_not_own_a_harness(self):
        """It uses ours, so a comparison against another ModelAgent is controlled."""
        assert ModelAgent("local", ScriptedProvider([])).owns_harness is False

    def test_the_trajectory_is_labelled_with_the_agent_and_repeat(self, connectors):
        traj = ModelAgent("local", ScriptedProvider([scripted_step(text="ok")])).run(
            task(), connectors, repeat=2
        )
        assert traj.agent_id == "local"
        assert traj.repeat == 2

    def test_a_provider_failure_is_contained_in_the_trajectory(self, connectors):
        traj = ModelAgent("local", ScriptedProvider([scripted_step(error="down")])).run(
            task(), connectors
        )
        assert traj.status is RunStatus.ERROR
        assert "down" in traj.error

    def test_state_left_behind_is_observable_afterwards(self, connectors):
        provider = ScriptedProvider([
            scripted_step(tool_calls=[("tickets__set_priority",
                                       {"id": "T-1004", "priority": "urgent"})]),
            scripted_step(text="done"),
        ])
        ModelAgent("local", provider).run(task(), connectors)
        ticket = connectors[0].probe("tickets__get_ticket", {"id": "T-1004"}).structured["ticket"]
        assert ticket["priority"] == "urgent"


class TestAgentSeam:
    """Whatever the agent is, everything downstream sees the same shape."""

    def test_an_agent_that_owns_its_harness_is_flagged(self):
        class FakeCliAgent:
            id = "some-cli"
            owns_harness = True

            def run(self, task, connectors, repeat=0):
                raise NotImplementedError

        assert isinstance(FakeCliAgent(), Agent)
        assert FakeCliAgent().owns_harness is True

    def test_the_flag_is_what_the_report_uses_to_label_the_comparison(self):
        """A CLI agent brings its own harness, so the comparison is a product
        comparison rather than a controlled one, and that has to be visible."""
        controlled = ModelAgent("a", ScriptedProvider([]))
        assert controlled.owns_harness is False
