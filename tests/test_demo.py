"""The demo command: the whole pipeline, visible, with nothing connected."""
import json

import pytest

from crossbar.cli import main
from crossbar.demo import run_demo
from crossbar.judging import Outcome


class TestRunDemo:
    def test_it_runs_the_shipped_example(self, tmp_path):
        result = run_demo(str(tmp_path))
        assert len(result.attempts) == 8  # 4 tasks x 1 repeat x 2 roles

    def test_the_diligent_model_passes_and_the_idle_one_does_not(self, tmp_path):
        from crossbar.domain import Role

        result = run_demo(str(tmp_path))
        candidate = [a for a in result.attempts if a.role is Role.CANDIDATE]
        baseline = [a for a in result.attempts if a.role is Role.BASELINE]
        assert all(a.judgement.passed for a in candidate)
        assert not any(a.judgement.passed for a in baseline)

    def test_everything_is_graded(self, tmp_path):
        result = run_demo(str(tmp_path))
        assert all(a.judgement.outcome is Outcome.GRADED for a in result.attempts)

    def test_it_writes_the_usual_layout(self, tmp_path):
        run_demo(str(tmp_path))
        assert (tmp_path / "run.json").exists()
        assert len(list((tmp_path / "plans").iterdir())) == 4
        assert len(list((tmp_path / "attempts").iterdir())) == 8

    def test_it_needs_no_network_or_keys(self, tmp_path, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        assert run_demo(str(tmp_path)).attempts

    def test_repeats_can_be_raised(self, tmp_path):
        assert len(run_demo(str(tmp_path), repeats=2).attempts) == 16


class TestDemoCommand:
    def test_the_command_prints_a_verdict(self, tmp_path, capsys):
        code = main(["demo", "--out", str(tmp_path)])
        out = capsys.readouterr().out
        assert code == 0
        assert "VERDICT" in out.upper()

    def test_it_says_the_models_are_scripted(self, tmp_path, capsys):
        main(["demo", "--out", str(tmp_path)])
        out = capsys.readouterr().out.lower()
        assert "scripted" in out or "no models" in out

    def test_it_recommends_the_model_that_did_the_work(self, tmp_path, capsys):
        main(["demo", "--out", str(tmp_path)])
        assert "diligent" in capsys.readouterr().out

    def test_the_results_can_then_be_reported(self, tmp_path, capsys):
        main(["demo", "--out", str(tmp_path)])
        capsys.readouterr()
        assert main(["report", str(tmp_path), "--json"]) == 0
        assert json.loads(capsys.readouterr().out)["models"]


class TestDemoQuiet:
    def test_progress_lines_can_be_suppressed(self, tmp_path, capsys):
        main(["demo", "--out", str(tmp_path), "--quiet"])
        out = capsys.readouterr().out
        assert "VERDICT" in out.upper()
        assert "[  1/" not in out
