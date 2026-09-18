"""Connectors: the seam between the agent and the environment.

Exercised against a real MCP server subprocess, not a mock.
"""
import sys

import pytest

from crossbar.connectors import (
    ConnectorError,
    McpConnector,
    ToolSpec,
    build_connector,
    known_connectors,
)
from crossbar.domain import ConnectorConfig
from crossbar.environment import EnvironmentHandle

PY = sys.executable

SERVERS = [
    {"name": "tickets", "command": "${CROSSBAR_PYTHON}",
     "args": ["-m", "tests.fixtures.tickets_server"]},
]


def config(**overrides) -> ConnectorConfig:
    options = {"servers": SERVERS}
    read_only = overrides.pop("read_only_tools", ())
    options.update(overrides)
    return ConnectorConfig(name="mcp", options=options, read_only_tools=tuple(read_only))


@pytest.fixture
def handle(tmp_path) -> EnvironmentHandle:
    return EnvironmentHandle(workspace=str(tmp_path))


@pytest.fixture
def connector(handle):
    c = McpConnector(config())
    c.setup(handle)
    yield c
    c.teardown()


class TestLifecycle:
    def test_setup_starts_every_server(self, connector):
        assert connector.is_running

    def test_teardown_stops_them(self, handle):
        c = McpConnector(config())
        c.setup(handle)
        c.teardown()
        assert not c.is_running

    def test_teardown_is_idempotent(self, handle):
        c = McpConnector(config())
        c.setup(handle)
        c.teardown()
        c.teardown()

    def test_a_server_that_will_not_start_raises_connector_error(self, handle):
        broken = ConnectorConfig(
            name="mcp", options={"servers": [{"name": "x", "command": "no-such-binary-xyz"}]}
        )
        c = McpConnector(broken)
        with pytest.raises(ConnectorError, match="x"):
            c.setup(handle)

    def test_a_failed_setup_leaves_nothing_running(self, handle):
        broken = ConnectorConfig(
            name="mcp",
            options={"servers": SERVERS + [{"name": "x", "command": "no-such-binary-xyz"}]},
        )
        c = McpConnector(broken)
        with pytest.raises(ConnectorError):
            c.setup(handle)
        assert not c.is_running

    def test_calling_before_setup_raises(self):
        with pytest.raises(ConnectorError, match="not set up"):
            McpConnector(config()).call("tickets__list_tickets", {})

    def test_an_environment_with_no_servers_is_rejected(self, handle):
        c = McpConnector(ConnectorConfig(name="mcp", options={"servers": []}))
        with pytest.raises(ConnectorError, match="server"):
            c.setup(handle)


class TestTools:
    def test_tools_are_namespaced_by_server(self, connector):
        names = {t.name for t in connector.tools()}
        assert "tickets__list_tickets" in names
        assert "tickets__set_priority" in names

    def test_tool_specs_carry_description_and_schema(self, connector):
        spec = next(t for t in connector.tools() if t.name == "tickets__get_ticket")
        assert isinstance(spec, ToolSpec)
        assert "Fetch one ticket" in spec.description
        assert spec.input_schema["properties"]["id"]["type"] == "string"

    def test_tool_names_are_model_safe(self, connector):
        for spec in connector.tools():
            assert spec.name.replace("_", "").isalnum()
            assert len(spec.name) <= 64

    def test_every_tool_records_its_connector(self, connector):
        assert all(t.connector == "mcp" for t in connector.tools())

    def test_tools_are_cached(self, connector):
        assert connector.tools() is connector.tools()


class TestCalling:
    def test_a_call_reaches_the_server(self, connector):
        result = connector.call("tickets__set_priority", {"id": "T-1001", "priority": "urgent"})
        assert result.is_error is False
        assert "urgent" in result.text

    def test_calls_mutate_real_state(self, connector):
        connector.call("tickets__set_priority", {"id": "T-1001", "priority": "urgent"})
        ticket = connector.call("tickets__get_ticket", {"id": "T-1001"}).structured["ticket"]
        assert ticket["priority"] == "urgent"

    def test_a_tool_error_is_flagged_not_raised(self, connector):
        assert connector.call("tickets__explode", {}).is_error is True

    def test_an_unknown_tool_raises(self, connector):
        with pytest.raises(ConnectorError, match="unknown tool"):
            connector.call("tickets__imaginary", {})

    def test_a_tool_from_another_connector_raises(self, connector):
        with pytest.raises(ConnectorError, match="unknown tool"):
            connector.call("browser__click", {})


