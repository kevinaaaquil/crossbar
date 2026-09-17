"""Environment layer: bringing a task's MCP servers up and tearing them down."""
import os
import stat
import sys
import textwrap

import pytest

from crossbar.env import (
    EnvironmentError_,
    DockerEnvironment,
    LocalEnvironment,
    build_environment,
)
from crossbar.tasks import EnvironmentSpec, ServerSpec

PY = sys.executable

NOTES = ServerSpec(name="notes", command=PY, args=("-m", "tests.fixtures.notes_server"))
NOTES2 = ServerSpec(name="scratch", command=PY, args=("-m", "tests.fixtures.notes_server"))


def local_spec(*servers: ServerSpec) -> EnvironmentSpec:
    return EnvironmentSpec(kind="local", servers=servers or (NOTES,))


@pytest.fixture
def env():
    e = LocalEnvironment(local_spec())
    e.start()
    yield e
    e.stop()


class TestLocalEnvironment:
    def test_start_brings_up_every_server(self):
        e = LocalEnvironment(local_spec(NOTES, NOTES2))
        e.start()
        try:
            assert set(e.clients) == {"notes", "scratch"}
            assert all(c.is_running for c in e.clients.values())
        finally:
            e.stop()

    def test_stop_shuts_every_server_down(self):
        e = LocalEnvironment(local_spec(NOTES, NOTES2))
        e.start()
        clients = list(e.clients.values())
        e.stop()
        assert not any(c.is_running for c in clients)

    def test_tools_are_aggregated_across_servers(self):
        e = LocalEnvironment(local_spec(NOTES, NOTES2))
        e.start()
        try:
            names = {t.api_name for t in e.tools()}
            assert "notes__create_note" in names
            assert "scratch__create_note" in names
        finally:
            e.stop()

    def test_api_names_are_model_safe(self, env):
        for tool in env.tools():
            assert tool.api_name.replace("_", "").replace("-", "").isalnum()
            assert len(tool.api_name) <= 64

    def test_call_routes_by_api_name(self, env):
        result = env.call("notes__create_note", {"title": "routed"})
        assert "created note" in result.text

    def test_call_accepts_the_dotted_name_too(self, env):
        env.call("notes.create_note", {"title": "dotted"})
        listed = env.call("notes__list_notes", {})
        assert [n["title"] for n in listed.structured["notes"]] == ["dotted"]

    def test_call_with_a_single_server_accepts_the_bare_name(self, env):
        result = env.call("create_note", {"title": "bare"})
        assert result.is_error is False

    def test_unknown_tool_raises(self, env):
        with pytest.raises(EnvironmentError_, match="unknown tool"):
            env.call("notes__nope", {})

    def test_servers_are_isolated_from_each_other(self):
        e = LocalEnvironment(local_spec(NOTES, NOTES2))
        e.start()
        try:
            e.call("notes__create_note", {"title": "only-here"})
            other = e.call("scratch__list_notes", {})
            assert other.structured == {"notes": []}
        finally:
            e.stop()

    def test_context_manager_cleans_up(self):
        with LocalEnvironment(local_spec()) as e:
            assert e.clients["notes"].is_running
            client = e.clients["notes"]
        assert not client.is_running

    def test_stop_is_safe_before_start(self):
        LocalEnvironment(local_spec()).stop()

    def test_failed_server_startup_cleans_up_the_others(self):
        broken = ServerSpec(name="broken", command="definitely-not-real-xyz")
        e = LocalEnvironment(local_spec(NOTES, broken))
        with pytest.raises(EnvironmentError_):
            e.start()
        assert e.clients == {}

    def test_environment_is_reset_between_runs(self):
        spec = local_spec()
        first = LocalEnvironment(spec)
        first.start()
        first.call("notes__create_note", {"title": "stale"})
        first.stop()

        second = LocalEnvironment(spec)
        second.start()
        try:
            assert second.call("notes__list_notes", {}).structured == {"notes": []}
        finally:
            second.stop()


class TestDockerEnvironment:
    """Exercised against a stub `docker` binary so no daemon is required."""

    @pytest.fixture
    def fake_docker(self, tmp_path):
        log = tmp_path / "docker.log"
        script = tmp_path / "docker"
        script.write_text(
            textwrap.dedent(
                f"""\
                #!/bin/sh
                echo "$@" >> {log}
                case "$1" in
                  run) echo "fakecontainer123" ;;
                  exec)
                    shift
                    while [ "$1" = "-i" ] || [ "$1" = "-e" ]; do
                      if [ "$1" = "-e" ]; then shift; fi
                      shift
                    done
                    shift  # container id
                    exec "$@"
                    ;;
                  rm|stop|kill) : ;;
                esac
                """
            )
        )
        script.chmod(script.stat().st_mode | stat.S_IEXEC)
        return script, log

    def docker_spec(self):
        return EnvironmentSpec(kind="docker", servers=(NOTES,), image="python:3.12-slim")

    def test_start_runs_a_container_from_the_task_image(self, fake_docker):
        script, log = fake_docker
        e = DockerEnvironment(self.docker_spec(), docker_bin=str(script))
        e.start()
        try:
            assert "python:3.12-slim" in log.read_text()
            assert e.container_id == "fakecontainer123"
        finally:
            e.stop()

    def test_servers_are_reachable_through_docker_exec(self, fake_docker):
        script, _ = fake_docker
        e = DockerEnvironment(self.docker_spec(), docker_bin=str(script))
        e.start()
        try:
            result = e.call("notes__create_note", {"title": "in-container"})
            assert "created note" in result.text
        finally:
            e.stop()

    def test_stop_removes_the_container(self, fake_docker):
        script, log = fake_docker
        e = DockerEnvironment(self.docker_spec(), docker_bin=str(script))
        e.start()
        e.stop()
        assert "rm" in log.read_text()

    def test_network_is_disabled_by_default(self, fake_docker):
        script, log = fake_docker
        e = DockerEnvironment(self.docker_spec(), docker_bin=str(script))
        e.start()
        try:
            assert "--network none" in log.read_text()
        finally:
            e.stop()

    def test_missing_docker_binary_raises_a_clear_error(self):
        e = DockerEnvironment(self.docker_spec(), docker_bin="no-such-docker-xyz")
        with pytest.raises(EnvironmentError_, match="docker"):
            e.start()


