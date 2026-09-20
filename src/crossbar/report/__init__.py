"""Rendering results for people. Shared by the CLI and the TUI so the two
cannot disagree about what a run said."""

from crossbar.report.attempts import attempt_line, render_attempt
from crossbar.report.text import (
    render_models,
    render_report,
    render_verdict,
    write_markdown,
)

__all__ = [
    "attempt_line",
    "render_attempt","render_models", "render_report", "render_verdict", "write_markdown"]
