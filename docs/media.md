# 图片云存储

后端提供私有 OSS 媒体接口，前端仅在用户手动同步时上传当前正文实际引用的图片。
正文始终保存稳定的 `assetId`，不保存临时签名 URL；新客户端缺少本地 Blob 时会从云端取回并写入 IndexedDB。

## 在哪里验证

媒体接口的自动化测试已经在本地完成。OSS 和 RAM 角色只能在绑定了
`DuetDocModelReaderRole` 的阿里云 ECS 上验证，因此下面所有命令都应在
**FinalShell 连接到服务器后打开的终端**中执行，不是在 Windows PowerShell、WSL 或
Docker Desktop 终端中执行。

你不需要手工创建、下载或上传测试图片。仓库中的 `app.media_smoke` 会在内存中生成一张
1x1 像素的 PNG，通过签名 URL 上传到 OSS，验证读取和禁止覆盖，最后自动删除它。

## 部署并验证

### 1. 先让服务器取得包含媒体功能的代码

本地改动提交并推送后，在服务器的 FinalShell 终端执行：

```bash
cd /opt/duet-doc-backend
git fetch origin
git switch feat/remote-model-delivery
git pull --ff-only
```

执行 `git log -1 --oneline`，确认最上方是包含图片云存储功能的最新提交。

### 2. 修改服务器环境变量

在 1Panel 文件管理器中打开 `/opt/duet-doc-backend/.env`，或在服务器终端使用编辑器，
增加以下配置。`daisy-duet-media` 要替换为你实际创建的图片 Bucket 名称：

```dotenv
OSS_MEDIA_BUCKET=daisy-duet-media
MEDIA_MAX_SIZE_BYTES=10485760
MEDIA_URL_TTL_SECONDS=900
```

复用已有 `OSS_REGION`、`OSS_ENDPOINT`、`OSS_ECS_ROLE_NAME`。
`OSS_BUCKET` 仍然是模型权重 Bucket，不要改成图片 Bucket。
图片 Bucket 保持私有；RAM 角色需要对 `media/v1/*` 有 GetObject、PutObject、DeleteObject 权限。
浏览器 CORS 允许 GET、HEAD、PUT，允许 Headers 为 `*`。

保存 `.env` 后，不要把它提交到 Git。

### 3. 在服务器重新构建并迁移数据库

```bash
cd /opt/duet-doc-backend
docker compose -f compose.server.yaml build api
docker compose -f compose.server.yaml run --rm api alembic upgrade head
docker compose -f compose.server.yaml up -d
docker compose -f compose.server.yaml exec api alembic current
```

最后一条命令应输出：

```text
0008 (head)
```

再确认服务状态：

```bash
docker compose -f compose.server.yaml ps
curl http://127.0.0.1:8000/api/v1/health
```

`postgres` 和 `api` 应显示为运行中或健康，健康接口应返回 `status: ok`。

### 4. 在服务器自动上传一张测试图片

仍然在同一个 FinalShell 服务器终端执行：

```bash
cd /opt/duet-doc-backend
docker compose -f compose.server.yaml exec api python -m app.media_smoke
```

脚本会自动完成以下操作：

1. 在内存中生成一张 1x1 PNG，不在服务器磁盘创建图片文件。
2. 使用 ECS RAM 角色为该图片生成 OSS 上传地址。
3. 将图片上传到 `media/v1/_checks/<随机ID>/pixel.png`。
4. 校验图片大小、类型和 MD5，并通过签名地址读取原始内容。
5. 再次上传同一路径，确认 OSS 拒绝覆盖。
6. 删除这张测试图片，并确认它已经不存在。

全部成功时输出应为：

```text
上传成功
文件大小、类型和校验值验证成功
签名读取成功
禁止覆盖验证成功
测试图片已删除
```

这表示 Bucket 名称、ECS RAM 角色和读写删除权限均配置正确。脚本不会操作现有图片，
也不会写入业务数据库。如果失败，只会提示异常类型，不会打印签名 URL 或临时凭据。
失败时检查服务器 `.env` 中的 `OSS_MEDIA_BUCKET`、角色名和 RAM 策略资源路径。

这项测试不经过浏览器，因此不验证 CORS。跨域设置应通过前端浏览器 PUT 上传单独验证。

## 接口约定

所有接口携带 `Authorization: Bearer <access_token>`；workspace_id 必须属于当前用户。
未配置媒体存储时返回 503。签名地址属于临时凭证，不记录到日志或正文中。

### 申请上传

`POST /api/v1/workspaces/{workspace_id}/media/uploads`

```json
{
  "asset_id": "asset-客户端生成并在重试时复用的标识",
  "content_type": "image/png",
  "size_bytes": 68,
  "md5_hex": "图片原始字节的32位小写MD5"
}
```

