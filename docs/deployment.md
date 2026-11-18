# 部署与运维

使用 Docker Compose 部署独立实例。初始化配置、启动服务、创建管理员后，再在网页配置模型与文档解析服务。

在仓库根目录运行：

```bash
./deploy.sh init
./deploy.sh build
./deploy.sh up
./deploy.sh exec api python -m zhiheng create-admin --username admin
```

`init` 只创建新的 `deploy/core/.env.core`，生成独立数据库密码、Fernet 密钥、向量存储、业务 MinIO 管理凭据及沙盒 API key，权限为 `0600`，拒绝覆盖已有文件。自定义新文件使用 `./deploy.sh --env-file /absolute/path/to/new-core.env COMMAND`；配置字段见[示例配置](../deploy/core/.env.core.example)。脚本显式选择该文件，不读取旧根 `.env`。升级既有配置时，补齐 `CORE_VECTOR_ACCESS_KEY` 和 `CORE_VECTOR_SECRET_KEY` 的独立随机值，保留原数据库密码与 Fernet 密钥。

`CORE_DB_PASSWORD` 仅支持字母、数字、下划线和连字符组成的 URL-safe 原始口令，不能预先 URL 编码。`init` 的随机密码符合这一限制。默认网页为 `http://127.0.0.1:19080`，生成的配置显式使用本机 HTTP Cookie 和允许来源。对外提供 HTTPS 时，将 `CORE_COOKIE_SECURE` 设置为 `true`，并按实际访问地址调整 `CORE_ALLOWED_ORIGINS` 和绑定地址。

启动依次等待 PostgreSQL / Redis、Milvus 及其 etcd / 对象存储健康，迁移成功后启动 worker，再等待 API 和网页就绪。管理员必须显式创建，没有固定默认口令。`./deploy.sh down` 保留新实例数据卷；配置校验使用 `./deploy.sh config --quiet`，避免把包含密钥的展开配置输出到日志。

受管文件统一保存到独立业务 MinIO：聊天附件、知识库原文、解析产物以及通过文件接口上传的技能包使用同一私有业务桶。API 与 worker 使用相同的桶和专用应用账号，不挂载业务本地文件卷。业务文件不提供静态直链，下载继续经过身份、当前权限及 SHA-256 完整性校验；模型、会话和资源 API 的文件接口保持原协议。解析临时文件使用容器临时目录。

向量服务固定为 Milvus 2.6.25，配套 etcd 和对象存储使用独立的 `vector-etcd-data`、`vector-objects-data`、`milvus-data` 卷，均不向宿主机发布端口。API / worker 通过内部 `http://milvus:19530` 连接。向量 SDK、模型维度和索引参数由知识模块校验，读取请求不会自动创建索引。

备份应同时保存 PostgreSQL、Redis、业务对象存储 `file-objects-data`、三个向量服务卷、受保护的 `.env.core` 和对应应用镜像。业务 MinIO 使用独立卷及管理凭据，备份时也应停止 `file-objects` 并保存匹配的配置。先停止网页、API 与 worker，再依次停止 Milvus、etcd 和对象存储；确认服务正常退出后复制其卷，并导出数据库和 Redis。数据库中的活动索引版本必须与向量备份对应，不能仅备份 SQL 元数据或单个向量卷。Fernet 密钥和向量存储凭据必须与对应数据一起保留，否则无法读取已存储的凭据或对象。

升级前完成备份；更改配置文件中的数据库密码不会自动修改已初始化 PostgreSQL 用户的密码。恢复时使用匹配的应用版本、完整数据和密钥，在独立实例上验证迁移与健康状态后再开放访问。

## 业务文件统一使用 MinIO

`file-objects` 是独立的业务 MinIO 服务，数据卷为 `file-objects-data`，与 Milvus 的 `vector-objects` 分别维护。`file-objects-init` 是运行后退出的初始化容器：创建私有业务桶、关闭匿名访问，并为 API / Worker 配置桶范围的读写账号。

