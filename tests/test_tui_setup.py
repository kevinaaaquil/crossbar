"""The Setup screen: building `.crossbar/config.yaml` without hand-editing YAML.

Everything here is offline. The screen is driven through ``run_test()`` the way
a person drives it, and the files it writes are loaded back with
``load_project`` — a config that the rest of crossbar cannot read is not a
config, however good it looks on screen.

The backend is ``crossbar.configwriter``, which is already tested on its own.
These tests are about the wiring: that the screen never writes something
invalid, never clobbers a project it was asked to edit, and never reworded a
problem the user has to act on.
"""

from dataclasses import replace
from pathlib import Path

from textual.widgets import Button, Input, ListView, Select, Static, TabbedContent

from crossbar.configwriter import (
    PRESETS,
    ConfigDraft,
    ModelDraft,
    draft_from_config,
    validate_draft,
    write_config,
)
from crossbar.project import CONFIG_NAME, PROJECT_DIR, load_project
from crossbar.tui import CrossbarApp
from crossbar.tui.setup import SetupCommands, SetupPanel
from tests.test_orchestrator import FIXTURE, ROSTER, judge, single_task_test, solving_agent

TEST_DIR = str(Path(FIXTURE).resolve())
"""An absolute path, so a written config resolves it wherever it is loaded."""


FREE_TEXT_PANELS = (
    "setup-target",
    "setup-warning",
    "setup-preset-hint",
    "setup-problems",
    "setup-status",
)


# -- helpers ---------------------------------------------------------------


def config_path(tmp_path) -> Path:
    return tmp_path / PROJECT_DIR / CONFIG_NAME


def make_app(tmp_path, **kwargs) -> CrossbarApp:
    return CrossbarApp(
        roster=kwargs.pop("roster", ROSTER),
        tests=kwargs.pop("tests", [single_task_test()]),
        results_dir=str(kwargs.pop("results_dir", tmp_path / "runs")),
        judge=kwargs.pop("judge", judge()),
        agent_factory=solving_agent,
        config_path=str(kwargs.pop("config_path", config_path(tmp_path))),
        **kwargs,
    )


def panel_of(app) -> SetupPanel:
    return app.query_one(SetupPanel)


def text_of(app, selector) -> str:
    return str(app.query_one(selector, Static).content)


def set_value(app, selector, value) -> None:
    app.query_one(selector, Input).value = value


async def finish_sweep(app, pilot, timeout_s=30.0) -> None:
    """Wait for the worker thread to report the sweep is over."""
    waited = 0.0
    while waited < timeout_s:
        if app.sweep_done:
            await pilot.pause()
            return
        await pilot.pause(0.02)
        waited += 0.02
    raise AssertionError(f"the sweep did not finish: error={app.run_error!r}")


async def press_button(app, pilot, selector) -> None:
    app.query_one(selector, Button).press()
    await pilot.pause()


def sound_draft() -> ConfigDraft:
    """A draft with nothing wrong with it, to start the editing tests from."""
    return ConfigDraft(
        models=[
            ModelDraft(id="local", kind="openai", model="qwen",
                       base_url="http://localhost:11434/v1"),
            ModelDraft(id="frontier", kind="anthropic", model="big",
                       api_key_env="ANTHROPIC_API_KEY"),
        ],
        candidate="local",
        baseline="frontier",
        tests=[TEST_DIR],
    )


async def with_draft(app, pilot, draft: ConfigDraft) -> SetupPanel:
    """Open Setup on a ready-made draft, as though it had been loaded."""
    panel = panel_of(app)
    panel.load(draft)
    await pilot.press("s")
    await pilot.pause()
    return panel


# -- reaching the screen ---------------------------------------------------


class TestReachingSetup:
    async def test_pressing_s_opens_the_setup_tab(self, tmp_path):
        app = make_app(tmp_path)
        async with app.run_test() as pilot:
            await pilot.press("s")
            await pilot.pause()
            assert app.query_one(TabbedContent).active == "setup"

    async def test_the_command_palette_finds_and_opens_setup(self, tmp_path):
        app = make_app(tmp_path)
        async with app.run_test() as pilot:
            provider = SetupCommands(app.screen)
            hits = [hit async for hit in provider.search("setup")]
            assert hits, "typing 'setup' into the command palette found nothing"
            hits[0].command()
            await pilot.pause()
            assert app.query_one(TabbedContent).active == "setup"

    def test_the_provider_is_registered_with_the_app(self):
        assert SetupCommands in CrossbarApp.COMMANDS


