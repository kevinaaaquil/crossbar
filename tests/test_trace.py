"""Trajectory recording: the evidence trail for every rollout."""
import json

import pytest

from crossbar.providers import Usage
from crossbar.trace import (
    RunStatus,
    StepEvent,
    ToolEvent,
    Trajectory,
    load_trajectory,
)


def sample() -> Trajectory:
    traj = Trajectory(task_id="t1", agent_id="qwen+react", repeat=0)
    traj.record_step(StepEvent(index=0, text="thinking", usage=Usage(10, 4), duration_ms=120))
    traj.record_tool(
        ToolEvent(
            step=0,
            api_name="notes__create_note",
            server="notes",
            tool="create_note",
            arguments={"title": "Q3"},
            result_text="created note 'Q3'",
            duration_ms=8,
        )
    )
    traj.finish(status=RunStatus.COMPLETED, final_text="all done")
    return traj


class TestRecording:
    def test_steps_and_tool_events_are_kept_in_order(self):
        traj = sample()
        assert [s.index for s in traj.steps] == [0]
        assert [t.tool for t in traj.tool_events] == ["create_note"]

    def test_usage_is_summed_across_steps(self):
        traj = Trajectory(task_id="t", agent_id="a", repeat=0)
        traj.record_step(StepEvent(index=0, usage=Usage(10, 5)))
        traj.record_step(StepEvent(index=1, usage=Usage(7, 3)))
        assert traj.usage == Usage(17, 8)

    def test_tool_call_counts_are_available_by_qualified_name(self):
        traj = sample()
        assert traj.tool_call_counts() == {"notes.create_note": 1}

    def test_failed_tool_calls_are_counted_separately(self):
        traj = sample()
        traj.record_tool(
            ToolEvent(step=1, api_name="notes__explode", server="notes", tool="explode",
                      result_text="boom", is_error=True)
        )
        assert traj.error_count == 1
        assert traj.tool_call_count == 2

    def test_finish_sets_status_and_final_text(self):
        traj = sample()
        assert traj.status is RunStatus.COMPLETED
        assert traj.final_text == "all done"

    def test_wall_time_is_recorded(self):
        traj = sample()
        assert traj.wall_time_s >= 0

    def test_security_violations_are_recorded(self):
        traj = sample()
        traj.record_violation("called forbidden tool notes.delete_all")
        assert traj.security_violations == ["called forbidden tool notes.delete_all"]

    def test_a_fresh_trajectory_is_running(self):
        assert Trajectory(task_id="t", agent_id="a", repeat=0).status is RunStatus.RUNNING


class TestSerialisation:
    def test_round_trips_through_json(self, tmp_path):
        path = tmp_path / "run.json"
        original = sample()
        original.write(path)
        restored = load_trajectory(path)
        assert restored.task_id == original.task_id
        assert restored.agent_id == original.agent_id
        assert restored.status is RunStatus.COMPLETED
        assert restored.tool_events[0].arguments == {"title": "Q3"}
        assert restored.usage == original.usage

    def test_written_file_is_readable_json(self, tmp_path):
        path = tmp_path / "run.json"
        sample().write(path)
        data = json.loads(path.read_text())
        assert data["task_id"] == "t1"
        assert data["steps"][0]["text"] == "thinking"

    def test_creates_parent_directories(self, tmp_path):
        path = tmp_path / "deep" / "nested" / "run.json"
        sample().write(path)
        assert path.exists()

    def test_loading_a_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_trajectory(tmp_path / "nope.json")

    def test_large_tool_results_are_truncated_on_disk(self, tmp_path):
        traj = Trajectory(task_id="t", agent_id="a", repeat=0)
        traj.record_tool(
            ToolEvent(step=0, api_name="x__y", server="x", tool="y", result_text="z" * 50_000)
        )
        traj.finish(RunStatus.COMPLETED, "")
        path = tmp_path / "run.json"
        traj.write(path)
        stored = json.loads(path.read_text())["tool_events"][0]["result_text"]
        assert len(stored) < 50_000
        assert "truncated" in stored
