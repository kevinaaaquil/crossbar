"""The provider contract.

One method, ``complete``. Everything a harness needs from a model backend goes
through it, so adding a new vendor never touches the runner or the scorer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, Sequence


class ProviderError(RuntimeError):
    """The model backend could not be reached, or answered with something unusable."""


@dataclass(frozen=True)
class Usage:
    """Tokens consumed by one call. Cost is derived from this and the roster price."""

    input_tokens: int = 0
    output_tokens: int = 0

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(
            self.input_tokens + other.input_tokens,
            self.output_tokens + other.output_tokens,
        )

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass(frozen=True)
class ToolCall:
    """A model's request to invoke one tool."""

    id: str
    name: str
    arguments: Mapping[str, Any] = field(default_factory=dict)
    malformed_arguments: str | None = None
    """Raw argument text kept when it would not parse, for failure taxonomy."""


@dataclass(frozen=True)
class ToolDef:
    """A tool as advertised to the model."""

    name: str
    description: str
    input_schema: Mapping[str, Any]


@dataclass(frozen=True)
class Message:
    """One turn of the conversation, in a provider-neutral shape."""

    role: str
    content: str = ""
    tool_calls: Sequence[ToolCall] = ()
    tool_call_id: str | None = None
    is_error: bool = False


@dataclass(frozen=True)
class CompletionRequest:
    messages: Sequence[Message]
    tools: Sequence[ToolDef] = ()
    max_tokens: int = 2048
    temperature: float | None = None


@dataclass(frozen=True)
class CompletionResponse:
    text: str = ""
    tool_calls: Sequence[ToolCall] = ()
    usage: Usage = field(default_factory=Usage)
    stop_reason: str = ""
    raw: Mapping[str, Any] = field(default_factory=dict)


class Provider(Protocol):
    """Anything that can produce the next assistant step."""

    model: str

    def complete(self, request: CompletionRequest) -> CompletionResponse: ...
