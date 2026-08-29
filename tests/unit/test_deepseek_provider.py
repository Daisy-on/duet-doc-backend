import json

import httpx
import respx
from pydantic import SecretStr

from app.core.config import Settings
from app.providers.deepseek import DeepSeekProvider
from app.schemas.ai import (
    AICapability,
    AIMessage,
    AIRequest,
    CloudAITask,
    MessageRole,
    StreamEventType,
    ToolChoice,
)


def make_request() -> AIRequest:
    return AIRequest(
        request_id="request-1",
        task=CloudAITask.CHAT,
        messages=[AIMessage(role=MessageRole.USER, content="hello")],
    )


async def test_generate_maps_deepseek_response() -> None:
    settings = Settings(deepseek_api_key=SecretStr("test-key"))
    endpoint = "https://api.deepseek.com/chat/completions"
    response_body = {
        "choices": [
            {
                "message": {"content": "hello back", "reasoning_content": "thinking"},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5},
    }

    async with httpx.AsyncClient() as client:
        provider = DeepSeekProvider(settings, client)
        with respx.mock:
            respx.post(endpoint).mock(return_value=httpx.Response(200, json=response_body))
            result = await provider.generate(make_request(), "deepseek-v4-flash")

    assert result.text == "hello back"
    assert result.reasoning_text == "thinking"
    assert result.usage is not None
    assert result.usage.total_tokens == 5


async def test_stream_maps_text_and_finish_events() -> None:
    settings = Settings(deepseek_api_key=SecretStr("test-key"))
    endpoint = "https://api.deepseek.com/chat/completions"
    chunks = [
        {"choices": [{"delta": {"content": "hello"}, "finish_reason": None}]},
        {"choices": [{"delta": {}, "finish_reason": "stop"}]},
    ]
    stream_body = "".join(f"data: {json.dumps(chunk)}\n\n" for chunk in chunks)
    stream_body += "data: [DONE]\n\n"

    async with httpx.AsyncClient() as client:
        provider = DeepSeekProvider(settings, client)
        with respx.mock:
            respx.post(endpoint).mock(return_value=httpx.Response(200, text=stream_body))
            events = [event async for event in provider.stream(make_request(), "model")]

    assert [event.event for event in events] == [
        StreamEventType.START,
        StreamEventType.TEXT_DELTA,
        StreamEventType.FINISH,
    ]
    assert events[1].text == "hello"
    assert events[-1].finish_reason == "stop"


async def test_stream_maps_validated_knowledge_search_tool_call() -> None:
    settings = Settings(deepseek_api_key=SecretStr("test-key"))
    endpoint = "https://api.deepseek.com/chat/completions"
    request = AIRequest(
        request_id="request-tool-1",
        task=CloudAITask.CHAT,
        messages=[AIMessage(role=MessageRole.USER, content="Find my WebGPU notes")],
        capabilities={AICapability.KNOWLEDGE_SEARCH},
        tool_choice=ToolChoice.AUTO,
    )
    chunks = [
        {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "call-1",
                                "type": "function",
                                "function": {
                                    "name": "search_knowledge_base",
                                    "arguments": '{"query":"Web',
                                },
                            }
                        ]
                    },
                    "finish_reason": None,
                }
            ]
        },
        {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {
                                "index": 0,
                                "function": {"arguments": 'GPU","top_k":3}'},
                            }
                        ]
                    },
                    "finish_reason": "tool_calls",
                }
            ]
        },
    ]
    stream_body = "".join(f"data: {json.dumps(chunk)}\n\n" for chunk in chunks)
    stream_body += "data: [DONE]\n\n"

    async with httpx.AsyncClient() as client:
        provider = DeepSeekProvider(settings, client)
        with respx.mock:
            route = respx.post(endpoint).mock(return_value=httpx.Response(200, text=stream_body))
            events = [event async for event in provider.stream(request, "model")]

    assert route.calls[0].request.content
    assert [event.event for event in events] == [
        StreamEventType.START,
        StreamEventType.TOOL_CALL,
        StreamEventType.FINISH,
    ]
    assert events[1].tool_call is not None
    assert events[1].tool_call.arguments == {
        "query": "WebGPU",
        "top_k": 3,
        "source_types": [],
        "sort_by": "relevance",
    }
    assert events[-1].finish_reason == "tool_calls"