`CORE_FILES_ROOT_USER` / `CORE_FILES_ROOT_PASSWORD` 仅用于存储管理与初始化；API / Worker 只接收 `CORE_FILES_ACCESS_KEY` / `CORE_FILES_SECRET_KEY`。应用账号不能创建其他桶、修改桶策略或访问 Milvus 的对象存储。桶名由 `CORE_FILES_BUCKET` 指定，默认 `zhiheng-files`，使用 3–63 位小写字母、数字和连字符。

新实例执行 `./deploy.sh init` 时生成两组独立凭据，`./deploy.sh up` 等待业务桶和账号初始化完成后启动 Worker 与 API。已有实例先备份受保护的环境文件，再运行 `./deploy.sh init-minio`；该命令补齐配置，保持已有完整凭据不变，发现不完整的凭据对时拒绝操作。所有实例配置均不进入 Git。

S3 内网地址默认为 `http://file-objects:9000`，不向宿主机发布。管理控制台默认为 `http://127.0.0.1:19001`，由 `CORE_FILES_CONSOLE_BIND` / `CORE_FILES_CONSOLE_PORT` 控制，登录信息从受保护的环境文件读取。需要远程管理时使用 SSH 隧道或受控 HTTPS 入口。API / Worker 的连接、区域、超时通过 `CORE_FILES_S3_ENDPOINT`、`CORE_FILES_S3_REGION`、`CORE_FILES_S3_TIMEOUT_SECONDS` 设置。

运行时只有 MinIO 文件存储，不提供本地后端切换、双写或本地读取兜底。对象存储不可用时返回明确的 503，API 就绪检查同时检查业务桶。`file-objects-data` 必须持久保存，并与数据库、业务 MinIO 配置一起备份。

### 从旧本地文件卷迁移

此过程只用于一次性搬迁既有数据，不保留旧存储运行方式。先备份数据库、旧文件卷及环境配置，然后使用新版本镜像执行下列命令。示例旧卷名为 `zhiheng-core_files-data`，自定义部署需要替换为实际名称。

~~~bash
./deploy.sh init-minio
./deploy.sh build
./deploy.sh run --rm file-objects-init
./deploy.sh stop web api worker
./deploy.sh run --rm --no-deps migrator

# 原文件卷只读挂载到一次性迁移容器，不挂载到 API / Worker。
./deploy.sh run --rm --no-deps \
  --volume zhiheng-core_files-data:/migration-source:ro \
  migrator python -m zhiheng migrate-files --source-root /migration-source --offline

./deploy.sh run --rm --no-deps \
  --volume zhiheng-core_files-data:/migration-source:ro \
  migrator python -m zhiheng migrate-files --source-root /migration-source --offline --verify-only

./deploy.sh up
~~~

使用自定义环境文件时，每条命令都加 `--env-file /absolute/path/to/core.env`；自定义镜像部署时，在环境文件中将 `CORE_BACKEND_IMAGE` 和 `CORE_FRONTEND_IMAGE` 填写为实际已构建的标签。源码模式使用对应的 `dev.sh` 命令，并在依赖变化后重新构建后端镜像。迁移期间保持 API / Worker 停止，验证成功后统一启动新版本。

迁移以数据库中尚未物理清理的文件记录为准，分批检查原始字节和目标对象的大小、SHA-256。文件 ID、对象标识、所有者、ACL、引用和历史会话不变。相同对象可重复校验，不同内容、缺失文件或未完成的物理清理会阻止迁移；已经复制的正确对象保留，修复后可继续。源卷不被修改，可作为离线备份保存；新部署不再声明或挂载该卷。

## 图谱运行连接

LightRAG 使用独立 `CORE_GRAPH_DATABASE_URL`，原生 `postgresql://` 格式；不复用迁移所有者连接，也不读取 SDK 的 PostgreSQL 环境变量。`init` 默认留空，管理员完成独立角色配置后再填写；为空时保持图谱不可用，向量知识库仍可使用。升级前备份原配置，保留数据库与 Fernet 密钥，再添加独立图谱连接。