# -- where it writes, and what it starts from ------------------------------


class TestLoadingAProject:
    async def test_a_fresh_project_starts_with_nothing_connected(self, tmp_path):
        app = make_app(tmp_path)
        async with app.run_test():
            assert panel_of(app).draft.models == []

    async def test_an_existing_config_pre_populates_the_form(self, tmp_path):
        write_config(sound_draft(), config_path(tmp_path))
        app = make_app(tmp_path)
        async with app.run_test():
            draft = panel_of(app).draft
            assert [m.id for m in draft.models] == ["local", "frontier"]
            assert draft.candidate == "local"
            assert draft.baseline == "frontier"

    async def test_the_run_settings_are_pre_populated_too(self, tmp_path):
        loaded = replace(sound_draft(), judge_tests=3, results_dir="somewhere-else")
        write_config(loaded, config_path(tmp_path))
        app = make_app(tmp_path)
        async with app.run_test():
            assert app.query_one("#setup-judge-tests", Input).value == "3"
            assert app.query_one("#setup-results-dir", Input).value == "somewhere-else"

    async def test_the_target_file_is_named_on_screen(self, tmp_path):
        app = make_app(tmp_path)
        async with app.run_test():
            assert str(config_path(tmp_path)) in text_of(app, "#setup-target")

    def test_with_no_path_given_it_finds_the_project_it_is_standing_in(
        self, tmp_path, monkeypatch
    ):
        write_config(sound_draft(), config_path(tmp_path))
        tests = [single_task_test()]  # loaded before the chdir: FIXTURE is relative
        monkeypatch.chdir(tmp_path)
        app = CrossbarApp(
            roster=ROSTER, tests=tests, results_dir=str(tmp_path),
            judge=judge(), agent_factory=solving_agent,
        )
        assert app.config_path == config_path(tmp_path)


# -- connecting a model ----------------------------------------------------


class TestConnectingFromAPreset:
    async def test_a_preset_leaves_only_the_model_name_to_type(self, tmp_path):
        app = make_app(tmp_path)
        async with app.run_test() as pilot:
            await pilot.press("s")
            app.query_one("#setup-preset", Select).value = "ollama"
            set_value(app, "#setup-new-id", "local")
            set_value(app, "#setup-new-model", "qwen3:8b")
            await press_button(app, pilot, "#setup-connect")

            connected = panel_of(app).draft.model("local")
            assert connected.kind == PRESETS["ollama"].kind
            assert connected.base_url == PRESETS["ollama"].base_url
            assert connected.model == "qwen3:8b"

    async def test_the_presets_hint_is_shown(self, tmp_path):
        app = make_app(tmp_path)
        async with app.run_test() as pilot:
            await pilot.press("s")
            app.query_one("#setup-preset", Select).value = "claude-code"
            await pilot.pause()
            assert PRESETS["claude-code"].hint in text_of(app, "#setup-preset-hint")

    async def test_a_preset_with_no_hint_shows_none(self, tmp_path):
        app = make_app(tmp_path)
        async with app.run_test() as pilot:
            await pilot.press("s")
            app.query_one("#setup-preset", Select).value = "openrouter"
            await pilot.pause()
            assert text_of(app, "#setup-preset-hint").strip() == ""

    async def test_a_connected_model_is_listed(self, tmp_path):
        app = make_app(tmp_path)
        async with app.run_test() as pilot:
            await pilot.press("s")
            app.query_one("#setup-preset", Select).value = "anthropic"
            set_value(app, "#setup-new-id", "frontier")
            set_value(app, "#setup-new-model", "claude-opus-5")
            await press_button(app, pilot, "#setup-connect")

            listing = app.query_one("#setup-models", ListView)
            assert len(listing) == 1
            assert "frontier" in str(listing.children[0].query_one(Static).content)

    async def test_an_id_already_in_use_is_refused_rather_than_replacing_it(self, tmp_path):
        """Silently overwriting a connection would take its prices with it."""
        app = make_app(tmp_path)
        async with app.run_test() as pilot:
            await with_draft(app, pilot, sound_draft())
            app.query_one("#setup-preset", Select).value = "ollama"
            set_value(app, "#setup-new-id", "frontier")
            set_value(app, "#setup-new-model", "qwen")
            await press_button(app, pilot, "#setup-connect")

            still_there = panel_of(app).draft.model("frontier")
            assert still_there.model == "big"
            assert len(panel_of(app).draft.models) == 2
            assert "already" in text_of(app, "#setup-status").lower()

    async def test_connecting_without_an_id_says_so_instead_of_adding(self, tmp_path):
        app = make_app(tmp_path)
        async with app.run_test() as pilot:
            await pilot.press("s")
            set_value(app, "#setup-new-model", "qwen")
            await press_button(app, pilot, "#setup-connect")
            assert panel_of(app).draft.models == []
            assert "id" in text_of(app, "#setup-status").lower()


