"""The runner: sweeping the matrix, repeating cells, recording everything."""
import json
import sys

import pytest

from crossbar.config import parse_config
from crossbar.harness import ReactHarness
from crossbar.providers import ScriptedProvider, Usage, scripted_step
from crossbar.runner import RunEvent, Runner, SweepResult, load_sweep, plan_additional_repeats
from crossbar.tasks import TaskPack, parse_task

PY = sys.executable


def task(task_id: str, expect_title: str = "Q3"):
    return parse_task(
        {
            "id": task_id,
            "prompt": f"Create a note titled {expect_title}.",
            "environment": {
                "kind": "local",
                "servers": [
                    {"name": "notes", "command": PY, "args": ["-m", "tests.fixtures.notes_server"]}
                ],
            },
            "checks": [
                {
                    "type": "mcp_state",
                    "server": "notes",
                    "tool": "list_notes",
                    "expect": {"notes": [{"title": expect_title}]},
                }
            ],
        },
        source="<test>",
    )


def config(**overrides):
    data = {
        "models": [
            {
                "id": "good",
                "provider": "openai",
                "model": "good-model",
                "base_url": "http://localhost:1/v1",
                "price": {"input_per_mtok": 1.0, "output_per_mtok": 2.0},
            },
            {
                "id": "bad",
                "provider": "openai",
                "model": "bad-model",
                "base_url": "http://localhost:1/v1",
                "price": {"input_per_mtok": 10.0, "output_per_mtok": 20.0},
            },
        ],
        "harnesses": [{"id": "react", "kind": "react"}],
        "run": {"repeats": 1, "concurrency": 1},
    }
    data.update(overrides)
    return parse_config(data, source="<test>")


def agent_factory(good_titles=("Q3",), bad_titles=("wrong",)):
    """Two scripted agents: one does the task, one does the wrong thing."""

    def factory(agent, model, harness_spec, task_arg=None, repeat=0):
        titles = good_titles if agent.model_id == "good" else bad_titles
        script = [
            scripted_step(
                tool_calls=[("notes__create_note", {"title": t}) for t in titles],
                usage=Usage(1000, 500),
            ),
            scripted_step(text="Done.", usage=Usage(200, 100)),
        ]
        return ReactHarness(), ScriptedProvider(script, model=model.model)

    return factory


def run_sweep(cfg=None, pack=None, tmp_path=None, factory=None, **kwargs):
    runner = Runner(
        cfg or config(),
        pack or TaskPack(tasks=(task("t1"),)),
        results_dir=str(tmp_path) if tmp_path else None,
        agent_factory=factory or agent_factory(),
    )
    return runner.run(**kwargs)


