"""Rendering results for humans: the verdict card, the matrix, the failures."""

from crossbar.report.text import (
    render_failures,
    render_matrix,
    render_report,
    render_verdict,
    write_markdown,
)

__all__ = [
    "render_failures",
    "render_matrix",
    "render_report",
    "render_verdict",
    "write_markdown",
]
