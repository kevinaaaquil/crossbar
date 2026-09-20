"""Environments: bringing the system under test up, resetting it, tearing it down."""
import os
import stat
import sys
import textwrap

import pytest

from crossbar.connectors import McpConnector
from crossbar.domain import ConnectorConfig, EnvironmentSpec
from crossbar.environment import (
    DockerEnvironment,
    EnvironmentError_,
    EnvironmentHandle,
    LocalEnvironment,
    RemoteExec,
    build_environment,
)

PY = sys.executable

MCP = ConnectorConfig(
    name="mcp",
    options={"servers": [{"name": "tickets", "command": "${CROSSBAR_PYTHON}",
                          "args": ["-m", "tests.fixtures.tickets_server"]}]},
)


def local_spec(**overrides) -> EnvironmentSpec:
    return EnvironmentSpec(kind="local", connectors=(MCP,), **overrides)


def docker_spec(**overrides) -> EnvironmentSpec:
    return EnvironmentSpec(kind="docker", connectors=(MCP,), image="demo:latest", **overrides)


class TestLocalEnvironment:
    def test_start_returns_a_handle_with_a_workspace(self):
        env = LocalEnvironment(local_spec())
        handle = env.start()
        try:
            assert isinstance(handle, EnvironmentHandle)
            assert os.path.isdir(handle.workspace)
        finally:
            env.stop()

    def test_there_is_no_command_prefix(self):
        env = LocalEnvironment(local_spec())
        try:
            assert env.start().command_prefix == ()
        finally:
            env.stop()

    def test_stop_removes_the_workspace(self):
        env = LocalEnvironment(local_spec())
        workspace = env.start().workspace
        env.stop()
        assert not os.path.exists(workspace)

    def test_stop_is_idempotent(self):
        env = LocalEnvironment(local_spec())
        env.start()
        env.stop()
        env.stop()

    def test_start_twice_returns_the_same_handle(self):
        env = LocalEnvironment(local_spec())
        try:
            assert env.start() is env.start()
        finally:
            env.stop()

    def test_it_works_as_a_context_manager(self):
        with LocalEnvironment(local_spec()) as handle:
            workspace = handle.workspace
            assert os.path.isdir(workspace)
        assert not os.path.exists(workspace)


class TestReset:
    """State must not leak between Attempts. This is the one that produces
    quietly wrong numbers if it breaks."""

    def test_reset_gives_a_fresh_workspace(self):
        env = LocalEnvironment(local_spec())
        first = env.start().workspace
        try:
            assert env.reset().workspace != first
        finally:
            env.stop()

    def test_reset_destroys_the_previous_workspace(self):
        env = LocalEnvironment(local_spec())
        first = env.start().workspace
        try:
            env.reset()
            assert not os.path.exists(first)
        finally:
            env.stop()

    def test_an_attempt_cannot_observe_the_previous_attempts_state(self):
        env = LocalEnvironment(local_spec())
        handle = env.start()
        try:
            first = McpConnector(MCP)
            first.setup(handle)
            first.call("tickets__set_priority", {"id": "T-1001", "priority": "urgent"})
            first.teardown()

            handle = env.reset()
            second = McpConnector(MCP)
            second.setup(handle)
            try:
                ticket = second.probe("tickets__get_ticket", {"id": "T-1001"}).structured["ticket"]
                assert ticket["priority"] == "normal", "state leaked across the reset"
            finally:
                second.teardown()
        finally:
            env.stop()

    def test_reset_before_start_starts(self):
        env = LocalEnvironment(local_spec())
        try:
            assert os.path.isdir(env.reset().workspace)
        finally:
            env.stop()

    def test_the_recreate_policy_is_the_default(self):
        assert LocalEnvironment(local_spec()).spec.reset == "recreate"


class TestDockerEnvironment:
    def test_start_runs_a_container_from_the_image(self, fake_docker):
        env = DockerEnvironment(docker_spec(), docker_bin=fake_docker.binary)
        env.start()
        try:
            assert "demo:latest" in fake_docker.log_text()
            assert env.container_id
        finally:
            env.stop()

    def test_the_handle_prefixes_commands_with_docker_exec(self, fake_docker):
        env = DockerEnvironment(docker_spec(), docker_bin=fake_docker.binary)
        handle = env.start()
        try:
            assert handle.command_prefix[0] == fake_docker.binary
            assert "exec" in handle.command_prefix
            assert env.container_id in handle.command_prefix
        finally:
            env.stop()

    def test_the_workspace_is_a_path_inside_the_container(self, fake_docker):
        env = DockerEnvironment(docker_spec(), docker_bin=fake_docker.binary)
        handle = env.start()
        try:
            assert handle.workspace.startswith("/")
            assert not os.path.isdir(handle.workspace) or handle.workspace != ""
        finally:
            env.stop()

    def test_networking_is_off_by_default(self, fake_docker):
        env = DockerEnvironment(docker_spec(), docker_bin=fake_docker.binary)
        env.start()
        try:
            assert "--network none" in fake_docker.log_text()
        finally:
            env.stop()

    def test_stop_removes_the_container(self, fake_docker):
        env = DockerEnvironment(docker_spec(), docker_bin=fake_docker.binary)
        env.start()
        container = env.container_id
        env.stop()
        log = fake_docker.log_text()
        assert "rm" in log and container in log

    def test_reset_replaces_the_container(self, fake_docker):
        env = DockerEnvironment(docker_spec(), docker_bin=fake_docker.binary)
        first = env.start().command_prefix
        try:
            assert env.reset().command_prefix != first
        finally:
            env.stop()

    def test_a_missing_docker_binary_is_reported_clearly(self):
        env = DockerEnvironment(docker_spec(), docker_bin="no-such-docker-xyz")
        with pytest.raises(EnvironmentError_, match="docker"):
            env.start()

    def test_servers_are_reachable_through_the_prefix(self, fake_docker):
        """The stub docker execs through, so a real MCP server answers."""
        env = DockerEnvironment(docker_spec(), docker_bin=fake_docker.binary)
        handle = env.start()
        connector = McpConnector(MCP)
        connector.setup(handle)
        try:
            assert "tickets__list_tickets" in {t.name for t in connector.tools()}
        finally:
            connector.teardown()
            env.stop()


