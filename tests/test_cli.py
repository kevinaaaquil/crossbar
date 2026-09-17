"""The command line: what a person types."""
import json
from pathlib import Path

import pytest

from crossbar.cli import main

ROOT = Path(__file__).resolve().parent.parent
PACK = str(ROOT / "taskpacks" / "support-triage")
CONFIG = str(ROOT / "crossbar.yaml")


def run(argv, capsys):
    code = main(argv)
    return code, capsys.readouterr().out


class TestHelp:
    def test_no_arguments_prints_usage(self, capsys):
        code, out = run([], capsys)
        assert code != 0
        assert "usage" in out.lower() or "usage" in capsys.readouterr().err.lower()

    def test_help_lists_the_commands(self, capsys):
        with pytest.raises(SystemExit):
            main(["--help"])
        out = capsys.readouterr().out
        for command in ("run", "report", "validate", "doctor", "tui"):
            assert command in out


class TestValidate:
    def test_a_good_pack_and_config_validate(self, capsys):
        code, out = run(["validate", "--config", CONFIG, "--pack", PACK], capsys)
        assert code == 0
        assert "4 task" in out or "4 tasks" in out

    def test_it_lists_the_matrix_it_would_run(self, capsys):
        _, out = run(["validate", "--config", CONFIG, "--pack", PACK], capsys)
        assert "mock-strong+react" in out

    def test_a_broken_task_is_reported_with_its_file(self, capsys, tmp_path):
        (tmp_path / "bad.task.yaml").write_text("id: x\n")
        code, out = run(["validate", "--config", CONFIG, "--pack", str(tmp_path)], capsys)
        assert code != 0
        assert "bad.task.yaml" in out

    def test_a_missing_config_is_reported(self, capsys, tmp_path):
        code, out = run(["validate", "--config", str(tmp_path / "nope.yaml"), "--pack", PACK], capsys)
        assert code != 0
        assert "not found" in out.lower()


class TestRun:
    def test_a_sweep_runs_and_prints_a_verdict(self, capsys, tmp_path):
        code, out = run(
            ["run", "--config", CONFIG, "--pack", PACK, "--repeats", "1", "--out", str(tmp_path)],
            capsys,
        )
        assert code == 0
        assert "CROSSBAR VERDICT" in out

    def test_results_are_written_to_the_output_directory(self, capsys, tmp_path):
        run(
            ["run", "--config", CONFIG, "--pack", PACK, "--repeats", "1", "--out", str(tmp_path)],
            capsys,
        )
        assert (tmp_path / "sweep.json").exists()
        assert (tmp_path / "report.md").exists()
        assert list((tmp_path / "traces").iterdir())

    def test_repeats_can_be_overridden_from_the_command_line(self, capsys, tmp_path):
        run(
            ["run", "--config", CONFIG, "--pack", PACK, "--repeats", "2", "--out", str(tmp_path)],
            capsys,
        )
        data = json.loads((tmp_path / "sweep.json").read_text())
        assert data["repeats"] == 2

    def test_json_output_is_machine_readable(self, capsys, tmp_path):
        code, out = run(
            [
                "run", "--config", CONFIG, "--pack", PACK,
                "--repeats", "1", "--out", str(tmp_path), "--json",
            ],
            capsys,
        )
        payload = json.loads(out)
        assert payload["verdict"]["winner"]
        assert payload["cells"]

    def test_progress_is_printed_while_running(self, capsys, tmp_path):
        _, out = run(
            ["run", "--config", CONFIG, "--pack", PACK, "--repeats", "1", "--out", str(tmp_path)],
            capsys,
        )
        assert "/" in out  # a completed/total counter

    def test_a_single_agent_can_be_selected(self, capsys, tmp_path):
        run(
            [
                "run", "--config", CONFIG, "--pack", PACK, "--repeats", "1",
                "--out", str(tmp_path), "--agent", "mock-weak+react",
            ],
            capsys,
        )
        data = json.loads((tmp_path / "sweep.json").read_text())
        assert {r["agent_id"] for r in data["records"]} == {"mock-weak+react"}

    def test_an_unknown_agent_is_rejected(self, capsys, tmp_path):
        code, out = run(
            [
                "run", "--config", CONFIG, "--pack", PACK,
                "--out", str(tmp_path), "--agent", "nobody+react",
            ],
            capsys,
        )
        assert code != 0
        assert "nobody+react" in out


class TestReport:
    def test_it_re_renders_a_saved_sweep(self, capsys, tmp_path):
        run(
            ["run", "--config", CONFIG, "--pack", PACK, "--repeats", "1", "--out", str(tmp_path)],
            capsys,
        )
        code, out = run(["report", str(tmp_path / "sweep.json")], capsys)
        assert code == 0
        assert "CROSSBAR VERDICT" in out
        assert "MATRIX" in out

    def test_a_missing_sweep_file_is_reported(self, capsys, tmp_path):
        code, out = run(["report", str(tmp_path / "nope.json")], capsys)
        assert code != 0
        assert "not found" in out.lower()

    def test_markdown_can_be_written_out(self, capsys, tmp_path):
        run(
            ["run", "--config", CONFIG, "--pack", PACK, "--repeats", "1", "--out", str(tmp_path)],
            capsys,
        )
        target = tmp_path / "out.md"
        run(["report", str(tmp_path / "sweep.json"), "--markdown", str(target)], capsys)
        assert "Crossbar report" in target.read_text()


class TestDoctor:
    def test_it_reports_on_the_local_setup(self, capsys):
        code, out = run(["doctor", "--config", CONFIG], capsys)
        assert code == 0
        assert "python" in out.lower()
        assert "docker" in out.lower()
        assert "claude" in out.lower()

    def test_it_says_which_models_are_ready_to_use(self, capsys):
        _, out = run(["doctor", "--config", CONFIG], capsys)
        assert "mock-strong" in out
        assert "ready" in out.lower()

    def test_it_flags_a_model_whose_key_is_missing(self, capsys, tmp_path, monkeypatch):
        monkeypatch.delenv("SOME_MISSING_KEY", raising=False)
        config = tmp_path / "c.yaml"
        config.write_text(
            "models:\n  - id: remote\n    provider: anthropic\n    model: x\n"
            "    api_key_env: SOME_MISSING_KEY\n"
        )
        _, out = run(["doctor", "--config", str(config)], capsys)
        assert "SOME_MISSING_KEY" in out


class TestInit:
    def test_it_writes_a_starter_config_and_pack(self, capsys, tmp_path):
        code, out = run(["init", str(tmp_path)], capsys)
        assert code == 0
        assert (tmp_path / "crossbar.yaml").exists()
        assert list((tmp_path / "taskpacks" / "support-triage").glob("*.task.yaml"))

    def test_it_refuses_to_overwrite_an_existing_config(self, capsys, tmp_path):
        (tmp_path / "crossbar.yaml").write_text("models: []\n")
        code, out = run(["init", str(tmp_path)], capsys)
        assert code != 0
        assert "exists" in out.lower()
