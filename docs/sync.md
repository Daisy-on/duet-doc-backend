# 第一阶段同步接口：中文操作与验证

当前已完成数据库表、开发测试身份、用户隔离和 Push/Pull 接口。前端同步尚未接入，IndexedDB 文档不会自动上传，也没有启用远程向量检索。

## 1. 环境准备（已初始化可跳过）

在后端目录执行，保持 Docker Desktop 运行：

```bash
docker compose up -d
uv sync
uv run alembic upgrade head
```

在 `.env` 配置连接本机 5433 的 `DATABASE_URL`，以及：

```dotenv
APP_ENV=development
DEV_AUTH_ENABLED=true
DEV_USER_ID=00000000-0000-0000-0000-000000000001
```

```bash
uv run python -m app.seed_dev
uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

也可以使用 PyCharm 已配置的运行按钮启动服务，不要同时启动两个占用 8000 端口的服务。初始化命令可以重复执行。

开发身份仅用于本机验证。任何能够访问该开发服务的人都会使用同一个测试用户；正式登录尚未实现，非 development 环境禁止开启此身份。

## 2. 查询工作空间

打开 `http://127.0.0.1:8000/docs`，展开 `GET /api/v1/workspaces`，点击 `Try it out`（试用）和 `Execute`（执行）。查看下方 `Server response` 的实际状态码与响应正文。

返回 `200` 且包含以下内容即成功，说明后端连接数据库、开发身份和工作空间查询正常：

```json
{
  "workspaces": [{
    "id": "00000000-0000-0000-0000-000000000002",
    "name": "Personal"
  }]
}
```

## 3. 上传一个测试知识库和文档

展开 `POST /api/v1/sync/push`，点击 `Try it out`，把请求框替换为以下完整 JSON，再点击 `Execute`：

```json
{
  "workspace_id": "00000000-0000-0000-0000-000000000002",
  "mutation_id": "a5550000-0000-4000-8000-000000000001",
  "operations": [
    {
      "entity_type": "knowledge_base",
      "entity_id": "kb-sync-manual-test",
      "operation": "upsert",
      "base_revision": 0,
      "data": {
        "name": "同步验证知识库",
        "description": "用于手动验证",
        "icon": "#6366f1",
        "created_at": "2026-09-05T00:00:00Z"
      }
    },
    {
      "entity_type": "document",
      "entity_id": "doc-sync-manual-test",
      "operation": "upsert",
      "base_revision": 0,
      "data": {
        "kb_id": "kb-sync-manual-test",
        "group_id": null,
        "title": "第一篇同步测试文档",
        "content": "<p>这是保存到 PostgreSQL 的正文。</p>",
        "content_format": "html",
        "created_at": "2026-09-05T00:00:00Z"
      }
    }
  ]
}
```

期望返回 `200`，results 中两条记录的 revision 都是 `1`。sequence 取决于工作空间之前的操作，不要求固定为 1、2。知识库和文档一起成功或一起回滚。

数据写入 PostgreSQL，暂时不会出现在前端知识库页面中。

## 4. 验证幂等重试

保持第三节请求完全不变，再点击一次 `Execute`。

期望仍返回 `200`，结果与第一次相同，revision 和 sequence 不增加。这证明网络重试不会重复写入。

mutation_id 表示一次业务变更：重试使用相同 ID 和相同内容，新修改必须换新 ID。同一 ID 携带不同内容返回 409。

## 5. 拉取数据

展开 `GET /api/v1/sync/pull`，点击 `Try it out`，填写：

| 参数 | 值 |
| --- | --- |
| workspace_id | `00000000-0000-0000-0000-000000000002` |
| cursor | `0` |
| limit | `100` |

执行后应返回 `200`。在 changes 中查找刚创建的两个 entity_id，文档 snapshot 应包含标题、正文和 revision 1。

- next_cursor：下次拉取使用的位置。
- has_more：是否还有下一页。
- snapshot：变更发生时保存的完整数据。
- group_end：同一组原子变更的结束位置。

将返回的 next_cursor 填入下一次的 cursor，拉取到 has_more 为 false。没有新修改时继续拉取，changes 应为空。不要自行猜测游标，也不能用 Push 返回的 sequence 推进拉取游标，否则可能跳过其他设备的变更。

## 6. 修改文档并验证冲突

回到 Push 接口，替换为：

