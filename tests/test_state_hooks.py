"""The state-hook contract: an Environment that can hand its state out and
take it back.

`reset: recreate` is correct and slow -- a fresh container per Attempt, paying
the image's start cost every time, and throwing away the state the Attempt
produced. An Environment whose server keeps a database can instead snapshot
the baseline once, and between Attempts put it back. What that buys is not only
speed: the snapshot of each Attempt's end state is a real artefact, which can
be graded, re-graded later, or started from to reproduce a failure.

The contract these tests hold crossbar to is the one in the reference
environment's docs/state-hooks.md.
"""

import pytest

from crossbar.domain import EnvironmentSpec, StateSpec
from crossbar.domain.loader import DomainError, parse_environment
from crossbar.environment import EnvironmentError_

pytest_plugins = ()

CONNECTORS = {"mcp": {"servers": [{"name": "s", "command": "true"}]}}


def env(**overrides):
    data = {
        "kind": "docker",
        "image": "img",
        "connectors": CONNECTORS,
    }
    data.update(overrides)
    return data


STATE = {
    "dir": "/app/db",
    "snapshot_dir": "/app/snapshots",
    "snapshot": "hooks/snapshot.sh",
    "restore": "hooks/restore.sh",
}


class TestDeclaringIt:
    def test_a_state_block_is_parsed(self):
        spec = parse_environment(env(reset="hooks", state=STATE))
        assert spec.reset == "hooks"
        assert spec.state == StateSpec(
            dir="/app/db",
            snapshot_dir="/app/snapshots",
            snapshot="hooks/snapshot.sh",
            restore="hooks/restore.sh",
        )

    def test_restart_after_restore_defaults_to_false(self):
        spec = parse_environment(env(reset="hooks", state=STATE))
        assert spec.state.restart_after_restore is False

    def test_a_server_that_caches_can_ask_for_a_restart(self):
        spec = parse_environment(
            env(reset="hooks", state={**STATE, "restart_after_restore": True})
        )
        assert spec.state.restart_after_restore is True

    def test_recreate_is_still_the_default(self):
        assert parse_environment(env()).reset == "recreate"
        assert parse_environment(env()).state is None


class TestWhatItRefuses:
    def test_hooks_without_a_state_block(self):
        with pytest.raises(DomainError, match="state"):
            parse_environment(env(reset="hooks"))

    @pytest.mark.parametrize("missing", sorted(STATE))
    def test_an_incomplete_state_block(self, missing):
        state = {k: v for k, v in STATE.items() if k != missing}
        with pytest.raises(DomainError, match=missing):
            parse_environment(env(reset="hooks", state=state))

    def test_a_snapshot_directory_inside_the_state_directory(self):
        """A snapshot written into the state directory is a second state file,
        and the next start deletes one of the two."""
        with pytest.raises(DomainError, match="inside"):
            parse_environment(
                env(reset="hooks", state={**STATE, "snapshot_dir": "/app/db/snaps"})
            )

    def test_the_two_directories_being_the_same(self):
        with pytest.raises(DomainError, match="inside"):
            parse_environment(
                env(reset="hooks", state={**STATE, "snapshot_dir": "/app/db"})
            )

    def test_a_trailing_slash_does_not_hide_the_nesting(self):
        with pytest.raises(DomainError, match="inside"):
            parse_environment(
                env(reset="hooks", state={**STATE, "dir": "/app/db/",
                                          "snapshot_dir": "/app/db/snaps/"})
            )

    def test_a_sibling_directory_is_fine(self):
        """/app/db-snapshots starts with /app/db as a string but is not inside
        it, and a path check that missed that would reject a legal layout."""
        spec = parse_environment(
            env(reset="hooks", state={**STATE, "snapshot_dir": "/app/db-snapshots"})
        )
        assert spec.state.snapshot_dir == "/app/db-snapshots"

    def test_a_state_block_without_the_hooks_policy(self):
        """Declaring state but leaving reset at recreate is a config that looks
        like it does something and does not."""
        with pytest.raises(DomainError, match="reset: hooks"):
            parse_environment(env(state=STATE))


# -- running the hooks -----------------------------------------------------


class FakeRunner:
    """Stands in for `docker exec` / a local subprocess."""

    def __init__(self, results=None):
        self.results = dict(results or {})
        self.ran: list[tuple[str, ...]] = []

    def __call__(self, argv, what):
        self.ran.append(tuple(argv))
        key = argv[0]
        result = self.results.get(key, ("", 0, ""))
        stdout, code, stderr = result
        if code != 0:
            raise EnvironmentError_(f"{what}: {stderr}")
        return stdout

    def ran_text(self):
        return [" ".join(a) for a in self.ran]


class FakeFiles:
    def __init__(self):
        self.out: list[tuple[str, str]] = []
        self.into: list[tuple[str, str]] = []
        self.cleared: list[str] = []

    def copy_out(self, remote, local):
        self.out.append((remote, str(local)))

    def copy_in(self, local, remote):
        self.into.append((str(local), remote))

    def clear(self, remote_dir):
        self.cleared.append(remote_dir)


def hooks(runner=None, files=None, spec=None):
    from crossbar.environment.state import StateHooks

    return StateHooks(
        spec or StateSpec(**STATE),
        run=runner or FakeRunner(),
        files=files or FakeFiles(),
    )


