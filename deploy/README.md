# 部署与版本交付

项目支持两种独立流程：镜像发布将完整版本包交给服务器；源码部署保留仓库内构建和运维方式。新发布实例使用固定安装目录，与已有源码实例分开管理。

| 流程 | 入口 | 配置与版本管理 |
| --- | --- | --- |
| 镜像发布 | 构建端 `./deploy.sh release build`；服务器包内 `./release.sh` | `--site` 安装目录的 `.env`、`runtime/`、`releases/`、`state.json`，兼容版本可应用回滚 |
| 源码统一部署 | 仓库根目录 `./deploy.sh init/update/...` | 仓库 `.env`、`compose.yaml`，按现有初始化与增量迁移流程更新 |

## 离线镜像发布

构建机准备 Node.js 24、npm、Python 3.10+ 与 Docker，在源码根目录执行：

```bash
npm install --prefix frontend
./deploy.sh release build --version 2026.09.30-rc1 --output /path/new-bundle
./deploy.sh release verify --bundle /path/new-bundle
```

输出目录必须尚不存在。发布包包括六个镜像的 `images.tar`、`release.json`、文件 SHA-256 校验值、运行脚本和配置；清单记录源码 revision、dirty 状态和快照哈希。应用镜像 tag 绑定版本与源码哈希，基础设施 tag 绑定镜像 ID，以便兼容升级复用原基础服务。

复制完整目录到 Linux amd64 服务器后，在包根目录执行：

```bash
./release.sh verify
./release.sh deploy --site /opt/zhiheng
./release.sh status --site /opt/zhiheng
./release.sh rollback --site /opt/zhiheng  # 需要已有上一成功版本
```

服务器需要 Python 3.10+、Docker Engine 与支持 `up --wait` 的 Compose v2，不需要源码 checkout 或 Node.js。包内 `--bundle` 默认为当前发布包；通过源码入口执行 `verify` / `deploy` 时需显式使用 `--bundle /path/new-bundle`。

所有版本沿用同一个安装目录，持久配置和密钥保存在其 `.env`，文件默认在 `data/`，固定运行配置在 `runtime/`，已接收发布包在 `releases/`，版本记录在 `state.json`。默认项目名由安装路径派生；首次部署前可在安装目录 `.env` 中配置项目名、端口、监听地址和数据目录。不要复制源码实例的 `.env` 或试图接管其容器，源码实例迁移须单独规划。

快捷更新与回滚仅允许数据和运行契约指纹相同的版本；兼容更新失败会尝试恢复原应用。SQL、插件 manifest、初始化器和非 SQL 数据格式（通过 `deploy/release/data-version` 声明）等变化必须制定专项备份与迁移方案，不自动降级数据库。`status` 是发布记录，不是实时健康检查。`.env` / `state.json` 丢失应恢复备份，不应重新初始化身份。

备份必须包括 Docker 命名卷、完整文件数据、`.env`、`state.json`、`runtime/` 和 `releases/`，不能只复制安装目录的 `data/`。文件 checksum 不是数字签名，只接收可信来源的包；后端 Python 镜像仍包含运行代码，不提供源码加密保证。

完整复制与可选 tar 打包命令、首次配置、失败恢复及备份边界见 [镜像发布包说明](./release/README.md)。以下章节继续说明源码部署，不能将 `./deploy.sh update` 的数据库迁移行为视为镜像发布的快捷升级。

## 源码部署：首次启动

宿主机准备 Docker Engine、Docker Compose v2（支持 `up --wait`）和 Python 3.10+。建议提供 8 GB 内存和足够的镜像/数据库磁盘空间。前端构建产物已经在版本库中，部署不需要 Node.js。

在新机器拉取仓库后执行：

```bash
./deploy.sh init
```

脚本自动生成私密 `.env`，构建后端，启动 PostgreSQL、两个 Redis、LiteLLM、Milvus，导入完整数据库结构，创建网关运行密钥，安装内置插件，最后启动平台和 nginx。访问 `http://服务器IP:12580/`。

初始管理员为 `admin`，默认密码为 `admin123`；首次登录后修改密码。

需要修改端口时，可先复制配置模板：

```bash
cp deploy/platform.env.example .env
# 编辑 PLATFORM_PORT，例如改成 8060
./deploy.sh init
```

`init` 可以重复执行，已保存的密钥和数据库不会重置。首次下载镜像和 Python 包需要网络，图谱分词资源已随源码提供。模型服务商凭据在进入平台后通过「模型中心」配置。

## 源码部署：更新和运行

```bash
./deploy.sh update          # 先构建镜像，再暂停应用、迁移并启动
./deploy.sh start           # 启动完整服务并等待健康检查
./deploy.sh restart         # 重启平台应用，不重启数据库
./deploy.sh stop            # 停止服务，保留数据
./deploy.sh status
./deploy.sh logs --tail=100
```

