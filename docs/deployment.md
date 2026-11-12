# 部署与运维

使用 Docker Compose 部署独立实例。初始化配置、启动服务、创建管理员后，再在网页配置模型与文档解析服务。

在仓库根目录运行：

```bash
./deploy.sh init
./deploy.sh build
./deploy.sh up
./deploy.sh exec api python -m zhiheng create-admin --username admin
```

`init` 只创建新的 `deploy/core/.env.core`，生成独立数据库密码、Fernet 密钥及向量存储凭据，权限为 `0600`，拒绝覆盖已有文件。自定义新文件使用 `./deploy.sh --env-file /absolute/path/to/new-core.env COMMAND`；配置字段见[示例配置](../deploy/core/.env.core.example)。脚本显式选择该文件，不读取旧根 `.env`。升级既有配置时，补齐 `CORE_VECTOR_ACCESS_KEY` 和 `CORE_VECTOR_SECRET_KEY` 的独立随机值，保留原数据库密码与 Fernet 密钥。

`CORE_DB_PASSWORD` 仅支持字母、数字、下划线和连字符组成的 URL-safe 原始口令，不能预先 URL 编码。`init` 的随机密码符合这一限制。默认网页为 `http://127.0.0.1:19080`，生成的配置显式使用本机 HTTP Cookie 和允许来源。对外提供 HTTPS 时，将 `CORE_COOKIE_SECURE` 设置为 `true`，并按实际访问地址调整 `CORE_ALLOWED_ORIGINS` 和绑定地址。

启动依次等待 PostgreSQL / Redis、Milvus 及其 etcd / 对象存储健康，迁移成功后启动 worker，再等待 API 和网页就绪。管理员必须显式创建，没有固定默认口令。`./deploy.sh down` 保留新实例数据卷；配置校验使用 `./deploy.sh config --quiet`，避免把包含密钥的展开配置输出到日志。

受管文件使用独立的 `files-data` 卷，同时挂载到 API 和 worker 的 `/var/lib/zhiheng/files`，由 UID/GID `10001:10001` 写入。该目录不对外提供静态访问，下载始终经过身份和文件权限校验。本机开发应将 `ZHIHENG_FILES_ROOT` 显式设置为可写目录，并在 API 与 worker 间使用相同路径。

向量服务固定为 Milvus 2.6.25，配套 etcd 和对象存储使用独立的 `vector-etcd-data`、`vector-objects-data`、`milvus-data` 卷，均不向宿主机发布端口。API / worker 通过内部 `http://milvus:19530` 连接。向量 SDK、模型维度和索引参数由知识模块校验，读取请求不会自动创建索引。

备份应同时保存 PostgreSQL、Redis、`files-data`、三个向量服务卷、受保护的 `.env.core` 和对应应用镜像。先停止网页、API 与 worker，再依次停止 Milvus、etcd 和对象存储；确认服务正常退出后复制其卷，并导出数据库和 Redis。数据库中的活动索引版本必须与向量备份对应，不能仅备份 SQL 元数据或单个向量卷。Fernet 密钥和向量存储凭据必须与对应数据一起保留，否则无法读取已存储的凭据或对象。

升级前完成备份；更改配置文件中的数据库密码不会自动修改已初始化 PostgreSQL 用户的密码。恢复时使用匹配的应用版本、完整数据和密钥，在独立实例上验证迁移与健康状态后再开放访问。

## 图谱运行连接

LightRAG 使用独立 `CORE_GRAPH_DATABASE_URL`，原生 `postgresql://` 格式；不复用迁移所有者连接，也不读取 SDK 的 PostgreSQL 环境变量。`init` 默认留空，管理员完成独立角色配置后再填写；为空时保持图谱不可用，向量知识库仍可使用。升级前备份原配置，保留数据库与 Fernet 密钥，再添加独立图谱连接。

由管理员在显式迁移完成后配置该登录角色：只授予数据库 CONNECT、`knowledge_graph_sdk` schema 的 USAGE 与其中十张表的 SELECT/INSERT/UPDATE/DELETE，不授予任何 schema CREATE、数据库 CREATE/TEMPORARY、对象所有权、superuser、CREATEDB 或 CREATEROLE。PostgreSQL 默认向 PUBLIC 授予 TEMPORARY，因此独立平台数据库须由管理员显式撤销 PUBLIC 的数据库 CREATE/TEMPORARY 与 public schema CREATE，再授予运行角色所需的窄权限；迁移/API 的数据库所有者权限保持有效。API/worker 初始化只核对实际权限、catalog 和现有索引，缺失时明确拒绝，不自行创建角色、表或索引。图谱角色凭据与数据库、文件、向量卷及应用镜像一起私密备份。

## 源码模式

先完成镜像构建和配置初始化，再在已安装前端依赖的仓库中启动：

~~~bash
cd frontend
npm ci
cd ..
./dev.sh up
./dev.sh restart-worker
~~~

前端使用 Vite 热更新，API 使用自动重载；Worker 修改后需重启。源码代理配置由脚本生成，保存在被 Git 忽略的实例目录中。API 与 Worker 必须使用相同的源码、迁移和文件卷。

## 局域网与 HTTPS

初始配置只开放本机 HTTP。其他设备访问时，将 CORE_WEB_BIND 改为合适的监听地址，例如 0.0.0.0，并把浏览器实际使用的完整来源加入 CORE_ALLOWED_ORIGINS。客户端使用服务器实际 IP 或域名，不使用 0.0.0.0。应用启动后再验证浏览器入口、登录和流式响应。

生产入口由可信反向代理提供 HTTPS，CORE_COOKIE_SECURE=true。数据库、Redis 和向量服务保持内部网络访问。WSL 网络模式、Windows 防火墙和外部路由分别决定跨设备连通性，见[局域网访问](api/lan-access.md)。

## Windows / WSL

在 Linux 文件系统中克隆仓库，并在 WSL 内执行部署命令。Docker 守护进程和 WSL 需要持续运行；浏览器启动不能替代服务启动。

可选的 Windows 启动辅助脚本需要明确传入发行版和实际 Linux 项目路径：

~~~powershell
powershell -File tools/start-platform.ps1 -Distribution Ubuntu-22.04 -LinuxProject /opt/zhiheng
~~~

请把示例路径替换为自己的克隆目录。该脚本维持 WSL 会话并启动已有平台容器，不安装 Docker、不创建系统计划任务。休眠、系统重启和 Docker 重启后，应重新核对健康状态。

## 排障

~~~bash
./deploy.sh ps
./deploy.sh logs --tail 100 api worker web
curl --fail http://127.0.0.1:19080/health/live
curl --fail http://127.0.0.1:19080/health/ready
~~~

- 502：代理已响应，但上游 API 或前端尚未就绪；先检查容器与启动日志。
- 503：检查数据库迁移、Redis 和 Worker 心跳，等待依赖完成启动。
- 本机可访问、其他设备超时：检查监听地址、WSL 转发、防火墙和实际访问 IP。
- 文档解析不可用：检查解析地址、Key 与配置页状态。官方 MinerU 使用 /api/v4；本地服务使用自部署 V1 Tasks 接口。
- 模型调用失败：根据运行记录核对服务连接、模型授权、输出限制和供应商错误。提交结果未知时先查原任务，避免重复调用。

升级前保存完整备份，使用匹配的配置和应用版本执行迁移。查看当前迁移版本以实际源码和迁移结果为准；不要根据文档中的旧版本号跳过迁移。