class TestReadOnlyClassification:
    """Evidence capture must not mutate what it measures."""

    def test_annotated_read_only_tools_are_probes(self, connector):
        probes = {p.name for p in connector.probes()}
        assert {"tickets__list_tickets", "tickets__get_ticket", "tickets__dump_db"} <= probes

    def test_mutating_tools_are_never_probes(self, connector):
        probes = {p.name for p in connector.probes()}
        assert "tickets__set_priority" not in probes
        assert "tickets__delete_all" not in probes

    def test_tools_are_marked_read_only_on_their_spec(self, connector):
        by_name = {t.name: t for t in connector.tools()}
        assert by_name["tickets__list_tickets"].read_only is True
        assert by_name["tickets__delete_all"].read_only is False

    def test_an_explicit_declaration_can_add_a_probe(self, handle):
        c = McpConnector(config(read_only_tools=["tickets__search_tickets"]))
        c.setup(handle)
        try:
            assert "tickets__search_tickets" in {p.name for p in c.probes()}
        finally:
            c.teardown()

    def test_a_declaration_cannot_be_avoided_by_naming_alone(self, handle):
        """A tool with no annotation and no declaration is never a probe, whatever
        it happens to be called."""
        c = McpConnector(config())
        c.setup(handle)
        try:
            assert "tickets__close_ticket" not in {p.name for p in c.probes()}
        finally:
            c.teardown()

    def test_probing_a_non_read_only_tool_is_refused(self, connector):
        with pytest.raises(ConnectorError, match="not read-only"):
            connector.probe("tickets__delete_all", {})

    def test_probing_does_not_change_state(self, connector):
        before = connector.probe("tickets__dump_db", {}).structured
        connector.probe("tickets__list_tickets", {})
        after = connector.probe("tickets__dump_db", {}).structured
        assert before == after

    def test_a_probe_returns_the_same_shape_as_a_call(self, connector):
        result = connector.probe("tickets__get_ticket", {"id": "T-1002"})
        assert result.structured["ticket"]["id"] == "T-1002"


class TestVariableExpansion:
    def test_the_interpreter_is_always_available(self, connector):
        assert connector.is_running  # ${CROSSBAR_PYTHON} resolved, or setup would fail

    def test_the_workspace_is_exported_to_servers(self, handle):
        c = McpConnector(config())
        c.setup(handle)
        try:
            c.call("tickets__set_priority", {"id": "T-1001", "priority": "high"})
        finally:
            c.teardown()
        assert (handle.workspace_path / "tickets.json").exists()

    def test_unknown_variables_are_left_alone(self, handle):
        assert handle.expand("${NOT_SET_ANYWHERE}/x") == "${NOT_SET_ANYWHERE}/x"

    def test_the_command_prefix_is_applied(self, tmp_path):
        """A docker environment prefixes every server command with docker exec."""
        handle = EnvironmentHandle(
            workspace=str(tmp_path), command_prefix=("echo", "--")
        )
        c = McpConnector(config())
        argv = c.build_argv(handle, {"command": "python", "args": ["-m", "x"]})
        assert argv[:2] == ["echo", "--"]
        assert argv[2:] == ["python", "-m", "x"]


class TestRegistry:
    def test_mcp_is_registered(self):
        assert "mcp" in known_connectors()

    def test_build_connector_returns_the_right_type(self):
        assert isinstance(build_connector(config()), McpConnector)

    def test_an_unknown_connector_raises(self):
        with pytest.raises(ConnectorError, match="telepathy"):
            build_connector(ConnectorConfig(name="telepathy", options={}))

    def test_the_registry_is_the_only_place_connectors_are_named(self):
        """Adding a connector must be a registration, not an edit elsewhere."""
        from crossbar.connectors import registry

        assert set(registry.CONNECTORS) == set(known_connectors())


class TestIntegrationWithTheFixtureTest:
    """Build connectors straight from the fixture Test's environment."""

    def test_connectors_build_from_a_loaded_environment(self, handle):
        from crossbar.domain import load_test

        env = load_test("tests/fixtures/tests/support-triage").environment
        built = [build_connector(c) for c in env.connectors]
        for c in built:
            c.setup(handle)
        try:
            names = {t.name for c in built for t in c.tools()}
            assert "tickets__list_tickets" in names
        finally:
            for c in built:
                c.teardown()

    def test_a_full_agent_style_interaction(self, connector):
        """What an agent would actually do, then what capture would read back."""
        connector.call("tickets__set_priority", {"id": "T-1001", "priority": "urgent"})
        connector.call("tickets__set_priority", {"id": "T-1004", "priority": "urgent"})

        dump = connector.probe("tickets__dump_db", {}).structured["tickets"]
        urgent = {t["id"] for t in dump if t["priority"] == "urgent"}
        assert urgent == {"T-1001", "T-1004"}
