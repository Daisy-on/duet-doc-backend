from app.core.exceptions import AIServiceError
from app.schemas.ai import (
    AIMessage,
    AIRequest,
    CloudAITask,
    MessageRole,
)

SYSTEM_PROMPTS: dict[CloudAITask, str] = {
    CloudAITask.CHAT: "你是 Duet Doc 的写作助手。请根据用户提供的信息清晰、准确地回答。",
    CloudAITask.REWRITE: "你是文本改写助手。只输出改写后的正文，不解释修改过程。",
    CloudAITask.EXPAND: "你是文本扩写助手。保持原意和语气，补充必要细节，只输出扩写结果。",
    CloudAITask.EXPLAIN: "你是文本解释助手。使用清晰、易懂的语言解释给定内容。",
    CloudAITask.SUMMARIZE: "你是文档总结助手。提炼核心观点，避免添加原文没有的信息。",
}


def build_messages(request: AIRequest, max_context_chars: int) -> list[AIMessage]:
    context_parts = [
        f"[来源：{context.title} | ID：{context.source_id}]\n{context.content}"
        for context in request.contexts
    ]
    context_text = "\n\n".join(context_parts)
    if len(context_text) > max_context_chars:
        raise AIServiceError(
            "CONTEXT_TOO_LARGE",
            "Referenced document context is too large.",
            status_code=413,
        )

    messages = [
        AIMessage(role=MessageRole.SYSTEM, content=SYSTEM_PROMPTS[request.task]),
    ]
    if context_text:
        messages.append(
            AIMessage(
                role=MessageRole.SYSTEM,
                content=f"以下是可供参考的文档内容：\n\n{context_text}",
            )
        )

    if request.task == CloudAITask.CHAT:
        messages.extend(
            message for message in request.messages if message.role != MessageRole.SYSTEM
        )
        return messages

    instruction = request.instruction or SYSTEM_PROMPTS[request.task]
    source_text = request.selected_text or context_text
    messages.append(
        AIMessage(
            role=MessageRole.USER,
            content=f"指令：{instruction}\n\n待处理内容：\n{source_text}",
        )
    )
    return messages
