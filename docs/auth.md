# 认证开发说明

## 现有开发数据绑定账号

已有文档和聊天都归属于 `.env` 中的 `DEV_USER_ID`。首次启用登录前，在
`DEV_AUTH_ENABLED=true` 的开发环境执行：

```powershell
.\.venv\Scripts\python.exe -m app.bind_dev_user --username daisy --display-name Daisy
```

命令会在终端中安全地询问两次密码，并把密码登录身份绑定到原有用户 UUID；不会移动、
复制或删除该用户的工作区数据。一个开发用户只能绑定一次密码身份。

绑定成功并完成前端认证接入后，可将 `.env` 中的 `DEV_AUTH_ENABLED` 改为 `false`，重启
后端。此时同步与 AI 接口都必须携带登录返回的 Access Token：

```text
Authorization: Bearer <access_token>
```

健康检查以及注册、登录、刷新接口保持公开。生产环境始终禁止开发身份回退。
