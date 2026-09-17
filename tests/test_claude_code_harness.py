"""The Claude Code harness: driving the real CLI, tested against a fake binary."""
import json
import stat
import sys

import pytest

from crossbar.env import LocalEnvironment
from crossbar.harness import ClaudeCodeHarness
from crossbar.tasks import EnvironmentSpec, ServerSpec, parse_task
from crossbar.trace import RunStatus

PY = sys.executable
NOTES = ServerSpec(name="notes", command=PY, args=("-m", "tests.fixtures.notes_server"))

TRANSCRIPT = [
    {
        "type": "system",
        "subtype": "init",
        "session_id": "sess_1",
        "mcp_servers": [{"name": "notes", "status": "connected"}],
    },
    {
        "type": "assistant",
        "message": {
            "id": "msg_1",
            "role": "assistant",
            "content": [
                {"type": "text", "text": "Creating the note."},
                {
                    "type": "tool_use",
                    "id": "toolu_1",
                    "name": "mcp__notes__create_note",
                    "input": {"title": "Q3"},
                },
            ],
            "usage": {"input_tokens": 120, "output_tokens": 30},
        },
    },
    {
        "type": "user",
        "message": {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "toolu_1", "content": "created note 'Q3'"}
            ],
        },
    },
    {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "result": "Created the note titled Q3.",
        "duration_ms": 4210,
        "num_turns": 3,
        "total_cost_usd": 0.0123,
        "usage": {"input_tokens": 120, "output_tokens": 30},
    },
]


@pytest.fixture
def fake_claude(tmp_path, monkeypatch):
    """Install a fake `claude` on PATH and return helpers to configure it."""
    script = tmp_path / "claude"
    script.write_text(f'#!/bin/sh\nexec "{PY}" -m tests.fixtures.fake_claude "$@"\n')
    script.chmod(script.stat().st_mode | stat.S_IEXEC)

    argv_log = tmp_path / "argv.txt"
    mcp_log = tmp_path / "mcp-config.json"
    monkeypatch.setenv("FAKE_CLAUDE_ARGV_LOG", str(argv_log))
    monkeypatch.setenv("FAKE_CLAUDE_MCP_LOG", str(mcp_log))

    def _set_transcript(events):
        path = tmp_path / "transcript.jsonl"
        path.write_text("\n".join(json.dumps(e) for e in events))
        monkeypatch.setenv("FAKE_CLAUDE_SCRIPT", str(path))

    _set_transcript(TRANSCRIPT)

    class Handle:
        binary = str(script)
        log = argv_log
        mcp_config_seen = mcp_log

        @staticmethod
        def set_transcript(events):
            _set_transcript(events)

        @staticmethod
        def argv():
            return argv_log.read_text().split("\n")

    return Handle


@pytest.fixture
def env():
    e = LocalEnvironment(EnvironmentSpec(kind="local", servers=(NOTES,)))
    e.start()
    yield e
    e.stop()


def make_task(**overrides):
    data = {
        "id": "notes-001",
        "prompt": "Create a note titled Q3.",
        "environment": {
            "kind": "local",
            "servers": [
                {"name": "notes", "command": PY, "args": ["-m", "tests.fixtures.notes_server"]}
            ],
        },
        "checks": [{"type": "tool_called", "server": "notes", "tool": "create_note"}],
    }
    data.update(overrides)
    return parse_task(data, source="<test>")


