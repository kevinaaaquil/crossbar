"""Rendering results for people. Shared by the CLI and the TUI so the two
cannot disagree about what a run said."""

from crossbar.report.text import (
    render_models,
    render_report,
    render_verdict,
    write_markdown,
)

__all__ = ["render_models", "render_report", "render_verdict", "write_markdown"]
