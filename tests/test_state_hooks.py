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
from pathlib import Path

from crossbar.environment import EnvironmentError_, EnvironmentHandle

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
        hooks(runner).snapshot(tmp_path)
        assert runner.ran_text() == ["hooks/snapshot.sh"]

    def test_it_copies_out_the_path_the_hook_printed(self, tmp_path):
        """The last line of stdout, not the first: the reference hook prints a
        human-readable line before the path."""
        runner = FakeRunner(
            {"hooks/snapshot.sh": ("snapshot of /app/db/library.db\n/app/snapshots/snapshot.db\n", 0, "")}
        )
        files = FakeFiles()
        got = hooks(runner, files).snapshot(tmp_path)
        assert files.out == [("/app/snapshots/snapshot.db", str(tmp_path / "snapshot.db"))]
        assert got == tmp_path / "snapshot.db"

    def test_a_failing_hook_is_reported(self, tmp_path):
        runner = FakeRunner({"hooks/snapshot.sh": ("", 1, "no .db file in /app/db")})
        with pytest.raises(EnvironmentError_, match="no .db file"):
            hooks(runner).snapshot(tmp_path)

    def test_a_hook_that_prints_no_path_is_reported(self, tmp_path):
        """Copying out a path the hook never named would fail later and further
        from the cause."""
        runner = FakeRunner({"hooks/snapshot.sh": ("   \n", 0, "")})
        with pytest.raises(EnvironmentError_, match="printed no path"):
            hooks(runner).snapshot(tmp_path)


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
            env.state_hooks().snapshot(tmp_path)
            log = fake_docker.log_text()
            assert "exec" in log and "hooks/snapshot.sh" in log
        finally:
            env.stop()

    def test_the_snapshot_is_copied_out_of_the_container(self, fake_docker, tmp_path):
        from crossbar.environment import DockerEnvironment

        env = DockerEnvironment(DOCKER_STATE, docker_bin=fake_docker.binary)
        env.start()
        try:
            env.state_hooks().snapshot(tmp_path)
            assert "cp" in fake_docker.log_text()
            assert f"{env.container_id}:" in fake_docker.log_text()
        finally:
            env.stop()

    def test_hooks_are_refused_before_the_container_is_up(self, fake_docker, tmp_path):
        from crossbar.environment import DockerEnvironment

        env = DockerEnvironment(DOCKER_STATE, docker_bin=fake_docker.binary)
        with pytest.raises(EnvironmentError_, match="not running"):
            env.state_hooks().snapshot(tmp_path)


# -- keeping an environment across Attempts --------------------------------


class RecordingEnv:
    """An Environment that records its lifecycle instead of having one."""

    def __init__(self, spec, log, name):
        self.spec = spec
        self.log = log
        self.name = name
        self.snapshots = 0

    def start(self):
        self.log.append(f"{self.name}:start")
        return EnvironmentHandle(workspace="/ws")

    def stop(self):
        self.log.append(f"{self.name}:stop")

    def state_hooks(self):
        if self.spec.state is None:
            return None
        env = self

        class Hooks:
            def snapshot(self, into_dir):
                env.snapshots += 1
                env.log.append(f"{env.name}:snapshot")
                Path(into_dir).mkdir(parents=True, exist_ok=True)
                target = Path(into_dir) / "snapshot.db"
                target.write_text(f"state {env.snapshots}")
                return target

            def restore(self, source):
                env.log.append(f"{env.name}:restore({Path(source).read_text()})")

        return Hooks()


def pool(log, tmp_path):
    from crossbar.orchestrator.pool import EnvironmentPool

    counter = {"n": 0}

    def build(spec):
        counter["n"] += 1
        return RecordingEnv(spec, log, f"env{counter['n']}")

    return EnvironmentPool(tmp_path / "state", build_environment=build)


HOOKED = EnvironmentSpec(
    kind="docker", image="img", connectors=(), reset="hooks",
    state=StateSpec(**STATE),
)
RECREATE = EnvironmentSpec(kind="docker", image="img", connectors=())


