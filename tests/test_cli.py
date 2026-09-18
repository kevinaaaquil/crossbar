"""The command line."""
import json
from pathlib import Path

import pytest

from crossbar.cli import main

ROOT = Path(__file__).resolve().parent.parent
FIXTURE_TEST = str(ROOT / "tests" / "fixtures" / "tests" / "support-triage")


@pytest.fixture
def roster_file(tmp_path):
    path = tmp_path / "roster.yaml"
    path.write_text(
        "models:\n"
        "  - id: local\n"
        "    provider: openai\n"
        "    model: qwen\n"
        "    base_url: http://localhost:1/v1\n"
        "    price: {input_per_mtok: 1.0, output_per_mtok: 2.0}\n"
        "  - id: frontier\n"
        "    provider: anthropic\n"
        "    model: big\n"
        "    price: {input_per_mtok: 10.0, output_per_mtok: 20.0}\n"
        "roles:\n"
        "  candidate: local\n"
        "  baseline: frontier\n"
    )
    return str(path)


def cli(argv, capsys):
    code = main(argv)
    return code, capsys.readouterr().out


class TestUsage:
    def test_no_arguments_prints_usage(self, capsys):
        code, out = cli([], capsys)
        assert code != 0
        assert "usage" in (out + capsys.readouterr().err).lower()

    def test_help_lists_the_commands(self, capsys):
        with pytest.raises(SystemExit):
            main(["--help"])
        out = capsys.readouterr().out
        for command in ("validate", "run", "judge", "report", "dump", "doctor", "tui"):
            assert command in out


class TestValidate:
    def test_a_good_setup_validates(self, capsys, roster_file):
        code, out = cli(["validate", "--roster", roster_file, "--test", FIXTURE_TEST], capsys)
        assert code == 0
        assert "2 task" in out

    def test_it_shows_the_roles(self, capsys, roster_file):
        _, out = cli(["validate", "--roster", roster_file, "--test", FIXTURE_TEST], capsys)
        assert "candidate" in out and "local" in out
        assert "baseline" in out and "frontier" in out

    def test_it_warns_when_the_judge_is_the_baseline(self, capsys, roster_file):
        _, out = cli(["validate", "--roster", roster_file, "--test", FIXTURE_TEST], capsys)
        assert "judge" in out.lower()

    def test_it_reports_how_many_attempts_would_run(self, capsys, roster_file):
        _, out = cli(["validate", "--roster", roster_file, "--test", FIXTURE_TEST], capsys)
        assert "8" in out  # 2 tasks x 2 repeats x 2 roles

    def test_a_broken_test_is_reported_with_its_file(self, capsys, roster_file, tmp_path):
        broken = tmp_path / "broken"
        broken.mkdir()
        (broken / "test.yaml").write_text("name: X\nenvironment: env.yaml\n")
        (broken / "env.yaml").write_text("kind: local\nconnectors: {mcp: {}}\n")
        (broken / "a.task.yaml").write_text("id: a\nprompt: p\n")
        code, out = cli(["validate", "--roster", roster_file, "--test", str(broken)], capsys)
        assert code != 0
        assert "a.task.yaml" in out and "golden" in out

    def test_a_missing_roster_is_reported(self, capsys, tmp_path):
        code, out = cli(
            ["validate", "--roster", str(tmp_path / "nope.yaml"), "--test", FIXTURE_TEST], capsys
        )
        assert code != 0
        assert "not found" in out.lower()

    def test_an_unknown_connector_is_caught_early(self, capsys, roster_file, tmp_path):
        bad = tmp_path / "bad"
        bad.mkdir()
        (bad / "test.yaml").write_text("name: X\nenvironment: env.yaml\n")
        (bad / "env.yaml").write_text("kind: local\nconnectors:\n  telepathy: {}\n")
        (bad / "a.task.yaml").write_text("id: a\nprompt: p\ngolden: g\n")
        code, out = cli(["validate", "--roster", roster_file, "--test", str(bad)], capsys)
        assert code != 0
        assert "telepathy" in out


