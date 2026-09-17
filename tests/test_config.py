"""Roster config: models, harnesses, the matrix they imply, and cost."""
import textwrap

import pytest

from crossbar.config import (
    AgentSpec,
    ConfigError,
    ModelSpec,
    Price,
    build_harness,
    build_provider,
    cost_usd,
    load_config,
    parse_config,
)
from crossbar.harness import ClaudeCodeHarness, ReactHarness, SingleShotHarness
from crossbar.providers import AnthropicProvider, OpenAICompatProvider, Usage

BASE = {
    "models": [
        {
            "id": "qwen-local",
            "provider": "openai",
            "model": "qwen3.6-plus",
            "base_url": "http://localhost:8000/v1",
            "price": {"input_per_mtok": 0.2, "output_per_mtok": 0.6},
        },
        {
            "id": "opus",
            "provider": "anthropic",
            "model": "claude-opus-5",
            "api_key_env": "ANTHROPIC_API_KEY",
            "price": {"input_per_mtok": 15, "output_per_mtok": 75},
        },
    ],
    "harnesses": [
        {"id": "react", "kind": "react"},
        {"id": "react-plus-verify", "kind": "react", "verify": True},
    ],
}


def config(**overrides):
    data = {**BASE, **overrides}
    return parse_config(data, source="<test>")


