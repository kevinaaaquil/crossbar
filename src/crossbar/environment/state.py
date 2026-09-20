"""Driving an Environment's state hooks.

The store-specific part of resetting an Environment lives in two executables
the Environment provides -- snapshot and restore. Everything here is about
calling them correctly, which is the same whether the state is SQLite, a
Postgres dump or a tarball of documents, and whether the hooks run in a
container or on this machine.

What makes this worth having over `reset: recreate` is not only that a file
copy is cheaper than a container boot. It is that the state an Attempt produced
becomes an artefact crossbar keeps: something to grade, to re-grade later, or
to start a container from when reproducing a failure.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Protocol

from crossbar.domain import StateSpec
from crossbar.environment.base import EnvironmentError_


class Files(Protocol):
    """Moving one file between here and wherever the Environment keeps state."""

    def copy_out(self, remote: str, local: Path) -> None: ...
    def copy_in(self, local: Path, remote_dir: str) -> None: ...
    def clear(self, remote_dir: str) -> None: ...


class StateHooks:
    """One Environment's snapshot and restore hooks."""

    def __init__(self, spec: StateSpec, run, files: Files) -> None:
        self.spec = spec
        self.run = run
        self.files = files

    def snapshot(self, into_dir: Path) -> Path:
        """Take a copy of the live state and bring it out into ``into_dir``.

        The file keeps the name the hook gave it. crossbar cannot know what
        extension this store's state carries -- .db, .dump, .tar -- and a name
        it invented would lie about the format to whatever opens it next.
        """
        printed = self.run([self.spec.snapshot], "the snapshot hook")
        remote = _last_line(printed)
        if not remote:
            raise EnvironmentError_(
                f"the snapshot hook {self.spec.snapshot!r} printed no path; it must "
                "print the file it wrote as its last line of output"
            )
        into_dir = Path(into_dir)
        into_dir.mkdir(parents=True, exist_ok=True)
        local = into_dir / PurePosixPath(remote).name
        self.files.copy_out(remote, local)
        return local

    def restore(self, source: Path) -> None:
        """Install ``source`` as the live state.

        The hook takes the first file in the snapshot directory by name, so the
        directory is emptied first: leaving exactly one file there is what makes
        "first" mean this one. Clearing is the caller's job on purpose -- a hook
        that tidied the directory would destroy the restore points it was asked
        to read from.
        """
        if not Path(source).exists():
            raise EnvironmentError_(f"cannot restore from {source}: it does not exist")
        self.files.clear(self.spec.snapshot_dir)
        self.files.copy_in(Path(source), self.spec.snapshot_dir)
        self.run([self.spec.restore], "the restore hook")


def _last_line(text: str) -> str:
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    return lines[-1] if lines else ""