支持 PNG、JPEG、WebP、GIF，默认最大 10 MiB。暂不支持 SVG。
MD5 用于 OSS 传输一致性检查，不用于用户认证或跨用户去重。
相同资源 ID 必须始终对应相同字节；修改图片应产生新 ID。

pending 响应包含 upload_url、headers、expires_at。浏览器使用 PUT 上传原始 Blob，
完整携带返回的 headers，不使用 FormData。签名中固定 Content-MD5、Content-Type 和
禁止覆盖；另外返回私有缓存策略请求头供上传时设置。重复申请不会增加资源记录。
已 ready 时 upload_url 为 null，直接复用该资源，不再次上传。

### 确认上传

`POST /api/v1/workspaces/{workspace_id}/media/{asset_id}/complete`

服务端通过 HEAD 校验实际大小、Content-Type、ETag（单次 PUT 的 MD5），通过后才标记 ready。
这里检查传输信息，不进行图片内容审核或解码。申请时的大小限制及上传后检查并不等于
OSS 层强制的上传字节配额。单次 PUT、无分片上传是当前接口约定。

若 PUT 已成功但响应丢失，重新 PUT 会返回 OSS 409（禁止覆盖）；此时调用 complete 验证，
不可把 OSS 409 直接当成上传成功。complete 可重复调用，文件不存在或信息不符返回 409。
签名或 OSS 网络异常返回 502，不伪造成功。

### 获取访问地址

`GET /api/v1/workspaces/{workspace_id}/media/{asset_id}/access`

返回 asset_id、url、expires_at，仅 ready 资源可访问。每次先检查工作空间归属。
正文保存 assetId，由前端按需解析成临时地址；旧版本地图片引用仍保留。

## 前端同步边界

- 点击同步后固定正文快照，找出实际引用的图片，再上传、确认、推送正文。
- 插图后立即删除或撤销，在首次同步前已无引用的图片不申请上传。
- 上传期间继续编辑进入下一次变更，上传完成不得用旧快照覆盖当前编辑器。
- 上传途中取消可能留下 pending 或未引用 ready 资源，本批不自动清理，也不提供删除接口。
- 文档同步事务会用当前正文中的 `assetId` 替换 `document_media_refs`；缺失、未完成或正在回收的资源会阻止正文提交。
- 零引用资源只记录 `unreferenced_at`，当前不会自动删除数据库记录或 OSS 对象。
- 本地 Blob、旧图片引用、历史版本均保留；不要通过正文引用表为空判断图片可删除。

## 浏览器手动验证

1. 在文档中插入一张小图片并保存，点击“立即同步”。图片上传失败时正文任务仍留在 Outbox，不会先把失效引用推到云端。
2. 图片同步成功后，在另一个浏览器或无痕窗口登录同一账号并同步。打开该文档，图片应从 OSS 加载并写入该浏览器的 IndexedDB。
3. 插入图片后立即撤销或删除，再点击同步。该图片不应出现在 OSS 的业务目录中。
4. 断开后端后点击同步，应显示失败；恢复后再次同步应成功，正文与图片均不丢失。

前端接入前必须先在服务器执行 `alembic upgrade head` 并确认版本为 `0008`。
`0007` 将媒体资源 ID 从 UUID 改为受格式约束的文本，以兼容已有客户端生成的 `asset-*` 标识。
`0008` 增加媒体引用生命周期字段。云端引用表只表达当前文档状态；本地历史版本继续依靠 IndexedDB Blob 恢复，
恢复后可用原 `assetId` 重新上传。迁移前已经存在但尚未经过文档同步的资源保持未标记状态，避免未来 GC 误删旧数据。

## 垃圾回收命令

垃圾回收只通过服务器命令运行，不提供 HTTP 接口。第一次必须先预览候选：

```bash
docker compose -f compose.server.yaml exec api python -m app.media_gc --dry-run
```

默认规则如下：

- `ready` 且连续 7 天没有当前文档引用的资源进入候选。
- `pending` 且创建超过 24 小时的资源进入候选。
- 上次删除失败而停留在 `deleting` 的资源优先重试。
- `unreferenced_at` 为空的迁移前资源不会进入候选。
- 单次最多处理 100 条，可通过 `--limit` 调整，最大 1000。

确认预览中的资源确实可以删除后，再显式执行：

```bash
docker compose -f compose.server.yaml exec api python -m app.media_gc --execute
```

也可以调整宽限期和批量大小：

```bash
docker compose -f compose.server.yaml exec api \
  python -m app.media_gc --dry-run --ready-days 14 --pending-hours 48 --limit 200
```

执行时先将数据库记录标记为 `deleting`，再删除 OSS 对象，最后删除数据库记录。
OSS 删除失败时记录保留为 `deleting`，下次执行会重试；同步服务不会让正文引用正在删除的资源。
如果候选数量等于 `limit`，应重复运行，直到 `--dry-run` 显示 0 个候选。