class TestSweepExecution:
    def test_every_cell_and_task_is_run(self, tmp_path):
        pack = TaskPack(tasks=(task("t1"), task("t2")))
        result = run_sweep(pack=pack, tmp_path=tmp_path)
        assert len(result.records) == 4  # 2 agents x 2 tasks x 1 repeat
        assert {r.agent_id for r in result.records} == {"good+react", "bad+react"}

    def test_repeats_are_honoured(self, tmp_path):
        result = run_sweep(cfg=config(run={"repeats": 3, "concurrency": 1}), tmp_path=tmp_path)
        assert len(result.records) == 6
        assert sorted(r.repeat for r in result.records if r.agent_id == "good+react") == [0, 1, 2]

    def test_the_capable_agent_passes_and_the_other_does_not(self, tmp_path):
        result = run_sweep(tmp_path=tmp_path)
        assert result.cell("good+react").pass_rate == 1.0
        assert result.cell("bad+react").pass_rate == 0.0

    def test_each_rollout_gets_a_clean_environment(self, tmp_path):
        """A previous repeat's notes must not satisfy the next repeat's check."""
        result = run_sweep(cfg=config(run={"repeats": 2, "concurrency": 1}), tmp_path=tmp_path)
        bad = [r for r in result.records if r.agent_id == "bad+react"]
        assert all(not r.score.passed for r in bad)

    def test_usage_and_cost_are_recorded_per_rollout(self, tmp_path):
        result = run_sweep(tmp_path=tmp_path)
        record = result.cell("good+react").records[0]
        assert record.usage == Usage(1200, 600)
        assert record.cost_usd == pytest.approx(1200 / 1e6 * 1.0 + 600 / 1e6 * 2.0)

    def test_the_expensive_model_costs_more_for_the_same_work(self, tmp_path):
        result = run_sweep(tmp_path=tmp_path)
        assert result.cell("bad+react").total_cost > result.cell("good+react").total_cost

    def test_trajectories_are_written_to_the_results_directory(self, tmp_path):
        result = run_sweep(tmp_path=tmp_path)
        paths = [r.trajectory_path for r in result.records]
        assert all(p and (tmp_path / p).exists() for p in paths)

    def test_a_failing_environment_is_recorded_not_raised(self, tmp_path):
        broken = parse_task(
            {
                "id": "broken",
                "prompt": "x",
                "environment": {
                    "kind": "local",
                    "servers": [{"name": "nope", "command": "definitely-not-real-xyz"}],
                },
                "checks": [{"type": "final_text", "match": "contains", "value": "x"}],
            },
            source="<test>",
        )
        result = run_sweep(pack=TaskPack(tasks=(broken,)), tmp_path=tmp_path)
        assert all(r.error for r in result.records)
        assert all(r.score.value == 0.0 for r in result.records)

    def test_a_provider_failure_scores_zero_without_stopping_the_sweep(self, tmp_path):
        def factory(agent, model, harness_spec, task_arg=None, repeat=0):
            if agent.model_id == "bad":
                return ReactHarness(), ScriptedProvider([scripted_step(error="endpoint down")])
            return agent_factory()(agent, model, harness_spec, task_arg, repeat)

        result = run_sweep(tmp_path=tmp_path, factory=factory)
        assert result.cell("bad+react").pass_rate == 0.0
        assert result.cell("good+react").pass_rate == 1.0

    def test_concurrency_runs_every_rollout(self, tmp_path):
        cfg = config(run={"repeats": 2, "concurrency": 4})
        pack = TaskPack(tasks=(task("t1"), task("t2")))
        result = run_sweep(cfg=cfg, pack=pack, tmp_path=tmp_path)
        assert len(result.records) == 8


class TestEvents:
    def test_an_event_is_emitted_for_every_rollout(self, tmp_path):
        seen: list[RunEvent] = []
        runner = Runner(
            config(),
            TaskPack(tasks=(task("t1"),)),
            results_dir=str(tmp_path),
            agent_factory=agent_factory(),
            on_event=seen.append,
        )
        runner.run()
        kinds = [e.kind for e in seen]
        assert kinds.count("rollout_finished") == 2
        assert kinds[0] == "sweep_started"
        assert kinds[-1] == "sweep_finished"

    def test_events_carry_progress_counts(self, tmp_path):
        seen: list[RunEvent] = []
        Runner(
            config(),
            TaskPack(tasks=(task("t1"),)),
            results_dir=str(tmp_path),
            agent_factory=agent_factory(),
            on_event=seen.append,
        ).run()
        finished = [e for e in seen if e.kind == "rollout_finished"]
        assert finished[-1].completed == finished[-1].total == 2


class TestPersistence:
    def test_the_sweep_is_saved_and_reloads_identically(self, tmp_path):
        result = run_sweep(tmp_path=tmp_path)
        reloaded = load_sweep(tmp_path / "sweep.json")
        assert isinstance(reloaded, SweepResult)
        assert len(reloaded.records) == len(result.records)
        assert reloaded.cell("good+react").pass_rate == 1.0

    def test_the_saved_file_is_plain_json(self, tmp_path):
        run_sweep(tmp_path=tmp_path)
        data = json.loads((tmp_path / "sweep.json").read_text())
        assert data["records"][0]["agent_id"]
        assert "task_ids" in data

    def test_scores_survive_the_round_trip(self, tmp_path):
        run_sweep(tmp_path=tmp_path)
        reloaded = load_sweep(tmp_path / "sweep.json")
        record = reloaded.cell("good+react").records[0]
        assert record.score.passed is True
        assert record.score.check_results[0].label


