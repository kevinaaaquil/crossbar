"""Setup: connecting models and writing `.crossbar/config.yaml` from the app.

The file stays the source of truth. Someone who would rather open an editor —
or a coding agent driving the project — should never have to come here, and
this screen writes exactly the file they would have written by hand, comments
and all. ``crossbar.configwriter`` owns that rendering, the validation and the
backup; everything below is the form in front of it.

Two rules shape the whole thing:

* **Nothing invalid is ever written.** ``validate_draft`` phrases its problems
  for a person, and they are shown in its words, not ours. The save button does
  not write while any remain.
* **An existing project is edited, not replaced.** The draft is loaded from the
  file on disk, so the Tests, the prices and the models nobody touched survive
  a save.
"""

from __future__ import annotations

from dataclasses import replace
from functools import partial
from pathlib import Path

from textual.app import ComposeResult
from textual.command import Hit, Hits, Provider
from textual.containers import Horizontal, VerticalScroll
from textual.css.query import NoMatches
from textual.widgets import Button, Input, Label, ListItem, ListView, Select, Static

from crossbar.configwriter import (
    PRESETS,
    ConfigDraft,
    ModelDraft,
    draft_from_config,
    preset,
    validate_draft,
    with_model,
    without_model,
    write_config,
)

ROLE_FIELDS = ("candidate", "baseline", "judge")
"""The three role slots, named as ``ConfigDraft`` names them."""

PALETTE_COMMAND = "Setup: connect models and write .crossbar/config.yaml"


class SetupCommands(Provider):
    """Setup in the command palette.

    Textual's palette (``ctrl+p``) is where a terminal app puts the commands a
    slash would carry elsewhere, so ``/setup`` lands here rather than as a
    bespoke prompt line nobody else in the app uses.
    """

    async def search(self, query: str) -> Hits:
        """Offer the Setup screen to anything that looks like a search for it."""
        matcher = self.matcher(query)
        score = matcher.match(PALETTE_COMMAND)
        if score > 0:
            yield Hit(
                score,
                matcher.highlight(PALETTE_COMMAND),
                partial(self.app.action_show_tab, "setup"),
                help="Connect models, assign roles, and write the project config.",
            )


