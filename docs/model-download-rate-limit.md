# 模型下载限流部署说明

模型清单接口为 `GET /api/v1/models/{model_id}/manifest`。当前保护分为三层：

- 同一用户、同一模型每小时最多签发 3 份新清单，每天最多 8 份。
- 签名 URL 有效期内重复请求会复用原清单，不重复消耗用户额度。
- 单个 API 进程按客户端 IP 每分钟补充 5 个令牌，并允许额外突发 3 次。

注册接口默认每 IP 每小时 5 次，登录接口默认每 IP 每分钟 10 次。超限统一返回
`429 Too Many Requests`，响应头包含 `Retry-After`。

用户额度记录在 PostgreSQL 的 `model_download_grants` 表中，重启 API 后仍然有效。
IP 令牌桶保存在 API 进程内，适合当前单实例演示部署；以后扩展到多个 API 实例时，
应迁移到 Redis 或由网关统一执行。

## 服务器环境变量

在服务器 `.env` 中保留或加入：

```dotenv
MODEL_DOWNLOAD_URL_TTL_SECONDS=900
MODEL_MANIFEST_USER_HOURLY_LIMIT=3
MODEL_MANIFEST_USER_DAILY_LIMIT=8
MODEL_MANIFEST_CACHE_SAFETY_SECONDS=30
MODEL_MANIFEST_IP_RATE_PER_MINUTE=5
MODEL_MANIFEST_IP_BURST=3
AUTH_REGISTER_IP_LIMIT_PER_HOUR=5
AUTH_LOGIN_IP_LIMIT_PER_MINUTE=10

# API 只监听 127.0.0.1，并且仅由本机 Nginx 反向代理时才设为 true。
TRUST_PROXY_HEADERS=true
```

本地开发直接访问 FastAPI 时保持 `TRUST_PROXY_HEADERS=false`，防止客户端伪造
`X-Forwarded-For` 绕过或干扰限流。

## 1Panel / Nginx

应用层已经具备 IP 限流。若需要再增加一层 Nginx 保护，在 Nginx 的 `http` 作用域加入：

```nginx
limit_req_zone $binary_remote_addr zone=duet_model_manifest:10m rate=5r/m;
limit_req_zone $binary_remote_addr zone=duet_auth:10m rate=10r/m;
```

在 Duet API 的站点 `server` 中加入精确路径规则。`limit_req_zone` 不能写在
`server` 或 `location` 内；1Panel 中通常应放在 OpenResty/Nginx 的全局配置中。

```nginx
location ~ ^/api/v1/models/[^/]+/manifest$ {
    limit_req zone=duet_model_manifest burst=3 nodelay;

    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_pass http://127.0.0.1:8000;
}

location ~ ^/api/v1/auth/(register|login)$ {
    limit_req zone=duet_auth burst=5 nodelay;

    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_pass http://127.0.0.1:8000;
}
```

修改后先执行 `nginx -t`，确认配置通过再重载。当前通过 SSH 隧道从本地前端访问时，
所有请求在服务器看来可能来自 `127.0.0.1`；这种验证方式不适合测试公网 IP 隔离，
但用户额度与清单复用仍可正常验证。

## 上线与检查

```bash
cd /opt/duet-doc-backend
docker compose -f compose.server.yaml build api
docker compose -f compose.server.yaml run --rm api alembic upgrade head
docker compose -f compose.server.yaml up -d
docker compose -f compose.server.yaml ps
```

迁移版本应为 `0005 (head)`。首次请求清单会插入一条授权记录；在签名 URL 过期前
重复请求同一模型，返回的 `expiresAt` 与文件 URL 应保持不变，授权记录数量也不增加。