`--no-build` 可用于已经准备好后端镜像的环境。默认 Compose 项目名为 `zhiheng`。也可以用 `PLATFORM_ENV_FILE=/绝对路径/配置文件 ./deploy.sh init` 指定环境文件；其中的数据目录相对于仓库根目录解析。

源码目录、CLI 和新部署默认配置统一使用 `zhiheng` 命名，平台 Compose 服务为 `zhiheng-backend-pg`、`zhiheng-frontend`、`zhiheng-redis`。更新时保留原有 `PLATFORM_COMPOSE_PROJECT`、`PLATFORM_DATA_DIR`、数据卷和密钥。

### 旧命名实例升级

若同一 Compose 项目仍有 `ruoyi-backend-pg`、`ruoyi-frontend`、`ruoyi-redis` 容器，统一入口会在构建或变更容器前拒绝启动类操作，已停止的旧容器也会触发检查。已有统一部署按以下步骤迁移：

1. 确认容器的 `com.docker.compose.project`、`com.docker.compose.service` 标签及数据挂载，备份 `.env`、数据库、数据卷和文件目录。
2. 在 `.env` 中保留原 `PLATFORM_COMPOSE_PROJECT`、`PLATFORM_DATA_DIR` 和全部密钥。确认已有数据库及 Owner；原库为 `ruoyi-fastapi` / `ruoyi` 时，显式设置 `PLATFORM_DB_NAME=ruoyi-fastapi` 和 `PLATFORM_DB_USER=ruoyi`；原库已为 `zhiheng-fastapi` / `zhiheng` 时，显式设置对应值。
3. 停止并移除确认过的三个 `ruoyi-*` 服务容器，保留其数据卷和 PostgreSQL 实例。不要使用 `down --volumes` 或 `docker rm -v`。
4. 确认同项目的旧命名容器已移除后，执行 `./deploy.sh update`；新的 `zhiheng-*` 服务使用原项目的数据卷和明确指定的数据库。

`RUOYI_DB_PASSWORD` / `RUOYI_REDIS_PASSWORD` 仅作为旧配置的读取兼容；对应新名称为 `ZHIHENG_DB_PASSWORD` / `ZHIHENG_REDIS_PASSWORD`。初始化写回新名称并保留原值；新旧名称同时存在且值不同时拒绝继续。手动整理 `.env` 时也必须保留原密钥值。