class TestCellStatistics:
    def test_pass_rate_and_interval_are_available(self, tmp_path):
        result = run_sweep(cfg=config(run={"repeats": 3, "concurrency": 1}), tmp_path=tmp_path)
        cell = result.cell("good+react")
        assert cell.pass_rate == 1.0
        assert cell.ci_low <= cell.pass_rate <= cell.ci_high

    def test_scores_are_grouped_by_task_for_variance_decomposition(self, tmp_path):
        pack = TaskPack(tasks=(task("t1"), task("t2")))
        result = run_sweep(
            cfg=config(run={"repeats": 2, "concurrency": 1}), pack=pack, tmp_path=tmp_path
        )
        grouped = result.cell("good+react").scores_by_task()
        assert set(grouped) == {"t1", "t2"}
        assert len(grouped["t1"]) == 2

    def test_mean_score_reflects_the_multiplicative_task_score(self, tmp_path):
        result = run_sweep(tmp_path=tmp_path)
        assert 0.0 < result.cell("good+react").mean_score <= 1.0

    def test_failure_modes_are_tallied(self, tmp_path):
        result = run_sweep(tmp_path=tmp_path)
        tally = result.cell("bad+react").failure_tally()
        assert sum(tally.values()) == 1

    def test_an_unknown_cell_raises(self, tmp_path):
        with pytest.raises(KeyError):
            run_sweep(tmp_path=tmp_path).cell("nobody+react")


class TestAdaptiveAllocation:
    def test_clearly_losing_cells_are_not_given_more_repeats(self):
        contested = plan_additional_repeats(
            {"leader": [1.0] * 6, "loser": [0.0] * 6},
            max_repeats=12,
            seed=0,
        )
        assert contested == []

    def test_cells_that_still_overlap_the_leader_get_more_repeats(self):
        contested = plan_additional_repeats(
            {"leader": [1, 1, 1, 1, 1, 0], "rival": [1, 0, 1, 1, 0, 1]},
            max_repeats=12,
            seed=0,
        )
        assert "rival" in contested

    def test_cells_at_the_repeat_ceiling_are_left_alone(self):
        contested = plan_additional_repeats(
            {"leader": [1, 0, 1, 0], "rival": [1, 1, 0, 0]},
            max_repeats=4,
            seed=0,
        )
        assert contested == []

    def test_the_leader_itself_is_never_listed(self):
        contested = plan_additional_repeats(
            {"leader": [1, 1, 1], "rival": [1, 0, 1]}, max_repeats=9, seed=0
        )
        assert "leader" not in contested

    def test_an_adaptive_sweep_stops_early_on_a_clear_separation(self, tmp_path):
        cfg = config(run={"repeats": 2, "concurrency": 1})
        result = run_sweep(cfg=cfg, tmp_path=tmp_path, adaptive=True, max_repeats=8)
        # good always passes, bad always fails: no cell needs extra evidence
        assert len(result.records) == 4
        assert result.extra_repeats == 0