# -- editing and removing --------------------------------------------------


class TestEditingAModel:
    async def test_selecting_a_model_loads_its_fields(self, tmp_path):
        app = make_app(tmp_path)
        async with app.run_test() as pilot:
            await with_draft(app, pilot, sound_draft())
            app.query_one("#setup-models", ListView).index = 0
            await pilot.pause()
            assert app.query_one("#setup-field-id", Input).value == "local"
            assert app.query_one("#setup-field-model", Input).value == "qwen"
            assert app.query_one("#setup-field-base-url", Input).value == (
                "http://localhost:11434/v1"
            )

    async def test_applying_a_change_updates_the_draft(self, tmp_path):
        app = make_app(tmp_path)
        async with app.run_test() as pilot:
            await with_draft(app, pilot, sound_draft())
            app.query_one("#setup-models", ListView).index = 0
            await pilot.pause()
            set_value(app, "#setup-field-base-url", "http://gpu-box:8000/v1")
            set_value(app, "#setup-field-api-key-env", "LOCAL_API_KEY")
            set_value(app, "#setup-field-input-price", "0.25")
            set_value(app, "#setup-field-output-price", "0.75")
            await press_button(app, pilot, "#setup-apply")

            edited = panel_of(app).draft.model("local")
            assert edited.base_url == "http://gpu-box:8000/v1"
            assert edited.api_key_env == "LOCAL_API_KEY"
            assert edited.input_price == 0.25
            assert edited.output_price == 0.75

    async def test_the_command_field_is_offered_only_for_an_agent_cli(self, tmp_path):
        draft = ConfigDraft(
            models=[
                ModelDraft(id="claude-code", kind="claude-cli", model="opus"),
                ModelDraft(id="frontier", kind="anthropic", model="big"),
            ],
            candidate="claude-code",
            baseline="frontier",
            tests=[TEST_DIR],
        )
        app = make_app(tmp_path)
        async with app.run_test() as pilot:
            await with_draft(app, pilot, draft)
            app.query_one("#setup-models", ListView).index = 0
            await pilot.pause()
            assert app.query_one("#setup-field-command", Input).display is True
            app.query_one("#setup-models", ListView).index = 1
            await pilot.pause()
            assert app.query_one("#setup-field-command", Input).display is False

    async def test_renaming_a_model_carries_its_role_across(self, tmp_path):
        app = make_app(tmp_path)
        async with app.run_test() as pilot:
            await with_draft(app, pilot, sound_draft())
            app.query_one("#setup-models", ListView).index = 0
            await pilot.pause()
            set_value(app, "#setup-field-id", "local-gpu")
            await press_button(app, pilot, "#setup-apply")

            draft = panel_of(app).draft
            assert [m.id for m in draft.models] == ["local-gpu", "frontier"]
            assert draft.candidate == "local-gpu"

    async def test_renaming_onto_another_models_id_is_refused(self, tmp_path):
        """Two connections merged into one is data loss with no error."""
        app = make_app(tmp_path)
        async with app.run_test() as pilot:
            await with_draft(app, pilot, sound_draft())
            app.query_one("#setup-models", ListView).index = 0
            await pilot.pause()
            set_value(app, "#setup-field-id", "frontier")
            await press_button(app, pilot, "#setup-apply")

            draft = panel_of(app).draft
            assert [m.id for m in draft.models] == ["local", "frontier"]
            assert draft.model("frontier").model == "big"
            assert "already" in text_of(app, "#setup-status").lower()

    async def test_an_unreadable_price_is_refused_rather_than_zeroed(self, tmp_path):
        """A price quietly read as zero understates the bill the run reports."""
        app = make_app(tmp_path)
        async with app.run_test() as pilot:
            await with_draft(app, pilot, sound_draft())
            app.query_one("#setup-models", ListView).index = 1
            await pilot.pause()
            set_value(app, "#setup-field-input-price", "fifteen")
            await press_button(app, pilot, "#setup-apply")

            assert panel_of(app).draft.model("frontier").input_price == 0.0
            assert panel_of(app).draft.model("frontier").api_key_env == "ANTHROPIC_API_KEY"
            assert "price" in text_of(app, "#setup-status").lower()

    async def test_removing_a_model_clears_the_role_it_held(self, tmp_path):
        app = make_app(tmp_path)
        async with app.run_test() as pilot:
            await with_draft(app, pilot, sound_draft())
            app.query_one("#setup-models", ListView).index = 1
            await pilot.pause()
            await press_button(app, pilot, "#setup-remove")

            draft = panel_of(app).draft
            assert [m.id for m in draft.models] == ["local"]
            assert draft.baseline is None

    async def test_highlighting_a_model_leaves_the_results_panel_alone(self, tmp_path):
        """Both lists live in the same app, and ListView.Highlighted bubbles.

        A run has to have happened first, or the results panel is empty either
        way and the test proves nothing."""
        app = make_app(tmp_path)
        async with app.run_test() as pilot:
            await pilot.press("r")
            await finish_sweep(app, pilot)
            await with_draft(app, pilot, sound_draft())
            before = text_of(app, "#results-detail")
            assert "graded" in before
            app.query_one("#setup-models", ListView).index = 1
            await pilot.pause()
            assert text_of(app, "#results-detail") == before