class TestReportAndDump:
    def finished_run(self, tmp_path):
        from crossbar.orchestrator import Orchestrator
        from tests.test_orchestrator import ROSTER, judge, single_task_test, solving_agent

        Orchestrator(
            roster=ROSTER, tests=[single_task_test()], results_dir=str(tmp_path),
            agent_factory=solving_agent, judge=judge(),
        ).run()
        return tmp_path

    def test_report_renders_a_stored_run(self, capsys, tmp_path):
        run_dir = self.finished_run(tmp_path)
        code, out = cli(["report", str(run_dir)], capsys)
        assert code == 0
        assert "VERDICT" in out.upper()

    def test_report_can_write_markdown(self, capsys, tmp_path):
        run_dir = self.finished_run(tmp_path)
        target = run_dir / "report.md"
        cli(["report", str(run_dir), "--markdown", str(target)], capsys)
        assert "Crossbar" in target.read_text()

    def test_report_on_a_missing_run_is_reported(self, capsys, tmp_path):
        code, out = cli(["report", str(tmp_path / "nope")], capsys)
        assert code != 0
        assert "not found" in out.lower() or "no run" in out.lower()

    def test_dump_writes_a_zip(self, capsys, tmp_path):
        run_dir = self.finished_run(tmp_path)
        code, out = cli(["dump", str(run_dir)], capsys)
        assert code == 0
        assert (run_dir / "dump.zip").exists()
        assert "dump.zip" in out

    def test_json_output_is_machine_readable(self, capsys, tmp_path):
        run_dir = self.finished_run(tmp_path)
        code, out = cli(["report", str(run_dir), "--json"], capsys)
        payload = json.loads(out)
        assert payload["models"]
        assert "verdict" in payload


class TestJudgeCommand:
    def test_it_judges_a_stored_unjudged_run(self, capsys, tmp_path):
        """The deferred path: plans and evidence were captured, grading was not
        done, and the user asks for it afterwards."""
        from crossbar.orchestrator import Orchestrator, load_run
        from tests.test_orchestrator import ROSTER, judge, single_task_test, solving_agent

        Orchestrator(
            roster=ROSTER, tests=[single_task_test()], results_dir=str(tmp_path),
            agent_factory=solving_agent, judge=judge(), judge_tests=0,
        ).run()
        assert all(a.judgement is None for a in load_run(tmp_path / "run.json").attempts)

        code, out = cli(["judge", str(tmp_path), "--scripted-pass"], capsys)
        assert code == 0
        assert all(a.judgement is not None for a in load_run(tmp_path / "run.json").attempts)

    def test_it_reports_a_missing_run(self, capsys, tmp_path):
        code, out = cli(["judge", str(tmp_path / "nope")], capsys)
        assert code != 0


class TestDoctor:
    def test_it_reports_on_the_local_setup(self, capsys, roster_file):
        code, out = cli(["doctor", "--roster", roster_file], capsys)
        assert code == 0
        assert "python" in out.lower()
        assert "docker" in out.lower()

    def test_it_says_which_models_are_ready(self, capsys, roster_file, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        _, out = cli(["doctor", "--roster", roster_file], capsys)
        assert "local" in out and "frontier" in out

    def test_it_lists_the_available_connectors(self, capsys, roster_file):
        _, out = cli(["doctor", "--roster", roster_file], capsys)
        assert "mcp" in out


class TestRun:
    def test_a_run_needs_a_reachable_model(self, capsys, roster_file, tmp_path):
        """Nothing is connected in the suite, so this must fail cleanly rather
        than hang or traceback."""
        code, out = cli(
            ["run", "--roster", roster_file, "--test", FIXTURE_TEST,
             "--out", str(tmp_path / "runs"), "--repeats", "1", "--no-judge"],
            capsys,
        )
        assert code == 0
        assert (tmp_path / "runs" / "run.json").exists()

    def test_attempts_are_recorded_even_when_the_model_is_unreachable(
        self, capsys, roster_file, tmp_path
    ):
        cli(
            ["run", "--roster", roster_file, "--test", FIXTURE_TEST,
             "--out", str(tmp_path / "runs"), "--repeats", "1", "--no-judge"],
            capsys,
        )
        data = json.loads((tmp_path / "runs" / "run.json").read_text())
        assert len(data["attempts"]) == 4
        assert all(a["error"] for a in data["attempts"])
