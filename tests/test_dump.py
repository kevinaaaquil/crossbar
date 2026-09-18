"""The dump: one zip of everything a run produced, for the user."""
import zipfile

import pytest

from crossbar.dump import DumpError, create_dump


@pytest.fixture
def run_dir(tmp_path):
    (tmp_path / "run.json").write_text('{"run_id": "abc"}')
    (tmp_path / "plans").mkdir()
    (tmp_path / "plans" / "escalate.json").write_text('{"task_id": "escalate"}')
    attempt = tmp_path / "attempts" / "local-t-r0"
    attempt.mkdir(parents=True)
    for name in ("attempt.json", "trajectory.json", "evidence.json", "judgement.json"):
        (attempt / name).write_text("{}")
    return tmp_path


class TestCreateDump:
    def test_it_writes_a_zip(self, run_dir):
        path = create_dump(run_dir)
        assert path.exists()
        assert zipfile.is_zipfile(path)

    def test_it_lands_beside_the_run_by_default(self, run_dir):
        assert create_dump(run_dir) == run_dir / "dump.zip"

    def test_it_contains_everything_the_run_produced(self, run_dir):
        with zipfile.ZipFile(create_dump(run_dir)) as archive:
            names = set(archive.namelist())
        assert "run.json" in names
        assert "plans/escalate.json" in names
        assert "attempts/local-t-r0/judgement.json" in names

    def test_contents_survive_the_round_trip(self, run_dir):
        with zipfile.ZipFile(create_dump(run_dir)) as archive:
            assert archive.read("run.json").decode() == '{"run_id": "abc"}'

    def test_an_earlier_dump_is_not_packed_into_the_new_one(self, run_dir):
        create_dump(run_dir)
        with zipfile.ZipFile(create_dump(run_dir)) as archive:
            assert "dump.zip" not in archive.namelist()

    def test_a_custom_destination_is_honoured(self, run_dir, tmp_path):
        target = tmp_path / "elsewhere" / "run-2026-09-19.zip"
        assert create_dump(run_dir, target) == target
        assert target.exists()

    def test_a_missing_run_directory_is_reported(self, tmp_path):
        with pytest.raises(DumpError, match="not a directory"):
            create_dump(tmp_path / "nope")

    def test_a_directory_with_no_run_file_is_reported(self, tmp_path):
        with pytest.raises(DumpError, match="run.json"):
            create_dump(tmp_path)

    def test_it_reports_what_it_packed(self, run_dir):
        path = create_dump(run_dir)
        with zipfile.ZipFile(path) as archive:
            assert len(archive.namelist()) == 6

    def test_re_dumping_replaces_the_previous_zip(self, run_dir):
        first = create_dump(run_dir)
        (run_dir / "extra.json").write_text("{}")
        with zipfile.ZipFile(create_dump(run_dir)) as archive:
            assert "extra.json" in archive.namelist()
        assert first.exists()


class TestIntegrationWithARealRun:
    def test_a_finished_run_dumps(self, tmp_path):
        from tests.test_orchestrator import ROSTER, judge, single_task_test, solving_agent
        from crossbar.orchestrator import Orchestrator

        Orchestrator(
            roster=ROSTER, tests=[single_task_test()], results_dir=str(tmp_path),
            agent_factory=solving_agent, judge=judge(),
        ).run()

        with zipfile.ZipFile(create_dump(tmp_path)) as archive:
            names = archive.namelist()
        assert "run.json" in names
        assert any(n.endswith("judgement.json") for n in names)
        assert any(n.endswith("evidence.json") for n in names)
        assert any(n.startswith("plans/") for n in names)