# -- roles -----------------------------------------------------------------


class TestRoles:
    async def test_only_connected_models_are_offered(self, tmp_path):
        app = make_app(tmp_path)
        async with app.run_test() as pilot:
            await with_draft(app, pilot, sound_draft())
            candidate = app.query_one("#setup-candidate", Select)
            offered = {v for v in candidate._legal_values if isinstance(v, str)}
            assert offered == {"local", "frontier"}

    async def test_choosing_a_role_updates_the_draft(self, tmp_path):
        app = make_app(tmp_path)
        async with app.run_test() as pilot:
            await with_draft(app, pilot, sound_draft())
            app.query_one("#setup-judge", Select).value = "frontier"
            await pilot.pause()
            assert panel_of(app).draft.judge == "frontier"

    async def test_a_removed_model_is_no_longer_offered(self, tmp_path):
        app = make_app(tmp_path)
        async with app.run_test() as pilot:
            await with_draft(app, pilot, sound_draft())
            app.query_one("#setup-models", ListView).index = 1
            await pilot.pause()
            await press_button(app, pilot, "#setup-remove")
            baseline = app.query_one("#setup-baseline", Select)
            offered = {v for v in baseline._legal_values if isinstance(v, str)}
            assert offered == {"local"}

    async def test_dropping_the_baseline_demands_a_judge_in_the_projects_words(
        self, tmp_path
    ):
        app = make_app(tmp_path)
        async with app.run_test() as pilot:
            panel = await with_draft(app, pilot, sound_draft())
            app.query_one("#setup-baseline", Select).value = Select.NULL
            await pilot.pause()
            problems = validate_draft(panel.current_draft())
            assert problems
            shown = text_of(app, "#setup-problems")
            assert all(problem in shown for problem in problems)


# -- validation ------------------------------------------------------------


