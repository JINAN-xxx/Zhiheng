# 智衡镜像发布包

本说明既保存在源码目录 `deploy/release/README.md`，也随构建复制为发布包根目录的 `README.md`。服务器接收完整发布目录后，可离线安装、更新和回滚应用，无需源码 checkout、Git、Node.js 或宿主机 Python 第三方依赖。后端 Python 镜像仍包含运行代码；镜像交付不提供源码加密或不可提取保证。

## 环境与交付物

| 环境 | 要求 |
| --- | --- |
| 构建机 | 源码、Python 3.10+、Node.js 24、npm、已安装的前端依赖、Docker Engine；首次准备基础镜像和 Python/npm 依赖需要网络 |
| 运行服务器 | Linux amd64、Python 3.10+、Docker Engine、Docker Compose v2，Compose 必须支持 `up --wait`；建议至少 8 GB 内存，并为镜像与数据预留磁盘空间 |

当前发布清单的平台固定为 `linux/amd64`，不包含 arm64 镜像。执行部署的账号需有 Docker 权限，以及发布目录的读取权限、安装目录的写入权限。实例运行不需要访问镜像仓库；调用外部模型仍需要访问相应服务商。

完整发布目录包含：

```text
new-bundle/
├── README.md                 本说明
├── release.sh                服务器管理入口
├── release.json              版本、来源、镜像 ID、兼容指纹与文件校验值
├── images.tar                六个镜像：backend、frontend、postgres、redis、litellm、milvus
├── compose.release.yaml      只使用包内固定镜像的编排
└── deploy/
    ├── platform.py           环境配置辅助脚本
    ├── release_cli.py        Python CLI 入口
    ├── release/              发布管理模块与 data-version
    ├── litellm/config.yaml   网关配置
    └── milvus/*.yaml         Milvus 配置
```

两个 Redis 服务共用一个 Redis 镜像，初始化任务和后端服务共用后端镜像。前端镜像内包含本次源码重新构建的静态资源。构建从隔离源码快照生成镜像，排除 `.env*`、本地数据、日志及常见私钥文件，不将实例配置作为发布内容。

`release.json` 保存 `source.revision`、`source.dirty`、`source.sha256`；未提交的源码变更会体现在 dirty 标记和快照哈希中，不能只凭 Git revision 判断产物内容。`files` 为包内文件的 SHA-256 校验值。应用镜像标签形如 `zhiheng-release/backend:2026.09.30-rc1-<源码哈希前12位>`；基础设施镜像使用 `zhiheng-release/postgres:sha-<镜像ID前12位>` 这类稳定标签，实际完整镜像 ID 也记录在清单中。

校验值用于发现文件损坏与内容不一致，不是发布者数字签名。只接收可信来源的发布包，并通过可信渠道核对交付信息；不要修改 `release.json` 或包内文件后继续部署。

## 在构建机生成发布包

在源码仓库根目录执行。`--output` 必须是尚不存在的新目录，不能覆盖已有发布目录：

```bash
node --version                    # 使用 Node.js 24
npm install --prefix zhiheng-fastapi-frontend
./deploy.sh release build --version 2026.09.30-rc1 --output /path/new-bundle
./deploy.sh release verify --bundle /path/new-bundle
```

构建命令使用已经安装的 `zhiheng-fastapi-frontend/node_modules`，不会自动安装 npm 依赖。它构建前后端镜像、准备基础设施镜像并导出 `images.tar`，不部署服务。版本号为 1–64 位 ASCII 字母、数字、点、下划线或短横线，首位必须是字母或数字。每个发布版本使用独立版本号和输出目录。

从源码入口操作发布包时，`verify` 和 `deploy` 必须显式指定 `--bundle`：

```bash
./deploy.sh release verify --bundle /path/new-bundle
./deploy.sh release deploy --bundle /path/new-bundle --site /opt/zhiheng
./deploy.sh release status --site /opt/zhiheng
./deploy.sh release rollback --site /opt/zhiheng
```

## 复制到服务器

下面以 SSH 用户 `ops`、服务器 `server` 为例，替换成实际账号和地址。目标发布目录应尚不存在；复制整个目录，不能只复制 `images.tar`：

