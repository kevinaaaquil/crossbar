"""Environments held across Attempts, where the Environment says they can be.

`reset: recreate` gives each Attempt its own container, which is correct and
costs a boot every time. An Environment that declares state hooks can be put
back instead of rebuilt: one container for the whole Test, a baseline taken
before any Attempt runs, and that baseline restored after each one.

The pool is what holds the baseline. It has to be something outside the
Environment, because the Environment is the thing being reset -- anything it
kept would be reset along with it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from crossbar.environment import build_environment as _build_environment


@dataclass
class Released:
    """What an Attempt left behind, once it has let the Environment go."""

    state_path: Path | None = None
    """The Attempt's end state, kept as a file."""

    dump: str = ""
    """The same state as text, if the Environment can render it. This is what
    a judge can actually read."""


@dataclass
class Lease:
    """One Attempt's hold on an Environment."""

    spec: Any
    environment: Any
    handle: Any
    hooks: Any = None
    ephemeral: bool = True
    """True when the Environment is this Attempt's alone and dies with it."""


class EnvironmentPool:
    def __init__(
        self,
        state_dir: Path,
        build_environment: Callable[[Any], Any] = _build_environment,
    ) -> None:
        self.state_dir = Path(state_dir)
        self._build = build_environment
        # Keyed by identity: the loader hands every Task that did not override
        # it the same EnvironmentSpec object, which is exactly the set that
        # should share a container. Two specs that merely look alike are two
        # environments.
        self._live: dict[int, tuple[Any, Any, Any, Path]] = {}

    def acquire(self, spec) -> Lease:
        if spec.reset != "hooks" or spec.state is None:
            environment = self._build(spec)
            return Lease(spec, environment, environment.start(), ephemeral=True)

        key = id(spec)
        if key not in self._live:
            environment = self._build(spec)
            handle = environment.start()
            hooks = environment.state_hooks()
            if spec.state.seed:
                # Before the baseline, not after: the baseline has to be the
                # seeded world, not whatever empty store the image shipped.
                hooks.restore(Path(spec.state.seed))
            baseline = hooks.snapshot(self.state_dir / "baseline" / str(key))
            self._live[key] = (environment, handle, hooks, baseline)
        environment, handle, hooks, _ = self._live[key]
        return Lease(spec, environment, handle, hooks=hooks, ephemeral=False)

    def release(self, lease: Lease, attempt_id: str) -> Released:
        """Finish with an Environment, and keep what the Attempt left behind."""
        if lease.ephemeral:
            lease.environment.stop()
            return Released()

        kept = lease.hooks.snapshot(self.state_dir / attempt_id)
        # Before the restore, necessarily: afterwards it would describe the
        # baseline rather than what this Attempt did.
        dumped = lease.hooks.dump()
        # Put the baseline back last: a failure above must not leave the next
        # Attempt starting in this one's world.
        _, _, _, baseline = self._live[id(lease.spec)]
        lease.hooks.restore(baseline)
        return Released(state_path=kept, dump=dumped)

    def close(self) -> None:
        for environment, _, _, _ in self._live.values():
            environment.stop()
        self._live.clear()