class TestValidation:
    async def test_the_problems_are_shown_exactly_as_the_backend_phrases_them(
        self, tmp_path
    ):
        app = make_app(tmp_path)
        async with app.run_test() as pilot:
            await pilot.press("s")
            await pilot.pause()
            shown = text_of(app, "#setup-problems")
            assert "Connect at least one model." in shown
            assert "Choose a candidate: the model you want to assess." in shown

    async def test_a_model_judging_itself_is_refused_in_the_backends_words(self, tmp_path):
        draft = ConfigDraft(
            models=[ModelDraft(id="solo", kind="anthropic", model="big")],
            candidate="solo",
            judge="solo",
            tests=[TEST_DIR],
        )
        app = make_app(tmp_path)
        async with app.run_test() as pilot:
            panel = await with_draft(app, pilot, draft)
            shown = text_of(app, "#setup-problems")
            for problem in validate_draft(panel.current_draft()):
                assert problem in shown
            assert "cannot judge itself" in shown

    async def test_saving_is_refused_while_a_problem_remains(self, tmp_path):
        app = make_app(tmp_path)
        async with app.run_test() as pilot:
            await pilot.press("s")
            await press_button(app, pilot, "#setup-save")
            assert not config_path(tmp_path).exists()
            # The screen's own refusal, not write_config's exception leaking
            # through: the problems are already on screen to be read.
            assert "fix the problems listed above" in text_of(app, "#setup-status")
            assert "Connect at least one model." in text_of(app, "#setup-problems")

    async def test_the_problems_clear_once_they_are_fixed(self, tmp_path):
        app = make_app(tmp_path)
        async with app.run_test() as pilot:
            await pilot.press("s")
            assert text_of(app, "#setup-problems").strip()
            await with_draft(app, pilot, sound_draft())
            assert text_of(app, "#setup-problems").strip() == ""


# -- overwriting -----------------------------------------------------------


class TestOverwriting:
    async def test_an_existing_file_is_flagged_as_rewritten_whole(self, tmp_path):
        write_config(sound_draft(), config_path(tmp_path))
        app = make_app(tmp_path)
        async with app.run_test() as pilot:
            await pilot.press("s")
            await pilot.pause()
            warning = text_of(app, "#setup-warning").lower()
            assert ".bak" in warning
            assert "comment" in warning

    async def test_a_fresh_project_is_not_warned_about_overwriting(self, tmp_path):
        app = make_app(tmp_path)
        async with app.run_test() as pilot:
            await pilot.press("s")
            await pilot.pause()
            assert ".bak" not in text_of(app, "#setup-warning")

    async def test_saving_over_a_config_keeps_the_old_one_as_a_backup(self, tmp_path):
        config_path(tmp_path).parent.mkdir(parents=True)
        config_path(tmp_path).write_text("# hand-written\nmodels: []\n")
        app = make_app(tmp_path)
        async with app.run_test() as pilot:
            await with_draft(app, pilot, sound_draft())
            await press_button(app, pilot, "#setup-save")
            backup = config_path(tmp_path).with_suffix(".yaml.bak")
            assert backup.exists()
            assert "# hand-written" in backup.read_text()


# -- a run in progress -----------------------------------------------------


class TestSweepGuard:
    async def test_saving_mid_sweep_is_refused(self, tmp_path):
        app = make_app(tmp_path)
        async with app.run_test() as pilot:
            await with_draft(app, pilot, sound_draft())
            app.sweep_running = True
            await press_button(app, pilot, "#setup-save")
            assert not config_path(tmp_path).exists()
            assert "run is in progress" in text_of(app, "#setup-status").lower()

    async def test_saving_works_once_the_sweep_is_over(self, tmp_path):
        app = make_app(tmp_path)
        async with app.run_test() as pilot:
            await with_draft(app, pilot, sound_draft())
            app.sweep_running = True
            await press_button(app, pilot, "#setup-save")
            app.sweep_running = False
            await press_button(app, pilot, "#setup-save")
            assert config_path(tmp_path).exists()


# -- saving ----------------------------------------------------------------


