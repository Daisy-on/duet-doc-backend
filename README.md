# DuetDoc Backend

DuetDoc 的 FastAPI 后端，负责用户认证、工作空间隔离、PostgreSQL 双向同步、云端 AI 网关、私有 OSS 模型交付和文档图片云存储。浏览器端幽灵文本与本地 RAG 仍在前端通过 WebGPU 执行。

## 当前能力

- 用户名密码注册与登录，Argon2id 密码哈希。
- 15 分钟 Access Token 与可轮换的 HttpOnly Refresh Token。
- 用户、工作空间和资源归属校验。
- 知识库、分组、文档、聊天会话与消息的增量 Push/Pull。
- Mutation 幂等、Revision 冲突检测、软删除和同步游标。
- DeepSeek 请求代理及 SSE 流式响应。
- 私有 OSS 端侧模型清单、短期签名 URL、按用户及 IP 限流。
- 私有 OSS 图片上传、校验、访问签名、当前文档引用跟踪和垃圾回收。
- PostgreSQL 17 + pgvector，为后续云端混合检索保留向量能力。

## 技术栈

- Python 3.12、FastAPI、Pydantic Settings
- SQLAlchemy 2、asyncpg、Alembic
- PostgreSQL 17、pgvector 0.8.6
- Alibaba Cloud OSS SDK、ECS RAM Role
- PyJWT、pwdlib/Argon2、sse-starlette
- uv、Ruff、Pyright、Pytest

## 本地开发

### 环境要求