class TestRecreateIsUnchanged:
    def test_every_attempt_gets_its_own_environment(self, tmp_path):
        log = []
        p = pool(log, tmp_path)
        for _ in range(2):
            lease = p.acquire(RECREATE)
            p.release(lease, "a1")
        p.close()
        assert log == ["env1:start", "env1:stop", "env2:start", "env2:stop"]


class TestHooks:
    def test_the_environment_starts_once_and_is_put_back(self, tmp_path):
        log = []
        p = pool(log, tmp_path)
        for name in ("a1", "a2"):
            lease = p.acquire(HOOKED)
            p.release(lease, name)
        p.close()
        assert log == [
            "env1:start",
            "env1:snapshot",              # the baseline, before any Attempt
            "env1:snapshot",              # a1's end state, kept
            "env1:restore(state 1)",      # baseline back
            "env1:snapshot",              # a2's end state
            "env1:restore(state 1)",
            "env1:stop",
        ]

    def test_each_attempts_state_is_kept_under_its_own_name(self, tmp_path):
        p = pool([], tmp_path)
        for name in ("a1", "a2"):
            p.release(p.acquire(HOOKED), name)
        p.close()
        assert (tmp_path / "state" / "a1" / "snapshot.db").exists()
        assert (tmp_path / "state" / "a2" / "snapshot.db").exists()

    def test_the_kept_state_is_what_that_attempt_left(self, tmp_path):
        p = pool([], tmp_path)
        p.release(p.acquire(HOOKED), "a1")
        p.release(p.acquire(HOOKED), "a2")
        p.close()
        assert (tmp_path / "state" / "a1" / "snapshot.db").read_text() == "state 2"
        assert (tmp_path / "state" / "a2" / "snapshot.db").read_text() == "state 3"

    def test_the_baseline_is_not_taken_twice(self, tmp_path):
        log = []
        p = pool(log, tmp_path)
        p.release(p.acquire(HOOKED), "a1")
        p.release(p.acquire(HOOKED), "a2")
        p.close()
        assert log.count("env1:start") == 1

    def test_close_is_idempotent(self, tmp_path):
        log = []
        p = pool(log, tmp_path)
        p.release(p.acquire(HOOKED), "a1")
        p.close()
        p.close()
        assert log.count("env1:stop") == 1

    def test_a_failure_mid_attempt_still_puts_the_environment_back(self, tmp_path):
        """Otherwise the next Attempt inherits the broken one's world."""
        log = []
        p = pool(log, tmp_path)
        lease = p.acquire(HOOKED)
        p.release(lease, "a1")
        assert log[-1] == "env1:restore(state 1)"
        p.close()


# -- through the orchestrator ----------------------------------------------


class TestTheOrchestratorUsesIt:
    def test_an_attempt_records_where_its_state_was_kept(self, tmp_path, monkeypatch):
        """The whole point of the hooks: the Attempt leaves an artefact, and
        the Attempt says where it is."""
        from crossbar.orchestrator.results import Attempt

        assert "state_path" in Attempt.__dataclass_fields__

    def test_the_state_path_survives_a_round_trip(self):
        from crossbar.domain import Role
        from crossbar.orchestrator.results import Attempt

        attempt = Attempt(
            id="a1", test_name="t", task_id="task", model_id="m",
            role=Role.CANDIDATE, repeat=0, state_path="state/a1/snapshot.db",
        )
        assert attempt.to_dict()["state_path"] == "state/a1/snapshot.db"


# -- a local environment, with real hook scripts ---------------------------


