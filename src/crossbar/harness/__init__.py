"""The harness: ours, fixed, invisible.

It drives a model through a Task using whatever Connectors are enabled. It never
learns what kind of Connector it is talking to.
"""

from crossbar.harness.loop import Harness, SYSTEM_PROMPT

__all__ = ["Harness", "SYSTEM_PROMPT"]
