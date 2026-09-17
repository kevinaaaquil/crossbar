"""The Anthropic Messages API, for running Claude models as a bare backend.

Distinct from the Claude Code harness: this is the model on its own, which is
the control condition you need if you want to attribute a score to the wrapper.
"""

from __future__ import annotations

from typing import Any, Sequence

from crossbar.providers.base import (
    CompletionRequest,
    CompletionResponse,
    Message,
    ProviderError,
    ToolCall,
    Usage,
)
from crossbar.providers.http_base import HttpProvider

API_VERSION = "2023-06-01"
DEFAULT_BASE_URL = "https://api.anthropic.com"


class AnthropicProvider(HttpProvider):
    def __init__(self, model: str, base_url: str = DEFAULT_BASE_URL, **kwargs) -> None:
        super().__init__(model=model, base_url=base_url, **kwargs)

    def complete(self, request: CompletionRequest) -> CompletionResponse:
        system, messages = _split_system(request.messages)
        payload: dict[str, Any] = {
            "model": self.model,
            "max_tokens": request.max_tokens,
            "messages": messages,
        }
        if system:
            payload["system"] = system
        if request.tools:
            payload["tools"] = [
                {
                    "name": t.name,
                    "description": t.description,
                    "input_schema": dict(t.input_schema) or {"type": "object", "properties": {}},
                }
                for t in request.tools
            ]
        if request.temperature is not None:
            payload["temperature"] = request.temperature

        headers = {
            "content-type": "application/json",
            "anthropic-version": API_VERSION,
            **self.extra_headers,
        }
        if self.api_key:
            headers["x-api-key"] = self.api_key

        body = self._post("/v1/messages", payload, headers)
        if "content" not in body:
            raise ProviderError(f"{self.model}: response had no 'content'")
        return _decode(body)


def _split_system(messages: Sequence[Message]) -> tuple[str, list[dict[str, Any]]]:
    """Anthropic takes the system prompt as a top-level field, not a message."""
    system_parts: list[str] = []
    encoded: list[dict[str, Any]] = []
    for message in messages:
        if message.role == "system":
            system_parts.append(message.content)
            continue
        if message.role == "tool":
            block = {
                "type": "tool_result",
                "tool_use_id": message.tool_call_id or "",
                "content": message.content,
            }
            if message.is_error:
                block["is_error"] = True
            # Consecutive tool results belong in one user turn.
            if encoded and encoded[-1]["role"] == "user" and isinstance(encoded[-1]["content"], list):
                encoded[-1]["content"].append(block)
            else:
                encoded.append({"role": "user", "content": [block]})
            continue
        if message.role == "assistant" and message.tool_calls:
            blocks: list[dict[str, Any]] = []
            if message.content:
                blocks.append({"type": "text", "text": message.content})
            blocks.extend(
                {
                    "type": "tool_use",
                    "id": call.id,
                    "name": call.name,
                    "input": dict(call.arguments),
                }
                for call in message.tool_calls
            )
            encoded.append({"role": "assistant", "content": blocks})
            continue
        encoded.append({"role": message.role, "content": message.content})
    return "\n\n".join(p for p in system_parts if p), encoded


def _decode(body: dict) -> CompletionResponse:
    text_parts: list[str] = []
    calls: list[ToolCall] = []
    for block in body.get("content") or []:
        if block.get("type") == "text":
            text_parts.append(block.get("text", ""))
        elif block.get("type") == "tool_use":
            calls.append(
                ToolCall(
                    id=str(block.get("id") or f"tu_{len(calls)}"),
                    name=str(block.get("name") or ""),
                    arguments=dict(block.get("input") or {}),
                )
            )
    usage = body.get("usage") or {}
    return CompletionResponse(
        text="".join(text_parts),
        tool_calls=calls,
        usage=Usage(
            input_tokens=int(usage.get("input_tokens") or 0),
            output_tokens=int(usage.get("output_tokens") or 0),
        ),
        stop_reason=str(body.get("stop_reason") or ""),
        raw=body,
    )
