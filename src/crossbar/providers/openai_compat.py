"""Any endpoint that speaks the OpenAI chat-completions API.

That covers vLLM, Ollama, LM Studio, Together, Fireworks, OpenRouter and most
self-hosted fine-tunes, which is the point: one adapter, every local model.
"""

from __future__ import annotations

import json
from typing import Any

from crossbar.providers.base import (
    CompletionRequest,
    CompletionResponse,
    Message,
    ProviderError,
    ToolCall,
    Usage,
)
from crossbar.providers.http_base import HttpProvider


class OpenAICompatProvider(HttpProvider):
    def complete(self, request: CompletionRequest) -> CompletionResponse:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [_encode_message(m) for m in request.messages],
            "max_tokens": request.max_tokens,
        }
        if request.tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": dict(t.input_schema) or {"type": "object", "properties": {}},
                    },
                }
                for t in request.tools
            ]
            payload["tool_choice"] = "auto"
        if request.temperature is not None:
            payload["temperature"] = request.temperature

        headers = {"content-type": "application/json", **self.extra_headers}
        if self.api_key:
            headers["authorization"] = f"Bearer {self.api_key}"

        body = self._post("/chat/completions", payload, headers)
        choices = body.get("choices")
        if not choices:
            raise ProviderError(f"{self.model}: response had no 'choices'")
        return _decode(body, choices[0])


def _encode_message(message: Message) -> dict[str, Any]:
    if message.role == "tool":
        return {
            "role": "tool",
            "content": message.content,
            "tool_call_id": message.tool_call_id or "",
        }
    encoded: dict[str, Any] = {"role": message.role, "content": message.content}
    if message.tool_calls:
        encoded["tool_calls"] = [
            {
                "id": call.id,
                "type": "function",
                "function": {
                    "name": call.name,
                    "arguments": call.malformed_arguments
                    if call.malformed_arguments is not None
                    else json.dumps(dict(call.arguments)),
                },
            }
            for call in message.tool_calls
        ]
    return encoded


def _decode(body: dict, choice: dict) -> CompletionResponse:
    message = choice.get("message") or {}
    usage = body.get("usage") or {}
    calls = []
    for raw in message.get("tool_calls") or []:
        function = raw.get("function") or {}
        arguments_text = function.get("arguments") or "{}"
        try:
            arguments = json.loads(arguments_text) if arguments_text.strip() else {}
            malformed = None
            if not isinstance(arguments, dict):
                arguments, malformed = {}, arguments_text
        except json.JSONDecodeError:
            arguments, malformed = {}, arguments_text
        calls.append(
            ToolCall(
                id=str(raw.get("id") or f"call_{len(calls)}"),
                name=str(function.get("name") or ""),
                arguments=arguments,
                malformed_arguments=malformed,
            )
        )
    return CompletionResponse(
        text=message.get("content") or "",
        tool_calls=calls,
        usage=Usage(
            input_tokens=int(usage.get("prompt_tokens") or 0),
            output_tokens=int(usage.get("completion_tokens") or 0),
        ),
        stop_reason=str(choice.get("finish_reason") or ""),
        raw=body,
    )
