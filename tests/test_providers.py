"""Model providers: request shaping, response parsing, usage, retries."""
import json

import httpx
import pytest

from crossbar.providers import (
    AnthropicProvider,
    CompletionRequest,
    Message,
    OpenAICompatProvider,
    ProviderError,
    ScriptedProvider,
    ToolCall,
    ToolDef,
    Usage,
    scripted_step,
)

TOOLS = [
    ToolDef(
        name="notes__create_note",
        description="Create a note",
        input_schema={"type": "object", "properties": {"title": {"type": "string"}}},
    )
]


def request(**kwargs) -> CompletionRequest:
    base = dict(
        messages=[Message(role="user", content="make a note")],
        tools=TOOLS,
        max_tokens=512,
    )
    base.update(kwargs)
    return CompletionRequest(**base)


def openai_response(**overrides):
    body = {
        "id": "chatcmpl-1",
        "choices": [{"message": {"role": "assistant", "content": "all done"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 11, "completion_tokens": 3, "total_tokens": 14},
    }
    body.update(overrides)
    return body


def make_openai(handler, **kwargs) -> OpenAICompatProvider:
    transport = httpx.MockTransport(handler)
    return OpenAICompatProvider(
        model="qwen3.6-plus",
        base_url="https://example.invalid/v1",
        api_key="sk-test",
        transport=transport,
        **kwargs,
    )


class TestOpenAIRequestShaping:
    def test_posts_to_the_chat_completions_endpoint(self):
        seen = {}

        def handler(req: httpx.Request) -> httpx.Response:
            seen["url"] = str(req.url)
            return httpx.Response(200, json=openai_response())

        make_openai(handler).complete(request())
        assert seen["url"] == "https://example.invalid/v1/chat/completions"

    def test_sends_the_bearer_token(self):
        seen = {}

        def handler(req):
            seen["auth"] = req.headers.get("authorization")
            return httpx.Response(200, json=openai_response())

        make_openai(handler).complete(request())
        assert seen["auth"] == "Bearer sk-test"

    def test_sends_the_model_id_and_messages(self):
        seen = {}

        def handler(req):
            seen["body"] = json.loads(req.content)
            return httpx.Response(200, json=openai_response())

        make_openai(handler).complete(request())
        assert seen["body"]["model"] == "qwen3.6-plus"
        assert seen["body"]["messages"] == [{"role": "user", "content": "make a note"}]

    def test_tools_are_sent_in_openai_function_format(self):
        seen = {}

        def handler(req):
            seen["body"] = json.loads(req.content)
            return httpx.Response(200, json=openai_response())

        make_openai(handler).complete(request())
        tool = seen["body"]["tools"][0]
        assert tool["type"] == "function"
        assert tool["function"]["name"] == "notes__create_note"
        assert tool["function"]["parameters"]["properties"]["title"]["type"] == "string"

    def test_no_tools_key_when_the_task_has_no_tools(self):
        seen = {}

        def handler(req):
            seen["body"] = json.loads(req.content)
            return httpx.Response(200, json=openai_response())

        make_openai(handler).complete(request(tools=[]))
        assert "tools" not in seen["body"]

    def test_assistant_tool_calls_are_serialised_back(self):
        seen = {}

        def handler(req):
            seen["body"] = json.loads(req.content)
            return httpx.Response(200, json=openai_response())

        messages = [
            Message(role="user", content="go"),
            Message(
                role="assistant",
                content="",
                tool_calls=[ToolCall(id="c1", name="notes__create_note", arguments={"title": "x"})],
            ),
            Message(role="tool", content="created", tool_call_id="c1"),
        ]
        make_openai(handler).complete(request(messages=messages))
        assistant = seen["body"]["messages"][1]
        assert assistant["tool_calls"][0]["function"]["name"] == "notes__create_note"
        assert json.loads(assistant["tool_calls"][0]["function"]["arguments"]) == {"title": "x"}
        assert seen["body"]["messages"][2] == {
            "role": "tool",
            "content": "created",
            "tool_call_id": "c1",
        }


class TestOpenAIResponseParsing:
    def test_plain_text_response_is_returned(self):
        provider = make_openai(lambda req: httpx.Response(200, json=openai_response()))
        result = provider.complete(request())
        assert result.text == "all done"
        assert result.tool_calls == []

    def test_usage_is_parsed(self):
        provider = make_openai(lambda req: httpx.Response(200, json=openai_response()))
        result = provider.complete(request())
        assert result.usage == Usage(input_tokens=11, output_tokens=3)

    def test_tool_calls_are_parsed_with_decoded_arguments(self):
        body = openai_response(
            choices=[
                {
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "type": "function",
                                "function": {
                                    "name": "notes__create_note",
                                    "arguments": '{"title": "Q3"}',
                                },
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ]
        )
        provider = make_openai(lambda req: httpx.Response(200, json=body))
        result = provider.complete(request())
        assert result.tool_calls == [
            ToolCall(id="call_1", name="notes__create_note", arguments={"title": "Q3"})
        ]
        assert result.stop_reason == "tool_calls"

    def test_malformed_tool_arguments_do_not_crash_the_run(self):
        body = openai_response(
            choices=[
                {
                    "message": {
                        "role": "assistant",
                        "tool_calls": [
                            {"id": "c", "function": {"name": "x", "arguments": "{not json"}}
                        ],
                    }
                }
            ]
        )
        provider = make_openai(lambda req: httpx.Response(200, json=body))
        call = provider.complete(request()).tool_calls[0]
        assert call.arguments == {}
        assert call.malformed_arguments == "{not json"

    def test_missing_usage_block_yields_zero_usage(self):
        body = openai_response()
        del body["usage"]
        provider = make_openai(lambda req: httpx.Response(200, json=body))
        assert provider.complete(request()).usage == Usage(0, 0)


class TestOpenAIErrors:
    def test_http_error_raises_provider_error_with_status(self):
        provider = make_openai(lambda req: httpx.Response(400, json={"error": {"message": "bad"}}))
        with pytest.raises(ProviderError, match="400"):
            provider.complete(request())

    def test_rate_limit_is_retried_then_succeeds(self):
        calls = {"n": 0}

        def handler(req):
            calls["n"] += 1
            if calls["n"] < 3:
                return httpx.Response(429, json={"error": "slow down"})
            return httpx.Response(200, json=openai_response())

        provider = make_openai(handler, max_retries=3, sleep=lambda _: None)
        assert provider.complete(request()).text == "all done"
        assert calls["n"] == 3

    def test_retries_are_bounded(self):
        calls = {"n": 0}

        def handler(req):
            calls["n"] += 1
            return httpx.Response(503, text="down")

        provider = make_openai(handler, max_retries=2, sleep=lambda _: None)
        with pytest.raises(ProviderError):
            provider.complete(request())
        assert calls["n"] == 3  # initial attempt plus two retries

    def test_client_errors_are_not_retried(self):
        calls = {"n": 0}

        def handler(req):
            calls["n"] += 1
            return httpx.Response(401, text="nope")

        provider = make_openai(handler, max_retries=3, sleep=lambda _: None)
        with pytest.raises(ProviderError, match="401"):
            provider.complete(request())
        assert calls["n"] == 1

    def test_non_json_response_raises_provider_error(self):
        provider = make_openai(lambda req: httpx.Response(200, text="<html>oops</html>"))
        with pytest.raises(ProviderError, match="JSON"):
            provider.complete(request())

    def test_response_without_choices_raises(self):
        provider = make_openai(lambda req: httpx.Response(200, json={"usage": {}}))
        with pytest.raises(ProviderError, match="choices"):
            provider.complete(request())


class TestAnthropicProvider:
    def make(self, handler, **kwargs):
        return AnthropicProvider(
            model="claude-opus-5",
            api_key="sk-ant-test",
            transport=httpx.MockTransport(handler),
            **kwargs,
        )

    def anthropic_body(self, **overrides):
        body = {
            "id": "msg_1",
            "content": [{"type": "text", "text": "done"}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 20, "output_tokens": 5},
        }
        body.update(overrides)
        return body

    def test_uses_the_messages_endpoint_and_api_key_header(self):
        seen = {}

        def handler(req):
            seen["url"] = str(req.url)
            seen["key"] = req.headers.get("x-api-key")
            seen["version"] = req.headers.get("anthropic-version")
            return httpx.Response(200, json=self.anthropic_body())

        self.make(handler).complete(request())
        assert seen["url"].endswith("/v1/messages")
        assert seen["key"] == "sk-ant-test"
        assert seen["version"]

    def test_system_message_is_hoisted_out_of_the_message_list(self):
        seen = {}

        def handler(req):
            seen["body"] = json.loads(req.content)
            return httpx.Response(200, json=self.anthropic_body())

        messages = [Message(role="system", content="be terse"), Message(role="user", content="hi")]
        self.make(handler).complete(request(messages=messages))
        assert seen["body"]["system"] == "be terse"
        assert [m["role"] for m in seen["body"]["messages"]] == ["user"]

    def test_tools_use_the_anthropic_schema_shape(self):
        seen = {}

        def handler(req):
            seen["body"] = json.loads(req.content)
            return httpx.Response(200, json=self.anthropic_body())

        self.make(handler).complete(request())
        assert seen["body"]["tools"][0]["name"] == "notes__create_note"
        assert seen["body"]["tools"][0]["input_schema"]["type"] == "object"

    def test_tool_use_blocks_are_parsed(self):
        body = self.anthropic_body(
            content=[
                {"type": "text", "text": "calling"},
                {"type": "tool_use", "id": "tu_1", "name": "notes__create_note", "input": {"title": "Q3"}},
            ],
            stop_reason="tool_use",
        )
        result = self.make(lambda req: httpx.Response(200, json=body)).complete(request())
        assert result.text == "calling"
        assert result.tool_calls == [
            ToolCall(id="tu_1", name="notes__create_note", arguments={"title": "Q3"})
        ]

    def test_tool_results_are_sent_as_user_content_blocks(self):
        seen = {}

        def handler(req):
            seen["body"] = json.loads(req.content)
            return httpx.Response(200, json=self.anthropic_body())

        messages = [
            Message(role="user", content="go"),
            Message(
                role="assistant",
                content="",
                tool_calls=[ToolCall(id="tu_1", name="notes__create_note", arguments={})],
            ),
            Message(role="tool", content="created", tool_call_id="tu_1"),
        ]
        self.make(handler).complete(request(messages=messages))
        last = seen["body"]["messages"][-1]
        assert last["role"] == "user"
        assert last["content"][0]["type"] == "tool_result"
        assert last["content"][0]["tool_use_id"] == "tu_1"

    def test_usage_is_parsed(self):
        provider = self.make(lambda req: httpx.Response(200, json=self.anthropic_body()))
        assert provider.complete(request()).usage == Usage(20, 5)


class TestScriptedProvider:
    def test_returns_scripted_steps_in_order(self):
        provider = ScriptedProvider(
            [
                scripted_step(tool_calls=[("notes__create_note", {"title": "a"})]),
                scripted_step(text="finished"),
            ]
        )
        first = provider.complete(request())
        assert first.tool_calls[0].name == "notes__create_note"
        assert provider.complete(request()).text == "finished"

    def test_records_every_request_it_received(self):
        provider = ScriptedProvider([scripted_step(text="ok")])
        provider.complete(request())
        assert provider.requests[0].messages[0].content == "make a note"

    def test_running_off_the_end_of_the_script_raises(self):
        provider = ScriptedProvider([scripted_step(text="ok")])
        provider.complete(request())
        with pytest.raises(ProviderError, match="script"):
            provider.complete(request())

    def test_reports_synthetic_usage_so_budgets_can_be_tested(self):
        provider = ScriptedProvider([scripted_step(text="ok", usage=Usage(100, 25))])
        assert provider.complete(request()).usage == Usage(100, 25)

    def test_can_raise_a_provider_error_on_a_given_step(self):
        provider = ScriptedProvider([scripted_step(error="upstream exploded")])
        with pytest.raises(ProviderError, match="upstream exploded"):
            provider.complete(request())

    def test_tool_call_ids_are_unique(self):
        provider = ScriptedProvider(
            [scripted_step(tool_calls=[("a", {}), ("b", {})])]
        )
        calls = provider.complete(request()).tool_calls
        assert calls[0].id != calls[1].id
