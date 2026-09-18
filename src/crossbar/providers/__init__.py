"""Model providers: anything that can turn messages plus tools into a next step."""

from crossbar.providers.base import (
    CompletionRequest,
    CompletionResponse,
    Message,
    Provider,
    ProviderError,
    ToolCall,
    ToolDef,
    Usage,
)
from crossbar.providers.openai_compat import OpenAICompatProvider
from crossbar.providers.anthropic import AnthropicProvider
from crossbar.providers.scripted import ScriptedProvider, scripted_step

__all__ = [
    "AnthropicProvider",
    "CompletionRequest",
    "CompletionResponse",
    "Message",
    "OpenAICompatProvider",
    "Provider",
    "ProviderError",
    "ScriptedProvider",
    "ToolCall",
    "ToolDef",
    "Usage",
    "scripted_step",
]
