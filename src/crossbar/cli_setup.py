"""`crossbar setup`: building a config by being asked, one thing at a time.

The same `configwriter` backend the Setup tab uses, driven by prompts instead of
widgets — so the platform is fully usable from a plain terminal, with no app and
no YAML editing. All three routes write the same file.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Protocol

from crossbar.configwriter import (
    PRESETS,
    ConfigDraft,
    ModelDraft,
    draft_from_config,
    preset,
    validate_draft,
    write_config,
)


class Prompter(Protocol):
    """Where the questions go and the answers come from."""

    def ask(self, question: str, default: str = "") -> str: ...
    def say(self, message: str = "") -> None: ...


class ConsolePrompter:
    """Reads the terminal."""

    def ask(self, question: str, default: str = "") -> str:
        suffix = f" [{default}]" if default else ""
        answer = input(f"{question}{suffix}: ").strip()
        return answer or default

    def say(self, message: str = "") -> None:
        print(message)


def run_setup(config_path: str | os.PathLike[str], prompter: Prompter | None = None) -> bool:
    """Walk through a config. Returns whether anything was written."""
    prompter = prompter or ConsolePrompter()
    target = Path(config_path)

    existing = draft_from_config(target) if target.exists() else ConfigDraft()
    if existing.models:
        prompter.say(f"Editing {target}")
        prompter.say(
            f"Already connected: {', '.join(m.id for m in existing.models)}"
        )
    else:
        prompter.say(f"This will write {target}")
    prompter.say()

    existing = _ask_which_to_drop(prompter, existing)
    models = _ask_models(prompter, existing)
    roles = _ask_roles_until_sound(prompter, models)
    tests = _ask_tests(prompter, existing, target.parent)
    run = _ask_run_settings(prompter, existing)

    draft = ConfigDraft(models=models, tests=tests, **roles, **run)
    draft = _fix_until_sound(prompter, draft)

    _show_summary(prompter, draft, target)
    if _yes(prompter, "Save this", default="y"):
        write_config(draft, target)
        prompter.say(f"\nWrote {target}")
        return True
    prompter.say("\nNothing was written.")
    return False


# -- models ----------------------------------------------------------------


def _ask_which_to_drop(prompter: Prompter, existing: ConfigDraft) -> ConfigDraft:
    """Which of the already-connected models to forget.

    ``crossbar init`` writes two placeholder models to show the shape of the
    file. Without this, a config built entirely by being asked still carries
    them, because every other question here only ever adds.
    """
    if not existing.models:
        return existing
    while True:
        answer = prompter.ask(
            "Remove any of them? Comma-separated ids, blank to keep all"
        ).strip()
        if not answer:
            return existing
        wanted = [part.strip() for part in answer.split(",") if part.strip()]
        known = {m.id for m in existing.models}
        unknown = [w for w in wanted if w not in known]
        if unknown:
            # Silently ignoring a typo here would leave the model in place and
            # look like it had been removed.
            prompter.say(f"  Not connected: {', '.join(unknown)}. Nothing removed.")
            continue
        kept = [m for m in existing.models if m.id not in set(wanted)]
        prompter.say(f"  Removed {', '.join(wanted)}.")
        prompter.say(
            f"  Connected: {', '.join(m.id for m in kept) or 'nothing'}"
        )
        prompter.say()
        return _replace_draft(existing, {"models": tuple(kept)})


def _ask_models(prompter: Prompter, existing: ConfigDraft) -> list[ModelDraft]:
    models = list(existing.models)
    if models and not _yes(prompter, "Connect another model", default="n"):
        return models

    while True:
        model = _ask_one_model(prompter)
        if model is not None:
            models = [m for m in models if m.id != model.id] + [model]
            prompter.say(f"  Connected {model.id}.")
        prompter.say()
        if not _yes(prompter, "Connect another model", default="n"):
            return models


def _ask_one_model(prompter: Prompter) -> ModelDraft | None:
    names = list(PRESETS)
    prompter.say("Where is this model served from?")
    for index, name in enumerate(names, start=1):
        chosen = PRESETS[name]
        prompter.say(f"  {index}. {chosen.label}")
    raw = prompter.ask("Choose a number", default="1")
    try:
        chosen_name = names[int(raw) - 1]
    except (ValueError, IndexError):
        prompter.say(f"  {raw!r} is not one of those. Skipping.")
        return None

    model_id = prompter.ask("A short name for it, used in reports")
    model_name = prompter.ask("The model name the endpoint expects")
    drafted = preset(chosen_name, model_id=model_id, model=model_name)

    if drafted.needs_base_url:
        drafted = _replace(
            drafted,
            base_url=prompter.ask("The endpoint it is served from", default=drafted.base_url),
        )
    if drafted.drives_itself:
        drafted = _replace(
            drafted, command=prompter.ask("The CLI command to run", default=drafted.command)
        )

    # A subscription login reads no variable, and asking would invite somebody
    # to paste a key that is never used.
    if drafted.auth == "api-key":
        # Saying this every time is worth it: a key pasted here would end up in
        # a file people commit.
        drafted = _replace(
            drafted,
            api_key_env=prompter.ask(
                "The NAME of the environment variable holding its key, "
                "never the key itself",
                default=drafted.api_key_env,
            ),
        )
    else:
        prompter.say(
            "  Uses your existing login, so there is no key to name. Note it "
            "cannot run --bare: your global CLAUDE.md reaches it."
        )

    prompter.say("  Prices let the report state what a run costs. Leave at 0 if unknown.")
    return _replace(
        drafted,
        input_price=_number(prompter, "Input price per million tokens", "0"),
        output_price=_number(prompter, "Output price per million tokens", "0"),
    )


# -- roles -----------------------------------------------------------------


def _ask_roles(prompter: Prompter, models: list[ModelDraft]) -> dict:
    ids = [m.id for m in models]
    prompter.say()
    prompter.say(f"Connected: {', '.join(ids) or 'nothing'}")

    candidate = prompter.ask("Which model are you assessing (the candidate)")
    baseline = prompter.ask(
        "Which model is it compared against (the baseline) — blank to assess it on its own"
    )
    judge = prompter.ask("Which model should judge the results — blank to let the baseline")

    if not judge and baseline:
        prompter.say(
            f"  Note: {baseline} will grade its own attempts. They are blinded, but it "
            "is a conflict of interest and every report will say so."
        )
    return {
        "candidate": candidate,
        "baseline": baseline or None,
        "judge": judge or None,
    }


# -- tests and settings ----------------------------------------------------


def _ask_roles_until_sound(prompter: Prompter, models: list[ModelDraft]) -> dict:
    """Ask for the roles, and fix them on the spot.

    Role problems are settled here rather than at the end, so nobody answers
    five more questions before being told the candidate was misspelled.
    """
    for _ in range(5):
        roles = _ask_roles(prompter, models)
        problems = [
            p for p in validate_draft(ConfigDraft(models=models, tests=["placeholder"], **roles))
            if "candidate" in p or "baseline" in p or "judge" in p or "itself" in p
        ]
        if not problems:
            return roles
        prompter.say()
        for problem in problems:
            prompter.say(f"  ! {problem}")
    return roles


def _ask_tests(
    prompter: Prompter, existing: ConfigDraft, project_dir: Path
) -> list[str]:
    """Test directories, checked against the disk as they are given.

    A directory that is not there is a typo, and this is the moment it is cheap
    to fix -- not the run it was supposed to precede.
    """
    prompter.say()
    while True:
        answer = prompter.ask(
            "Test directories, comma separated, relative to .crossbar",
            default=", ".join(existing.tests),
        )
        wanted = [part.strip() for part in answer.split(",") if part.strip()]
        missing = [w for w in wanted if not (project_dir / w).is_dir()]
        if not missing:
            return wanted
        prompter.say(f"  No such directory under {project_dir}: {', '.join(missing)}")


def _ask_run_settings(prompter: Prompter, existing: ConfigDraft) -> dict:
    prompter.say()
    repeats = prompter.ask(
        "How many times to attempt each Task — blank to use each Test's own",
        default=str(existing.repeats) if existing.repeats else "",
    )
    prompter.say("  Judging is the expensive part of a run, so it defaults to the first Test.")
    judge_tests = _number(prompter, "How many Tests to judge", str(existing.judge_tests), int)
    return {
        "repeats": int(repeats) if str(repeats).strip().isdigit() else None,
        "judge_tests": int(judge_tests),
    }


# -- validation and summary ------------------------------------------------


def _fix_until_sound(prompter: Prompter, draft: ConfigDraft) -> ConfigDraft:
    """Keep asking until the draft would actually load.

    Writing something invalid is worse than asking again: the next command
    would fail with a parse error rather than the plain sentence shown here.
    """
    for _ in range(5):
        problems = validate_draft(draft)
        if not problems:
            return draft
        prompter.say()
        for problem in problems:
            prompter.say(f"  ! {problem}")
        draft = _replace_draft(draft, _ask_roles(prompter, draft.models))
    return draft


def _show_summary(prompter: Prompter, draft: ConfigDraft, target: Path) -> None:
    prompter.say()
    prompter.say("READY TO WRITE " + str(target))
    for model in draft.models:
        roles = [
            name
            for name, held in (
                ("candidate", draft.candidate),
                ("baseline", draft.baseline),
                ("judge", draft.judge),
            )
            if held == model.id
        ]
        label = f" ({', '.join(roles)})" if roles else ""
        prompter.say(f"  {model.id}{label} — {model.kind}/{model.model}")
    prompter.say(f"  tests: {', '.join(draft.tests) or 'none'}")
    prompter.say(f"  judging {draft.judge_tests} Test(s)")
    prompter.say()


# -- small helpers ---------------------------------------------------------


def _yes(prompter: Prompter, question: str, default: str = "n") -> bool:
    return prompter.ask(f"{question}? (y/n)", default=default).strip().lower().startswith("y")


def _number(prompter: Prompter, question: str, default: str, cast=float):
    raw = prompter.ask(question, default=default)
    try:
        return cast(raw)
    except (TypeError, ValueError):
        prompter.say(f"  {raw!r} is not a number; using {default}.")
        return cast(default)


def _replace(model: ModelDraft, **changes) -> ModelDraft:
    from dataclasses import replace

    return replace(model, **changes)


def _replace_draft(draft: ConfigDraft, roles: dict) -> ConfigDraft:
    from dataclasses import replace

    return replace(draft, **roles)