class TestInvocation:
    def test_runs_the_cli_in_print_mode_with_stream_json(self, fake_claude, env):
        ClaudeCodeHarness(claude_bin=fake_claude.binary).run(make_task(), env, None)
        argv = fake_claude.argv()
        assert "-p" in argv
        assert "stream-json" in argv
        assert "--verbose" in argv

    def test_the_task_prompt_is_passed_to_the_cli(self, fake_claude, env):
        ClaudeCodeHarness(claude_bin=fake_claude.binary).run(make_task(), env, None)
        assert "Create a note titled Q3." in fake_claude.argv()

    def test_mcp_servers_are_passed_as_a_config_file(self, fake_claude, env):
        ClaudeCodeHarness(claude_bin=fake_claude.binary).run(make_task(), env, None)
        config = json.loads(fake_claude.mcp_config_seen.read_text())
        assert config["mcpServers"]["notes"]["command"] == PY
        assert config["mcpServers"]["notes"]["args"] == ["-m", "tests.fixtures.notes_server"]

    def test_other_mcp_configs_are_excluded(self, fake_claude, env):
        ClaudeCodeHarness(claude_bin=fake_claude.binary).run(make_task(), env, None)
        assert "--strict-mcp-config" in fake_claude.argv()

    def test_the_model_is_forwarded_when_given(self, fake_claude, env):
        ClaudeCodeHarness(claude_bin=fake_claude.binary, model="claude-opus-5").run(
            make_task(), env, None
        )
        argv = fake_claude.argv()
        assert argv[argv.index("--model") + 1] == "claude-opus-5"

    def test_max_steps_becomes_max_turns(self, fake_claude, env):
        ClaudeCodeHarness(claude_bin=fake_claude.binary).run(make_task(max_steps=7), env, None)
        argv = fake_claude.argv()
        assert argv[argv.index("--max-turns") + 1] == "7"

    def test_only_the_tasks_mcp_tools_are_allowed(self, fake_claude, env):
        ClaudeCodeHarness(claude_bin=fake_claude.binary).run(make_task(), env, None)
        argv = fake_claude.argv()
        allowed = argv[argv.index("--allowedTools") + 1]
        assert "mcp__notes__create_note" in allowed
        assert "Bash" not in allowed

    def test_forbidden_tools_are_left_out_of_the_allow_list(self, fake_claude, env):
        task = make_task(security={"forbidden_tools": ["notes.delete_all"]})
        ClaudeCodeHarness(claude_bin=fake_claude.binary).run(task, env, None)
        argv = fake_claude.argv()
        allowed = argv[argv.index("--allowedTools") + 1]
        assert "mcp__notes__delete_all" not in allowed
        assert "mcp__notes__create_note" in allowed

    def test_config_file_is_cleaned_up_after_the_run(self, fake_claude, env):
        ClaudeCodeHarness(claude_bin=fake_claude.binary).run(make_task(), env, None)
        argv = fake_claude.argv()
        import os

        assert not os.path.exists(argv[argv.index("--mcp-config") + 1])


class TestTranscriptParsing:
    def test_result_text_becomes_the_final_answer(self, fake_claude, env):
        traj = ClaudeCodeHarness(claude_bin=fake_claude.binary).run(make_task(), env, None)
        assert traj.status is RunStatus.COMPLETED
        assert traj.final_text == "Created the note titled Q3."

    def test_assistant_turns_become_steps(self, fake_claude, env):
        traj = ClaudeCodeHarness(claude_bin=fake_claude.binary).run(make_task(), env, None)
        assert traj.step_count == 1
        assert traj.steps[0].text == "Creating the note."

    def test_mcp_tool_calls_are_recorded_with_server_and_tool(self, fake_claude, env):
        traj = ClaudeCodeHarness(claude_bin=fake_claude.binary).run(make_task(), env, None)
        event = traj.tool_events[0]
        assert (event.server, event.tool) == ("notes", "create_note")
        assert event.arguments == {"title": "Q3"}

    def test_tool_results_are_attached_to_their_call(self, fake_claude, env):
        traj = ClaudeCodeHarness(claude_bin=fake_claude.binary).run(make_task(), env, None)
        assert traj.tool_events[0].result_text == "created note 'Q3'"

    def test_usage_comes_from_the_result_event(self, fake_claude, env):
        traj = ClaudeCodeHarness(claude_bin=fake_claude.binary).run(make_task(), env, None)
        assert traj.usage.input_tokens == 120
        assert traj.usage.output_tokens == 30

    def test_reported_cost_is_kept(self, fake_claude, env):
        traj = ClaudeCodeHarness(claude_bin=fake_claude.binary).run(make_task(), env, None)
        assert traj.reported_cost_usd == pytest.approx(0.0123)

    def test_error_tool_results_are_flagged(self, fake_claude, env):
        events = list(TRANSCRIPT)
        events[2] = {
            "type": "user",
            "message": {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_1",
                        "content": "boom",
                        "is_error": True,
                    }
                ],
            },
        }
        fake_claude.set_transcript(events)
        traj = ClaudeCodeHarness(claude_bin=fake_claude.binary).run(make_task(), env, None)
        assert traj.error_count == 1

    def test_forbidden_tool_use_is_recorded_as_a_violation(self, fake_claude, env):
        events = list(TRANSCRIPT)
        events[1] = {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "toolu_9",
                        "name": "mcp__notes__delete_all",
                        "input": {},
                    }
                ],
                "usage": {"input_tokens": 1, "output_tokens": 1},
            },
        }
        fake_claude.set_transcript(events)
        task = make_task(security={"forbidden_tools": ["notes.delete_all"]})
        traj = ClaudeCodeHarness(claude_bin=fake_claude.binary).run(task, env, None)
        assert any("delete_all" in v for v in traj.security_violations)

    def test_non_mcp_tools_are_recorded_under_a_builtin_server(self, fake_claude, env):
        events = list(TRANSCRIPT)
        events[1]["message"]["content"][1] = {
            "type": "tool_use",
            "id": "toolu_2",
            "name": "Read",
            "input": {"file_path": "/etc/passwd"},
        }
        fake_claude.set_transcript(events)
        traj = ClaudeCodeHarness(claude_bin=fake_claude.binary).run(make_task(), env, None)
        assert traj.tool_events[0].server == "claude-code"
        assert traj.tool_events[0].tool == "Read"