```json
{
  "workspace_id": "00000000-0000-0000-0000-000000000002",
  "mutation_id": "a5550000-0000-4000-8000-000000000002",
  "operations": [{
    "entity_type": "document",
    "entity_id": "doc-sync-manual-test",
    "operation": "upsert",
    "base_revision": 1,
    "data": {
      "kb_id": "kb-sync-manual-test",
      "group_id": null,
      "title": "测试文档（已修改）",
      "content": "<p>这是第二版正文。</p>",
      "content_format": "html",
      "created_at": "2026-09-05T00:00:00Z"
    }
  }]
}
```

期望 `200`，文档 revision 升至 `2`。继续 Pull 可以看到第二版快照；从 cursor 0 拉取会同时看到以前的变更快照，这是正常的。

然后仅将 mutation_id 改为 `a5550000-0000-4000-8000-000000000003`，仍保留 base_revision 1，再执行。

预期返回 **409**，detail 包含 `REVISION_CONFLICT` 和 current_revision 2。这是测试成功：服务器拒绝旧版本覆盖新版本。如果不换 mutation_id，则属于幂等重试，不会触发这个版本冲突。

## 7. 删除测试内容

完成第六节后，提交：

```json
{
  "workspace_id": "00000000-0000-0000-0000-000000000002",
  "mutation_id": "a5550000-0000-4000-8000-000000000004",
  "operations": [
    {
      "entity_type": "document",
      "entity_id": "doc-sync-manual-test",
      "operation": "delete",
      "base_revision": 2
    },
    {
      "entity_type": "knowledge_base",
      "entity_id": "kb-sync-manual-test",
      "operation": "delete",
      "base_revision": 1
    }
  ]
}
```

期望返回 `200`，再次拉取，最新快照的 deleted_at 非空。这是软删除，保留标记让其他设备知道内容被删除了。如果未执行第六节，文档仍为 revision 1，需要把文档删除的 base_revision 改为 1。

可以先仅提交知识库删除操作进行保护测试：知识库仍有活动文档时应返回 409；之后换一个新的 mutation_id，提交上面的完整删除组。

重复完整教程时，旧 mutation 会返回原结果。若想全新测试，应同时更换实体 ID、文档中的 kb_id 和各步骤的 mutation_id。

## 8. 协议约定

- 接口前缀为 `/api/v1`，字段使用 snake_case。前端毫秒时间戳转换为带时区的 ISO 字符串，现有字符串 ID 保持不变。
- 实体类型为 knowledge_base、group、document。小记继续使用 kb-memo-system 下的文档。
- 分组 data：kb_id、parent_group_id、name、sort_order、depth、created_at。
- 文档 data：kb_id、group_id、title、content、content_format、created_at。正文按字符串保存，格式为 tiptap_json 或 html。
- 服务端保留原创建时间，更新时间由服务器产生。
- 每次 Push 最多 100 个不同实体。新增使用 base_revision 0，更新和删除使用上次确认的 revision；删除不传 data。
- 删除或移动子树时，提交完整影响范围和各自版本。未知活动子记录、过期版本返回 409。超过 100 个实体需拆成每组都合法的操作，例如先删子记录再删父记录；多组之间没有整体原子性。
- Pull 不拆分原子变更组，limit 是目标值，实际最多多出 99 条。前端应在同一 IndexedDB 事务内应用整页并更新游标。
- 快照为写入时的不可变记录，避免后续移动造成早期页面引用未来父分组。当前保留变更日志，尚未实现清理。
- 冲突时保留本地修改，再拉取云端内容供用户选择，不可静默覆盖。
- 图片二进制、历史版本、聊天记录不在本轮同步范围内。

| 状态码 | 含义 |
| --- | --- |
| 200 | 请求成功 |
| 401 | 身份缺失或用户停用 |
| 404 | 工作空间不存在或不属于当前用户 |
| 409 | 版本、层级关系或幂等请求冲突 |
| 422 | 请求字段或格式不合法 |
| 503 | 未配置数据库地址 |

## 9. 自动验证

集成测试依赖已迁移的 PostgreSQL，使用随机用户和工作空间 ID，并回滚或清理自己的数据。

```bash
uv run pytest -q -p no:cacheprovider
uv run ruff check app migrations tests
uv run pyright
```

手动验证重点是第三至第七节。跨用户隔离、用户停用、分组循环、并发重试等边界已有自动测试覆盖。
