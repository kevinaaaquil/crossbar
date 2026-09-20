"""`crossbar setup`: building a config by being asked, one thing at a time.

No TUI. The same configwriter backend the Setup tab uses, driven by prompts, so
somebody on a plain terminal never has to open an editor or an app.
"""
import pytest

from crossbar.cli_setup import Prompter, run_setup
from crossbar.configwriter import draft_from_config
from crossbar.project import CONFIG_NAME, PROJECT_DIR


class FakePrompter(Prompter):
    """Feeds scripted answers and records what was asked."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.asked: list[str] = []
        self.shown: list[str] = []

    def ask(self, question: str, default: str = "") -> str:
        self.asked.append(question)
        if not self.answers:
            raise AssertionError(f"ran out of answers at: {question!r}")
        given = self.answers.pop(0)
        return default if given == "" and default else given

    def say(self, message: str = "") -> None:
        self.shown.append(message)

    @property
    def transcript(self) -> str:
        return "\n".join(self.asked + self.shown)


def config_path(tmp_path):
    return tmp_path / PROJECT_DIR / CONFIG_NAME


def _starter(tmp_path):
    """The config `crossbar init` writes, placeholder models and all."""
    from crossbar.project import STARTER_CONFIG

    config_path(tmp_path).write_text(STARTER_CONFIG)


@pytest.fixture(autouse=True)
def a_real_test_directory(tmp_path):
    """Setup checks Test directories against the disk, and every real project
    has this one: ``crossbar init`` writes it."""
    (tmp_path / PROJECT_DIR / "tests" / "support-triage").mkdir(
        parents=True, exist_ok=True
    )


TWO_MODELS = [
    "2",                      # preset: vLLM
    "local", "qwen3",         # id, model name
    "", "",                   # base_url (default), api_key_env (none)
    "0.2", "0.6",             # prices
    "y",                      # add another model
    "6",                      # preset: anthropic
    "frontier", "claude-opus-5",
    "ANTHROPIC_API_KEY",
    "15", "75",
    "n",                      # no more models
    "local",                  # candidate
    "frontier",               # baseline
    "",                       # judge: none, baseline judges
    "tests/support-triage",   # tests
    "3",                      # repeats
    "1",                      # judge_tests
    "y",                      # save
]


class TestAskingForModels:
    def test_it_offers_the_presets_by_name(self, tmp_path):
        prompter = FakePrompter(TWO_MODELS)
        run_setup(config_path(tmp_path), prompter)
        assert "Ollama" in prompter.transcript
        assert "Claude Code" in prompter.transcript

    def test_a_preset_fills_in_the_endpoint(self, tmp_path):
        prompter = FakePrompter(TWO_MODELS)
        run_setup(config_path(tmp_path), prompter)
        model = draft_from_config(config_path(tmp_path)).model("local")
        assert "8000" in model.base_url

    def test_several_models_can_be_added(self, tmp_path):
        run_setup(config_path(tmp_path), FakePrompter(TWO_MODELS))
        draft = draft_from_config(config_path(tmp_path))
        assert [m.id for m in draft.models] == ["local", "frontier"]

    def test_prices_are_asked_for(self, tmp_path):
        prompter = FakePrompter(TWO_MODELS)
        run_setup(config_path(tmp_path), prompter)
        assert any("price" in q.lower() or "mtok" in q.lower() for q in prompter.asked)
        assert draft_from_config(config_path(tmp_path)).model("local").input_price == 0.2

    def test_it_explains_that_a_key_is_a_variable_name(self, tmp_path):
        prompter = FakePrompter(TWO_MODELS)
        run_setup(config_path(tmp_path), prompter)
        transcript = prompter.transcript.lower()
        assert "name of" in transcript or "never the key" in transcript

    def test_an_agent_cli_asks_for_its_command(self, tmp_path):
        answers = [
            "7", "cc", "opus", "claude", "15", "75", "n",
            "cc", "", "",                 # candidate cc, no baseline...
            "cc",                          # ...so a judge is demanded; wrong first
            "cc", "", "",
            "tests/x", "1", "1", "y",
        ]
        prompter = FakePrompter(answers)
        try:
            run_setup(config_path(tmp_path), prompter)
        except AssertionError:
            pass  # the scripted answers may not satisfy validation; the ask is the point
        assert any("command" in q.lower() for q in prompter.asked)


class TestRoles:
    def test_it_asks_for_the_candidate_and_baseline(self, tmp_path):
        prompter = FakePrompter(TWO_MODELS)
        run_setup(config_path(tmp_path), prompter)
        assert any("candidate" in q.lower() for q in prompter.asked)
        assert any("baseline" in q.lower() for q in prompter.asked)

    def test_the_roles_land_in_the_config(self, tmp_path):
        run_setup(config_path(tmp_path), FakePrompter(TWO_MODELS))
        draft = draft_from_config(config_path(tmp_path))
        assert draft.candidate == "local"
        assert draft.baseline == "frontier"

    def test_leaving_the_judge_blank_warns_about_the_conflict(self, tmp_path):
        prompter = FakePrompter(TWO_MODELS)
        run_setup(config_path(tmp_path), prompter)
        assert "conflict" in prompter.transcript.lower()

    def test_a_single_model_run_is_offered(self, tmp_path):
        prompter = FakePrompter(TWO_MODELS)
        run_setup(config_path(tmp_path), prompter)
        assert any("on its own" in q.lower() or "blank" in q.lower()
                   for q in prompter.asked if "baseline" in q.lower())


class TestValidationBeforeWriting:
    def test_it_re_asks_rather_than_writing_something_invalid(self, tmp_path):
        """A candidate that is not a connected model: it must ask again rather
        than write a config the next command cannot load."""
        answers = [
            "2", "local", "qwen3", "", "", "0.2", "0.6", "y",
            "6", "frontier", "claude-opus-5", "ANTHROPIC_API_KEY", "15", "75", "n",
            "nobody", "frontier", "",    # a candidate that is not connected
            "local", "frontier", "",     # asked again straight away, correctly
            "tests/support-triage", "3", "1", "y",
        ]
        prompter = FakePrompter(answers)
        run_setup(config_path(tmp_path), prompter)
        assert any("nobody" in line for line in prompter.shown)
        assert draft_from_config(config_path(tmp_path)).candidate == "local"

    def test_declining_to_save_writes_nothing(self, tmp_path):
        answers = list(TWO_MODELS)
        answers[-1] = "n"
        run_setup(config_path(tmp_path), FakePrompter(answers))
        assert not config_path(tmp_path).exists()

    def test_it_shows_a_summary_before_saving(self, tmp_path):
        prompter = FakePrompter(TWO_MODELS)
        run_setup(config_path(tmp_path), prompter)
        transcript = prompter.transcript
        assert "local" in transcript and "frontier" in transcript
        assert any("save" in q.lower() for q in prompter.asked)

    def test_it_returns_true_when_it_wrote(self, tmp_path):
        assert run_setup(config_path(tmp_path), FakePrompter(TWO_MODELS)) is True

    def test_it_returns_false_when_it_did_not(self, tmp_path):
        answers = list(TWO_MODELS)
        answers[-1] = "n"
        assert run_setup(config_path(tmp_path), FakePrompter(answers)) is False


class TestWhatItWrites:
    def test_the_file_loads_as_a_project(self, tmp_path):
        from crossbar.demo.scripted import example_test_path
        from crossbar.project import load_project
        import shutil

        shutil.copytree(example_test_path(),
                        tmp_path / PROJECT_DIR / "tests" / "support-triage",
                        dirs_exist_ok=True)
        run_setup(config_path(tmp_path), FakePrompter(TWO_MODELS))
        assert load_project(tmp_path).tests[0].name == "Support triage"

    def test_run_settings_are_written(self, tmp_path):
        run_setup(config_path(tmp_path), FakePrompter(TWO_MODELS))
        draft = draft_from_config(config_path(tmp_path))
        assert draft.repeats == 3
        assert draft.judge_tests == 1

    def test_an_existing_config_is_the_starting_point(self, tmp_path):
        run_setup(config_path(tmp_path), FakePrompter(TWO_MODELS))
        prompter = FakePrompter(["",  # keep the models it already has
                                 "n", "local", "frontier", "", "tests/support-triage",
                                 "5", "1", "y"])
        run_setup(config_path(tmp_path), prompter)
        draft = draft_from_config(config_path(tmp_path))
        assert [m.id for m in draft.models] == ["local", "frontier"]
        assert draft.repeats == 5

    def test_it_says_where_it_wrote(self, tmp_path):
        prompter = FakePrompter(TWO_MODELS)
        run_setup(config_path(tmp_path), prompter)
        assert str(config_path(tmp_path)) in prompter.transcript


class TestTheCommand:
    def test_setup_is_a_command(self, capsys, tmp_path, monkeypatch):
        from crossbar.cli import main

        monkeypatch.chdir(tmp_path)
        with pytest.raises(SystemExit):
            main(["--help"])
        assert "setup" in capsys.readouterr().out


class TestDroppingModels:
    """`init` writes two placeholder models. Without a way to drop them, every
    project built by being asked carries junk it never agreed to."""

    def test_a_placeholder_can_be_removed(self, tmp_path):
        _starter(tmp_path)
        prompter = FakePrompter(
            [
                "my-model",              # drop one placeholder, keep frontier
                "y",                     # connect a real one
                "2", "local", "qwen3", "", "", "0", "0",
                "n",
                "local", "frontier", "",
                "tests/support-triage", "3", "1", "y",
            ]
        )
        run_setup(config_path(tmp_path), prompter=prompter)
        assert any("Remove any" in q for q in prompter.asked)
        draft = draft_from_config(config_path(tmp_path))
        assert sorted(m.id for m in draft.models) == ["frontier", "local"]

    def test_removing_every_model_is_allowed_and_you_start_over(self, tmp_path):
        _starter(tmp_path)
        prompter = FakePrompter(
            [
                "my-model, frontier",
                "2", "local", "qwen3", "", "", "0", "0",
                "y",
                "6", "frontier2", "claude-opus-5", "ANTHROPIC_API_KEY", "15", "75",
                "n",
                "local", "frontier2", "",
                "tests/support-triage", "3", "1", "y",
            ]
        )
        run_setup(config_path(tmp_path), prompter=prompter)
        draft = draft_from_config(config_path(tmp_path))
        assert sorted(m.id for m in draft.models) == ["frontier2", "local"]

    def test_an_unknown_id_to_remove_is_reported_not_ignored(self, tmp_path):
        _starter(tmp_path)
        prompter = FakePrompter(
            [
                "nosuchmodel",          # not a connected model
                "my-model, frontier",   # asked again
                "2", "local", "qwen3", "", "", "0", "0",
                "y",
                "6", "f2", "claude-opus-5", "ANTHROPIC_API_KEY", "15", "75",
                "n",
                "local", "f2", "",
                "tests/support-triage", "3", "1", "y",
            ]
        )
        run_setup(config_path(tmp_path), prompter=prompter)
        assert any("nosuchmodel" in s for s in prompter.shown)


class TestTestDirectories:
    """A Test directory that does not exist is a typo, and setup is the moment
    to say so -- not the run that was supposed to follow it."""

    def test_a_missing_test_directory_is_refused(self, tmp_path):
        _starter(tmp_path)
        prompter = FakePrompter(
            [
                "",                        # keep the placeholders
                "n",                       # connect nothing new
                "my-model", "frontier", "",
                "tests/nope",              # does not exist
                "tests/support-triage",    # asked again
                "3", "1", "y",
            ]
        )
        run_setup(config_path(tmp_path), prompter=prompter)
        assert any("tests/nope" in s for s in prompter.shown)
        draft = draft_from_config(config_path(tmp_path))
        assert list(draft.tests) == ["tests/support-triage"]
