"""The roster: which models are connected, and which role each one holds.

Roles are labels the user assigns. They do not change how a Task executes. They
carry two consequences and no others: Candidate Attempts run before Baseline
Attempts for a given Test, and the Baseline judges when no Judge is assigned.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

import yaml

from crossbar.domain import Role
from crossbar.providers import AnthropicProvider, OpenAICompatProvider, Provider, Usage

PROVIDER_KINDS = ("openai", "anthropic", "claude-cli")


class RosterError(ValueError):
    """The roster is malformed, or assigns a role to a model that is not there."""


@dataclass(frozen=True)
class Price:
    """Published price per million tokens. Zero means unpriced."""

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
    command: str = "claude"
    """Agent-CLI models only: the binary to run."""

    @property
    def drives_itself(self) -> bool:
        """True for an agent CLI, which brings its own harness rather than
        being driven through ours."""
        return self.provider == "claude-cli"

    def api_key(self) -> str:
        if self.api_key_inline:
            return self.api_key_inline
        return os.environ.get(self.api_key_env, "") if self.api_key_env else ""


@dataclass(frozen=True)
class Roster:
    models: tuple[ModelSpec, ...]
    roles: Mapping[str, str]
    source: str = "<memory>"

    def model(self, model_id: str) -> ModelSpec:
        for model in self.models:
            if model.id == model_id:
                return model
        raise RosterError(f"{self.source}: unknown model {model_id!r}")

    def assigned(self, role: Role) -> ModelSpec:
        """The model holding a role. The Judge falls back to the Baseline."""
        model_id = self.roles.get(role.value)
        if model_id is None and role is Role.JUDGE:
            model_id = self.roles.get(Role.BASELINE.value)
        if model_id is None:
            raise RosterError(f"{self.source}: no model assigned to the {role.value} role")
        return self.model(model_id)

    @property
    def execution_roles(self) -> tuple[Role, ...]:
        """Roles that execute Attempts, in the order they run.

        Whichever of Candidate and Baseline are assigned. Single mode is one of
        them; which label the user chose is their business, not the machinery's.
        """
        return tuple(
            role
            for role in (Role.CANDIDATE, Role.BASELINE)
            if self.roles.get(role.value)
        )

    @property
    def is_single_model(self) -> bool:
        """One model assessed on its own rather than compared.

        Answers "is this good enough at all", which is a different and usually
        earlier question than "is it as good as what we pay for".
        """
        return len(self.execution_roles) == 1

    @property
    def under_test(self) -> ModelSpec:
        """The model being assessed, without having to know its label."""
        return self.assigned(self.execution_roles[0])

    @property
    def judge_is_baseline(self) -> bool:
        """True when the Baseline is grading its own Attempts.

        A structural conflict of interest. Blinding is the mitigation, but the
        report still has to say so. Never true without a Baseline.
        """
        if self.is_single_model:
            return False
        return self.assigned(Role.JUDGE).id == self.assigned(Role.BASELINE).id




def parse_roster(data: Any, source: str = "<memory>") -> Roster:
    if not isinstance(data, Mapping):
        raise RosterError(f"{source}: the roster must be a mapping")

    raw_models = data.get("models")
    if not isinstance(raw_models, list) or not raw_models:
        raise RosterError(f"{source}: the roster needs at least one entry under 'models'")

    models: list[ModelSpec] = []
    seen: set[str] = set()
    for entry in raw_models:
        if not isinstance(entry, Mapping):
            raise RosterError(f"{source}: each model must be a mapping")
        model_id = str(entry.get("id") or "")
        if not model_id:
            raise RosterError(f"{source}: a model is missing 'id'")
        if model_id in seen:
            raise RosterError(f"{source}: duplicate model id {model_id!r}")
        seen.add(model_id)

        provider = str(entry.get("provider") or "")
        if provider not in PROVIDER_KINDS:
            raise RosterError(
                f"{source}: model {model_id!r} has unknown provider {provider!r}; "
                f"expected one of {list(PROVIDER_KINDS)}"
            )
        base_url = str(entry.get("base_url") or "")
        if provider == "openai" and not base_url:
            raise RosterError(
                f"{source}: model {model_id!r} needs a 'base_url' — the "
                "OpenAI-compatible endpoint it is served from"
            )
        price = entry.get("price") or {}
        models.append(
            ModelSpec(
                id=model_id,
                provider=provider,
                model=str(entry.get("model") or model_id),
                base_url=base_url,
                api_key_env=str(entry.get("api_key_env") or ""),
                api_key_inline=str(entry.get("api_key") or ""),
                price=Price(
                    float(price.get("input_per_mtok") or 0.0),
                    float(price.get("output_per_mtok") or 0.0),
                ),
                max_tokens=int(entry.get("max_tokens") or 2048),
                temperature=entry.get("temperature"),
                command=str(entry.get("command") or "claude"),
            )
        )

    roles = data.get("roles") or {}
    if not isinstance(roles, Mapping):
        raise RosterError(f"{source}: 'roles' must be a mapping")
    roles = {str(k): str(v) for k, v in roles.items()}

    executing = [r for r in (Role.CANDIDATE, Role.BASELINE) if roles.get(r.value)]
    if not executing:
        raise RosterError(
            f"{source}: every run needs a model to test; assign one to "
            "'candidate' or 'baseline'"
        )

    known = {m.id for m in models}
    for role, model_id in roles.items():
        if model_id not in known:
            raise RosterError(f"{source}: role {role!r} references unknown model {model_id!r}")

    if len(executing) == 1:
        # A single-model run has nothing for the judge to fall back to, and a
        # model grading its own work is the one conflict we will not allow
        # silently -- in a comparison it is at least visible in the report.
        solo = roles[executing[0].value]
        judge = roles.get(Role.JUDGE.value)
        if not judge:
            raise RosterError(
                f"{source}: a single-model run has nothing for the judge to fall back "
                "to, so a judge must be assigned explicitly"
            )
        if judge == solo:
            raise RosterError(
                f"{source}: {solo!r} cannot judge itself; assign a different model as "
                "judge, or add a second model to compare against"
            )

    return Roster(models=tuple(models), roles=roles, source=source)


def load_roster(path: str | os.PathLike[str]) -> Roster:
    target = Path(path)
    if not target.exists():
        raise RosterError(f"roster file not found: {target}")
    try:
        data = yaml.safe_load(target.read_text())
    except yaml.YAMLError as exc:
        raise RosterError(f"{target}: malformed YAML: {exc}") from exc
    return parse_roster(data, source=str(target))


def build_provider(model: ModelSpec) -> Provider | None:
    """The HTTP client for a model, or None when it drives itself."""
    if model.drives_itself:
        return None
    if model.provider == "openai":
        return OpenAICompatProvider(
            model=model.model, base_url=model.base_url, api_key=model.api_key()
        )
    if model.provider == "anthropic":
        kwargs: dict[str, Any] = {"model": model.model, "api_key": model.api_key()}
        if model.base_url:
            kwargs["base_url"] = model.base_url
        return AnthropicProvider(**kwargs)
    raise RosterError(f"unknown provider {model.provider!r}")


def cost_usd(usage: Usage, price: Price) -> float:
    return (
        usage.input_tokens * price.input_per_mtok
        + usage.output_tokens * price.output_per_mtok
    ) / 1_000_000
