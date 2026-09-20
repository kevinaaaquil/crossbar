"""Building a config without hand-editing YAML."""
import pytest
import yaml

from crossbar.configwriter import (
    PRESETS,
    ConfigDraft,
    ModelDraft,
    draft_from_config,
    preset,
    render_config,
    validate_draft,
    write_config,
)
from crossbar.project import CONFIG_NAME, PROJECT_DIR, load_project


def a_draft(**overrides) -> ConfigDraft:
    data = {
        "models": [
            ModelDraft(id="kimi", kind="openai", model="kimi-k2-0905-preview",
                       base_url="https://api.moonshot.ai/v1",
                       api_key_env="MOONSHOT_API_KEY",
                       input_price=0.6, output_price=2.5),
            ModelDraft(id="opus", kind="anthropic", model="claude-opus-5",
                       api_key_env="ANTHROPIC_API_KEY",
                       input_price=15.0, output_price=75.0),
        ],
        "candidate": "kimi",
        "baseline": "opus",
        "tests": ["tests/support-triage"],
    }
    data.update(overrides)
    return ConfigDraft(**data)


class TestRendering:
    def test_it_renders_valid_yaml(self):
        assert isinstance(yaml.safe_load(render_config(a_draft())), dict)

    def test_the_models_are_there(self):
        data = yaml.safe_load(render_config(a_draft()))
        assert [m["id"] for m in data["models"]] == ["kimi", "opus"]

    def test_an_openai_model_carries_its_endpoint(self):
        data = yaml.safe_load(render_config(a_draft()))
        model = next(m for m in data["models"] if m["id"] == "kimi")
        assert model["provider"] == "openai"
        assert model["base_url"] == "https://api.moonshot.ai/v1"

    def test_an_anthropic_model_has_no_base_url(self):
        data = yaml.safe_load(render_config(a_draft()))
        model = next(m for m in data["models"] if m["id"] == "opus")
        assert "base_url" not in model

    def test_prices_are_written(self):
        data = yaml.safe_load(render_config(a_draft()))
        model = next(m for m in data["models"] if m["id"] == "kimi")
        assert model["price"] == {"input_per_mtok": 0.6, "output_per_mtok": 2.5}

    def test_roles_are_written(self):
        data = yaml.safe_load(render_config(a_draft()))
        assert data["roles"]["candidate"] == "kimi"
        assert data["roles"]["baseline"] == "opus"

    def test_a_single_model_draft_omits_the_baseline(self):
        draft = a_draft(baseline=None, judge="opus")
        assert "baseline" not in yaml.safe_load(render_config(draft))["roles"]

    def test_an_agent_cli_model_writes_its_command(self):
        draft = a_draft(models=[
            ModelDraft(id="cc", kind="claude-cli", model="opus", command="claude"),
            ModelDraft(id="kimi", kind="openai", model="k", base_url="http://x/v1"),
        ], candidate="kimi", baseline="cc")
        model = next(m for m in yaml.safe_load(render_config(draft))["models"] if m["id"] == "cc")
        assert model["provider"] == "claude-cli"
        assert model["command"] == "claude"
        assert "base_url" not in model

    def test_run_settings_are_written(self):
        data = yaml.safe_load(render_config(a_draft(judge_tests=2, results_dir="out")))
        assert data["run"]["judge_tests"] == 2
        assert data["run"]["results_dir"] == "out"

    def test_the_output_is_commented_for_a_human(self):
        text = render_config(a_draft())
        assert text.count("#") > 5

    def test_it_explains_that_provider_means_format_not_vendor(self):
        """The single most confusing field in the file."""
        assert "format" in render_config(a_draft()).lower()

    def test_optional_model_settings_are_only_written_when_set(self):
        draft = a_draft(models=[ModelDraft(id="k", kind="openai", model="k",
                                           base_url="http://x/v1", max_tokens=8192,
                                           temperature=0.3)],
                        candidate="k", baseline=None, judge=None)
        model = yaml.safe_load(render_config(draft))["models"][0]
        assert model["max_tokens"] == 8192
        assert model["temperature"] == 0.3

    def test_unset_optional_settings_are_absent(self):
        model = yaml.safe_load(render_config(a_draft()))["models"][0]
        assert "temperature" not in model


