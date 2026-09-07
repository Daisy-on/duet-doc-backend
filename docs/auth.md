# 认证开发说明

完成本地旧用户迁移后，应将 `.env` 中的 `DEV_AUTH_ENABLED` 设为 `false` 并重启后端。
此时同步与 AI 接口都必须携带登录返回的 Access Token：

```text
Authorization: Bearer <access_token>
```

健康检查以及注册、登录、刷新接口保持公开。生产环境始终禁止开发身份回退。
