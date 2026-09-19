"""Agents: anything that can execute a Task and return a Trajectory.

The Harness is one implementation, not the only path. An external agent CLI
brings its own loop and still satisfies this protocol, so evidence capture,
judging and statistics never learn the difference.
"""

from crossbar.agents.base import Agent
from crossbar.agents.cli_agent import CliAgent
from crossbar.agents.model_agent import ModelAgent

__all__ = ["Agent", "CliAgent", "ModelAgent"]