class SetupPanel(VerticalScroll):
    """The Setup form: connect models, assign roles, save the config."""

    def __init__(self, config_path: str | Path, **kwargs) -> None:
        super().__init__(**kwargs)
        self.config_path = Path(config_path)
        self.draft = ConfigDraft()
        self.selected_id = ""
        """Which connected model the edit fields are showing."""

        self._listed_ids: list[str] = []
        """The connected ids in list order, so a highlight maps back to a model."""

    # -- layout ------------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Static(
            "SETUP\n\n"
            "  This writes the same file you could edit by hand. Nothing here is\n"
            "  magic: `.crossbar/config.yaml` stays the source of truth, and you\n"
            "  can go back to your editor at any point.",
            id="setup-intro",
            markup=False,
        )
        yield Static(id="setup-target", markup=False)
        yield Static(id="setup-warning", markup=False)

        yield Static("\nCONNECT A MODEL", id="setup-connect-heading", markup=False)
        with Horizontal(classes="setup-row"):
            yield Select(
                [(chosen.label, name) for name, chosen in PRESETS.items()],
                value="custom",
                allow_blank=False,
                id="setup-preset",
            )
            yield Input(placeholder="id, e.g. local", id="setup-new-id")
            yield Input(placeholder="model name", id="setup-new-model")
            yield Button("Connect", id="setup-connect")
        yield Static(id="setup-preset-hint", markup=False)

        yield Static("\nCONNECTED MODELS", id="setup-models-heading", markup=False)
        yield ListView(id="setup-models")
        with Horizontal(classes="setup-row"):
            yield Input(placeholder="id", id="setup-field-id")
            yield Input(placeholder="model name", id="setup-field-model")
            yield Input(placeholder="base_url", id="setup-field-base-url")
        with Horizontal(classes="setup-row"):
            yield Input(placeholder="api_key_env", id="setup-field-api-key-env")
            yield Input(placeholder="command", id="setup-field-command")
            yield Input(placeholder="$/Mtok in", id="setup-field-input-price")
            yield Input(placeholder="$/Mtok out", id="setup-field-output-price")
            # Optional: blank means "not set", which is not the same as zero.
            yield Input(placeholder="max_tokens", id="setup-field-max-tokens")
            yield Input(placeholder="temperature", id="setup-field-temperature")
        with Horizontal(classes="setup-row"):
            yield Button("Apply changes", id="setup-apply")
            yield Button("Remove model", id="setup-remove")

        yield Static("\nROLES", id="setup-roles-heading", markup=False)
        with Horizontal(classes="setup-row"):
            yield Label("Candidate")
            yield Select([], prompt="(choose)", id="setup-candidate")
            yield Label("Baseline")
            yield Select([], prompt="(none)", id="setup-baseline")
            yield Label("Judge")
            yield Select([], prompt="(none)", id="setup-judge")

        yield Static("\nTESTS AND RUN SETTINGS", id="setup-run-heading", markup=False)
        with Horizontal(classes="setup-row"):
            # One line, comma separated: the config takes a list, but a
            # multi-line editor here would be a worse version of the file.
            yield Label("Tests")
            yield Input(placeholder="tests/support-triage, ...", id="setup-tests")
        with Horizontal(classes="setup-row"):
            yield Label("Tests to judge")
            yield Input(id="setup-judge-tests", classes="setup-narrow")
            yield Label("Repeats")
            yield Input(placeholder="each Test's own", id="setup-repeats",
                        classes="setup-narrow")
            yield Label("Results dir")
            yield Input(id="setup-results-dir", classes="setup-narrow")

        yield Static(id="setup-problems", markup=False)
        with Horizontal(classes="setup-row"):
            yield Button("Save", id="setup-save", variant="primary")
        yield Static(id="setup-status", markup=False)

    def on_mount(self) -> None:
        self.load(self._draft_on_disk())
        self._show_hint()

    # -- loading -----------------------------------------------------------

    def _draft_on_disk(self) -> ConfigDraft:
        """Edit what is already there, rather than starting from nothing."""
        if not self.config_path.is_file():
            return ConfigDraft()
        try:
            return draft_from_config(self.config_path)
        except Exception as exc:
            # A config we cannot parse is the user's to fix in their editor;
            # starting blank here would invite them to overwrite it.
            self._status(f"{self.config_path} could not be read: {exc}")
            return ConfigDraft()

    def load(self, draft: ConfigDraft) -> None:
        """Show a draft, replacing whatever the form was holding."""
        self.draft = draft
        self._fill(self.query_one("#setup-tests", Input), ", ".join(draft.tests))
        self._fill(self.query_one("#setup-judge-tests", Input), str(draft.judge_tests))
        self._fill(self.query_one("#setup-results-dir", Input), draft.results_dir)
        self._load_fields(draft.models[0] if draft.models else None)
        self.refresh_view()

    # -- the draft as the form now stands ----------------------------------

    def current_draft(self) -> ConfigDraft:
        """The draft including the free-text fields, which have no button.

        An empty 'Tests to judge' means none, matching the Tests tab: judging
        is the expensive part of the bill, and reading a blank field as "judge
        everything" would spend the user's money without being asked.
        """
        return replace(
            self.draft,
            tests=self._tests(),
            judge_tests=max(0, self._judge_tests()),
            repeats=self._repeats(),
            results_dir=self._text("#setup-results-dir").strip() or "runs",
        )

    def _repeats(self) -> int | None:
        """How many times to attempt each Task, or None to leave each Test to
        its own. Unreadable text becomes 0, which validation rejects rather
        than silently running once."""
        raw = self._text("#setup-repeats").strip()
        if not raw:
            return None
        try:
            return int(raw)
        except ValueError:
            return 0

    def _tests(self) -> list[str]:
        return [part.strip() for part in self._text("#setup-tests").split(",") if part.strip()]

    def _judge_tests(self) -> int:
        """How many Tests to judge; -1 when the field cannot be read at all."""
        raw = self._text("#setup-judge-tests").strip()
        try:
            return int(raw)
        except ValueError:
            return -1

    def problems(self) -> list[str]:
        """What stands between this form and a file, in the backend's words.

        The wording comes from ``validate_draft`` untouched: it is already
        phrased for the person who has to act on it, and paraphrasing it here
        would mean two vocabularies for the same fault.
        """
        found = list(validate_draft(self.current_draft()))
        if self._judge_tests() < 0 and self._text("#setup-judge-tests").strip():
            found.append("'Tests to judge' must be a whole number.")
        return found

    # -- connecting --------------------------------------------------------

    def connect(self) -> None:
        """Add a model from the chosen preset."""
        model_id = self._text("#setup-new-id").strip()
        model_name = self._text("#setup-new-model").strip()
        if not model_id or not model_name:
            self._status(
                "Give the connection an id and the model name before connecting it."
            )
            return

        if self.draft.model(model_id) is not None:
            # `with_model` would replace it, taking its prices and its tuning
            # with it. An id already in use is a typo far more often than a
            # deliberate reconnection.
            self._status(f"{model_id!r} is already connected. Edit or remove it first.")
            return

        chosen = self.query_one("#setup-preset", Select).value
        if not isinstance(chosen, str):
            chosen = "custom"
        self.draft = with_model(self.draft, preset(chosen, model_id, model_name))
        self._fill(self.query_one("#setup-new-id", Input), "")
        self._fill(self.query_one("#setup-new-model", Input), "")
        self._status(f"Connected {model_id}.")
        self._load_fields(self.draft.model(model_id))
        self.refresh_view()

    # -- editing -----------------------------------------------------------

    def select_model(self, model_id: str) -> None:
        """Select a model by id, keeping the visible list in step."""
        self._load_fields(self.draft.model(model_id))
        listing = self.query_one("#setup-models", ListView)
        if model_id in self._listed_ids:
            listing.index = self._listed_ids.index(model_id)

    def apply_fields(self) -> None:
        """Write the edit fields back onto the selected model."""
        current = self.draft.model(self.selected_id)
        if current is None:
            self._status("Pick a model from the list before applying changes.")
            return

        prices = {}
        for field, selector in (
            ("input_price", "#setup-field-input-price"),
            ("output_price", "#setup-field-output-price"),
        ):
            raw = self._text(selector).strip()
            try:
                prices[field] = float(raw) if raw else 0.0
            except ValueError:
                # Quietly zeroing a price understates the bill, which is the
                # number this whole product exists to report.
                self._status(f"{raw!r} is not a price. Nothing was changed.")
                return

        optional: dict[str, Any] = {}
        for field, selector, cast in (
            ("max_tokens", "#setup-field-max-tokens", int),
            ("temperature", "#setup-field-temperature", float),
        ):
            raw = self._text(selector).strip()
            if not raw:
                # Blank means unset. It is not zero: temperature 0 is
                # deterministic sampling, and writing it would change the run.
                optional[field] = None
                continue
            try:
                optional[field] = cast(raw)
            except ValueError:
                self._status(f"{raw!r} is not a number. Nothing was changed.")
                return

        new_id = self._text("#setup-field-id").strip() or current.id
        if new_id != current.id and self.draft.model(new_id) is not None:
            # Renaming onto another id would fold two connections into one and
            # leave nothing to say which model the results came from.
            self._status(f"{new_id!r} is already connected. Nothing was changed.")
            return

        edited = replace(
            current,
            id=new_id,
            model=self._text("#setup-field-model").strip(),
            base_url=self._text("#setup-field-base-url").strip(),
            api_key_env=self._text("#setup-field-api-key-env").strip(),
            command=self._text("#setup-field-command").strip() or current.command,
            **prices,
            **optional,
        )
        self.draft = self._replacing(current.id, edited)
        self._status(f"Updated {edited.id}.")
        self._load_fields(self.draft.model(edited.id))
        self.refresh_view()

    def _replacing(self, old_id: str, edited: ModelDraft) -> ConfigDraft:
        """Swap one model for its edited self, in place and keeping its roles.

        ``with_model`` appends, and ``without_model`` clears the roles the id
        held — right for a removal, wrong for a rename, where the model is the
        same connection under a new name.
        """
        order = {model.id: index for index, model in enumerate(self.draft.models)}
        order[edited.id] = order.get(old_id, len(order))
        rebuilt = with_model(without_model(self.draft, old_id), edited)
        roles = {
            field: (edited.id if getattr(self.draft, field) == old_id
                    else getattr(self.draft, field))
            for field in ROLE_FIELDS
        }
        return replace(
            rebuilt,
            models=sorted(rebuilt.models, key=lambda model: order[model.id]),
            **roles,
        )

    def remove_selected(self) -> None:
        """Disconnect the selected model, and with it any role it held."""
        if self.draft.model(self.selected_id) is None:
            self._status("Pick a model from the list before removing one.")
            return
        removed = self.selected_id
        self.draft = without_model(self.draft, removed)
        self._status(f"Removed {removed}.")
        self._load_fields(self.draft.models[0] if self.draft.models else None)
        self.refresh_view()

    # -- saving ------------------------------------------------------------

    def save(self) -> bool:
        """Write the config, or say why it was not written."""
        if getattr(self.app, "sweep_running", False):
            # The orchestrator read this file when the run was planned. Rewriting
            # it now would leave the run and the config saying different things,
            # with nothing on screen to show which one the results came from.
            self._status(
                "Not saved: a run is in progress, and saving now would change what "
                "it is running under. Wait for it to finish."
            )
            return False

        found = self.problems()
        if found:
            self._show_problems(found)
            self._status("Not saved: fix the problems listed above first.")
            return False

        draft = self.current_draft()
        replacing = self.config_path.exists()
        try:
            written = write_config(draft, self.config_path)
        except (OSError, ValueError) as exc:
            self._status(f"Not saved: {exc}")
            return False

        self.draft = draft
        backup = f" The previous file is at {written.name}.bak." if replacing else ""
        self._status(f"Saved {written}.{backup}")
        self.refresh_view()
        return True

    # -- painting ----------------------------------------------------------

    def refresh_view(self) -> None:
        """Repaint everything derived from the draft."""
        self._refresh_roles()
        self._refresh_notices()
        self._show_problems(self.problems())
        # The list is rebuilt off the message loop because clearing a ListView
        # is asynchronous, and appending into one mid-clear loses items.
        self.call_later(self._refresh_models)

    async def _refresh_models(self) -> None:
        listing = self.query_one("#setup-models", ListView)
        await listing.clear()
        self._listed_ids = [model.id for model in self.draft.models]
        for model in self.draft.models:
            listing.append(ListItem(Static(_connection_line(model), markup=False)))
        if self.selected_id in self._listed_ids:
            listing.index = self._listed_ids.index(self.selected_id)

    def _refresh_roles(self) -> None:
        options = [(model.id, model.id) for model in self.draft.models]
        known = {model.id for model in self.draft.models}
        for field in ROLE_FIELDS:
            select = self.query_one(f"#setup-{field}", Select)
            select.set_options(options)
            held = getattr(self.draft, field)
            select.value = held if held in known else Select.NULL

    def _refresh_notices(self) -> None:
        self._set("#setup-target", f"  Writes {self.config_path}")
        if self.config_path.exists():
            self._set(
                "#setup-warning",
                "\n  !! That file already exists, and saving rewrites it whole. The\n"
                "     previous version is kept alongside it as config.yaml.bak, but\n"
                "     comments and layout you wrote by hand will not survive the\n"
                "     rewrite — only what this screen knows about is written back.",
            )
        else:
            self._set("#setup-warning", "")

    def _show_problems(self, found: list[str] | None = None) -> None:
        found = self.problems() if found is None else found
        if not found:
            self._set("#setup-problems", "")
            return
        lines = ["", "  FIX THESE BEFORE SAVING", ""]
        lines.extend(f"    - {problem}" for problem in found)
        self._set("#setup-problems", "\n".join(lines))

    def _show_hint(self) -> None:
        chosen = self.query_one("#setup-preset", Select).value
        hint = PRESETS[chosen].hint if isinstance(chosen, str) else ""
        self._set("#setup-preset-hint", f"  {hint}" if hint else "")

    def _load_fields(self, model: ModelDraft | None) -> None:
        """Show one model's fields, or empty ones when nothing is selected."""
        self.selected_id = model.id if model else ""
        for selector, value in (
            ("#setup-field-id", model.id if model else ""),
            ("#setup-field-model", model.model if model else ""),
            ("#setup-field-base-url", model.base_url if model else ""),
            ("#setup-field-api-key-env", model.api_key_env if model else ""),
            ("#setup-field-command", model.command if model else ""),
            ("#setup-field-input-price", f"{model.input_price:g}" if model else ""),
            ("#setup-field-output-price", f"{model.output_price:g}" if model else ""),
            ("#setup-field-max-tokens",
             str(model.max_tokens) if model and model.max_tokens is not None else ""),
            ("#setup-field-temperature",
             f"{model.temperature:g}" if model and model.temperature is not None else ""),
        ):
            self._fill(self.query_one(selector, Input), value)
        # A bare model endpoint has no command to run, and offering one invites
        # a config the loader will reject.
        self.query_one("#setup-field-command", Input).display = bool(
            model and model.drives_itself
        )

    # -- events ------------------------------------------------------------

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        actions = {
            "setup-connect": self.connect,
            "setup-apply": self.apply_fields,
            "setup-remove": self.remove_selected,
            "setup-save": self.save,
        }
        action = actions.get(event.button.id or "")
        if action is not None:
            action()

    def on_select_changed(self, event: Select.Changed) -> None:
        event.stop()
        if event.select.id == "setup-preset":
            self._show_hint()
            return
        field = (event.select.id or "").removeprefix("setup-")
        if field not in ROLE_FIELDS:
            return
        value = event.value if isinstance(event.value, str) else None
        # A candidate is a plain string in the draft; the optional roles are None.
        self.draft = replace(
            self.draft, **{field: (value or "") if field == "candidate" else value}
        )
        self._show_problems()

    def on_input_changed(self, event: Input.Changed) -> None:
        event.stop()
        if (event.input.id or "") in ("setup-tests", "setup-judge-tests", "setup-results-dir"):
            self._show_problems()

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        """Load the highlighted model's fields.

        Stopped here so it never reaches the app, whose own handler drives the
        results browser and would repaint it from a list that is not its own.
        """
        event.stop()
        index = event.list_view.index
        if index is None or not 0 <= index < len(self._listed_ids):
            return
        self._load_fields(self.draft.model(self._listed_ids[index]))

    # -- small helpers -----------------------------------------------------

    def _text(self, selector: str) -> str:
        """An input's text, tolerating the widget not existing yet.

        ``Input.Changed`` fires during compose, before the screen is on the
        stack, so anything reached from a handler has to survive a miss.
        """
        try:
            return str(self.query_one(selector, Input).value)
        except NoMatches:
            return ""

    def _fill(self, widget: Input, value: str) -> None:
        """Set an input without letting its Changed message loop back round."""
        with widget.prevent(Input.Changed):
            widget.value = value

    def _set(self, selector: str, text: str) -> None:
        try:
            self.query_one(selector, Static).update(text)
        except NoMatches:
            return

    def _status(self, message: str) -> None:
        self._set("#setup-status", f"\n  {message}")


def _connection_line(model: ModelDraft) -> str:
    """One connected model, as a single line in the list."""
    where = model.base_url or model.api_key_env or ""
    return f"{model.id:<20}{model.kind:<14}{model.model:<24}{where}"