class TestValidation:
    def test_a_sound_draft_has_no_problems(self):
        assert validate_draft(a_draft()) == []

    def test_a_missing_candidate_is_a_problem(self):
        assert any("candidate" in p for p in validate_draft(a_draft(candidate="")))

    def test_a_candidate_that_is_not_a_model_is_a_problem(self):
        assert any("ghost" in p for p in validate_draft(a_draft(candidate="ghost")))

    def test_duplicate_ids_are_a_problem(self):
        models = [ModelDraft(id="x", kind="anthropic", model="a"),
                  ModelDraft(id="x", kind="anthropic", model="b")]
        assert any("duplicate" in p.lower() for p in validate_draft(
            a_draft(models=models, candidate="x", baseline=None, judge="x")))

    def test_an_openai_model_without_a_base_url_is_a_problem(self):
        models = [ModelDraft(id="x", kind="openai", model="a")]
        assert any("base_url" in p for p in validate_draft(
            a_draft(models=models, candidate="x", baseline=None, judge="x")))

    def test_a_single_model_draft_needs_a_judge(self):
        assert any("judge" in p for p in validate_draft(a_draft(baseline=None, judge=None)))

    def test_a_candidate_may_not_judge_itself(self):
        problems = validate_draft(a_draft(baseline=None, judge="kimi"))
        assert any("itself" in p or "own" in p for p in problems)

    def test_no_tests_is_a_problem(self):
        assert any("test" in p.lower() for p in validate_draft(a_draft(tests=[])))

    def test_problems_are_phrased_for_a_person(self):
        for problem in validate_draft(a_draft(candidate="")):
            assert problem[0].isupper() or problem.startswith("'")


class TestWriting:
    def test_what_it_writes_loads_as_a_project(self, tmp_path):
        from crossbar.demo.scripted import example_test_path
        import shutil

        root = tmp_path / PROJECT_DIR
        (root / "tests").mkdir(parents=True)
        shutil.copytree(example_test_path(), root / "tests" / "support-triage")

        write_config(a_draft(), root / CONFIG_NAME)
        loaded = load_project(tmp_path)
        assert [m.id for m in loaded.roster.models] == ["kimi", "opus"]
        assert loaded.roster.roles["candidate"] == "kimi"
        assert loaded.roster.roles["baseline"] == "opus"
        assert loaded.tests[0].name == "Support triage"

    def test_it_refuses_to_write_an_invalid_draft(self, tmp_path):
        with pytest.raises(ValueError, match="candidate"):
            write_config(a_draft(candidate=""), tmp_path / "config.yaml")

    def test_it_backs_up_what_it_replaces(self, tmp_path):
        target = tmp_path / "config.yaml"
        target.write_text("# my own notes\nmodels: []\n")
        write_config(a_draft(), target)
        assert (tmp_path / "config.yaml.bak").read_text().startswith("# my own notes")

    def test_it_does_not_back_up_when_there_was_nothing_there(self, tmp_path):
        write_config(a_draft(), tmp_path / "config.yaml")
        assert not (tmp_path / "config.yaml.bak").exists()

    def test_it_creates_parent_directories(self, tmp_path):
        write_config(a_draft(), tmp_path / "a" / "b" / "config.yaml")
        assert (tmp_path / "a" / "b" / "config.yaml").exists()


class TestRoundTrip:
    def test_an_existing_config_can_be_loaded_for_editing(self, tmp_path):
        target = tmp_path / "config.yaml"
        write_config(a_draft(judge_tests=3), target)
        draft = draft_from_config(target)
        assert [m.id for m in draft.models] == ["kimi", "opus"]
        assert draft.candidate == "kimi"
        assert draft.judge_tests == 3

    def test_model_details_survive_the_round_trip(self, tmp_path):
        target = tmp_path / "config.yaml"
        write_config(a_draft(), target)
        model = next(m for m in draft_from_config(target).models if m.id == "kimi")
        assert model.kind == "openai"
        assert model.base_url == "https://api.moonshot.ai/v1"
        assert model.api_key_env == "MOONSHOT_API_KEY"
        assert model.input_price == 0.6

    def test_rewriting_an_unchanged_draft_changes_nothing(self, tmp_path):
        target = tmp_path / "config.yaml"
        write_config(a_draft(), target)
        first = target.read_text()
        write_config(draft_from_config(target), target)
        assert target.read_text() == first


