食策AI CloudBase Run 老板版部署包

用途：GitHub -> Tencent CloudBase Run，部署 FastAPI API + 同域 Web 老板版。

本包保持在 GitHub 网页单次上传 100 文件限制以内。
已移除 tests、完整 docs、miniprogram、samples、__pycache__、本地 SQLite、平台专用启动脚本等非生产运行必需内容。

CloudBase：
- 外部访问端口：80
- 服务端口：8080
- SHICE_ENV=production
- DATABASE_URL=<Neon 完整 PostgreSQL URI>
- MINI_TOKEN_SECRET=<随机长密钥>
- WEB_ACCESS_PASSWORD=<你设置的强测试访问密码>

上线后：
- / = 食策AI Web 老板版
- /api/health = 后端健康检查
- /api/mini/v1/** = 生产 API
