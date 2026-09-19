"""The `.crossbar` project folder.

crossbar is installed, not cloned, so it has to find its own setup the way any
other command-line tool does: a dot-folder in the project, discovered by walking
up from wherever the user happens to be standing.

One config file holds everything — the models, their roles, which Tests to run,
and the run settings. Splitting it across several files means several files to
keep in step.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml

from crossbar.connectors import known_connectors
from crossbar.domain import DomainError, Test, load_test
from crossbar.roster import Roster, RosterError, parse_roster

PROJECT_DIR = ".crossbar"
CONFIG_NAME = "config.yaml"
DEFAULT_RESULTS_DIR = "runs"


class ProjectError(ValueError):
    """The project folder is missing, malformed, or points at something absent."""


@dataclass(frozen=True)
class Project:
    """A loaded `.crossbar` folder."""

    root: Path
    """The `.crossbar` directory itself."""

    roster: Roster
    tests: tuple[Test, ...]
    results_dir: Path
    judge_tests: int = 1

    @property
    def project_root(self) -> Path:
        """The directory containing `.crossbar`."""
        return self.root.parent


def find_project(start: str | os.PathLike[str] | None = None) -> Path | None:
    """Walk up looking for a `.crossbar` directory, as git does for `.git`."""
    current = Path(start or Path.cwd()).resolve()
    for directory in (current, *current.parents):
        candidate = directory / PROJECT_DIR
        if (candidate / CONFIG_NAME).is_file():
            return candidate
    return None


def load_project(start: str | os.PathLike[str] | None = None) -> Project:
    """Find and load the project the user is standing in."""
    root = find_project(start)
    if root is None:
        raise ProjectError(
            f"no {PROJECT_DIR}/{CONFIG_NAME} found here or in any parent directory. "
            "Run `crossbar init` to create one."
        )

    config_path = root / CONFIG_NAME
    try:
        data = yaml.safe_load(config_path.read_text()) or {}
    except yaml.YAMLError as exc:
        raise ProjectError(f"{config_path}: malformed YAML: {exc}") from exc
    if not isinstance(data, Mapping):
        raise ProjectError(f"{config_path}: the config must be a mapping")

    try:
        roster = parse_roster(data, source=str(config_path))
    except RosterError as exc:
        raise ProjectError(str(exc)) from exc

    raw_tests = data.get("tests") or []
    if not isinstance(raw_tests, list) or not raw_tests:
        raise ProjectError(
            f"{config_path}: list at least one Test directory under 'tests'"
        )

    tests: list[Test] = []
    for entry in raw_tests:
        path = _resolve(root, str(entry))
        if not path.is_dir():
            raise ProjectError(f"{config_path}: no Test directory at {entry!r} ({path})")
        try:
            tests.append(load_test(path, known_connectors=known_connectors()))
        except DomainError as exc:
            raise ProjectError(str(exc)) from exc

    run = data.get("run") or {}
    if not isinstance(run, Mapping):
        raise ProjectError(f"{config_path}: 'run' must be a mapping")

    return Project(
        root=root,
        roster=roster,
        tests=tuple(tests),
        results_dir=_resolve(root, str(run.get("results_dir") or DEFAULT_RESULTS_DIR)),
        judge_tests=int(run.get("judge_tests", 1)),
    )


def init_project(directory: str | os.PathLike[str] = ".") -> list[Path]:
    """Scaffold a `.crossbar` folder with a commented config and the example Test."""
    from crossbar.demo.scripted import example_test_path

    root = Path(directory).resolve() / PROJECT_DIR
    if root.exists():
        raise ProjectError(f"{root} already exists; not overwriting it")

    tests_dir = root / "tests"
    tests_dir.mkdir(parents=True)
    shutil.copytree(example_test_path(), tests_dir / "support-triage")

    config_path = root / CONFIG_NAME
    config_path.write_text(STARTER_CONFIG)

    created = [config_path, tests_dir / "support-triage"]
    return created


def _resolve(root: Path, value: str) -> Path:
    """Relative paths in the config are relative to `.crossbar` itself.

    Absolute paths are left alone, so a Test can live anywhere.
    """
    path = Path(value)
    return path if path.is_absolute() else root / path


STARTER_CONFIG = """\
# crossbar project config.
#
# Everything lives here: the models you are comparing, which role each holds,
# which Tests to run, and where results go.
#
# EDIT THE MODELS BELOW before running anything — they point at placeholder
# endpoints, and a run against them will fail the pre-flight check.

models:
  # Your model. Any OpenAI-compatible endpoint works: vLLM, Ollama, LM Studio,
  # Together, Fireworks, OpenRouter, or your own server.
  - id: my-model
    provider: openai
    model: your-model-name
    base_url: http://localhost:8000/v1
    api_key_env: LOCAL_API_KEY        # the NAME of an env var, never the key
    price: {input_per_mtok: 0.20, output_per_mtok: 0.60}

  # The frontier model you are comparing against.
  - id: frontier
    provider: anthropic
    model: claude-opus-5
    api_key_env: ANTHROPIC_API_KEY
    price: {input_per_mtok: 15.0, output_per_mtok: 75.0}

roles:
  candidate: my-model       # the model under test
  baseline: frontier        # what it is compared against — omit for a
                            # single-model assessment, but then a judge is
                            # required and cannot be the candidate
  # judge: some-third-model
  #
  # Leave the judge unset and the baseline grades its own attempts. They are
  # blinded, but it is a conflict of interest and every report will say so.

tests:
  - tests/support-triage    # relative to this .crossbar folder

run:
  results_dir: runs         # results land in .crossbar/runs
  judge_tests: 1            # how many Tests to judge; judging is the expensive
                            # part of the bill, so this defaults to the first
"""
