"""Building `.crossbar/config.yaml` without hand-editing YAML.

The file stays the source of truth — a technically inclined person, or a coding
agent, should be able to edit it directly and never open the UI. This module is
the other door: it turns a set of choices into the same file, commented the same
way, so neither route produces something the other cannot read.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Mapping

import yaml

MODEL_KINDS = ("openai", "anthropic", "claude-cli")


@dataclass(frozen=True)
class ModelDraft:
    """One connected model, before it becomes YAML."""

    id: str
    kind: str
    model: str
    base_url: str = ""
    api_key_env: str = ""
    command: str = "claude"
    input_price: float = 0.0
    output_price: float = 0.0
    max_tokens: int | None = None
    temperature: float | None = None

    @property
    def needs_base_url(self) -> bool:
        return self.kind == "openai"

    @property
    def drives_itself(self) -> bool:
        return self.kind == "claude-cli"


@dataclass(frozen=True)
class ConfigDraft:
    """Everything `.crossbar/config.yaml` holds."""

    models: list[ModelDraft] = field(default_factory=list)
    candidate: str = ""
    baseline: str | None = None
    judge: str | None = None
    tests: list[str] = field(default_factory=list)
    repeats: int | None = None
    judge_tests: int = 1
    results_dir: str = "runs"

    @property
    def is_single_model(self) -> bool:
        return not self.baseline

    def model(self, model_id: str) -> ModelDraft | None:
        return next((m for m in self.models if m.id == model_id), None)


# -- presets ---------------------------------------------------------------


@dataclass(frozen=True)
class Preset:
    """A starting point for a connection, so nobody has to remember a base URL."""

    label: str
    kind: str
    base_url: str = ""
    api_key_env: str = ""
    hint: str = ""


PRESETS: dict[str, Preset] = {
    "ollama": Preset(
        label="Ollama (local)",
        kind="openai",
        base_url="http://localhost:11434/v1",
        hint="Usually needs no key.",
    ),
    "vllm": Preset(
        label="vLLM (local)",
        kind="openai",
        base_url="http://localhost:8000/v1",
        hint="Usually needs no key.",
    ),
    "moonshot": Preset(
        label="Moonshot (Kimi)",
        kind="openai",
        base_url="https://api.moonshot.ai/v1",
        api_key_env="MOONSHOT_API_KEY",
    ),
    "openrouter": Preset(
        label="OpenRouter",
        kind="openai",
        base_url="https://openrouter.ai/api/v1",
        api_key_env="OPENROUTER_API_KEY",
    ),
    "together": Preset(
        label="Together",
        kind="openai",
        base_url="https://api.together.xyz/v1",
        api_key_env="TOGETHER_API_KEY",
    ),
    "anthropic": Preset(
        label="Anthropic API (the bare model)",
        kind="anthropic",
        api_key_env="ANTHROPIC_API_KEY",
    ),
    "claude-code": Preset(
        label="Claude Code CLI (the shipped agent)",
        kind="claude-cli",
        api_key_env="ANTHROPIC_API_KEY",
        hint="Brings its own harness, so results are a product comparison. "
             "Runs with --bare, which needs ANTHROPIC_API_KEY.",
    ),
    "custom": Preset(
        label="Something else (OpenAI-compatible)",
        kind="openai",
        hint="Any endpoint that speaks /chat/completions.",
    ),
}


def preset(name: str, model_id: str, model: str) -> ModelDraft:
    """Start a model from a known endpoint shape."""
    if name not in PRESETS:
        raise KeyError(f"unknown preset {name!r}; known: {sorted(PRESETS)}")
    chosen = PRESETS[name]
    return ModelDraft(
        id=model_id,
        kind=chosen.kind,
        model=model,
        base_url=chosen.base_url,
        api_key_env=chosen.api_key_env,
    )


# -- validation ------------------------------------------------------------


def validate_draft(draft: ConfigDraft) -> list[str]:
    """Problems a person can act on, in their own words. Empty means sound."""
    problems: list[str] = []
    ids = [m.id for m in draft.models]

    if not draft.models:
        problems.append("Connect at least one model.")
    for model_id in {i for i in ids if ids.count(i) > 1}:
        problems.append(f"Duplicate model id {model_id!r}: every id must be unique.")
    for model in draft.models:
        if not model.id:
            problems.append("Every model needs an id.")
        if model.kind not in MODEL_KINDS:
            problems.append(f"{model.id!r}: unknown kind {model.kind!r}.")
        if model.needs_base_url and not model.base_url:
            problems.append(
                f"{model.id!r} needs a base_url — the endpoint it is served from."
            )

    if not draft.candidate:
        problems.append("Choose a candidate: the model you want to assess.")
    elif draft.candidate not in ids:
        problems.append(f"The candidate {draft.candidate!r} is not one of the connected models.")

    if draft.baseline and draft.baseline not in ids:
        problems.append(f"The baseline {draft.baseline!r} is not one of the connected models.")
    if draft.judge and draft.judge not in ids:
        problems.append(f"The judge {draft.judge!r} is not one of the connected models.")

    if draft.is_single_model:
        if not draft.judge:
            problems.append(
                "With no baseline there is nothing for the judge to fall back to, "
                "so choose a judge."
            )
        elif draft.judge == draft.candidate:
            problems.append(
                f"{draft.candidate!r} cannot judge itself. Choose a different judge, "
                "or add a baseline to compare against."
            )

    if not draft.tests:
        problems.append("Add at least one Test directory.")
    if draft.judge_tests < 0:
        problems.append("'Tests to judge' cannot be negative.")
    return problems


# -- rendering -------------------------------------------------------------


def render_config(draft: ConfigDraft) -> str:
    """The same commented file a person would have written by hand."""
    lines = [
        "# crossbar project config.",
        "#",
        "# Edit this by hand, or run `crossbar tui` and use Setup. Both write the",
        "# same file, so neither locks you out of the other.",
        "",
        "models:",
    ]
    for model in draft.models:
        lines.extend(_render_model(model))
    lines.append("")
    lines.append("roles:")
    lines.append(f"  candidate: {draft.candidate}       # the model under test")
    if draft.baseline:
        lines.append(f"  baseline: {draft.baseline}        # what it is compared against")
    else:
        lines.append("  # baseline:               # omitted: a single-model assessment")
    if draft.judge:
        lines.append(f"  judge: {draft.judge}")
    else:
        lines.append("  # judge:                  # unset, so the baseline judges its own")
        lines.append("  #                         # attempts — blinded, but a conflict of")
        lines.append("  #                         # interest the report will flag")

    lines.append("")
    lines.append("tests:                      # relative to this .crossbar folder")
    for path in draft.tests:
        lines.append(f"  - {path}")

    lines.append("")
    lines.append("run:")
    lines.append(f"  results_dir: {draft.results_dir}")
    lines.append(
        f"  judge_tests: {draft.judge_tests}            "
        "# judging is the expensive part of the bill"
    )
    if draft.repeats is not None:
        lines.append(f"  repeats: {draft.repeats}                # overrides each Test's own")
    return "\n".join(lines) + "\n"


def _render_model(model: ModelDraft) -> list[str]:
    lines = [f"  - id: {model.id}"]
    if model.kind == "openai":
        lines.append("    provider: openai          # the API format, not the vendor")
    elif model.kind == "claude-cli":
        lines.append("    provider: claude-cli      # a shipped agent, not a bare model")
    else:
        lines.append(f"    provider: {model.kind}")
    lines.append(f"    model: {model.model}")
    if model.base_url:
        lines.append(f"    base_url: {model.base_url}")
    if model.drives_itself and model.command:
        lines.append(f"    command: {model.command}")
    if model.api_key_env:
        lines.append(
            f"    api_key_env: {model.api_key_env}   # the NAME of a variable, never the key"
        )
    if model.max_tokens is not None:
        lines.append(f"    max_tokens: {model.max_tokens}")
    if model.temperature is not None:
        lines.append(f"    temperature: {model.temperature}")
    lines.append(
        f"    price: {{input_per_mtok: {model.input_price}, "
        f"output_per_mtok: {model.output_price}}}"
    )
    lines.append("")
    return lines


# -- reading and writing ---------------------------------------------------


def write_config(draft: ConfigDraft, path: str | os.PathLike[str]) -> Path:
    """Write the config, refusing anything that would not load.

    Anything already there is copied to `.bak` first: this replaces the whole
    file, so a hand-written comment would otherwise vanish without trace.
    """
    problems = validate_draft(draft)
    if problems:
        raise ValueError("; ".join(problems))

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        target.with_suffix(target.suffix + ".bak").write_text(target.read_text())
    target.write_text(render_config(draft))
    return target


def draft_from_config(path: str | os.PathLike[str]) -> ConfigDraft:
    """Load an existing config back into an editable draft."""
    data: Mapping[str, Any] = yaml.safe_load(Path(path).read_text()) or {}
    roles = data.get("roles") or {}
    run = data.get("run") or {}

    models = []
    for entry in data.get("models") or []:
        price = entry.get("price") or {}
        models.append(
            ModelDraft(
                id=str(entry.get("id", "")),
                kind=str(entry.get("provider", "openai")),
                model=str(entry.get("model", "")),
                base_url=str(entry.get("base_url") or ""),
                api_key_env=str(entry.get("api_key_env") or ""),
                command=str(entry.get("command") or "claude"),
                input_price=float(price.get("input_per_mtok") or 0.0),
                output_price=float(price.get("output_per_mtok") or 0.0),
                max_tokens=entry.get("max_tokens"),
                temperature=entry.get("temperature"),
            )
        )

    return ConfigDraft(
        models=models,
        candidate=str(roles.get("candidate") or ""),
        baseline=str(roles.get("baseline")) if roles.get("baseline") else None,
        judge=str(roles.get("judge")) if roles.get("judge") else None,
        tests=[str(t) for t in data.get("tests") or []],
        repeats=run.get("repeats"),
        judge_tests=int(run.get("judge_tests", 1)),
        results_dir=str(run.get("results_dir") or "runs"),
    )


def with_model(draft: ConfigDraft, model: ModelDraft) -> ConfigDraft:
    """Add or replace a model, keeping the rest of the draft intact."""
    models = [m for m in draft.models if m.id != model.id] + [model]
    return replace(draft, models=models)


def without_model(draft: ConfigDraft, model_id: str) -> ConfigDraft:
    """Remove a model, clearing any role it held."""
    return replace(
        draft,
        models=[m for m in draft.models if m.id != model_id],
        candidate="" if draft.candidate == model_id else draft.candidate,
        baseline=None if draft.baseline == model_id else draft.baseline,
        judge=None if draft.judge == model_id else draft.judge,
    )
