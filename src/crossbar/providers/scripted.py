"""A provider that replays a fixed script.

This is what lets the rest of the system be tested for real: a scripted model
drives genuine MCP servers through a genuine harness loop, so every component
downstream of the model is exercised without a single network call.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from crossbar.providers.base import (
    CompletionRequest,
    CompletionResponse,
    ProviderError,
    ToolCall,
    Usage,
)


@dataclass(frozen=True)
class ScriptedStep:
    text: str = ""
    tool_calls: Sequence[tuple[str, Mapping[str, Any]]] = ()
    usage: Usage = field(default_factory=lambda: Usage(10, 5))
    error: str | None = None
    stop_reason: str = ""


def scripted_step(
    text: str = "",
    tool_calls: Sequence[tuple[str, Mapping[str, Any]]] = (),
    usage: Usage | None = None,
    error: str | None = None,
) -> ScriptedStep:
    """Build one step of a scripted model run."""
    return ScriptedStep(
        text=text,
        tool_calls=tuple(tool_calls),
        usage=usage or Usage(10, 5),
        error=error,
        stop_reason="tool_calls" if tool_calls else "stop",
    )


class ScriptedProvider:
    def __init__(self, script: Sequence[ScriptedStep], model: str = "scripted") -> None:
        self.model = model
        self.script = list(script)
        self.requests: list[CompletionRequest] = []
        self._index = 0
        self._call_counter = 0

    def complete(self, request: CompletionRequest) -> CompletionResponse:
        self.requests.append(request)
        if self._index >= len(self.script):
            raise ProviderError(
                f"scripted provider ran off the end of its script after {self._index} steps"
            )
        step = self.script[self._index]
        self._index += 1
        if step.error:
            raise ProviderError(step.error)

        calls = []
        for name, arguments in step.tool_calls:
            self._call_counter += 1
            calls.append(ToolCall(id=f"sc_{self._call_counter}", name=name, arguments=dict(arguments)))
        return CompletionResponse(
            text=step.text,
            tool_calls=calls,
            usage=step.usage,
            stop_reason=step.stop_reason,
        )

    @property
    def steps_used(self) -> int:
        return self._index
