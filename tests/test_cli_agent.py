"""The CLI agent: an external agent that brings its own harness.

Driven against a fake `claude` binary — the parser is checked against the real
CLI's observed output shape, not against what it was assumed to be.
"""
import json
import stat
import sys

import pytest

from crossbar.agents import Agent, CliAgent
from crossbar.connectors import McpConnector
from crossbar.domain import ConnectorConfig, EnvironmentSpec, Limits, Task
from crossbar.environment import LocalEnvironment
from crossbar.trace import RunStatus

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
        "limits": Limits(max_steps=9, timeout_s=60),
        "environment": EnvironmentSpec(kind="local", connectors=(MCP,)),
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


@pytest.fixture
def fake(tmp_path, monkeypatch):
    script = tmp_path / "claude"
    script.write_text(f'#!/bin/sh\nexec "{PY}" -m tests.fixtures.fake_claude "$@"\n')
    script.chmod(script.stat().st_mode | stat.S_IEXEC)

    argv_log = tmp_path / "argv.txt"
    mcp_log = tmp_path / "mcp.json"
    monkeypatch.setenv("FAKE_CLAUDE_ARGV_LOG", str(argv_log))
    monkeypatch.setenv("FAKE_CLAUDE_MCP_LOG", str(mcp_log))

    class Handle:
        binary = str(script)

        @staticmethod
        def argv():
            return argv_log.read_text().split("\n")

        @staticmethod
        def mcp_config():
            return json.loads(mcp_log.read_text())

        @staticmethod
        def stream(events):
            path = tmp_path / "stream.jsonl"
            path.write_text("\n".join(json.dumps(e) for e in events))
            monkeypatch.setenv("FAKE_CLAUDE_STREAM", str(path))

    return Handle


class TestInvocation:
    def test_it_runs_the_cli_headless(self, fake, connectors):
        CliAgent("cc", claude_bin=fake.binary).run(make_task(), connectors)
        argv = fake.argv()
        assert "-p" in argv
        assert "stream-json" in argv
        assert "--verbose" in argv

    def test_the_task_prompt_is_passed(self, fake, connectors):
        CliAgent("cc", claude_bin=fake.binary).run(make_task(), connectors)
        assert "Escalate T-1001 to urgent." in fake.argv()

    def test_it_runs_bare_by_default(self, fake, connectors):
        """Otherwise the operator's own hooks, skills and memory contaminate the
        measurement — observed directly against the real CLI."""
        CliAgent("cc", claude_bin=fake.binary).run(make_task(), connectors)
        assert "--bare" in fake.argv()

    def test_bare_can_be_turned_off(self, fake, connectors):
        CliAgent("cc", claude_bin=fake.binary, bare=False).run(make_task(), connectors)
        assert "--bare" not in fake.argv()

    def test_only_the_tasks_mcp_servers_are_offered(self, fake, connectors):
        CliAgent("cc", claude_bin=fake.binary).run(make_task(), connectors)
        assert "--strict-mcp-config" in fake.argv()
        config = fake.mcp_config()
        assert list(config["mcpServers"]) == ["tickets"]

    def test_the_servers_share_the_run_workspace(self, fake, connectors):
        """So the state the CLI leaves behind is state we can read back."""
        CliAgent("cc", claude_bin=fake.binary).run(make_task(), connectors)
        env = fake.mcp_config()["mcpServers"]["tickets"]["env"]
        assert env["CROSSBAR_WORKSPACE"] == connectors[0].handle.workspace

    def test_only_the_tasks_tools_are_allowed(self, fake, connectors):
        CliAgent("cc", claude_bin=fake.binary).run(make_task(), connectors)
        argv = fake.argv()
        allowed = argv[argv.index("--allowedTools") + 1]
        assert "mcp__tickets__set_priority" in allowed
        assert "Bash" not in allowed

    def test_the_step_limit_is_passed_through(self, fake, connectors):
        CliAgent("cc", claude_bin=fake.binary).run(make_task(), connectors)
        argv = fake.argv()
        assert argv[argv.index("--max-turns") + 1] == "9"

    def test_the_model_is_forwarded(self, fake, connectors):
        CliAgent("cc", model="opus", claude_bin=fake.binary).run(make_task(), connectors)
        argv = fake.argv()
        assert argv[argv.index("--model") + 1] == "opus"

    def test_the_config_file_is_cleaned_up(self, fake, connectors):
        import os

        CliAgent("cc", claude_bin=fake.binary).run(make_task(), connectors)
        argv = fake.argv()
        assert not os.path.exists(argv[argv.index("--mcp-config") + 1])


