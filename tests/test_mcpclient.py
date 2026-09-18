"""MCP stdio client, exercised against a real fixture server subprocess."""
import sys

import pytest

from crossbar.mcpclient import (
    McpError,
    McpStdioClient,
    ToolResult,
    ToolSpec,
)

PY = sys.executable


def make_client(**kwargs) -> McpStdioClient:
    return McpStdioClient(
        name="notes",
        command=PY,
        args=["-m", "tests.fixtures.notes_server"],
        **kwargs,
    )


@pytest.fixture
def client():
    c = make_client()
    c.start()
    yield c
    c.stop()


class TestHandshake:
    def test_start_completes_the_initialize_handshake(self, client):
        assert client.is_running
        assert client.server_info["name"] == "notes"

    def test_protocol_version_is_recorded(self, client):
        assert client.protocol_version

    def test_stop_terminates_the_subprocess(self):
        c = make_client()
        c.start()
        c.stop()
        assert not c.is_running

    def test_stop_is_idempotent(self):
        c = make_client()
        c.start()
        c.stop()
        c.stop()

    def test_starting_a_missing_command_raises_mcp_error(self):
        c = McpStdioClient(name="ghost", command="definitely-not-a-real-binary-xyz")
        with pytest.raises(McpError):
            c.start()

    def test_context_manager_starts_and_stops(self):
        with make_client() as c:
            assert c.is_running
        assert not c.is_running

    def test_env_vars_reach_the_server(self):
        c = make_client(env={"NOTES_SERVER_NAME": "custom-name"})
        c.start()
        try:
            assert c.server_info["name"] == "custom-name"
        finally:
            c.stop()


class TestListTools:
    def test_lists_the_servers_tools(self, client):
        tools = client.list_tools()
        assert all(isinstance(t, ToolSpec) for t in tools)
        assert {t.name for t in tools} >= {"create_note", "list_notes", "delete_all"}

    def test_tool_spec_carries_description_and_schema(self, client):
        tool = next(t for t in client.list_tools() if t.name == "create_note")
        assert "Create a note" in tool.description
        assert tool.input_schema["properties"]["title"]["type"] == "string"

    def test_qualified_name_is_namespaced_by_server(self, client):
        tool = next(t for t in client.list_tools() if t.name == "create_note")
        assert tool.qualified_name == "notes.create_note"

    def test_tools_are_cached_after_first_call(self, client):
        assert client.list_tools() is client.list_tools()

    def test_tool_annotations_are_preserved(self):
        """Servers advertise readOnlyHint; evidence capture depends on it."""
        c = McpStdioClient(
            name="tickets",
            command=PY,
            args=["-m", "tests.fixtures.tickets_server"],
        )
        c.start()
        try:
            by_name = {t.name: t for t in c.list_tools()}
            assert by_name["list_tickets"].annotations.get("readOnlyHint") is True
            assert by_name["delete_all"].annotations.get("readOnlyHint") is None
        finally:
            c.stop()

    def test_tools_without_annotations_have_an_empty_mapping(self, client):
        assert all(isinstance(t.annotations, dict) for t in client.list_tools())


class TestCallTool:
    def test_successful_call_returns_text_content(self, client):
        result = client.call_tool("create_note", {"title": "hello"})
        assert isinstance(result, ToolResult)
        assert result.is_error is False
        assert "created note" in result.text

    def test_state_persists_across_calls(self, client):
        client.call_tool("create_note", {"title": "one"})
        client.call_tool("create_note", {"title": "two"})
        listed = client.call_tool("list_notes", {})
        assert [n["title"] for n in listed.structured["notes"]] == ["one", "two"]

    def test_tool_reported_error_is_flagged_not_raised(self, client):
        result = client.call_tool("explode", {})
        assert result.is_error is True
        assert "exploded" in result.text

    def test_unknown_tool_raises_mcp_error(self, client):
        with pytest.raises(McpError, match="unknown tool"):
            client.call_tool("no_such_tool", {})

    def test_calling_before_start_raises(self):
        c = make_client()
        with pytest.raises(McpError, match="not started"):
            c.call_tool("list_notes", {})

    def test_structured_content_is_exposed_when_present(self, client):
        result = client.call_tool("list_notes", {})
        assert result.structured == {"notes": []}

    def test_result_without_structured_content_has_none(self, client):
        result = client.call_tool("create_note", {"title": "x"})
        assert result.structured is None

    def test_each_request_uses_a_fresh_id(self, client):
        client.call_tool("list_notes", {})
        first = client.last_request_id
        client.call_tool("list_notes", {})
        assert client.last_request_id > first


class TestFailureModes:
    def test_server_that_exits_immediately_raises_on_start(self):
        c = McpStdioClient(name="dead", command=PY, args=["-c", "raise SystemExit(1)"])
        with pytest.raises(McpError):
            c.start()

    def test_server_that_never_answers_times_out(self):
        c = McpStdioClient(
            name="mute",
            command=PY,
            args=["-c", "import time; time.sleep(30)"],
            timeout_s=0.5,
        )
        with pytest.raises(McpError, match="timed out"):
            c.start()

    def test_stderr_is_captured_for_diagnosis(self):
        c = McpStdioClient(
            name="noisy",
            command=PY,
            args=["-c", "import sys; sys.stderr.write('boom\\n'); raise SystemExit(2)"],
        )
        with pytest.raises(McpError) as exc:
            c.start()
        assert "boom" in str(exc.value)

    def test_call_after_server_death_raises(self, client):
        client.stop()
        with pytest.raises(McpError):
            client.call_tool("list_notes", {})
