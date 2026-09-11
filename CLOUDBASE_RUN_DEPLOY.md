# 食策AI CloudBase Run 部署

该包用于 CloudBase Run：FastAPI API + 同域 Web 老板版。

- 容器端口：8080
- Web 首页：`GET /`
- Web 静态资源：`/web/**`
- 健康检查：`GET /api/health`
- 生产 API：`/api/mini/v1/**`
- 必填环境变量：`SHICE_ENV=production`、`DATABASE_URL`、`MINI_TOKEN_SECRET`
- 微信登录启用时还需：`WECHAT_APPID`、`WECHAT_SECRET`

当前 Web 为公开演示模式：访问根地址会自动获取共享演示身份，不需要访问密码。
旧 `static/index.html` 仍保留作本地兼容，但生产首页已经切换到 `web/index.html`，legacy API 仍被生产路由守卫阻止。