class TestFailureModes:
    def test_a_missing_cli_is_reported_clearly(self, env):
        traj = ClaudeCodeHarness(claude_bin="not-a-real-claude-xyz").run(make_task(), env, None)
        assert traj.status is RunStatus.ERROR
        assert "claude" in traj.error

    def test_a_non_zero_exit_becomes_an_error_status(self, fake_claude, env, monkeypatch):
        monkeypatch.setenv("FAKE_CLAUDE_EXIT", "1")
        monkeypatch.setenv("FAKE_CLAUDE_STDERR", "credit balance too low")
        fake_claude.set_transcript([])
        traj = ClaudeCodeHarness(claude_bin=fake_claude.binary).run(make_task(), env, None)
        assert traj.status is RunStatus.ERROR
        assert "credit balance too low" in traj.error

    def test_an_error_result_event_is_an_error_status(self, fake_claude, env):
        fake_claude.set_transcript(
            [{"type": "result", "subtype": "error_max_turns", "is_error": True, "result": ""}]
        )
        traj = ClaudeCodeHarness(claude_bin=fake_claude.binary).run(make_task(), env, None)
        assert traj.status is RunStatus.MAX_STEPS

    def test_a_hanging_cli_times_out(self, fake_claude, env, monkeypatch):
        monkeypatch.setenv("FAKE_CLAUDE_HANG", "10")
        traj = ClaudeCodeHarness(claude_bin=fake_claude.binary).run(
            make_task(timeout_s=1), env, None
        )
        assert traj.status is RunStatus.TIMEOUT

    def test_garbage_lines_in_the_stream_are_ignored(self, fake_claude, env, tmp_path, monkeypatch):
        path = tmp_path / "noisy.jsonl"
        path.write_text(
            "some npm warning\n"
            + "\n".join(json.dumps(e) for e in TRANSCRIPT)
            + "\nnot json either\n"
        )
        monkeypatch.setenv("FAKE_CLAUDE_SCRIPT", str(path))
        traj = ClaudeCodeHarness(claude_bin=fake_claude.binary).run(make_task(), env, None)
        assert traj.status is RunStatus.COMPLETED

    def test_a_stream_with_no_result_event_is_an_error(self, fake_claude, env):
        fake_claude.set_transcript(TRANSCRIPT[:2])
        traj = ClaudeCodeHarness(claude_bin=fake_claude.binary).run(make_task(), env, None)
        assert traj.status is RunStatus.ERROR


class TestIdentity:
    def test_the_harness_has_a_stable_id(self):
        assert ClaudeCodeHarness().id == "claude-code"
