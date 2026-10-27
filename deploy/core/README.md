# 新平台部署配置

完整的 Docker 和本机开发步骤见[根 README](../../README.md)。本目录只管理独立的 `zhiheng-core` 实例，配置、数据库、Redis 和持久卷均与 `legacy/` 归档实例分离。

在仓库根目录运行：

```bash
./deploy.sh init
./deploy.sh build
./deploy.sh up
./deploy.sh exec api python -m zhiheng create-admin --username admin
```

`init` 只创建新的 `deploy/core/.env.core`，生成独立数据库密码、Fernet 密钥及向量存储凭据，权限为 `0600`，拒绝覆盖已有文件。自定义新文件使用 `./deploy.sh --env-file /absolute/path/to/new-core.env COMMAND`；配置字段见[示例文件](.env.core.example)。脚本显式选择该文件，不读取旧根 `.env`。升级既有配置时，补齐 `CORE_VECTOR_ACCESS_KEY` 和 `CORE_VECTOR_SECRET_KEY` 的独立随机值，保留原数据库密码与 Fernet 密钥。

`CORE_DB_PASSWORD` 仅支持字母、数字、下划线和连字符组成的 URL-safe 原始口令，不能预先 URL 编码。`init` 的随机密码符合这一限制。默认网页为 `http://127.0.0.1:19080`，生成的配置显式使用本机 HTTP Cookie 和允许来源。对外提供 HTTPS 时，将 `CORE_COOKIE_SECURE` 设置为 `true`，并按实际访问地址调整 `CORE_ALLOWED_ORIGINS` 和绑定地址。

启动依次等待 PostgreSQL / Redis、Milvus 及其 etcd / 对象存储健康，迁移成功后启动 worker，再等待 API 和网页就绪。管理员必须显式创建，没有固定默认口令。`./deploy.sh down` 保留新实例数据卷；配置校验使用 `./deploy.sh config --quiet`，避免把包含密钥的展开配置输出到日志。

受管文件使用独立的 `files-data` 卷，同时挂载到 API 和 worker 的 `/var/lib/zhiheng/files`，由 UID/GID `10001:10001` 写入。该目录不对外提供静态访问，下载始终经过身份和文件权限校验。本机开发应将 `ZHIHENG_FILES_ROOT` 显式设置为可写目录，并在 API 与 worker 间使用相同路径。

向量服务固定为 Milvus 2.6.25，配套 etcd 和对象存储使用独立的 `vector-etcd-data`、`vector-objects-data`、`milvus-data` 卷，均不向宿主机发布端口。API / worker 通过内部 `http://milvus:19530` 连接。向量 SDK、模型维度和索引参数由知识模块校验，读取请求不会自动创建索引。

备份应同时保存 PostgreSQL、Redis、`files-data`、三个向量服务卷、受保护的 `.env.core` 和对应应用镜像。先停止网页、API 与 worker，再依次停止 Milvus、etcd 和对象存储；确认服务正常退出后复制其卷，并导出数据库和 Redis。数据库中的活动索引版本必须与向量备份对应，不能仅备份 SQL 元数据或单个向量卷。Fernet 密钥和向量存储凭据必须与对应数据一起保留，否则无法读取已存储的凭据或对象。

升级前完成备份；更改配置文件中的数据库密码不会自动修改已初始化 PostgreSQL 用户的密码。恢复时使用匹配的应用版本、完整数据和密钥，在独立实例上验证迁移与健康状态后再开放访问。
