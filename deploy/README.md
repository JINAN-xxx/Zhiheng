# 一键部署

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

## 更新和运行

```bash
./deploy.sh update          # 先构建镜像，再暂停应用、迁移并启动
./deploy.sh start           # 启动完整服务并等待健康检查
./deploy.sh restart         # 重启平台应用，不重启数据库
./deploy.sh stop            # 停止服务，保留数据
./deploy.sh status
./deploy.sh logs --tail=100
```

`--no-build` 可用于已经准备好后端镜像的环境。默认 Compose 项目名为 `zhiheng`。也可以用 `PLATFORM_ENV_FILE=/绝对路径/配置文件 ./deploy.sh init` 指定环境文件；其中的数据目录相对于仓库根目录解析。

## 数据库入口

完整初始化文件为 [`zhiheng-fastapi-backend/sql/platform-pg.sql`](../zhiheng-fastapi-backend/sql/platform-pg.sql)。该文件按基础 SQL、AI 插件、智能体扩展的迁移和种子自动生成，包含成功迁移账本。

维护迁移后，在后端开发环境重新生成并提交：

```bash
cd zhiheng-fastapi-backend
python scripts/build_platform_sql.py
python scripts/build_platform_sql.py --check
```

合并文件用于空库；正常部署会将 SQL 和初始化完成标记放在同一个事务中执行，失败整体回滚。已有统一部署通过插件增量迁移更新，不重复导入合并文件。手工初始化空库时必须使用单事务和错误中止选项，且应通过统一初始化任务建立完成标记。

知识图谱按知识库创建的表和 Milvus 集合由 LightRAG 在启用时初始化，无需手工建表。

## 持久数据

PostgreSQL、平台 Redis 和 LiteLLM Redis 使用 Compose 项目专属数据卷。技能、知识库原文和元数据、平台附件及 Milvus 数据保存在 `PLATFORM_DATA_DIR` 下。备份时同时保存 `.env`、数据库卷和这些文件目录。

统一栈只向宿主机开放 nginx 端口，浏览器接口走 `/docker-api`，开放 API 走 `/v1`。数据库、Redis、Milvus、LiteLLM 在内部网络通信。

## 旧部署

旧的拆分部署入口迁至：

```bash
./deploy/legacy/manage.sh status
./deploy/legacy/manage.sh update
```

已有按发布目录管理的实例继续使用相应 `data/agent-releases/.../manage.sh`。统一入口检测到旧环境文件时会停止，避免把旧密码或数据卷接到新建实例。迁移旧实例需要同时迁移其数据库、LiteLLM Salt、密钥及文件数据；本次不会自动接管正在运行的旧实例。

## 失败排查

如果初始化失败，后端不会启动，检查初始化任务与依赖状态：

```bash
./deploy.sh status
./deploy.sh logs --tail=100 platform-init
```

修正配置后重试 `./deploy.sh init`。更新时构建失败会保留正在运行的应用；数据库迁移失败会保留数据、停止后端继续启动，以免新代码访问不完整结构。