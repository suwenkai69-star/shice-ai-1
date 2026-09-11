# 食策AI CloudBase Run 部署

该包用于 CloudBase Run 后端 API。

- 容器端口：8080
- 健康检查：`GET /api/health`
- 生产 API：`/api/mini/v1/**`
- 必填环境变量：`SHICE_ENV=production`、`DATABASE_URL`、`MINI_TOKEN_SECRET`
- 微信登录启用时还需：`WECHAT_APPID`、`WECHAT_SECRET`

注意：当前生产模式不会发布旧版网页根路由；旧 `static/index.html` 调用的是本地版 `/api/state` 等接口，不能作为生产 Web 前端直接使用。