```bash
# 构建机执行
scp -r /path/new-bundle ops@server:/home/ops/zhiheng-2026.09.30-rc1

# 服务器执行
cd /home/ops/zhiheng-2026.09.30-rc1
./release.sh verify
```

也可打包后传输，以下为替代上述目录复制的完整步骤：

```bash
# 构建机执行
tar -C /path/new-bundle -czf /path/zhiheng-2026.09.30-rc1.tar.gz .
scp /path/zhiheng-2026.09.30-rc1.tar.gz ops@server:/home/ops/

# 服务器执行，解压到一个新目录
mkdir /home/ops/zhiheng-2026.09.30-rc1
tar -xzf /home/ops/zhiheng-2026.09.30-rc1.tar.gz -C /home/ops/zhiheng-2026.09.30-rc1
cd /home/ops/zhiheng-2026.09.30-rc1
./release.sh verify
```

发布包内 `release.sh` 的 `--bundle` 默认是它所在的发布目录，与调用时的工作目录无关。`verify` 校验清单与文件内容，不启动容器，也不代表服务健康。

## 首次安装与实例配置

选择固定的安装目录，例如 `/opt/zhiheng`。后续所有发布都使用同一个 `--site`，不要为每次升级更换安装目录。直接部署会生成实例配置；需要自定义配置时，只在首次部署前创建以下文件：

```bash
# 由有权创建 /opt/zhiheng 的部署账号执行；仅用于尚未初始化的新实例
install -d -m 700 /opt/zhiheng
umask 077
cat > /opt/zhiheng/.env <<'EOF'
PLATFORM_PORT=12580
PLATFORM_BIND_ADDRESS=0.0.0.0
EOF
```

可在首次创建的 `.env` 中追加以下配置；未设置时采用默认值：

| 配置 | 默认与用途 |
| --- | --- |
| `PLATFORM_COMPOSE_PROJECT` | 根据安装目录路径派生 `zhiheng-<哈希前12位>`，为不同实例隔离容器和命名卷 |
| `PLATFORM_PORT` | `12580`，Nginx 对外端口；同机实例应使用不同端口 |
| `PLATFORM_BIND_ADDRESS` | `0.0.0.0`，宿主机监听地址 |
| `PLATFORM_DATA_DIR` | 安装目录下的 `data`；自定义相对路径按安装目录解析 |
| `PLATFORM_DB_NAME` / `PLATFORM_DB_USER` | `zhiheng-fastapi` / `zhiheng`，平台数据库及 Owner |

不要把源码部署的 `.env` 直接复制给发布实例。发布管理器为新实例独立生成并持久保存数据库密码、Redis 密码、JWT、API Key Pepper、LiteLLM 密钥和 Salt，以及传输加密密钥；后续升级和回滚复用原值。项目名、数据路径、数据库身份和密钥属于实例身份，初始化后不要重新生成或随意更改。

在已复制的发布目录执行：

```bash
./release.sh verify
./release.sh deploy --site /opt/zhiheng
./release.sh status --site /opt/zhiheng
```

发布管理器导入并核对镜像，等待基础服务健康、完成数据库与插件初始化、启动前后端，检查后端版本、插件状态、Nginx 页面和 API 路由后才记录成功版本。默认访问 `http://服务器IP:12580/`，页面接口为 `/docker-api/`，开放 API 为 `/v1/`。初始管理员为 `admin`，默认密码为 `admin123`，首次登录后修改密码。

同一 Compose 项目已有其他安装实例或源码部署容器时，发布管理器拒绝接管。现有源码部署与拆分部署需专项迁移，不要通过复用其项目名、数据目录或直接执行本命令切换交付方式。

安装目录的结构如下：

```text
/opt/zhiheng/
├── .env                      实例配置和持久密钥，权限 0600
├── data/                     默认的技能、知识库、附件与 Milvus 数据
├── runtime/                  固定路径的 LiteLLM / Milvus 运行配置
├── releases/<version>/       已接收的完整发布包，包含 images.tar
├── state.json                实例身份、最近成功版本、上一版本和未完成操作
└── .release.lock              同一实例发布互斥锁
```