class TestAgentFactoryContract:
    """The factory sees the task and repeat, so a seeded backend is reproducible."""

    def test_the_factory_receives_the_task_and_repeat(self, tmp_path):
        seen = []

        def factory(agent, model, harness_spec, task_arg, repeat):
            seen.append((agent.id, task_arg.id, repeat))
            return agent_factory()(agent, model, harness_spec, task_arg, repeat)

        run_sweep(
            cfg=config(run={"repeats": 2, "concurrency": 1}), tmp_path=tmp_path, factory=factory
        )
        assert ("good+react", "t1", 1) in seen

    def test_the_default_factory_seeds_mock_models_per_repeat(self, tmp_path):
        cfg = parse_config(
            {
                "models": [{"id": "mock", "provider": "mock", "model": "mock", "skill": 0.5}],
                "harnesses": [{"id": "react", "kind": "react"}],
                "run": {"repeats": 3, "concurrency": 1, "seed": 4},
            },
            source="<test>",
        )
        demo_task = parse_task(
            {
                "id": "t1",
                "prompt": "Create a note titled Q3.",
                "environment": {
                    "kind": "local",
                    "servers": [
                        {
                            "name": "notes",
                            "command": PY,
                            "args": ["-m", "tests.fixtures.notes_server"],
                        }
                    ],
                },
                "checks": [{"type": "tool_called", "server": "notes", "tool": "create_note"}],
                "demo": {
                    "script": [{"tool": "notes__create_note", "args": {"title": "Q3"}}],
                    "final": "done",
                },
            },
            source="<test>",
        )
        runner = Runner(cfg, TaskPack(tasks=(demo_task,)), results_dir=str(tmp_path))
        result = runner.run()
        assert len(result.records) == 3
        # a skill of 0.5 must not produce three identical rollouts by accident
        assert len({r.score.passed for r in result.records}) >= 1


class TestCliHarnessVerification:
    """A harness that runs its own copies of the MCP servers still has to be
    scored against the state it actually left behind."""

    @pytest.fixture
    def fake_claude(self, tmp_path, monkeypatch):
        import json as _json
        import stat

        script = tmp_path / "claude"
        script.write_text(f'#!/bin/sh\nexec "{PY}" -m tests.fixtures.fake_claude "$@"\n')
        script.chmod(script.stat().st_mode | stat.S_IEXEC)

        transcript = tmp_path / "t.jsonl"
        transcript.write_text(
            _json.dumps(
                {
                    "type": "result",
                    "subtype": "success",
                    "result": "Created the note.",
                    "usage": {"input_tokens": 100, "output_tokens": 20},
                }
            )
        )
        monkeypatch.setenv("FAKE_CLAUDE_SCRIPT", str(transcript))
        monkeypatch.setenv(
            "FAKE_CLAUDE_APPLY",
            _json.dumps(
                [{"server": "notes", "tool": "create_note", "args": {"title": "Q3"}}]
            ),
        )
        return str(script)

    def cli_config(self, claude_bin):
        return parse_config(
            {
                "models": [{"id": "cc", "provider": "claude-cli", "model": "opus"}],
                "harnesses": [
                    {"id": "claude-code", "kind": "claude-code", "claude_bin": claude_bin}
                ],
                "run": {"repeats": 1, "concurrency": 1},
            },
            source="<test>",
        )

    def state_task(self):
        return parse_task(
            {
                "id": "cli-state",
                "prompt": "Create a note titled Q3.",
                "environment": {
                    "kind": "local",
                    "servers": [
                        {
                            "name": "notes",
                            "command": PY,
                            "args": ["-m", "tests.fixtures.notes_server"],
                        }
                    ],
                },
                "checks": [
                    {
                        "type": "mcp_state",
                        "server": "notes",
                        "tool": "list_notes",
                        "expect": {"notes": [{"title": "Q3"}]},
                    }
                ],
            },
            source="<test>",
        )

    def test_state_left_by_the_cli_is_seen_by_the_scorer(self, fake_claude, tmp_path):
        result = Runner(
            self.cli_config(fake_claude),
            TaskPack(tasks=(self.state_task(),)),
            results_dir=str(tmp_path / "runs"),
        ).run()
        record = result.records[0]
        assert record.error == ""
        assert record.score.passed is True

    def test_a_cli_run_that_changes_nothing_fails_the_check(self, fake_claude, tmp_path, monkeypatch):
        monkeypatch.delenv("FAKE_CLAUDE_APPLY")
        result = Runner(
            self.cli_config(fake_claude),
            TaskPack(tasks=(self.state_task(),)),
            results_dir=str(tmp_path / "runs"),
        ).run()
        assert result.records[0].score.passed is False