class TestBuildEnvironment:
    def test_local_kind_builds_a_local_environment(self):
        assert isinstance(build_environment(local_spec()), LocalEnvironment)

    def test_docker_kind_builds_a_docker_environment(self):
        spec = EnvironmentSpec(kind="docker", servers=(NOTES,), image="img")
        assert isinstance(build_environment(spec), DockerEnvironment)

    def test_unknown_kind_raises(self):
        spec = EnvironmentSpec(kind="carrier-pigeon", servers=(NOTES,))
        with pytest.raises(EnvironmentError_, match="carrier-pigeon"):
            build_environment(spec)


class TestWorkspace:
    """Each run gets a scratch directory that every MCP server can see.

    Without it, a harness that spawns its own copy of a server (Claude Code
    does) would have state the post-run checks cannot reach.
    """

    def test_a_workspace_directory_exists_while_the_environment_is_up(self):
        e = LocalEnvironment(local_spec())
        e.start()
        try:
            assert os.path.isdir(e.workspace)
        finally:
            e.stop()

    def test_the_workspace_path_is_exported_to_every_server(self):
        e = LocalEnvironment(local_spec(NOTES, NOTES2))
        e.start()
        try:
            for client in e.clients.values():
                assert client.env["CROSSBAR_WORKSPACE"] == str(e.workspace)
        finally:
            e.stop()

    def test_the_workspace_is_removed_on_stop(self):
        e = LocalEnvironment(local_spec())
        e.start()
        path = e.workspace
        e.stop()
        assert not os.path.exists(path)

    def test_a_caller_supplied_workspace_is_reused_and_kept(self, tmp_path):
        e = LocalEnvironment(local_spec(), workspace=str(tmp_path))
        e.start()
        assert e.workspace == str(tmp_path)
        e.stop()
        assert tmp_path.exists()

    def test_each_run_gets_a_different_workspace(self):
        first, second = LocalEnvironment(local_spec()), LocalEnvironment(local_spec())
        first.start()
        second.start()
        try:
            assert first.workspace != second.workspace
        finally:
            first.stop()
            second.stop()

    def test_server_state_written_to_the_workspace_survives_a_reconnect(self, tmp_path):
        """A durable server can be inspected after the agent's own client is gone."""
        spec = local_spec()
        writer = LocalEnvironment(spec, workspace=str(tmp_path))
        writer.start()
        writer.call("notes__create_note", {"title": "durable"})
        writer.stop()

        reader = LocalEnvironment(spec, workspace=str(tmp_path))
        reader.start()
        try:
            titles = [n["title"] for n in reader.call("notes__list_notes", {}).structured["notes"]]
            assert titles == ["durable"]
        finally:
            reader.stop()


class TestCommandExpansion:
    """Server commands may reference environment variables, so a task pack is
    portable across machines that disagree about where things live."""

    def test_the_interpreter_running_crossbar_is_always_available(self):
        spec = EnvironmentSpec(
            kind="local",
            servers=(
                ServerSpec(
                    name="notes",
                    command="${CROSSBAR_PYTHON}",
                    args=("-m", "tests.fixtures.notes_server"),
                ),
            ),
        )
        e = LocalEnvironment(spec)
        e.start()
        try:
            assert e.clients["notes"].is_running
        finally:
            e.stop()

    def test_arguments_are_expanded_too(self, monkeypatch):
        monkeypatch.setenv("CROSSBAR_TEST_MODULE", "tests.fixtures.notes_server")
        spec = EnvironmentSpec(
            kind="local",
            servers=(
                ServerSpec(name="notes", command=PY, args=("-m", "${CROSSBAR_TEST_MODULE}")),
            ),
        )
        e = LocalEnvironment(spec)
        e.start()
        try:
            assert e.clients["notes"].is_running
        finally:
            e.stop()

    def test_an_undefined_variable_is_left_alone(self, monkeypatch):
        monkeypatch.delenv("CROSSBAR_NOT_SET", raising=False)
        e = LocalEnvironment(local_spec())
        assert e.expand("${CROSSBAR_NOT_SET}/x") == "${CROSSBAR_NOT_SET}/x"

    def test_the_workspace_can_be_referenced_by_a_server(self, tmp_path):
        e = LocalEnvironment(local_spec(), workspace=str(tmp_path))
        e.start()
        try:
            assert e.expand("${CROSSBAR_WORKSPACE}/db.json") == f"{tmp_path}/db.json"
        finally:
            e.stop()


class TestFactoryOptions:
    def test_a_workspace_can_be_passed_through_the_factory(self, tmp_path):
        env = build_environment(local_spec(), workspace=str(tmp_path))
        env.start()
        try:
            assert env.workspace == str(tmp_path)
        finally:
            env.stop()
        assert tmp_path.exists(), "a caller-supplied workspace must not be deleted"

    def test_docker_options_still_reach_the_docker_environment(self):
        spec = EnvironmentSpec(kind="docker", servers=(NOTES,), image="img")
        env = build_environment(spec, docker_bin="my-docker", workspace="/tmp/x")
        assert env.docker_bin == "my-docker"
        assert env.workspace == "/tmp/x"