class TestParsing:
    def test_the_result_becomes_the_final_answer(self, fake, connectors):
        traj = CliAgent("cc", claude_bin=fake.binary).run(make_task(), connectors)
        assert traj.status is RunStatus.COMPLETED
        assert traj.final_text == "Done."

    def test_thinking_blocks_are_not_treated_as_text(self, fake, connectors):
        traj = CliAgent("cc", claude_bin=fake.binary).run(make_task(), connectors)
        assert traj.steps[0].text == "Working on it."
        assert "hidden" not in traj.steps[0].text

    def test_cache_tokens_are_counted(self, fake, connectors):
        """The real CLI reports cache_creation and cache_read alongside
        input_tokens; ignoring them understates usage by orders of magnitude."""
        traj = CliAgent("cc", claude_bin=fake.binary).run(make_task(), connectors)
        assert traj.usage.input_tokens == 10 + 100 + 500
        assert traj.usage.output_tokens == 20

    def test_the_reported_cost_is_kept(self, fake, connectors):
        traj = CliAgent("cc", claude_bin=fake.binary).run(make_task(), connectors)
        assert traj.reported_cost_usd == pytest.approx(0.0042)

    def test_mcp_tool_calls_are_recorded(self, fake, connectors):
        fake.stream([
            {"type": "assistant", "message": {"role": "assistant", "content": [
                {"type": "tool_use", "id": "t1", "name": "mcp__tickets__set_priority",
                 "input": {"id": "T-1001", "priority": "urgent"}}], "usage": {}}},
            {"type": "user", "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "t1", "content": "done"}]}},
            {"type": "result", "subtype": "success", "is_error": False, "result": "ok"},
        ])
        traj = CliAgent("cc", claude_bin=fake.binary).run(make_task(), connectors)
        event = traj.tool_events[0]
        assert (event.server, event.tool) == ("tickets", "set_priority")
        assert event.arguments == {"id": "T-1001", "priority": "urgent"}
        assert event.result_text == "done"

    def test_unknown_event_types_are_ignored(self, fake, connectors):
        """The real stream carries hook_started, rate_limit_event and more."""
        fake.stream([
            {"type": "system", "subtype": "hook_started", "hook_id": "x"},
            {"type": "rate_limit_event", "rate_limit_info": {}},
            {"type": "system", "subtype": "thinking_tokens", "estimated_tokens": 50},
            {"type": "result", "subtype": "success", "is_error": False, "result": "fine"},
        ])
        traj = CliAgent("cc", claude_bin=fake.binary).run(make_task(), connectors)
        assert traj.status is RunStatus.COMPLETED
        assert traj.final_text == "fine"

    def test_non_json_lines_are_ignored(self, fake, connectors, tmp_path, monkeypatch):
        path = tmp_path / "noisy.jsonl"
        path.write_text(
            "npm warning\n"
            + json.dumps({"type": "result", "subtype": "success",
                          "is_error": False, "result": "ok"})
            + "\nnot json\n"
        )
        monkeypatch.setenv("FAKE_CLAUDE_STREAM", str(path))
        traj = CliAgent("cc", claude_bin=fake.binary).run(make_task(), connectors)
        assert traj.status is RunStatus.COMPLETED

    def test_is_error_wins_over_the_subtype(self, fake, connectors):
        """Observed on the real CLI: subtype 'success' with is_error true."""
        fake.stream([{"type": "result", "subtype": "success", "is_error": True,
                      "result": "Not logged in · Please run /login"}])
        traj = CliAgent("cc", claude_bin=fake.binary).run(make_task(), connectors)
        assert traj.status is RunStatus.ERROR
        assert "Not logged in" in traj.error

    def test_running_out_of_turns_is_not_an_error(self, fake, connectors):
        fake.stream([{"type": "result", "subtype": "error_max_turns",
                      "is_error": True, "result": ""}])
        traj = CliAgent("cc", claude_bin=fake.binary).run(make_task(), connectors)
        assert traj.status is RunStatus.MAX_STEPS


class TestFailureModes:
    def test_a_missing_binary_is_reported_clearly(self, connectors):
        traj = CliAgent("cc", claude_bin="no-such-claude-xyz").run(make_task(), connectors)
        assert traj.status is RunStatus.ERROR
        assert "claude" in traj.error

    def test_a_non_zero_exit_is_an_error(self, fake, connectors, monkeypatch):
        monkeypatch.setenv("FAKE_CLAUDE_EXIT", "1")
        monkeypatch.setenv("FAKE_CLAUDE_STDERR", "credit balance too low")
        fake.stream([])
        traj = CliAgent("cc", claude_bin=fake.binary).run(make_task(), connectors)
        assert traj.status is RunStatus.ERROR
        assert "credit balance too low" in traj.error

    def test_a_stream_with_no_result_is_an_error(self, fake, connectors):
        fake.stream([{"type": "system", "subtype": "init"}])
        traj = CliAgent("cc", claude_bin=fake.binary).run(make_task(), connectors)
        assert traj.status is RunStatus.ERROR

    def test_a_hanging_cli_times_out(self, fake, connectors, monkeypatch):
        monkeypatch.setenv("FAKE_CLAUDE_HANG", "10")
        task = make_task(limits=Limits(timeout_s=1))
        traj = CliAgent("cc", claude_bin=fake.binary).run(task, connectors)
        assert traj.status is RunStatus.TIMEOUT


class TestItIsAnAgent:
    def test_it_satisfies_the_protocol(self):
        assert isinstance(CliAgent("cc"), Agent)

    def test_it_declares_that_it_owns_its_harness(self):
        """Which is what makes the comparison a product comparison, and what
        tells the orchestrator to reconnect before capturing evidence."""
        assert CliAgent("cc").owns_harness is True

    def test_the_agent_id_is_the_model_id(self):
        assert CliAgent("claude-code").id == "claude-code"

    def test_the_trajectory_is_labelled(self, fake, connectors):
        traj = CliAgent("cc", claude_bin=fake.binary).run(make_task(), connectors, repeat=2)
        assert traj.agent_id == "cc"
        assert traj.repeat == 2


class TestItReallyDrivesTheServers:
    def test_state_the_cli_changes_is_not_visible_to_our_connectors(
        self, fake, connectors, monkeypatch
    ):
        """The reason the orchestrator must reconnect before capture: our
        connector holds a server process that never saw the CLI's changes."""
        monkeypatch.setenv(
            "FAKE_CLAUDE_APPLY",
            json.dumps([{"server": "tickets", "tool": "set_priority",
                         "args": {"id": "T-1001", "priority": "urgent"}}]),
        )
        CliAgent("cc", claude_bin=fake.binary).run(make_task(), connectors)

        stale = connectors[0].probe("tickets__get_ticket", {"id": "T-1001"})
        assert stale.structured["ticket"]["priority"] == "normal", (
            "if this ever passes as 'urgent', the staleness this guards against is gone"
        )

    def test_a_fresh_connector_does_see_it(self, fake, connectors, monkeypatch):
        monkeypatch.setenv(
            "FAKE_CLAUDE_APPLY",
            json.dumps([{"server": "tickets", "tool": "set_priority",
                         "args": {"id": "T-1001", "priority": "urgent"}}]),
        )
        handle = connectors[0].handle
        CliAgent("cc", claude_bin=fake.binary).run(make_task(), connectors)

        fresh = McpConnector(MCP)
        fresh.setup(handle)
        try:
            ticket = fresh.probe("tickets__get_ticket", {"id": "T-1001"}).structured["ticket"]
            assert ticket["priority"] == "urgent"
        finally:
            fresh.teardown()