class TestSaving:
    async def test_what_is_written_loads_as_a_project(self, tmp_path):
        app = make_app(tmp_path)
        async with app.run_test() as pilot:
            await with_draft(app, pilot, sound_draft())
            set_value(app, "#setup-results-dir", "elsewhere")
            set_value(app, "#setup-judge-tests", "2")
            await press_button(app, pilot, "#setup-save")

        project = load_project(tmp_path)
        assert [m.id for m in project.roster.models] == ["local", "frontier"]
        assert project.roster.roles["candidate"] == "local"
        assert project.results_dir == tmp_path / PROJECT_DIR / "elsewhere"
        assert project.judge_tests == 2

    async def test_a_model_connected_on_screen_survives_the_round_trip(self, tmp_path):
        app = make_app(tmp_path)
        async with app.run_test() as pilot:
            panel = await with_draft(app, pilot, sound_draft())
            app.query_one("#setup-preset", Select).value = "ollama"
            set_value(app, "#setup-new-id", "spare")
            set_value(app, "#setup-new-model", "qwen3:32b")
            await press_button(app, pilot, "#setup-connect")
            await press_button(app, pilot, "#setup-save")
            written = draft_from_config(config_path(tmp_path))
            assert [m.id for m in written.models] == [m.id for m in panel.draft.models]
            assert written.model("spare").base_url == PRESETS["ollama"].base_url

    async def test_editing_a_loaded_project_does_not_clobber_the_rest_of_it(self, tmp_path):
        write_config(sound_draft(), config_path(tmp_path))
        app = make_app(tmp_path)
        async with app.run_test() as pilot:
            await pilot.press("s")
            await pilot.pause()
            app.query_one("#setup-models", ListView).index = 0
            await pilot.pause()
            set_value(app, "#setup-field-model", "qwen3:8b")
            await press_button(app, pilot, "#setup-apply")
            await press_button(app, pilot, "#setup-save")

        written = draft_from_config(config_path(tmp_path))
        assert written.model("local").model == "qwen3:8b"
        assert written.model("frontier").api_key_env == "ANTHROPIC_API_KEY"
        assert written.tests == [TEST_DIR]

    async def test_the_saved_path_is_reported(self, tmp_path):
        app = make_app(tmp_path)
        async with app.run_test() as pilot:
            await with_draft(app, pilot, sound_draft())
            await press_button(app, pilot, "#setup-save")
            assert str(config_path(tmp_path)) in text_of(app, "#setup-status")


# -- markup ----------------------------------------------------------------


class TestMarkup:
    """Anything a person typed can contain brackets, and a bare '[/]' raises
    MarkupError while the panel is painting. ``Static.content`` hands back the
    raw string whether markup is on or off, so only a real render catches it."""

    async def test_every_free_text_panel_has_markup_turned_off(self, tmp_path):
        app = make_app(tmp_path)
        async with app.run_test():
            panels = [app.query_one(f"#{name}", Static) for name in FREE_TEXT_PANELS]
            assert [w.id for w in panels if w._render_markup] == []

    async def test_brackets_in_a_connected_models_fields_still_paint(self, tmp_path):
        """The tab has to be opened: an unrendered pane hides the bug."""
        bracketed = "http://localhost:8000/v1?tag=[/]"
        app = make_app(tmp_path)
        async with app.run_test() as pilot:
            await pilot.press("s")
            app.query_one("#setup-preset", Select).value = "custom"
            set_value(app, "#setup-new-id", "odd[/]name")
            set_value(app, "#setup-new-model", "qwen")
            await press_button(app, pilot, "#setup-connect")
            app.query_one("#setup-models", ListView).index = 0
            await pilot.pause()
            set_value(app, "#setup-field-base-url", bracketed)
            await press_button(app, pilot, "#setup-apply")
            await pilot.pause()
            app.export_screenshot()
            assert bracketed in str(panel_of(app).draft.model("odd[/]name").base_url)

    async def test_brackets_in_a_problem_still_paint(self, tmp_path):
        app = make_app(tmp_path)
        async with app.run_test() as pilot:
            draft = ConfigDraft(
                models=[ModelDraft(id="odd[/]id", kind="openai", model="q")],
                candidate="missing[/]model",
                tests=[TEST_DIR],
            )
            await with_draft(app, pilot, draft)
            app.export_screenshot()
            assert "odd[/]id" in text_of(app, "#setup-problems")