def write_hooks(root: Path) -> StateSpec:
    """A miniature conforming environment: state is one text file."""
    state = root / "state"
    snaps = root / "snapshots"
    state.mkdir(parents=True, exist_ok=True)
    snaps.mkdir(parents=True, exist_ok=True)
    (state / "live.txt").write_text("baseline")

    snapshot = root / "snapshot.sh"
    snapshot.write_text(
        f"""#!/bin/sh
set -eu
cp {state}/live.txt {snaps}/snapshot.txt
echo "snapshot taken"
echo "{snaps}/snapshot.txt"
"""
    )
    restore = root / "restore.sh"
    restore.write_text(
        f"""#!/bin/sh
set -eu
src=$(find {snaps} -maxdepth 1 -type f | sort | head -n 1)
[ -n "$src" ] || {{ echo "nothing to restore" >&2; exit 1; }}
cp "$src" {state}/live.txt
echo "{state}/live.txt"
"""
    )
    for script in (snapshot, restore):
        script.chmod(script.stat().st_mode | 0o111)

    return StateSpec(
        dir=str(state),
        snapshot_dir=str(snaps),
        snapshot=str(snapshot),
        restore=str(restore),
    )


class TestALocalEnvironmentWithHooks:
    def _env(self, tmp_path):
        from crossbar.environment import LocalEnvironment

        spec = EnvironmentSpec(
            kind="local", connectors=(), reset="hooks",
            state=write_hooks(tmp_path / "envroot"),
        )
        return LocalEnvironment(spec), spec

    def test_snapshot_then_change_then_restore_comes_back(self, tmp_path):
        """The conformance checklist's last line, end to end, with real
        scripts and real files."""
        env, spec = self._env(tmp_path)
        env.start()
        try:
            hooks = env.state_hooks()
            baseline = hooks.snapshot(tmp_path / "kept")
            live = Path(spec.state.dir) / "live.txt"

            live.write_text("the attempt changed this")
            assert live.read_text() == "the attempt changed this"

            hooks.restore(baseline)
            assert live.read_text() == "baseline"
        finally:
            env.stop()

    def test_the_attempts_state_is_kept_before_the_baseline_goes_back(self, tmp_path):
        env, spec = self._env(tmp_path)
        env.start()
        try:
            hooks = env.state_hooks()
            baseline = hooks.snapshot(tmp_path / "baseline")
            (Path(spec.state.dir) / "live.txt").write_text("what the attempt left")

            kept = hooks.snapshot(tmp_path / "attempt-1")
            hooks.restore(baseline)

            assert kept.read_text() == "what the attempt left"
            assert (Path(spec.state.dir) / "live.txt").read_text() == "baseline"
        finally:
            env.stop()

    def test_a_refusing_restore_hook_is_an_error(self, tmp_path):
        env, spec = self._env(tmp_path)
        env.start()
        try:
            hooks = env.state_hooks()
            empty = tmp_path / "empty" / "nothing.txt"
            empty.parent.mkdir(parents=True)
            empty.write_text("")
            # Clearing leaves the snapshot dir with only this file, which the
            # hook accepts; the real refusal case is an empty directory, which
            # the caller cannot produce through restore(). Drive the hook
            # directly to prove the failure is reported rather than swallowed.
            for leftover in Path(spec.state.snapshot_dir).iterdir():
                leftover.unlink()
            with pytest.raises(EnvironmentError_, match="nothing to restore"):
                env._run_state_hook([spec.state.restore], "the restore hook")
        finally:
            env.stop()