class TestModels:
    def test_models_are_parsed(self):
        cfg = config()
        assert [m.id for m in cfg.models] == ["qwen-local", "opus"]

    def test_model_carries_its_endpoint_and_price(self):
        model = config().model("qwen-local")
        assert model.base_url == "http://localhost:8000/v1"
        assert model.price.input_per_mtok == 0.2

    def test_api_key_is_read_from_the_named_environment_variable(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-xyz")
        assert config().model("opus").api_key() == "sk-ant-xyz"

    def test_a_missing_api_key_reads_as_empty_not_an_error(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        assert config().model("opus").api_key() == ""

    def test_an_inline_api_key_is_supported_for_quick_starts(self):
        cfg = config(
            models=[
                {
                    "id": "m",
                    "provider": "openai",
                    "model": "x",
                    "base_url": "http://localhost:1/v1",
                    "api_key": "sk-inline",
                }
            ]
        )
        assert cfg.model("m").api_key() == "sk-inline"

    def test_unknown_model_id_raises(self):
        with pytest.raises(ConfigError, match="ghost"):
            config().model("ghost")

    def test_duplicate_model_ids_are_rejected(self):
        with pytest.raises(ConfigError, match="duplicate"):
            config(models=[BASE["models"][0], BASE["models"][0]])

    def test_unknown_provider_is_rejected(self):
        with pytest.raises(ConfigError, match="telepathy"):
            config(models=[{"id": "m", "provider": "telepathy", "model": "x"}])

    def test_openai_provider_requires_a_base_url(self):
        with pytest.raises(ConfigError, match="base_url"):
            config(models=[{"id": "m", "provider": "openai", "model": "x"}])

    def test_missing_price_defaults_to_zero(self):
        cfg = config(models=[{"id": "m", "provider": "anthropic", "model": "x"}])
        assert cfg.model("m").price == Price(0.0, 0.0)


class TestHarnesses:
    def test_harnesses_are_parsed(self):
        assert [h.id for h in config().harnesses] == ["react", "react-plus-verify"]

    def test_unknown_harness_kind_is_rejected(self):
        with pytest.raises(ConfigError, match="autogen"):
            config(harnesses=[{"id": "h", "kind": "autogen"}])

    def test_defaults_are_used_when_no_harnesses_are_configured(self):
        cfg = config(harnesses=[])
        assert "react" in [h.id for h in cfg.harnesses]

    def test_duplicate_harness_ids_are_rejected(self):
        with pytest.raises(ConfigError, match="duplicate"):
            config(harnesses=[{"id": "react", "kind": "react"}] * 2)


class TestMatrix:
    def test_the_default_matrix_is_the_full_cross_product(self):
        cells = config().agents
        assert len(cells) == 4
        assert AgentSpec(model_id="qwen-local", harness_id="react") in cells

    def test_agent_ids_are_readable_and_unique(self):
        ids = [a.id for a in config().agents]
        assert "qwen-local+react" in ids
        assert len(set(ids)) == len(ids)

    def test_an_explicit_agent_list_overrides_the_cross_product(self):
        cfg = config(agents=[{"model": "opus", "harness": "react"}])
        assert [a.id for a in cfg.agents] == ["opus+react"]

    def test_an_agent_referencing_an_unknown_model_is_rejected(self):
        with pytest.raises(ConfigError, match="ghost"):
            config(agents=[{"model": "ghost", "harness": "react"}])

    def test_an_agent_referencing_an_unknown_harness_is_rejected(self):
        with pytest.raises(ConfigError, match="ghost"):
            config(agents=[{"model": "opus", "harness": "ghost"}])

    def test_claude_cli_models_pair_only_with_the_claude_code_harness(self):
        with pytest.raises(ConfigError, match="claude-code"):
            config(
                models=[{"id": "cc", "provider": "claude-cli", "model": "opus"}],
                harnesses=[{"id": "react", "kind": "react"}],
                agents=[{"model": "cc", "harness": "react"}],
            )

    def test_the_cross_product_skips_impossible_pairs(self):
        cfg = config(
            models=[
                {"id": "cc", "provider": "claude-cli", "model": "opus"},
                BASE["models"][0],
            ],
            harnesses=[{"id": "react", "kind": "react"}, {"id": "cc", "kind": "claude-code"}],
        )
        assert sorted(a.id for a in cfg.agents) == ["cc+cc", "qwen-local+react"]

    def test_a_baseline_can_be_named(self):
        cfg = config(run={"baseline": "opus+react"})
        assert cfg.run.baseline == "opus+react"

    def test_the_first_agent_is_the_baseline_by_default(self):
        assert config().run.baseline == "qwen-local+react"


class TestRunSettings:
    def test_defaults_are_sensible(self):
        run = config().run
        assert run.repeats == 3
        assert run.seed == 0
        assert run.concurrency >= 1

    def test_values_are_read_from_the_config(self):
        run = config(run={"repeats": 7, "seed": 42, "concurrency": 2}).run
        assert (run.repeats, run.seed, run.concurrency) == (7, 42, 2)

    def test_repeats_must_be_positive(self):
        with pytest.raises(ConfigError, match="repeats"):
            config(run={"repeats": 0})


class TestBuilders:
    def test_openai_models_build_an_openai_provider(self):
        provider = build_provider(config().model("qwen-local"))
        assert isinstance(provider, OpenAICompatProvider)
        assert provider.model == "qwen3.6-plus"

    def test_anthropic_models_build_an_anthropic_provider(self):
        assert isinstance(build_provider(config().model("opus")), AnthropicProvider)

    def test_claude_cli_models_have_no_standalone_provider(self):
        cfg = config(models=[{"id": "cc", "provider": "claude-cli", "model": "opus"}])
        assert build_provider(cfg.model("cc")) is None

    def test_react_harness_is_built_from_its_spec(self):
        cfg = config()
        harness = build_harness(cfg.harness("react-plus-verify"), cfg.model("qwen-local"))
        assert isinstance(harness, ReactHarness)
        assert harness.verify is True

    def test_single_shot_harness_is_built(self):
        cfg = config(harnesses=[{"id": "s", "kind": "single-shot"}])
        assert isinstance(build_harness(cfg.harness("s"), cfg.model("opus")), SingleShotHarness)

    def test_claude_code_harness_gets_the_model_name(self):
        cfg = config(
            models=[{"id": "cc", "provider": "claude-cli", "model": "opus"}],
            harnesses=[{"id": "cc", "kind": "claude-code"}],
        )
        harness = build_harness(cfg.harness("cc"), cfg.model("cc"))
        assert isinstance(harness, ClaudeCodeHarness)
        assert harness.model == "opus"


class TestCost:
    def test_cost_uses_the_per_million_token_price(self):
        price = Price(input_per_mtok=3.0, output_per_mtok=15.0)
        assert cost_usd(Usage(1_000_000, 1_000_000), price) == pytest.approx(18.0)

    def test_partial_millions_are_prorated(self):
        assert cost_usd(Usage(1000, 0), Price(3.0, 15.0)) == pytest.approx(0.003)

    def test_zero_price_means_zero_cost(self):
        assert cost_usd(Usage(10_000, 10_000), Price(0, 0)) == 0.0


class TestLoadFromDisk:
    def test_reads_a_yaml_file(self, tmp_path):
        path = tmp_path / "crossbar.yaml"
        path.write_text(
            textwrap.dedent(
                """
                models:
                  - id: local
                    provider: openai
                    model: qwen
                    base_url: http://localhost:8000/v1
                harnesses:
                  - id: react
                    kind: react
                run:
                  repeats: 4
                """
            )
        )
        cfg = load_config(path)
        assert cfg.model("local").model == "qwen"
        assert cfg.run.repeats == 4

    def test_a_missing_file_raises_config_error(self, tmp_path):
        with pytest.raises(ConfigError, match="not found"):
            load_config(tmp_path / "nope.yaml")

    def test_malformed_yaml_raises_config_error(self, tmp_path):
        path = tmp_path / "crossbar.yaml"
        path.write_text("models: [oops\n")
        with pytest.raises(ConfigError, match="crossbar.yaml"):
            load_config(path)

    def test_a_config_without_models_is_rejected(self, tmp_path):
        path = tmp_path / "crossbar.yaml"
        path.write_text("harnesses: []\n")
        with pytest.raises(ConfigError, match="model"):
            load_config(path)


class TestMockModels:
    """The built-in mock backend: a runnable demo with no keys and no network."""

    def mock_config(self, **model_overrides):
        model = {"id": "mock-strong", "provider": "mock", "model": "mock-strong", "skill": 0.9}
        model.update(model_overrides)
        return config(models=[model], harnesses=[{"id": "react", "kind": "react"}])

    def test_a_mock_model_needs_no_base_url_or_key(self):
        assert self.mock_config().model("mock-strong").api_key() == ""

    def test_the_skill_level_is_read_from_the_config(self):
        assert self.mock_config().model("mock-strong").skill == 0.9

    def test_skill_defaults_to_perfect(self):
        cfg = config(models=[{"id": "m", "provider": "mock", "model": "m"}])
        assert cfg.model("m").skill == 1.0

    def test_building_a_mock_provider_returns_a_mock(self):
        from crossbar.providers import MockProvider

        provider = build_provider(self.mock_config().model("mock-strong"))
        assert isinstance(provider, MockProvider)
        assert provider.skill == 0.9

    def test_the_task_and_seed_are_threaded_through(self):
        from crossbar.tasks import parse_task

        task = parse_task(
            {
                "id": "t",
                "prompt": "p",
                "environment": {"kind": "local", "servers": [{"name": "n", "command": "c"}]},
                "checks": [{"type": "final_text", "match": "contains", "value": "x"}],
            },
            source="<test>",
        )
        provider = build_provider(self.mock_config().model("mock-strong"), task=task, seed=11)
        assert provider.task is task
        assert provider.seed == 11

    def test_mock_models_pair_with_ordinary_harnesses(self):
        assert [a.id for a in self.mock_config().agents] == ["mock-strong+react"]
