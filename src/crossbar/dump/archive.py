"""Packing a run directory.

One zip, no variants. It is an output for the user, never an input to scoring —
we define the Task, so we already know what to check.
"""

from __future__ import annotations

import os
import zipfile
from pathlib import Path

DEFAULT_NAME = "dump.zip"


class DumpError(RuntimeError):
    """There was nothing to dump, or it could not be written."""


def create_dump(results_dir: str | os.PathLike[str], destination: str | os.PathLike[str] | None = None) -> Path:
    """Zip everything a run produced and return where it landed."""
    root = Path(results_dir)
    if not root.is_dir():
        raise DumpError(f"{root}: not a directory")
    if not (root / "run.json").exists():
        raise DumpError(f"{root}: no run.json here, so there is no run to dump")

    target = Path(destination) if destination else root / DEFAULT_NAME
    target.parent.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            if path == target or path.name == DEFAULT_NAME:
                continue  # never pack a previous dump into this one
            archive.write(path, path.relative_to(root).as_posix())
    return target
