# Duet Doc Backend

Duet Doc 的 Python/FastAPI 后端，提供云端 AI 网关、SSE 流式响应，以及 PostgreSQL 文档同步接口。浏览器端幽灵文本仍由 WebGPU 本地模型执行。

数据库迁移、认证和同步接口协议分别见 [同步说明](docs/sync.md) 与 [认证说明](docs/auth.md)。

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

## 服务器容器部署

`compose.server.yaml` 在同一个 Compose 项目中运行 FastAPI 和 PostgreSQL + pgvector。数据库不映射宿主机端口，API 只监听宿主机的 `127.0.0.1:8000`，供 1Panel/OpenResty 反向代理使用。

复制 `.env.example` 为 `.env`，至少修改数据库密码、`AUTH_JWT_SECRET`、前端来源和 AI API Key。服务器容器使用 Docker 服务名连接数据库：

```env
DATABASE_URL=postgresql+asyncpg://duet_doc:<password>@postgres:5432/duet_doc
```

首次部署先构建镜像并启动数据库：

```bash
docker compose -f compose.server.yaml build
docker compose -f compose.server.yaml up -d postgres
docker compose -f compose.server.yaml run --rm api alembic upgrade head
docker compose -f compose.server.yaml up -d api
```

验证服务：

```bash
docker compose -f compose.server.yaml ps
curl http://127.0.0.1:8000/api/v1/health
```

更新代码后重新构建 API，并在启动前执行数据库迁移：

```bash
git pull
docker compose -f compose.server.yaml build api
docker compose -f compose.server.yaml run --rm api alembic upgrade head
docker compose -f compose.server.yaml up -d api
```

PostgreSQL 数据保存在 `duet_doc_postgres_data` Volume 中。不要在正常停止或更新时使用 `docker compose down --volumes`，否则会删除数据库卷。

## 检查

```powershell
uv run ruff format --check .
uv run ruff check .
uv run pyright
uv run pytest
```
