食策AI CloudBase Run 精简部署包

用途：GitHub -> Tencent CloudBase Run 部署后端 API。

本包特意控制在 GitHub 网页上传 100 文件限制以内。
已移除：tests、docs、miniprogram、samples、__pycache__、本地 SQLite 数据库、
Windows/macOS 启动脚本、Vercel 专用文件等非 CloudBase 运行必需内容。

保留：
- FastAPI 主程序
- mini API
- PostgreSQL 持久化与 migration
- repositories
- services
- recognition
- AI/notifications
- benchmark seed
- Dockerfile / requirements

CloudBase：
- 外部访问端口：80
- 服务端口：8080
- SHICE_ENV=production
- DATABASE_URL=<Neon 完整 PostgreSQL URI>
- MINI_TOKEN_SECRET=<随机长密钥>