PostgreSQL、LiteLLM Redis、平台 Redis 使用 Docker 命名卷，分别对应 `postgres-data`、`litellm-redis-data`、`platform-redis-data`，不在上述 `data/` 中。`runtime/` 的固定挂载路径使未变更的基础设施容器无需因发布目录改变而重新创建；配置与发布包不一致时会拒绝覆盖，不能直接编辑它来绕过运行契约。

## 更新、回滚与失败恢复

将下一版本复制到新的发布目录，在该目录运行同一个安装目录的 `deploy`：

```bash
# 新发布目录内执行
./release.sh verify
./release.sh deploy --site /opt/zhiheng
./release.sh status --site /opt/zhiheng

# 恢复上一成功应用版本
./release.sh rollback --site /opt/zhiheng
./release.sh status --site /opt/zhiheng
```

`status` 只读取本地发布记录，显示最近成功版本、上一成功版本和未完成操作，**不是实时健康检查**。升级期间会停止前后端应用，因此应安排维护窗口；这里不提供无中断滚动发布。

快捷升级和回滚只接受平台与两项兼容指纹均相同的版本：

- `data_sha256`：后端 SQL、插件 manifest、迁移与种子文件、初始化器，以及 `deploy/release/data-version` 的内容。
- `runtime_sha256`：基础设施镜像 ID、发布 Compose、环境辅助逻辑、LiteLLM 与 Milvus 配置等基础运行契约。

涉及 SQL、插件 manifest、初始化器或基础服务/配置变化时，必须先制定专项迁移与备份恢复方案，不能强行改写指纹来通过快捷检查。修改非 SQL 持久格式（例如知识库元数据、技能文件格式）时，维护者还必须更新源码中的 `deploy/release/data-version`；该文件是声明这类数据契约变化的标记，不是自动迁移脚本。

兼容更新失败时，管理器会尝试恢复更新前的应用版本和相应插件状态，数据库与持久文件保留；命令仍以失败退出，不能将自动恢复视为新版本部署成功。回滚不会自动降级数据库，也不是用户业务数据的时间点恢复。

- 首次部署失败：保留配置和数据，修正问题后重试同一个发布包；没有上一成功版本可回滚。
- 更新失败且自动恢复成功：记录仍指向原成功版本，排查新版本后再发布。
- 进程中断或自动恢复失败：查看 `status` 和该实例的容器日志，修正原因后执行 `rollback --site /opt/zhiheng`。未完成恢复记录会保留，以便重试；首次部署失败仍须重试原包。
- `.env` 或 `state.json` 丢失、损坏，或持久身份不一致：从对应实例备份恢复。不要删除记录、创建新密钥或把已有数据当空实例重新初始化。

不要直接运行 `docker compose -f compose.release.yaml up`；发布管理器负责装配固定镜像、实例标签、环境和运行配置路径。

## 备份与保留

每次更新前按维护窗口制定一致性备份，保留可验证的恢复副本。备份范围必须包含：

1. PostgreSQL 中平台与 LiteLLM 数据，以及 PostgreSQL、两个 Redis 的 Docker 命名卷；采用数据库原生备份或停止写入后的卷快照，不能直接复制运行中的数据库文件作为一致备份。
2. `PLATFORM_DATA_DIR` 指向的完整文件目录，包括技能、知识库、附件与 Milvus；自定义目录在安装目录外时也要备份。
3. 安装目录的 `.env`、`state.json`、`runtime/` 和 `releases/`，以及使用的项目名、卷名和挂载路径。`.env` 含解密所需的密钥与 Salt，应限制备份访问权限。

只备份 `/opt/zhiheng/data` 不完整：它没有 PostgreSQL、Redis 数据和发布身份。保留 `releases/` 中当前及上一成功版本的完整包；回滚会重新核对并加载其中的镜像，不要仅保留前后端镜像标签。恢复需要成套恢复配置、记录、文件和数据库/卷；复制安装目录本身不会自动恢复 Docker 命名卷。

本入口不自动执行数据备份、跨契约迁移或数据库降级，也不自动清理历史发布包。清理容器、镜像或卷前先确认备份与回滚所需版本；不要使用 `down --volumes` 删除实例数据。
