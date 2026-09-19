"""The command line."""
import json
from pathlib import Path

import pytest

from crossbar.cli import main
from crossbar.domain import Role

ROOT = Path(__file__).resolve().parent.parent
FIXTURE_TEST = str(ROOT / "tests" / "fixtures" / "tests" / "support-triage")


@pytest.fixture(autouse=True)
def _dummy_key(monkeypatch):
    """Most tests need a key present; the ones about missing keys delete it."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")


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
        "    api_key_env: ANTHROPIC_API_KEY\n"
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


class TestPreflightOnRun:
    """A run costs money, so it checks itself before committing to one."""

    def broken_test(self, tmp_path):
        directory = tmp_path / "broken-env"
        directory.mkdir()
        (directory / "test.yaml").write_text("name: Broken\nenvironment: env.yaml\n")
        (directory / "env.yaml").write_text(
            "kind: local\nconnectors:\n  mcp:\n    servers:\n"
            "      - name: x\n        command: no-such-binary-xyz\n"
        )
        (directory / "a.task.yaml").write_text("id: a\nprompt: p\ngolden: g\n")
        return str(directory)

    def test_run_reports_its_preflight(self, capsys, roster_file, tmp_path, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        code, out = cli(
            ["run", "--roster", roster_file, "--test", FIXTURE_TEST,
             "--out", str(tmp_path / "runs"), "--repeats", "1", "--no-judge"],
            capsys,
        )
        assert "PRE-FLIGHT" in out.upper()

    def test_a_broken_environment_stops_the_run_before_it_starts(
        self, capsys, roster_file, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        out_dir = tmp_path / "runs"
        code, out = cli(
            ["run", "--roster", roster_file, "--test", self.broken_test(tmp_path),
             "--out", str(out_dir)],
            capsys,
        )
        assert code != 0
        assert "FAIL" in out.upper()
        assert not (out_dir / "run.json").exists(), "nothing should have been spent"

    def test_a_missing_key_stops_the_run(self, capsys, roster_file, tmp_path, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        out_dir = tmp_path / "runs"
        code, out = cli(
            ["run", "--roster", roster_file, "--test", FIXTURE_TEST, "--out", str(out_dir)],
            capsys,
        )
        assert code != 0
        assert "ANTHROPIC_API_KEY" in out

    def test_warnings_do_not_stop_a_run(self, capsys, roster_file, tmp_path, monkeypatch):
        """The baseline judging itself is the user's call, not a blocker."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        code, out = cli(
            ["run", "--roster", roster_file, "--test", FIXTURE_TEST,
             "--out", str(tmp_path / "runs"), "--repeats", "1", "--no-judge"],
            capsys,
        )
        assert code == 0
        assert "WARN" in out.upper()

    def test_preflight_can_be_skipped(self, capsys, roster_file, tmp_path, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        code, out = cli(
            ["run", "--roster", roster_file, "--test", FIXTURE_TEST,
             "--out", str(tmp_path / "runs"), "--repeats", "1", "--no-judge",
             "--skip-preflight"],
            capsys,
        )
        assert code == 0
        assert "PRE-FLIGHT" not in out.upper()

    def test_validate_runs_the_same_checks(self, capsys, roster_file, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        code, out = cli(["validate", "--roster", roster_file, "--test", FIXTURE_TEST], capsys)
        assert code == 0
        assert "PRE-FLIGHT" in out.upper()

    def test_validate_fails_on_a_broken_environment(self, capsys, roster_file, tmp_path):
        code, out = cli(
            ["validate", "--roster", roster_file, "--test", self.broken_test(tmp_path)], capsys
        )
        assert code != 0

    def test_doctor_reports_the_same_model_key_state(self, capsys, roster_file, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        _, out = cli(["doctor", "--roster", roster_file], capsys)
        assert "ANTHROPIC_API_KEY" in out


class TestProjectFolderCommands:
    """An installed binary finds its setup in .crossbar, not from flags."""

    def test_init_scaffolds_a_project(self, capsys, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        code, out = cli(["init"], capsys)
        assert code == 0
        assert (tmp_path / ".crossbar" / "config.yaml").exists()
        assert "crossbar" in out

    def test_init_says_what_to_do_next(self, capsys, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _, out = cli(["init"], capsys)
        assert "edit" in out.lower()
        assert "validate" in out or "run" in out

    def test_init_refuses_to_overwrite(self, capsys, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        cli(["init"], capsys)
        code, out = cli(["init"], capsys)
        assert code != 0
        assert "exists" in out.lower()

    def test_validate_uses_the_project_when_no_flags_are_given(
        self, capsys, tmp_path, monkeypatch
    ):
        monkeypatch.chdir(tmp_path)
        cli(["init"], capsys)
        capsys.readouterr()
        code, out = cli(["validate"], capsys)
        assert "Support triage" in out

    def test_commands_work_from_a_subdirectory(self, capsys, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        cli(["init"], capsys)
        nested = tmp_path / "deep" / "inside"
        nested.mkdir(parents=True)
        monkeypatch.chdir(nested)
        capsys.readouterr()
        _, out = cli(["validate"], capsys)
        assert "Support triage" in out

    def test_a_missing_project_says_how_to_make_one(self, capsys, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        code, out = cli(["validate"], capsys)
        assert code != 0
        assert "crossbar init" in out

    def test_a_fresh_project_fails_preflight_on_placeholder_endpoints(
        self, capsys, tmp_path, monkeypatch
    ):
        """It must not look like a working setup that quietly does nothing."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        cli(["init"], capsys)
        capsys.readouterr()
        code, out = cli(["validate"], capsys)
        assert code != 0
        assert "FAIL" in out.upper()

    def test_explicit_flags_still_win_over_the_project(
        self, capsys, tmp_path, roster_file, monkeypatch
    ):
        monkeypatch.chdir(tmp_path)
        cli(["init"], capsys)
        capsys.readouterr()
        code, out = cli(["validate", "--roster", roster_file, "--test", FIXTURE_TEST], capsys)
        assert "frontier" in out

    def test_results_default_into_the_project_folder(self, capsys, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("LOCAL_API_KEY", "x")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "y")
        cli(["init"], capsys)
        capsys.readouterr()
        cli(["run", "--repeats", "1", "--no-judge", "--skip-preflight"], capsys)
        assert (tmp_path / ".crossbar" / "runs" / "run.json").exists()


class TestTuiCommand:
    """`crossbar tui` must go through the same project loading as everything
    else. It did not, and typing it in a directory with no project produced a
    raw TypeError traceback."""

    def test_tui_outside_a_project_fails_cleanly(self, capsys, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        code, out = cli(["tui"], capsys)
        assert code != 0
        assert "crossbar init" in out
        assert "Traceback" not in out

    def test_tui_builds_the_app_from_the_project(self, capsys, tmp_path, monkeypatch):
        from crossbar.tui import CrossbarApp

        built = {}
        monkeypatch.setattr(CrossbarApp, "run", lambda self, *a, **k: built.update(app=self))
        monkeypatch.chdir(tmp_path)
        cli(["init"], capsys)
        capsys.readouterr()

        code, _ = cli(["tui"], capsys)
        assert code == 0
        assert [t.name for t in built["app"].tests] == ["Support triage"]
        assert built["app"].roster.assigned(Role.CANDIDATE).id == "my-model"

    def test_the_tui_gets_a_judge(self, capsys, tmp_path, monkeypatch):
        from crossbar.judging import Judge
        from crossbar.tui import CrossbarApp

        built = {}
        monkeypatch.setattr(CrossbarApp, "run", lambda self, *a, **k: built.update(app=self))
        monkeypatch.chdir(tmp_path)
        cli(["init"], capsys)
        capsys.readouterr()
        cli(["tui"], capsys)
        assert isinstance(built["app"].judge, Judge)

    def test_the_tui_writes_into_the_project(self, capsys, tmp_path, monkeypatch):
        from crossbar.tui import CrossbarApp

        built = {}
        monkeypatch.setattr(CrossbarApp, "run", lambda self, *a, **k: built.update(app=self))
        monkeypatch.chdir(tmp_path)
        cli(["init"], capsys)
        capsys.readouterr()
        cli(["tui"], capsys)
        assert ".crossbar" in built["app"].results_dir

    def test_explicit_flags_still_work(self, capsys, tmp_path, roster_file, monkeypatch):
        from crossbar.tui import CrossbarApp

        built = {}
        monkeypatch.setattr(CrossbarApp, "run", lambda self, *a, **k: built.update(app=self))
        monkeypatch.chdir(tmp_path)
        cli(["tui", "--roster", roster_file, "--test", FIXTURE_TEST], capsys)
        assert built["app"].roster.assigned(Role.CANDIDATE).id == "local"


class TestUnexpectedErrors:
    """A stack trace is a bug report, not a user interface."""

    def test_an_unexpected_failure_prints_a_message_not_a_traceback(
        self, capsys, tmp_path, monkeypatch
    ):
        import crossbar.cli as cli_module

        monkeypatch.setattr(
            cli_module, "_cmd_doctor", lambda args: (_ for _ in ()).throw(RuntimeError("boom"))
        )
        code, out = cli(["doctor"], capsys)
        assert code != 0
        assert "boom" in out
        assert "Traceback" not in out

    def test_the_traceback_is_available_when_asked_for(self, capsys, monkeypatch):
        import crossbar.cli as cli_module

        monkeypatch.setenv("CROSSBAR_TRACEBACK", "1")
        monkeypatch.setattr(
            cli_module, "_cmd_doctor", lambda args: (_ for _ in ()).throw(RuntimeError("boom"))
        )
        with pytest.raises(RuntimeError):
            main(["doctor"])