由管理员在显式迁移完成后配置该登录角色：只授予数据库 CONNECT、`knowledge_graph_sdk` schema 的 USAGE 与其中十张表的 SELECT/INSERT/UPDATE/DELETE，不授予任何 schema CREATE、数据库 CREATE/TEMPORARY、对象所有权、superuser、CREATEDB 或 CREATEROLE。PostgreSQL 默认向 PUBLIC 授予 TEMPORARY，因此独立平台数据库须由管理员显式撤销 PUBLIC 的数据库 CREATE/TEMPORARY 与 public schema CREATE，再授予运行角色所需的窄权限；迁移/API 的数据库所有者权限保持有效。API/worker 初始化只核对实际权限、catalog 和现有索引，缺失时明确拒绝，不自行创建角色、表或索引。图谱角色凭据与数据库、文件、向量卷及应用镜像一起私密备份。

## 工作空间文件执行

普通任务问答不需要沙盒。Python/Shell 和办公文件制作需要可选的 `workspace` profile，它包含独立 OpenSandbox 管理服务与办公运行镜像：

```bash
COMPOSE_PROFILES=workspace ./deploy.sh build sandbox-manager sandbox-runtime
COMPOSE_PROFILES=workspace ./deploy.sh up
```

新配置已生成独立 `CORE_SANDBOX_API_KEY` 和内部访问地址；旧实例需先在受保护的环境文件中补齐，不能复用模型、数据库或 MinIO 密钥。办公镜像和官方 execd、egress 镜像也需在 Docker 守护进程可用。源码模式先完成镜像构建，再使用 `COMPOSE_PROFILES=workspace ./dev.sh up`。

管理服务仅绑定 Docker 网桥地址，API/Worker 通过认证 HTTP 访问；只有管理服务持有 Docker socket。任务输入只读，代码处于独立的非 root 隔离 session，成果与工作快照写入公共 MinIO。临时沙盒卷不替代业务对象存储，也不作为恢复备份。

部署还需为使用者单独授予“工作空间 → 文件处理与代码执行”。升级包含 `0041_work_agent` 迁移与官方 `work_checkpoints`；旧成员任务保留历史只读，新任务使用目标、模型、技能和文件执行流程。配置、隔离条件、限额与清理规则见[工作空间执行环境](deployment/workspace-sandbox.md)，接口见[工作空间任务 Agent](api/workspace-agent.md)。

当前迁移头为 `0043_workspace_execution`，清理任务级执行开关；`0042_workspace_merge` 连接了工作空间 `0041_work_agent` 与助手附件 `0041_assistant_attachments` 两条迁移。空库、旧版本及已应用任一条 `0041` 的实例均使用正常迁移升级；另一条功能迁移会补齐，已有功能数据保留。已执行的迁移文件和版本标记应保持原样，升级前按常规流程备份。

## 源码模式

先完成镜像构建和配置初始化，再在已安装前端依赖的仓库中启动：

~~~bash
cd frontend
npm ci
cd ..
./dev.sh up
./dev.sh restart-worker
~~~

前端使用 Vite 热更新，API 使用自动重载；Worker 修改后需重启。源码代理配置由脚本生成，保存在被 Git 忽略的实例目录中。API 与 Worker 必须使用相同的源码、迁移和业务 MinIO 配置。

源码依赖或迁移更新时，先备份，再执行 `./deploy.sh build` 重新构建镜像、`./dev.sh migrate` 显式升级数据库，最后执行 `./dev.sh up`。源码启动会核对镜像依赖与当前 schema；出现不匹配时按这一顺序处理。API 与 Worker 启动不执行迁移。

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
- 工作空间文件执行不可用：核对 `workspace` profile、独立沙盒 API key、运行镜像、管理服务保护检查及用户的代码执行权限；沙箱暂不可用时，普通问答和资料阅读仍可使用。

升级前保存完整备份，使用匹配的配置和应用版本执行迁移。查看当前迁移版本以实际源码和迁移结果为准；不要根据文档中的旧版本号跳过迁移。
