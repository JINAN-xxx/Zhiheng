# 新平台部署配置

完整的 Docker 和本机开发步骤见[根 README](../../README.md)。本目录只管理独立的 `zhiheng-core` 实例，配置、数据库、Redis 和持久卷均与 `legacy/` 归档实例分离。

在仓库根目录运行：

```bash
./deploy.sh init
./deploy.sh build
./deploy.sh up
./deploy.sh exec api python -m zhiheng create-admin --username admin
```

`init` 只创建新的 `deploy/core/.env.core`，生成独立数据库密码和 Fernet 密钥，权限为 `0600`，拒绝覆盖已有文件。自定义新文件使用 `./deploy.sh --env-file /absolute/path/to/new-core.env COMMAND`；配置字段见[示例文件](.env.core.example)。脚本显式选择该文件，不读取旧根 `.env`。

`CORE_DB_PASSWORD` 仅支持字母、数字、下划线和连字符组成的 URL-safe 原始口令，不能预先 URL 编码。`init` 的随机密码符合这一限制。默认网页为 `http://127.0.0.1:19080`，生成的配置显式使用本机 HTTP Cookie 和允许来源。对外提供 HTTPS 时，将 `CORE_COOKIE_SECURE` 设置为 `true`，并按实际访问地址调整 `CORE_ALLOWED_ORIGINS` 和绑定地址。

启动依次等待 PostgreSQL / Redis 健康、迁移成功、worker 心跳和 API 就绪。管理员必须显式创建，没有固定默认口令。`./deploy.sh down` 保留新实例数据卷；配置校验使用 `./deploy.sh config --quiet`，避免把包含密钥的展开配置输出到日志。

受管文件使用独立的 `files-data` 卷，同时挂载到 API 和 worker 的 `/var/lib/zhiheng/files`，由 UID/GID `10001:10001` 写入。该目录不对外提供静态访问，下载始终经过身份和文件权限校验。本机开发应将 `ZHIHENG_FILES_ROOT` 显式设置为可写目录，并在 API 与 worker 间使用相同路径。

备份应同时保存新 PostgreSQL 数据、新 Redis 持久数据、`files-data` 文件字节和受保护的 `.env.core`。备份前停止 API 与 worker，保持数据库和字节处于同一时间点；不要只备份文件元数据。Fernet 密钥必须与对应数据库一起保留，否则无法解密已存储的供应商凭据。升级前完成备份；更改配置文件中的数据库密码不会自动修改已初始化 PostgreSQL 用户的密码。恢复时使用匹配的应用版本、数据和密钥，在新实例上验证迁移与健康状态后再开放访问。