class TestPresets:
    def test_there_are_presets_for_the_common_endpoints(self):
        assert {"ollama", "vllm", "anthropic", "claude-code"} <= set(PRESETS)

    def test_a_preset_fills_in_the_fiddly_parts(self):
        drafted = preset("ollama", model_id="local", model="llama3.3")
        assert drafted.kind == "openai"
        assert "11434" in drafted.base_url

    def test_the_anthropic_preset_needs_no_base_url(self):
        assert preset("anthropic", model_id="opus", model="claude-opus-5").base_url == ""

    def test_the_claude_code_preset_is_an_agent_cli(self):
        drafted = preset("claude-code", model_id="cc", model="opus")
        assert drafted.kind == "claude-cli"
        assert drafted.command == "claude"

    def test_every_known_endpoint_preset_is_complete(self):
        """Pick one of these and the only thing left to type is the model name."""
        for name in PRESETS:
            if name == "custom":
                continue
            drafted = preset(name, model_id="m", model="some-model")
            draft = a_draft(models=[drafted], candidate="m", baseline=None, judge=None)
            problems = [p for p in validate_draft(draft) if "judge" not in p]
            assert problems == [], f"{name}: {problems}"

    def test_the_custom_preset_asks_for_the_endpoint(self):
        """It cannot know the URL, so it must say so rather than look complete."""
        drafted = preset("custom", model_id="m", model="some-model")
        draft = a_draft(models=[drafted], candidate="m", baseline=None, judge=None)
        assert any("base_url" in p for p in validate_draft(draft))

    def test_an_unknown_preset_raises(self):
        with pytest.raises(KeyError):
            preset("telepathy", model_id="m", model="m")

    def test_presets_carry_a_human_label(self):
        assert all(p.label for p in PRESETS.values())


class TestOptionalNumericSettings:
    """repeats, max_tokens and temperature are plain numbers. The only subtlety
    is that unset and zero are different things."""

    def test_repeats_is_written_when_set(self):
        assert yaml.safe_load(render_config(a_draft(repeats=5)))["run"]["repeats"] == 5

    def test_repeats_is_absent_when_unset(self):
        assert "repeats" not in yaml.safe_load(render_config(a_draft()))["run"]

    def test_a_zero_temperature_is_written_not_dropped(self):
        """Temperature 0 means deterministic sampling. Treating it as 'unset'
        would silently change how the model is run."""
        draft = a_draft(models=[ModelDraft(id="k", kind="openai", model="k",
                                           base_url="http://x/v1", temperature=0.0)],
                        candidate="k", baseline=None, judge=None)
        assert yaml.safe_load(render_config(draft))["models"][0]["temperature"] == 0.0

    def test_a_zero_max_tokens_is_rejected_rather_than_written(self):
        draft = a_draft(models=[ModelDraft(id="k", kind="openai", model="k",
                                           base_url="http://x/v1", max_tokens=0)],
                        candidate="k", baseline=None, judge=None)
        assert any("max_tokens" in p for p in validate_draft(draft))

    def test_negative_repeats_are_rejected(self):
        assert any("repeats" in p.lower() for p in validate_draft(a_draft(repeats=0)))

    def test_a_temperature_outside_the_usable_range_is_rejected(self):
        draft = a_draft(models=[ModelDraft(id="k", kind="openai", model="k",
                                           base_url="http://x/v1", temperature=9.0)],
                        candidate="k", baseline=None, judge=None)
        assert any("temperature" in p for p in validate_draft(draft))

    def test_these_survive_a_round_trip(self, tmp_path):
        target = tmp_path / "config.yaml"
        draft = a_draft(
            repeats=4,
            models=[ModelDraft(id="k", kind="openai", model="k", base_url="http://x/v1",
                               max_tokens=8192, temperature=0.3)],
            candidate="k", baseline=None, judge="k2",
        )
        draft = ConfigDraft(**{**draft.__dict__, "judge": None, "baseline": "k"})
        draft = a_draft(repeats=4, models=[
            ModelDraft(id="k", kind="openai", model="k", base_url="http://x/v1",
                       max_tokens=8192, temperature=0.3),
            ModelDraft(id="b", kind="anthropic", model="b"),
        ], candidate="k", baseline="b")
        write_config(draft, target)
        back = draft_from_config(target)
        assert back.repeats == 4
        model = back.model("k")
        assert model.max_tokens == 8192
        assert model.temperature == 0.3