class TestFactory:
    def test_local_spec_builds_a_local_environment(self):
        assert isinstance(build_environment(local_spec()), LocalEnvironment)

    def test_docker_spec_builds_a_docker_environment(self):
        assert isinstance(build_environment(docker_spec()), DockerEnvironment)

    def test_options_reach_the_environment(self):
        env = build_environment(docker_spec(), docker_bin="my-docker")
        assert env.docker_bin == "my-docker"

    def test_an_unknown_kind_raises(self):
        spec = EnvironmentSpec(kind="carrier-pigeon", connectors=(MCP,))
        with pytest.raises(EnvironmentError_, match="carrier-pigeon"):
            build_environment(spec)


class TestIntegrationWithTheFixtureTest:
    def test_a_loaded_test_environment_starts_and_serves_tools(self):
        from crossbar.domain import load_test

        spec = load_test("tests/fixtures/tests/support-triage").environment
        env = build_environment(spec)
        handle = env.start()
        connector = McpConnector(spec.connectors[0])
        connector.setup(handle)
        try:
            dump = connector.probe("tickets__dump_db", {}).structured["tickets"]
            assert len(dump) == 6
            assert all(t["priority"] != "urgent" for t in dump)
        finally:
            connector.teardown()
            env.stop()


class TestRemoteExec:
    """Environment variables and a working directory for a command the prefix
    runs somewhere else.

    Without this, ``env:`` in a Test lands on the local ``docker exec`` process
    and never reaches the server inside the container: no error, no effect.
    """

    def _remote_handle(self):
        return EnvironmentHandle(
            workspace="/tmp/ws",
            command_prefix=("docker", "exec", "-i", "cid"),
            remote=RemoteExec(insert_at=3),
        )

    def test_env_vars_are_injected_into_the_remote_argv(self):
        launch = self._remote_handle().launch("srv", (), env={"PGHOST": "db"})
        assert launch.argv == [
            "docker", "exec", "-i", "-e", "PGHOST=db", "cid", "srv",
        ]

    def test_the_remote_process_env_carries_no_task_variables(self):
        """They went into the argv; applying them locally too would be a lie
        about where they take effect."""
        launch = self._remote_handle().launch("srv", (), env={"PGHOST": "db"})
        assert "PGHOST" not in launch.env

    def test_cwd_is_injected_into_the_remote_argv(self):
        launch = self._remote_handle().launch("srv", (), cwd="/srv/app")
        assert launch.argv[:6] == ["docker", "exec", "-i", "-w", "/srv/app", "cid"]
        assert launch.cwd is None

    def test_variables_are_expanded_before_injection(self):
        handle = EnvironmentHandle(
            workspace="/tmp/ws",
            command_prefix=("docker", "exec", "-i", "cid"),
            remote=RemoteExec(insert_at=3),
        )
        launch = handle.launch("srv", (), env={"W": "${CROSSBAR_WORKSPACE}/x"})
        assert "W=/tmp/ws/x" in launch.argv

    def test_a_local_handle_applies_env_to_the_process_instead(self):
        handle = EnvironmentHandle(workspace="/tmp/ws")
        launch = handle.launch("srv", ("-m",), env={"PGHOST": "db"})
        assert launch.argv == ["srv", "-m"]
        assert launch.env["PGHOST"] == "db"

    def test_a_local_handle_keeps_the_cwd_on_the_process(self):
        handle = EnvironmentHandle(workspace="/tmp/ws")
        launch = handle.launch("srv", (), cwd="${CROSSBAR_WORKSPACE}/sub")
        assert launch.cwd == "/tmp/ws/sub"

    def test_the_docker_environment_inserts_before_the_container_id(self, fake_docker):
        env = DockerEnvironment(docker_spec(), docker_bin=fake_docker.binary)
        handle = env.start()
        try:
            launch = handle.launch("srv", (), env={"K": "v"})
            assert launch.argv[-2:] == [env.container_id, "srv"]
            assert "K=v" in launch.argv
        finally:
            env.stop()
