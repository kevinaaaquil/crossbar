"""The roster: which models, which harnesses, and the matrix they imply.

One file describes the whole sweep. Adding a fine-tuned local model is three
lines of YAML pointing at an OpenAI-compatible endpoint, which is the point:
the thing under test should be easy to swap.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml

from crossbar.harness import ClaudeCodeHarness, ReactHarness, SingleShotHarness
from crossbar.providers import (
    AnthropicProvider,
    MockProvider,
    OpenAICompatProvider,
    Provider,
    Usage,
)

PROVIDER_KINDS = ("openai", "anthropic", "claude-cli", "mock")
HARNESS_KINDS = ("react", "single-shot", "claude-code")

DEFAULT_HARNESSES = [
    {"id": "react", "kind": "react"},
    {"id": "react-plus-verify", "kind": "react", "verify": True},
]


class ConfigError(ValueError):
    """The roster is malformed or internally inconsistent."""


@dataclass(frozen=True)
class Price:
    """Published price per million tokens. Zero means "not priced"."""

    input_per_mtok: float = 0.0
    output_per_mtok: float = 0.0


@dataclass(frozen=True)
class ModelSpec:
    id: str
    provider: str
    model: str
    base_url: str = ""
    api_key_env: str = ""
    api_key_inline: str = ""
    price: Price = field(default_factory=Price)
    max_tokens: int = 2048
    temperature: float | None = None
    skill: float = 1.0
    """Mock models only: how reliably the scripted demo agent executes a step."""
    recover: float = 0.8
    """Mock models only: how often a self-check turn catches a missed step."""

    def api_key(self) -> str:
        """Key from the environment, or an inline one for quick experiments."""
        if self.api_key_inline:
            return self.api_key_inline
        return os.environ.get(self.api_key_env, "") if self.api_key_env else ""

    @property
    def needs_provider(self) -> bool:
        """Claude Code brings its own model plumbing; everything else needs a client."""
        return self.provider != "claude-cli"


@dataclass(frozen=True)
class HarnessSpec:
    id: str
    kind: str
    options: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AgentSpec:
    """One cell of the matrix: a model paired with a harness."""

    model_id: str
    harness_id: str

    @property
    def id(self) -> str:
        return f"{self.model_id}+{self.harness_id}"


@dataclass(frozen=True)
class RunSettings:
    repeats: int = 3
    seed: int = 0
    concurrency: int = 4
    baseline: str = ""
    results_dir: str = "runs"


@dataclass(frozen=True)
class Config:
    models: tuple[ModelSpec, ...]
    harnesses: tuple[HarnessSpec, ...]
    agents: tuple[AgentSpec, ...]
    run: RunSettings
    source: str = "<memory>"

    def model(self, model_id: str) -> ModelSpec:
        for model in self.models:
            if model.id == model_id:
                return model
        raise ConfigError(f"{self.source}: unknown model {model_id!r}")

    def harness(self, harness_id: str) -> HarnessSpec:
        for harness in self.harnesses:
            if harness.id == harness_id:
                return harness
        raise ConfigError(f"{self.source}: unknown harness {harness_id!r}")

    def agent(self, agent_id: str) -> AgentSpec:
        for agent in self.agents:
            if agent.id == agent_id:
                return agent
        raise ConfigError(f"{self.source}: unknown agent {agent_id!r}")


# -- parsing ---------------------------------------------------------------


def parse_config(data: Any, source: str = "<memory>") -> Config:
    if not isinstance(data, Mapping):
        raise ConfigError(f"{source}: config must be a mapping")

    models = _parse_models(data.get("models"), source)
    harnesses = _parse_harnesses(data.get("harnesses"), source)
    agents = _parse_agents(data.get("agents"), models, harnesses, source)
    run = _parse_run(data.get("run"), agents, source)
    return Config(models=models, harnesses=harnesses, agents=agents, run=run, source=source)


def _parse_models(raw: Any, source: str) -> tuple[ModelSpec, ...]:
    if not isinstance(raw, list) or not raw:
        raise ConfigError(f"{source}: config needs at least one entry under 'models'")
    models: list[ModelSpec] = []
    seen: set[str] = set()
    for entry in raw:
        if not isinstance(entry, Mapping):
            raise ConfigError(f"{source}: each model must be a mapping")
        model_id = str(entry.get("id") or "")
        if not model_id:
            raise ConfigError(f"{source}: model is missing 'id'")
        if model_id in seen:
            raise ConfigError(f"{source}: duplicate model id {model_id!r}")
        seen.add(model_id)
        provider = str(entry.get("provider") or "")
        if provider not in PROVIDER_KINDS:
            raise ConfigError(
                f"{source}: model {model_id!r} has unknown provider {provider!r}; "
                f"expected one of {list(PROVIDER_KINDS)}"
            )
        base_url = str(entry.get("base_url") or "")
        if provider == "openai" and not base_url:
            raise ConfigError(
                f"{source}: model {model_id!r} needs a 'base_url' "
                "(the OpenAI-compatible endpoint of your server)"
            )
        price_raw = entry.get("price") or {}
        models.append(
            ModelSpec(
                id=model_id,
                provider=provider,
                model=str(entry.get("model") or model_id),
                base_url=base_url,
                api_key_env=str(entry.get("api_key_env") or ""),
                api_key_inline=str(entry.get("api_key") or ""),
                price=Price(
                    float(price_raw.get("input_per_mtok") or 0.0),
                    float(price_raw.get("output_per_mtok") or 0.0),
                ),
                max_tokens=int(entry.get("max_tokens") or 2048),
                temperature=entry.get("temperature"),
                skill=float(entry.get("skill", 1.0)),
                recover=float(entry.get("recover", 0.8)),
            )
        )
    return tuple(models)


def _parse_harnesses(raw: Any, source: str) -> tuple[HarnessSpec, ...]:
    entries = raw if isinstance(raw, list) and raw else DEFAULT_HARNESSES
    harnesses: list[HarnessSpec] = []
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, Mapping):
            raise ConfigError(f"{source}: each harness must be a mapping")
        harness_id = str(entry.get("id") or "")
        kind = str(entry.get("kind") or "")
        if not harness_id:
            raise ConfigError(f"{source}: harness is missing 'id'")
        if kind not in HARNESS_KINDS:
            raise ConfigError(
                f"{source}: harness {harness_id!r} has unknown kind {kind!r}; "
                f"expected one of {list(HARNESS_KINDS)}"
            )
        if harness_id in seen:
            raise ConfigError(f"{source}: duplicate harness id {harness_id!r}")
        seen.add(harness_id)
        options = {k: v for k, v in entry.items() if k not in ("id", "kind")}
        harnesses.append(HarnessSpec(id=harness_id, kind=kind, options=options))
    return tuple(harnesses)


def _compatible(model: ModelSpec, harness: HarnessSpec) -> bool:
    """Claude Code drives its own model; every other harness drives a provider."""
    if harness.kind == "claude-code":
        return model.provider == "claude-cli"
    return model.provider != "claude-cli"


def _parse_agents(
    raw: Any,
    models: Sequence[ModelSpec],
    harnesses: Sequence[HarnessSpec],
    source: str,
) -> tuple[AgentSpec, ...]:
    if not isinstance(raw, list) or not raw:
        return tuple(
            AgentSpec(model.id, harness.id)
            for model in models
            for harness in harnesses
            if _compatible(model, harness)
        )

    by_model = {m.id: m for m in models}
    by_harness = {h.id: h for h in harnesses}
    agents: list[AgentSpec] = []
    for entry in raw:
        model_id = str(entry.get("model") or "")
        harness_id = str(entry.get("harness") or "")
        if model_id not in by_model:
            raise ConfigError(f"{source}: agent references unknown model {model_id!r}")
        if harness_id not in by_harness:
            raise ConfigError(f"{source}: agent references unknown harness {harness_id!r}")
        if not _compatible(by_model[model_id], by_harness[harness_id]):
            raise ConfigError(
                f"{source}: model {model_id!r} uses the Claude Code CLI, so it can only "
                "be paired with a harness of kind 'claude-code' (and vice versa)"
            )
        agents.append(AgentSpec(model_id, harness_id))
    return tuple(agents)


def _parse_run(raw: Any, agents: Sequence[AgentSpec], source: str) -> RunSettings:
    data = raw if isinstance(raw, Mapping) else {}
    repeats = int(data.get("repeats", 3))
    if repeats <= 0:
        raise ConfigError(f"{source}: 'repeats' must be at least 1")
    concurrency = int(data.get("concurrency", 4))
    if concurrency <= 0:
        raise ConfigError(f"{source}: 'concurrency' must be at least 1")
    baseline = str(data.get("baseline") or (agents[0].id if agents else ""))
    return RunSettings(
        repeats=repeats,
        seed=int(data.get("seed", 0)),
        concurrency=concurrency,
        baseline=baseline,
        results_dir=str(data.get("results_dir") or "runs"),
    )


def load_config(path: str | os.PathLike[str]) -> Config:
    target = Path(path)
    if not target.exists():
        raise ConfigError(f"config file not found: {target}")
    try:
        data = yaml.safe_load(target.read_text())
    except yaml.YAMLError as exc:
        raise ConfigError(f"{target}: malformed YAML: {exc}") from exc
    return parse_config(data, source=str(target))


# -- builders --------------------------------------------------------------


def build_provider(model: ModelSpec, task: Any = None, seed: int = 0) -> Provider | None:
    """Create the client for a model, or None when the harness owns the model.

    ``task`` and ``seed`` matter only for the built-in mock backend, which
    replays the task's demo script deterministically for a given seed.
    """
    if model.provider == "mock":
        return MockProvider(
            model=model.model, task=task, skill=model.skill, seed=seed, recover=model.recover
        )
    if model.provider == "openai":
        return OpenAICompatProvider(
            model=model.model, base_url=model.base_url, api_key=model.api_key()
        )
    if model.provider == "anthropic":
        provider_kwargs: dict[str, Any] = {"model": model.model, "api_key": model.api_key()}
        if model.base_url:
            provider_kwargs["base_url"] = model.base_url
        return AnthropicProvider(**provider_kwargs)
    return None


def build_harness(harness: HarnessSpec, model: ModelSpec):
    if harness.kind == "react":
        return ReactHarness(
            verify=bool(harness.options.get("verify", False)),
            max_tokens=model.max_tokens,
            temperature=model.temperature,
        )
    if harness.kind == "single-shot":
        return SingleShotHarness(max_tokens=model.max_tokens)
    if harness.kind == "claude-code":
        return ClaudeCodeHarness(
            claude_bin=str(harness.options.get("claude_bin", "claude")),
            model=model.model or None,
            permission_mode=str(harness.options.get("permission_mode", "bypassPermissions")),
        )
    raise ConfigError(f"unknown harness kind {harness.kind!r}")


def cost_usd(usage: Usage, price: Price) -> float:
    """Dollar cost of one rollout at the roster's prices."""
    return (
        usage.input_tokens * price.input_per_mtok + usage.output_tokens * price.output_per_mtok
    ) / 1_000_000
