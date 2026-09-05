# Duet Doc Backend

Duet Doc 的 Python/FastAPI 后端，提供云端 AI 网关、SSE 流式响应，以及 PostgreSQL 文档同步接口。浏览器端幽灵文本仍由 WebGPU 本地模型执行。

数据库迁移、开发测试身份和同步接口协议见 [同步说明](docs/sync.md)。同步功能目前仅用于本机开发验证，前端接入将在后续批次完成。

## 环境要求

- Python 3.12+
- uv
- DeepSeek API Key（调用真实云端模型时需要）

## 初始化

```powershell
uv sync
Copy-Item .env.example .env
```

在 `.env` 中填写 `DEEPSEEK_API_KEY`。真实 `.env` 已被 Git 忽略。

## 启动

```powershell
uv run uvicorn app.main:app --reload
```

服务地址：`http://127.0.0.1:8000`

- OpenAPI：`http://127.0.0.1:8000/docs`
- 健康检查：`http://127.0.0.1:8000/api/v1/health`

## API

```text
POST /api/v1/ai/generate
POST /api/v1/ai/stream
```

请求示例：

```json
{
  "task": "chat",
  "messages": [
    { "role": "user", "content": "请帮我整理这段内容" }
  ],
  "options": {
    "thinking": false,
    "maxTokens": 2048,
    "temperature": 0.5
  }
}
```

流式接口使用 SSE，事件包括 `start`、`reasoning_delta`、`text_delta`、`usage`、`finish` 和 `error`。

## 检查

```powershell
uv run ruff format --check .
uv run ruff check .
uv run pyright
uv run pytest
```
