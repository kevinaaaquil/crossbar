"""The roster: connecting models and assigning them roles."""
import textwrap

import pytest

from crossbar.providers import AnthropicProvider, OpenAICompatProvider
from crossbar.roster import (
    ModelSpec,
    Price,
    Roster,
    RosterError,
    build_provider,
    cost_usd,
    load_roster,
    parse_roster,
)
from crossbar.domain import Role
from crossbar.providers import Usage

BASE = {
    "models": [
        {"id": "local-qwen", "provider": "openai", "model": "qwen3",
         "base_url": "http://localhost:8000/v1",
         "price": {"input_per_mtok": 0.2, "output_per_mtok": 0.6}},
        {"id": "opus", "provider": "anthropic", "model": "claude-opus-5",
         "api_key_env": "ANTHROPIC_API_KEY",
         "price": {"input_per_mtok": 15, "output_per_mtok": 75}},
    ],
    "roles": {"candidate": "local-qwen", "baseline": "opus"},
}


def roster(**overrides) -> Roster:
    return parse_roster({**BASE, **overrides}, source="<test>")


class TestModels:
    def test_models_are_parsed(self):
        assert [m.id for m in roster().models] == ["local-qwen", "opus"]

    def test_a_model_carries_its_endpoint_and_price(self):
        model = roster().model("local-qwen")
        assert model.base_url == "http://localhost:8000/v1"
        assert model.price == Price(0.2, 0.6)

    def test_the_api_key_comes_from_the_named_variable(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-xyz")
        assert roster().model("opus").api_key() == "sk-ant-xyz"

    def test_a_missing_key_reads_as_empty(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        assert roster().model("opus").api_key() == ""

    def test_unknown_providers_are_rejected(self):
        with pytest.raises(RosterError, match="telepathy"):
            roster(models=[{"id": "m", "provider": "telepathy", "model": "x"}],
                   roles={"candidate": "m", "baseline": "m"})

    def test_openai_models_need_a_base_url(self):
        with pytest.raises(RosterError, match="base_url"):
            roster(models=[{"id": "m", "provider": "openai", "model": "x"}],
                   roles={"candidate": "m", "baseline": "m"})

    def test_duplicate_ids_are_rejected(self):
        with pytest.raises(RosterError, match="duplicate"):
            roster(models=[BASE["models"][0], BASE["models"][0]])

    def test_an_unknown_model_id_raises(self):
        with pytest.raises(RosterError, match="ghost"):
            roster().model("ghost")

    def test_price_defaults_to_zero(self):
        cfg = roster(models=[{"id": "m", "provider": "anthropic", "model": "x"}],
                     roles={"candidate": "m", "baseline": "m"})
        assert cfg.model("m").price == Price(0.0, 0.0)


class TestRoles:
    def test_candidate_and_baseline_are_assigned(self):
        assert roster().assigned(Role.CANDIDATE).id == "local-qwen"
        assert roster().assigned(Role.BASELINE).id == "opus"

    def test_at_least_a_candidate_and_a_baseline_are_required(self):
        with pytest.raises(RosterError, match="baseline"):
            roster(roles={"candidate": "local-qwen"})

    def test_roles_may_not_reference_an_unknown_model(self):
        with pytest.raises(RosterError, match="ghost"):
            roster(roles={"candidate": "ghost", "baseline": "opus"})

    def test_the_judge_falls_back_to_the_baseline(self):
        assert roster().assigned(Role.JUDGE).id == "opus"

    def test_an_explicit_judge_is_used(self):
        cfg = roster(roles={"candidate": "local-qwen", "baseline": "opus", "judge": "local-qwen"})
        assert cfg.assigned(Role.JUDGE).id == "local-qwen"

    def test_the_same_model_may_hold_several_roles(self):
        cfg = roster(roles={"candidate": "opus", "baseline": "opus"})
        assert cfg.assigned(Role.CANDIDATE).id == cfg.assigned(Role.BASELINE).id

    def test_judge_is_baseline_reports_the_conflict(self):
        """The report must be able to say the judge is grading its own work."""
        assert roster().judge_is_baseline is True

    def test_an_independent_judge_has_no_conflict(self):
        cfg = roster(roles={"candidate": "local-qwen", "baseline": "opus", "judge": "local-qwen"})
        assert cfg.judge_is_baseline is False

    def test_more_models_than_roles_is_fine(self):
        models = BASE["models"] + [{"id": "third", "provider": "anthropic", "model": "x"}]
        assert len(roster(models=models).models) == 3

    def test_execution_order_puts_the_candidate_first(self):
        assert [r.value for r in roster().execution_roles] == ["candidate", "baseline"]


class TestBuilders:
    def test_openai_models_build_an_openai_provider(self):
        assert isinstance(build_provider(roster().model("local-qwen")), OpenAICompatProvider)

    def test_anthropic_models_build_an_anthropic_provider(self):
        assert isinstance(build_provider(roster().model("opus")), AnthropicProvider)

    def test_the_model_name_reaches_the_provider(self):
        assert build_provider(roster().model("local-qwen")).model == "qwen3"


class TestCost:
    def test_cost_uses_the_per_million_price(self):
        assert cost_usd(Usage(1_000_000, 1_000_000), Price(3.0, 15.0)) == pytest.approx(18.0)

    def test_partial_millions_are_prorated(self):
        assert cost_usd(Usage(1000, 0), Price(3.0, 15.0)) == pytest.approx(0.003)

    def test_an_unpriced_model_costs_nothing(self):
        assert cost_usd(Usage(10_000, 10_000), Price()) == 0.0


class TestLoadingFromDisk:
    def test_a_roster_file_loads(self, tmp_path):
        path = tmp_path / "roster.yaml"
        path.write_text(
            textwrap.dedent(
                """
                models:
                  - id: local
                    provider: openai
                    model: qwen
                    base_url: http://localhost:8000/v1
                  - id: opus
                    provider: anthropic
                    model: claude-opus-5
                roles:
                  candidate: local
                  baseline: opus
                """
            )
        )
        loaded = load_roster(path)
        assert isinstance(loaded, Roster)
        assert loaded.assigned(Role.CANDIDATE).model == "qwen"

    def test_a_missing_file_raises(self, tmp_path):
        with pytest.raises(RosterError, match="not found"):
            load_roster(tmp_path / "nope.yaml")

    def test_malformed_yaml_names_the_file(self, tmp_path):
        path = tmp_path / "roster.yaml"
        path.write_text("models: [oops\n")
        with pytest.raises(RosterError, match="roster.yaml"):
            load_roster(path)

    def test_a_roster_with_no_models_is_rejected(self, tmp_path):
        path = tmp_path / "roster.yaml"
        path.write_text("roles: {}\n")
        with pytest.raises(RosterError, match="model"):
            load_roster(path)