class TestSnapshot:
    def test_it_runs_the_hook(self, tmp_path):
        runner = FakeRunner({"hooks/snapshot.sh": ("/app/snapshots/snapshot.db\n", 0, "")})
        hooks(runner).snapshot(tmp_path / "baseline.db")
        assert runner.ran_text() == ["hooks/snapshot.sh"]

    def test_it_copies_out_the_path_the_hook_printed(self, tmp_path):
        """The last line of stdout, not the first: the reference hook prints a
        human-readable line before the path."""
        runner = FakeRunner(
            {"hooks/snapshot.sh": ("snapshot of /app/db/library.db\n/app/snapshots/snapshot.db\n", 0, "")}
        )
        files = FakeFiles()
        got = hooks(runner, files).snapshot(tmp_path / "baseline.db")
        assert files.out == [("/app/snapshots/snapshot.db", str(tmp_path / "baseline.db"))]
        assert got == tmp_path / "baseline.db"

    def test_a_failing_hook_is_reported(self, tmp_path):
        runner = FakeRunner({"hooks/snapshot.sh": ("", 1, "no .db file in /app/db")})
        with pytest.raises(EnvironmentError_, match="no .db file"):
            hooks(runner).snapshot(tmp_path / "baseline.db")

    def test_a_hook_that_prints_no_path_is_reported(self, tmp_path):
        """Copying out a path the hook never named would fail later and further
        from the cause."""
        runner = FakeRunner({"hooks/snapshot.sh": ("   \n", 0, "")})
        with pytest.raises(EnvironmentError_, match="printed no path"):
            hooks(runner).snapshot(tmp_path / "baseline.db")


class TestRestore:
    def test_it_puts_exactly_one_file_where_the_hook_will_look(self, tmp_path):
        """The hook takes the first file in the snapshot directory by name. The
        caller is what makes 'first' unambiguous, by leaving one there."""
        files = FakeFiles()
        source = tmp_path / "baseline.db"
        source.write_bytes(b"x")
        hooks(FakeRunner(), files).restore(source)
        assert files.cleared == ["/app/snapshots"]
        assert files.into == [(str(source), "/app/snapshots")]

    def test_it_clears_before_it_copies(self, tmp_path):
        """The other order would delete the file it had just put there."""
        order = []
        files = FakeFiles()
        files.clear = lambda d: order.append("clear")
        files.copy_in = lambda l, r: order.append("copy")
        source = tmp_path / "baseline.db"
        source.write_bytes(b"x")
        hooks(FakeRunner(), files).restore(source)
        assert order == ["clear", "copy"]

    def test_it_runs_the_restore_hook_last(self, tmp_path):
        runner = FakeRunner()
        source = tmp_path / "baseline.db"
        source.write_bytes(b"x")
        hooks(runner).restore(source)
        assert runner.ran_text() == ["hooks/restore.sh"]

    def test_a_refused_restore_is_reported(self, tmp_path):
        runner = FakeRunner({"hooks/restore.sh": ("", 1, "not a library database")})
        source = tmp_path / "baseline.db"
        source.write_bytes(b"x")
        with pytest.raises(EnvironmentError_, match="not a library database"):
            hooks(runner).restore(source)

    def test_restoring_something_that_is_not_there(self, tmp_path):
        with pytest.raises(EnvironmentError_, match="does not exist"):
            hooks().restore(tmp_path / "missing.db")


# -- the docker environment ------------------------------------------------


DOCKER_STATE = EnvironmentSpec(
    kind="docker",
    image="img",
    connectors=(),
    reset="hooks",
    state=StateSpec(**STATE),
)


class TestDockerStateHooks:
    def test_an_environment_without_a_state_block_offers_no_hooks(self, fake_docker):
        from crossbar.environment import DockerEnvironment

        env = DockerEnvironment(
            EnvironmentSpec(kind="docker", image="img", connectors=()),
            docker_bin=fake_docker.binary,
        )
        assert env.state_hooks() is None

    def test_the_hook_runs_inside_the_container(self, fake_docker, tmp_path):
        from crossbar.environment import DockerEnvironment

        env = DockerEnvironment(DOCKER_STATE, docker_bin=fake_docker.binary)
        env.start()
        try:
            env.state_hooks().snapshot(tmp_path / "baseline.db")
            log = fake_docker.log_text()
            assert "exec" in log and "hooks/snapshot.sh" in log
        finally:
            env.stop()

    def test_the_snapshot_is_copied_out_of_the_container(self, fake_docker, tmp_path):
        from crossbar.environment import DockerEnvironment

        env = DockerEnvironment(DOCKER_STATE, docker_bin=fake_docker.binary)
        env.start()
        try:
            env.state_hooks().snapshot(tmp_path / "baseline.db")
            assert "cp" in fake_docker.log_text()
            assert f"{env.container_id}:" in fake_docker.log_text()
        finally:
            env.stop()

    def test_hooks_are_refused_before_the_container_is_up(self, fake_docker, tmp_path):
        from crossbar.environment import DockerEnvironment

        env = DockerEnvironment(DOCKER_STATE, docker_bin=fake_docker.binary)
        with pytest.raises(EnvironmentError_, match="not running"):
            env.state_hooks().snapshot(tmp_path / "baseline.db")