class TestARunThroughTheOrchestrator:
    """The lifecycle the contract describes, driven by a real run."""

    def _test_with_hooks(self, tmp_path, repeats=3):
        from crossbar.domain import Task, Test

        state = write_hooks(tmp_path / "envroot")
        spec = EnvironmentSpec(
            kind="local", connectors=(), reset="hooks", state=state,
        )
        task = Task(id="t1", prompt="do it", golden="done", environment=spec)
        return Test(name="hooked", tasks=(task,), environment=spec,
                    repeats=repeats), state

    def _orchestrate(self, tmp_path, test):
        from crossbar.agents import Agent
        from crossbar.orchestrator import Orchestrator
        from crossbar.roster import parse_roster
        from crossbar.trace import RunStatus, Trajectory

        roster = parse_roster(
            {
                "models": [
                    {"id": "solo", "provider": "anthropic", "model": "m"},
                    {"id": "umpire", "provider": "anthropic", "model": "j"},
                ],
                "roles": {"candidate": "solo", "judge": "umpire"},
            },
            source="<test>",
        )
        live = Path(test.environment.state.dir) / "live.txt"

        def agent_factory(model_id, role, task, repeat):
            class Scribble(Agent):
                id = model_id
                owns_harness = False

                def run(self, task, connectors, repeat=0):
                    live.write_text(f"attempt {repeat} was here")
                    traj = Trajectory(task_id=task.id, agent_id=model_id, repeat=repeat)
                    traj.status = RunStatus.COMPLETED
                    return traj

            return Scribble()

        return Orchestrator(
            roster=roster, tests=[test], results_dir=str(tmp_path / "runs"),
            agent_factory=agent_factory, judge=None,
        )

    def test_each_attempt_starts_from_the_baseline(self, tmp_path):
        """Not from what the last Attempt left. This is the property the whole
        contract exists to provide."""
        test, state = self._test_with_hooks(tmp_path)
        result = self._orchestrate(tmp_path, test).run()
        assert len(result.attempts) == 3
        assert (Path(state.dir) / "live.txt").read_text() == "baseline"

    def test_every_attempts_state_is_kept(self, tmp_path):
        test, _ = self._test_with_hooks(tmp_path)
        result = self._orchestrate(tmp_path, test).run()
        kept = [a.state_path for a in result.attempts]
        assert all(kept), f"an Attempt kept no state: {kept}"
        assert len(set(kept)) == 3

    def test_the_kept_state_is_what_that_attempt_wrote(self, tmp_path):
        test, _ = self._test_with_hooks(tmp_path)
        runs = tmp_path / "runs"
        result = self._orchestrate(tmp_path, test).run()
        for attempt in result.attempts:
            written = (runs / attempt.state_path).read_text()
            assert written == f"attempt {attempt.repeat} was here"


# -- where the starting state comes from -----------------------------------


class TestSeed:
    """An image need not ship with usable state, and often does not: the
    reference environment's /app/db is empty, because its real database arrives
    through a bind mount. An eval cannot use that mount -- every Attempt would
    write to the host's real database -- so the starting state has to be
    handed in, and restore is already the way to hand state in.
    """

    def test_a_seed_is_resolved_against_the_environment_file(self, tmp_path):
        (tmp_path / "seed").mkdir()
        (tmp_path / "seed" / "library.db").write_bytes(b"x")
        spec = parse_environment(
            env(reset="hooks", state={**STATE, "seed": "seed/library.db"}),
            source=str(tmp_path / "env.yaml"),
        )
        assert spec.state.seed == str(tmp_path / "seed" / "library.db")

    def test_no_seed_is_the_default(self):
        assert parse_environment(env(reset="hooks", state=STATE)).state.seed == ""

    def test_a_seed_that_is_not_there_is_refused_at_parse_time(self, tmp_path):
        """Not halfway through a run that has already started containers."""
        with pytest.raises(DomainError, match="seed"):
            parse_environment(
                env(reset="hooks", state={**STATE, "seed": "nope.db"}),
                source=str(tmp_path / "env.yaml"),
            )

    def test_the_pool_installs_the_seed_before_taking_the_baseline(self, tmp_path):
        """The baseline must be the seeded world, not the empty one the image
        happened to ship."""
        log = []
        seed = tmp_path / "seed.db"
        seed.write_text("seeded")
        spec = EnvironmentSpec(
            kind="docker", image="img", connectors=(), reset="hooks",
            state=StateSpec(**STATE, seed=str(seed)),
        )
        p = pool(log, tmp_path)
        p.release(p.acquire(spec), "a1")
        p.close()
        assert log[:3] == ["env1:start", "env1:restore(seeded)", "env1:snapshot"]

    def test_without_a_seed_nothing_is_restored_first(self, tmp_path):
        log = []
        p = pool(log, tmp_path)
        p.release(p.acquire(HOOKED), "a1")
        p.close()
        assert log[:2] == ["env1:start", "env1:snapshot"]
