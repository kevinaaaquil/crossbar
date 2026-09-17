"""The built-in mock model: lets the whole bench run with no API key.

It replays a task's `demo` script with a configurable skill level, so the
crossover a real sweep produces can be demonstrated - and tested - offline.
"""
import sys

import pytest

from crossbar.env import LocalEnvironment
from crossbar.harness import ReactHarness
from crossbar.providers import MockProvider
from crossbar.tasks import EnvironmentSpec, ServerSpec, parse_task
from crossbar.trace import RunStatus

PY = sys.executable
NOTES = ServerSpec(name="notes", command=PY, args=("-m", "tests.fixtures.notes_server"))

DEMO = {
    "script": [
        {"tool": "notes__create_note", "args": {"title": "Q3"}},
        {"tool": "notes__tag_note", "args": {"title": "Q3", "tag": "done"}},
    ],
    "final": "Created and tagged the note.",
}


def make_task(demo=DEMO, **overrides):
    data = {
        "id": "notes-001",
        "prompt": "Create a note titled Q3 and tag it done.",
        "environment": {
            "kind": "local",
            "servers": [
                {"name": "notes", "command": PY, "args": ["-m", "tests.fixtures.notes_server"]}
            ],
        },
        "checks": [{"type": "tool_called", "server": "notes", "tool": "create_note"}],
    }
    if demo is not None:
        data["demo"] = demo
    data.update(overrides)
    return parse_task(data, source="<test>")


@pytest.fixture
def env():
    e = LocalEnvironment(EnvironmentSpec(kind="local", servers=(NOTES,)))
    e.start()
    yield e
    e.stop()


class TestDemoScriptParsing:
    def test_a_task_can_carry_a_demo_script(self):
        task = make_task()
        assert task.demo is not None
        assert task.demo.steps[0].tool == "notes__create_note"
        assert task.demo.final == "Created and tagged the note."

    def test_tasks_without_a_demo_block_have_none(self):
        assert make_task(demo=None).demo is None

    def test_a_demo_step_without_a_tool_is_rejected(self):
        from crossbar.tasks import TaskValidationError

        with pytest.raises(TaskValidationError, match="tool"):
            make_task(demo={"script": [{"args": {}}]})


class TestPerfectSkill:
    def test_replays_every_scripted_step(self, env):
        task = make_task()
        provider = MockProvider(model="mock-strong", task=task, skill=1.0, seed=1)
        traj = ReactHarness().run(task, env, provider)
        assert traj.status is RunStatus.COMPLETED
        assert [e.tool for e in traj.tool_events] == ["create_note", "tag_note"]

    def test_ends_with_the_scripted_final_answer(self, env):
        task = make_task()
        traj = ReactHarness().run(task, env, MockProvider("m", task, skill=1.0, seed=1))
        assert traj.final_text == "Created and tagged the note."

    def test_reports_plausible_token_usage(self, env):
        task = make_task()
        traj = ReactHarness().run(task, env, MockProvider("m", task, skill=1.0, seed=1))
        assert traj.usage.total > 0

    def test_a_task_with_no_demo_script_just_answers(self, env):
        task = make_task(demo=None)
        traj = ReactHarness().run(task, env, MockProvider("m", task, skill=1.0, seed=1))
        assert traj.status is RunStatus.COMPLETED
        assert traj.tool_events == []


class TestImperfectSkill:
    def test_zero_skill_never_completes_the_script(self, env):
        task = make_task()
        traj = ReactHarness().run(task, env, MockProvider("m", task, skill=0.0, seed=1))
        assert [e.tool for e in traj.tool_events] != ["create_note", "tag_note"]

    def test_the_same_seed_always_behaves_the_same(self, env):
        task = make_task()
        first = ReactHarness().run(task, env, MockProvider("m", task, skill=0.5, seed=9))
        second = ReactHarness().run(task, env, MockProvider("m", task, skill=0.5, seed=9))
        assert [e.tool for e in first.tool_events] == [e.tool for e in second.tool_events]

    def test_different_seeds_can_behave_differently(self, env):
        task = make_task()
        runs = {
            tuple(e.tool for e in ReactHarness().run(task, env, MockProvider("m", task, 0.5, seed)).tool_events)
            for seed in range(12)
        }
        assert len(runs) > 1

    def test_a_middling_model_passes_some_runs_and_not_others(self, env):
        task = make_task()
        outcomes = [
            len(ReactHarness().run(task, env, MockProvider("m", task, 0.6, seed)).tool_events) == 2
            for seed in range(20)
        ]
        assert any(outcomes) and not all(outcomes)

    def test_skill_is_clamped_to_a_probability(self):
        task = make_task()
        assert MockProvider("m", task, skill=5.0, seed=1).skill == 1.0
        assert MockProvider("m", task, skill=-2.0, seed=1).skill == 0.0


class TestReproducibilityAcrossProcesses:
    def test_the_seed_does_not_depend_on_python_string_hashing(self):
        """A sweep must replay identically in a fresh interpreter."""
        import subprocess
        import sys as _sys

        snippet = (
            "import sys; sys.path.insert(0, 'src'); sys.path.insert(0, '.');"
            "from crossbar.providers import MockProvider;"
            "from tests.test_mock_model import make_task;"
            "p = MockProvider('m', make_task(), skill=0.5, seed=3);"
            "print([round(p._rng.random(), 6) for _ in range(5)])"
        )
        outputs = {
            subprocess.run(
                [_sys.executable, "-c", snippet],
                capture_output=True,
                text=True,
                env={"PYTHONHASHSEED": str(seed), "PATH": "/usr/bin:/bin"},
            ).stdout.strip()
            for seed in (0, 1, 2)
        }
        assert len(outputs) == 1
        assert outputs != {""}


class TestVerifyTurnRecovery:
    """A self-check turn is a second chance, which is why harness choice moves
    a weak model more than a strong one."""

    def test_the_mock_retries_skipped_steps_when_asked_to_verify(self, env):
        task = make_task()
        harness = ReactHarness(verify=True)
        traj = harness.run(task, env, MockProvider("m", task, skill=0.0, seed=2, recover=1.0))
        assert [e.tool for e in traj.tool_events] == ["create_note", "tag_note"]

    def test_without_a_verify_turn_nothing_is_recovered(self, env):
        task = make_task()
        traj = ReactHarness(verify=False).run(
            task, env, MockProvider("m", task, skill=0.0, seed=2, recover=1.0)
        )
        assert traj.tool_events == []

    def test_recovery_is_itself_imperfect(self, env):
        task = make_task()
        recovered = [
            len(
                ReactHarness(verify=True)
                .run(task, env, MockProvider("m", task, 0.0, seed, recover=0.5))
                .tool_events
            )
            for seed in range(16)
        ]
        assert any(recovered) and not all(r == 2 for r in recovered)

    def test_a_strong_model_has_little_left_to_recover(self, env):
        task = make_task()
        traj = ReactHarness(verify=True).run(
            task, env, MockProvider("m", task, skill=1.0, seed=2, recover=1.0)
        )
        assert [e.tool for e in traj.tool_events] == ["create_note", "tag_note"]