以上步骤适用于已有统一部署。原拆分部署先使用原发布入口管理并完成迁移，参见 [拆分部署兼容入口](#拆分部署兼容入口)。

## 数据库入口

平台数据库默认名称为 `zhiheng-fastapi`、Owner 为 `zhiheng`。新部署可在初始化前通过 `.env` 的 `PLATFORM_DB_NAME` 和 `PLATFORM_DB_USER` 指定名称；保留旧库的实例应明确设置这两个值，指向已有数据库。这些配置只选择数据库；要将实体库名和账号也改成 `zhiheng` 命名，需要单独规划数据库迁移，部署脚本不会自动重命名生产数据。

新库使用 `public.zhiheng_bootstrap_state` 完成标记，初始化兼容已有 `public.ruoyi_bootstrap_state`。仅存在旧默认库 `ruoyi-fastapi`、新默认库 `zhiheng-fastapi` 不存在时，脚本拒绝自动创建空库，要求显式选择已有数据库或先完成迁移。

完整初始化文件为 [`backend/sql/platform-pg.sql`](../backend/sql/platform-pg.sql)。该文件按基础 SQL、AI 插件、智能体扩展的迁移和种子自动生成，包含成功迁移账本。

维护迁移后，在后端开发环境重新生成并提交：

```bash
cd backend
python scripts/build_platform_sql.py
python scripts/build_platform_sql.py --check
```

合并文件用于空库；正常部署会将 SQL 和初始化完成标记放在同一个事务中执行，失败整体回滚。已有统一部署通过插件增量迁移更新，不重复导入合并文件。手工初始化空库时必须使用单事务和错误中止选项，且应通过统一初始化任务建立完成标记。

知识图谱按知识库创建的表和 Milvus 集合由 LightRAG 在启用时初始化，无需手工建表。

## 持久数据

统一栈的 PostgreSQL、平台 Redis 和 LiteLLM Redis 使用 Compose 项目专属的命名卷，保留原项目名和卷即可复用。拆分部署的 Redis `/data` 默认使用镜像声明的匿名卷，服务更名后必须显式接回旧卷，见 [拆分 Redis 旧卷迁移](#拆分-redis-旧卷迁移)。技能、知识库原文和元数据、平台附件及 Milvus 数据保存在 `PLATFORM_DATA_DIR` 下。备份时同时保存 `.env`、数据库卷和这些文件目录。

统一栈只向宿主机开放 nginx 端口，浏览器接口走 `/docker-api`，开放 API 走 `/v1`。数据库、Redis、Milvus、LiteLLM 在内部网络通信。

## 拆分部署兼容入口

`deploy/legacy/manage.sh` 保留平台与 LiteLLM 分开编排的部署方式，路径中的 `legacy` 表示这一编排方式。已使用当前 zhiheng 命名、且无需额外覆盖配置的拆分实例可执行：

```bash
./deploy/legacy/manage.sh status
./deploy/legacy/manage.sh update
```

该入口默认使用根目录 [`docker-compose.pg.yml`](../docker-compose.pg.yml)，新默认项目名为 `zhiheng-vue3-fastapi`，平台服务、容器和平台网络使用 `zhiheng` 命名，通过外部 `litellm-network` 连接独立 LiteLLM 栈。使用 `ZHIHENG_COMPOSE_PROJECT`、`ZHIHENG_COMPOSE_FILE`、`ZHIHENG_ENV_FILE` 选择项目、编排文件和环境文件；初始化时可用 `ZHIHENG_SHARED_POSTGRES_CONTAINER` 指定共享 PostgreSQL 容器。对应 `RUOYI_*` 变量仅作为只读回退兼容。不要在拆分部署中直接运行根目录的 `docker compose up`，它会选择统一栈 `compose.yaml`。

原 `ruoyi` 实例应先沿用原发布入口管理；按发布目录管理的实例继续使用相应 `data/agent-releases/.../manage.sh`。迁移前确认并备份数据库、数据卷、LiteLLM Salt、全部密钥和文件数据，保持原项目名、数据目录和密钥；先按下文记录并配置旧 Redis 卷，再停止并移除确认过的旧平台容器且保留卷。拆分 Compose 支持用 `PLATFORM_DB_NAME` / `PLATFORM_DB_USER` 显式选择已有数据库。完成容器和数据挂载迁移后，使用保留旧卷覆盖配置的更新命令。`init` 面向新数据库，仅存在旧默认库时会拒绝另建空库。当前脚本不会直接接管原 `ruoyi` 实例。

统一入口检测到拆分部署环境文件时会停止。切换到统一栈需要单独迁移数据库和文件数据，不能仅通过更改项目名或重跑 `init` 完成。

### 拆分 Redis 旧卷迁移

拆分 Redis 的匿名卷不会因保留项目名而自动接到更名后的服务。`docker rm` 不带 `-v` 只保留卷；必须在替换容器前显式配置挂载。

先确认实际旧 Redis 容器名，通过以下命令查看挂载信息，记录目标为 `/data` 的卷 `Name`，并在停止写入后备份该卷及相关配置。此命令仅显示挂载，不显示环境变量中的密钥：

```bash
# 将 ruoyi-redis 替换为已确认的旧 Redis 容器名
docker inspect --format '{{json .Mounts}}' ruoyi-redis
```

在仓库根目录保存 `redis-migration.yaml`，将 `<实际旧卷>` 替换为刚记录的卷名：

```yaml
services:
  zhiheng-redis:
    volumes:
      - previous-redis-data:/data
volumes:
  previous-redis-data:
    external: true
    name: '<实际旧卷>'
```

直接运行 Compose 前，将 `.env` 中旧 `RUOYI_DB_PASSWORD`、`RUOYI_REDIS_PASSWORD` 的键名分别改为 `ZHIHENG_DB_PASSWORD`、`ZHIHENG_REDIS_PASSWORD`，保留原值及 `0600` 权限，不打印或重新生成密钥；若新旧键同时存在，先确认值一致再保留规范名称。管理脚本能在内存中兼容旧变量，直接 Compose 只识别新密钥名。还需显式设置 `PLATFORM_DB_NAME` / `PLATFORM_DB_USER` 指向已有数据库。

确认备份、覆盖配置和密钥后，停止并移除旧平台容器且保留卷，再使用原项目名启动。将下面的 `原项目名` 替换为旧容器标签中确认的项目名：

```bash
docker compose --project-name '原项目名' --env-file .env \
  -f docker-compose.pg.yml -f redis-migration.yaml up -d --build
```

启动后用 `docker inspect --format '{{json .Mounts}}' zhiheng-redis` 确认 `/data` 挂载的是记录的旧卷。部署和后续更新必须始终使用同一项目名、环境文件及这两个 `-f` 配置；将覆盖文件纳入部署配置备份。不要改回默认的 `./deploy/legacy/manage.sh update`，它未加载此覆盖文件，会丢失显式旧卷挂载配置。

## 失败排查

如果初始化失败，后端不会启动，检查初始化任务与依赖状态：

```bash
./deploy.sh status
./deploy.sh logs --tail=100 platform-init
```

修正配置后重试 `./deploy.sh init`。更新时构建失败会保留正在运行的应用；数据库迁移失败会保留数据、停止后端继续启动，以免新代码访问不完整结构。