- Python 3.12+
- [uv](https://docs.astral.sh/uv/)
- Docker Desktop 或兼容的 Docker Compose
- DeepSeek API Key（仅调用真实云端 AI 时需要）

### 初始化

```powershell
cd duet-doc-backend
Copy-Item .env.example .env
uv sync
docker compose up -d
uv run alembic upgrade head
```

本地 Compose 只启动 PostgreSQL + pgvector，并绑定到 `127.0.0.1:5433`。默认连接格式为：

```dotenv
DATABASE_URL=postgresql+asyncpg://duet_doc:<POSTGRES_PASSWORD>@127.0.0.1:5433/duet_doc
```

至少修改 `.env` 中的 `POSTGRES_PASSWORD` 与 `AUTH_JWT_SECRET`；使用云端 AI 时填写 `DEEPSEEK_API_KEY`。真实 `.env` 已被 Git 忽略，不要提交密钥。

### 启动 API

```powershell
uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

- OpenAPI：`http://127.0.0.1:8000/docs`
- 健康检查：`http://127.0.0.1:8000/api/v1/health`
- 前端开发地址：`http://localhost:5173`

首次使用直接通过前端注册。`DEV_AUTH_ENABLED` 仅用于本机兼容旧测试数据，正常开发和生产均应设为 `false`。

## 主要配置

完整配置及默认值见 `.env.example`，以下是部署时最重要的几组：

```dotenv
APP_ENV=development
FRONTEND_ORIGINS=http://localhost:5173

POSTGRES_DB=duet_doc
POSTGRES_USER=duet_doc
POSTGRES_PASSWORD=<随机数据库密码>
DATABASE_URL=postgresql+asyncpg://duet_doc:<随机数据库密码>@127.0.0.1:5433/duet_doc

AUTH_JWT_SECRET=<至少32字节的随机值>
AUTH_ACCESS_TOKEN_MINUTES=15
AUTH_REFRESH_TOKEN_DAYS=30

DEEPSEEK_API_KEY=
DEEPSEEK_BASE_URL=https://api.deepseek.com
```

生产环境模型与媒体存储还需要：

```dotenv
OSS_REGION=cn-chengdu
OSS_ENDPOINT=https://oss-cn-chengdu.aliyuncs.com
OSS_BUCKET=<私有模型Bucket>
OSS_MEDIA_BUCKET=<私有图片Bucket>
OSS_ECS_ROLE_NAME=DuetDocModelReaderRole
```

ECS 上通过绑定的 RAM Role 获取自动轮换临时凭证，不要在 `.env` 中保存 AccessKey。模型 Bucket 只需读取权限；媒体 Bucket 需要 `GetObject`、`PutObject` 和 `DeleteObject`，并限制在项目使用的对象前缀。

仓库不附带模型权重，也不向克隆或 Fork 后的部署提供项目维护者的 OSS。自行部署者需要准备自己的私有 Bucket，将模型文件按 `app/services/model_delivery.py` 中的白名单目录与文件名上传，并配置自己的 RAM Role 或等价凭证。前端只向当前连接的后端申请模型清单，不包含固定 OSS 下载地址。

推荐模型来源和精度：

| 用途         | Hugging Face 来源                                                                           | 推荐文件                                                 |
| ------------ | ------------------------------------------------------------------------------------------- | -------------------------------------------------------- |
| 本地语义检索 | [Xenova/bge-large-zh-v1.5](https://huggingface.co/Xenova/bge-large-zh-v1.5)                | `onnx/model_fp16.onnx`（FP16，1024 维）                  |
| 幽灵文本     | [onnx-community/Qwen3.5-0.8B-ONNX](https://huggingface.co/onnx-community/Qwen3.5-0.8B-ONNX) | decoder、embed tokens、vision encoder 的 `_q4f16` 文件组 |

FP16 BGE 与云端 SiliconFlow `BAAI/bge-large-zh-v1.5` 共用文本向量空间；Q4F16 Qwen 更适合浏览器端的下载体积和 WebGPU 内存预算。复制配置、Tokenizer、模板和 ONNX 外部数据文件时，必须保持 `MODEL_CATALOG` 声明的相对路径。建议固定上游 revision；若使用其他版本或精度，需要同步修改前后端模型 ID、文件清单和大小，并重新建立受影响的本地向量索引。下载和再分发前还应阅读对应模型仓库的最新许可证与模型卡。

## API 概览

所有业务接口使用 `/api/v1` 前缀。健康检查、注册、登录和 Token 刷新无需 Access Token，其余业务接口按各自认证约定执行：

```text
Authorization: Bearer <access_token>
```

| 模块           | 接口                                                                                                                                                                   |
| -------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 健康检查       | `GET /health`                                                                                                                                                          |
| 认证           | `POST /auth/register`、`POST /auth/login`、`POST /auth/refresh`、`POST /auth/logout`、`GET /auth/me`                                                                   |
| 工作空间与同步 | `GET /workspaces`、`POST /sync/push`、`GET /sync/pull`、`GET /sync/status`                                                                                             |
| 云端 AI        | `POST /ai/generate`、`POST /ai/stream`                                                                                                                                 |
| 端侧模型       | `GET /models/{model_id}/manifest`                                                                                                                                      |
| 图片媒体       | `POST /workspaces/{workspace_id}/media/uploads`、`POST /workspaces/{workspace_id}/media/{asset_id}/complete`、`GET /workspaces/{workspace_id}/media/{asset_id}/access` |

当前模型白名单：

```text
bge-large-zh-v1.5-fp16
qwen3.5-0.8b-opt-q4f16
```

模型清单默认按用户、模型限制为每小时 3 份、每天 8 份；签名 URL 有效期内会复用同一份清单。注册、登录和清单接口另有进程内 IP 限流，超限返回 `429`。

## 同步与存储边界

- PostgreSQL 保存用户、工作空间、文档结构、正文、聊天、同步日志和媒体元数据。
- pgvector 保存浏览器上传或用户确认后由云端生成的 BGE 文本向量。图片处理需单独同意：`qwen3-vl-flash` 生成描述，再由 BGE 建立图片描述向量；统一回答检索待后续接入。
- OSS 模型 Bucket 保存端侧模型文件，后端只为白名单对象签发短期读取 URL。
- OSS 媒体 Bucket 保存文档图片，正文只保存稳定 `assetId`，不保存签名 URL。
- `document_media_refs` 只表达当前文档引用；浏览器本地历史版本及 Blob 不由云端 GC 删除。
- 收藏和浏览器本地历史版本不进入云同步；用户手动同步时可上传已建立的 BGE 文本索引。

协议和验证细节见：

- [认证说明](docs/auth.md)
- [同步协议](docs/sync.md)
- [图片云存储与垃圾回收](docs/media.md)
- [模型下载限流](docs/model-download-rate-limit.md)

## 服务器容器部署

`compose.server.yaml` 在同一 Compose 项目内运行 FastAPI 与 PostgreSQL + pgvector：

- PostgreSQL 不暴露宿主机端口。
- FastAPI 只绑定宿主机 `127.0.0.1:8000`。
- 公网访问应由 1Panel/OpenResty/Nginx 反向代理提供。
- PostgreSQL 数据保存在 Compose 管理的 `duet_doc_postgres_data` 命名卷中（实际卷名通常带 Compose 项目前缀）。

服务器 `.env` 中的数据库连接由 Compose 覆盖为容器网络地址：

```dotenv
DATABASE_URL=postgresql+asyncpg://duet_doc:<POSTGRES_PASSWORD>@postgres:5432/duet_doc
```

升级到 Alembic `0011` 前先备份数据库并停止旧 `rag-worker`。该迁移会清空旧 E5/Qwen 文本及图片向量、索引任务，改为 1024 维；不会删除文档、原图或同步数据。服务器 `.env` 需设置 `SILICONFLOW_API_KEY`、`RAG_EMBEDDING_MODEL=BAAI/bge-large-zh-v1.5`、`RAG_EMBEDDING_DIMENSION=1024`，图片索引还需 `DASHSCOPE_API_KEY`、`OSS_MEDIA_BUCKET` 和已授权读取图片的 ECS RAM Role。可按 `.env.example` 设置 `RAG_VISION_BASE_URL` 与 `RAG_VISION_MODEL`。图片索引批次沿用现有表，无新增迁移；部署时需要重建 `api` 和 `rag-worker`。

首次部署或服务器切换到 `main` 后执行：

```bash
cd /opt/duet-doc-backend
git fetch origin
git switch main
git pull --ff-only
docker compose -f compose.server.yaml stop rag-worker
docker compose -f compose.server.yaml build api rag-worker
docker compose -f compose.server.yaml up -d postgres
docker compose -f compose.server.yaml run --rm api alembic upgrade head
docker compose -f compose.server.yaml up -d
docker compose -f compose.server.yaml ps
docker compose -f compose.server.yaml exec api alembic current
curl http://127.0.0.1:8000/api/v1/health
```

更新代码时重复构建、迁移和 `up -d` 即可。不要运行 `docker compose down --volumes`，否则会删除数据库卷。

### 媒体配置验证

在已绑定 RAM Role 的 ECS 上执行：

```bash
docker compose -f compose.server.yaml exec api python -m app.media_smoke
```

脚本会生成、上传、校验并删除一张 1x1 PNG，不操作业务图片。完整成功输出应包含“上传成功”“签名读取成功”“禁止覆盖验证成功”和“测试图片已删除”。

### 图片垃圾回收

先预览候选，确认后再执行：

```bash
docker compose -f compose.server.yaml exec api python -m app.media_gc --dry-run
docker compose -f compose.server.yaml exec api python -m app.media_gc --execute
```

默认回收连续 7 天没有当前文档引用的 `ready` 图片，以及超过 24 小时未完成的 `pending` 上传。正式执行会同时删除 OSS 对象和数据库记录；建议通过 1Panel 定时任务每天执行一次，并保留运行日志。

## 质量检查

```powershell
uv run ruff format --check .
uv run ruff check .
uv run pyright
uv run pytest
```

数据库结构统一由 Alembic 管理。`docker/postgres/init.sql` 只负责首次创建 pgvector 扩展，不用于维护业务表结构。
