from app.schemas.ai import (
    AICapability,
    AIContext,
    AIMessage,
    AIRequest,
    AIToolCall,
    AIToolContinuation,
    AssistantToolName,
    CloudAITask,
    ContextOrigin,
    ContextSourceType,
    MessageRole,
)
from app.services.prompt_registry import build_messages


def test_manual_context_precedes_retrieved_context() -> None:
    request = AIRequest(
        task=CloudAITask.CHAT,
        messages=[AIMessage(role=MessageRole.USER, content="Compare these notes")],
        contexts=[
            AIContext(
                source_id="retrieved-1",
                title="Retrieved note",
                content="Retrieved content",
                source_type=ContextSourceType.MEMO,
                origin=ContextOrigin.LOCAL_RETRIEVAL,
                score=0.99,
            ),
            AIContext(
                source_id="manual-1",
                title="Pinned note",
                content="Manual content",
                origin=ContextOrigin.MANUAL,
                score=0.1,
            ),
        ],
    )

    messages = build_messages(request, max_context_chars=2_000)

    context_message = messages[-2]
    assert context_message.role == MessageRole.USER
    assert context_message.content.index("Pinned note") < context_message.content.index(
        "Retrieved note"
    )


def test_tool_continuation_keeps_tool_result_separate() -> None:
    tool_call = AIToolCall(
        id="call-1",
        name=AssistantToolName.SEARCH_KNOWLEDGE_BASE,
        arguments={"query": "WebGPU"},
        reasoning_content="I should inspect the user's notes.",
    )
    request = AIRequest(
        task=CloudAITask.CHAT,
        messages=[AIMessage(role=MessageRole.USER, content="What did I write about WebGPU?")],
        contexts=[
            AIContext(
                source_id="doc-1",
                title="WebGPU notes",
                content="WebGPU runs inference on local hardware.",
                origin=ContextOrigin.LOCAL_RETRIEVAL,
                chunk_id="doc-1:0",
            )
        ],
        capabilities={AICapability.KNOWLEDGE_SEARCH},
        tool_continuation=AIToolContinuation(tool_call=tool_call),
    )

    messages = build_messages(request, max_context_chars=2_000)

    assert messages[-2].role == MessageRole.ASSISTANT
    assert messages[-2].tool_calls == [tool_call]
    assert messages[-1].role == MessageRole.TOOL
    assert messages[-1].tool_call_id == "call-1"
    assert "WebGPU notes" in messages[-1].content
