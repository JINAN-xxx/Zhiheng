# 一键部署管理脚本设计

## 目标

在仓库根目录提供一个统一的 `deploy.sh`，让当前 PostgreSQL 部署支持首次初始化、启动、重启、停止、状态查看、日志查看和重新构建更新。脚本默认使用 `docker-compose.pg.yml`，不重复导入数据库基线，不覆盖现有密钥。

## 命令接口

```bash
./deploy.sh init       # 首次部署或补齐基础环境后启动
./deploy.sh start      # 启动已有容器
./deploy.sh restart    # 重启已有容器
./deploy.sh stop       # 停止平台容器
./deploy.sh status     # 查看 Compose 服务状态
./deploy.sh logs       # 查看后端、前端和 Redis 日志
./deploy.sh update     # 重新构建镜像并启动
./deploy.sh help       # 查看帮助
```

`init` 要求 Docker 可用，并检查共享 LiteLLM 容器 `litellm-db` 已运行；随后调用现有 `deploy/zhiheng/init-shared-postgres.sh` 完成幂等密钥和数据库初始化，最后构建并启动 PostgreSQL Compose 服务。`start`、`restart`、`stop`、`status` 和 `logs` 不执行数据库初始化。`update` 只重新构建当前 Compose 文件中的镜像并启动，不删除数据卷。

## 文件与配置

- `deploy.sh`：根目录入口脚本，解析命令、校验依赖、调用 Compose 和数据库初始化脚本。
- `docker-compose.pg.yml`：继续作为唯一的 PostgreSQL 服务编排来源，不在脚本中复制服务定义。
- `.env`：由现有初始化脚本维护，脚本只检查存在性，不打印密钥内容。

脚本通过脚本自身目录计算仓库根路径，因此从任意当前目录执行都有效。可通过 `DOCKER_COMMAND` 覆盖 Docker 命令，通过 `COMPOSE_COMMAND` 覆盖 Compose 命令，通过 `ZHIHENG_COMPOSE_FILE` 选择 Compose 文件，通过 `ZHIHENG_COMPOSE_PROJECT` 指定 Compose 项目名；默认项目名为 `zhiheng-vue3-fastapi`，避免从不同目录执行时找不到已有容器。

## 安全与错误处理

- 缺少 Docker、Compose、`openssl`、`flock` 或 Compose 文件时立即失败并给出中文提示。
- `init` 只调用现有幂等初始化脚本，不使用删库或强制删除容器的命令。
- `stop` 使用 `compose stop`，保留容器和数据卷，便于后续 `start` 恢复。
- `restart` 使用 `compose restart`；当服务尚未创建时提示先执行 `init` 或 `start`。
- `logs` 默认跟随最近日志，支持透传参数，例如 `./deploy.sh logs --tail=100`。
- 每个命令执行后返回 Compose 原始退出码；成功时输出访问地址和下一步提示。

## 测试策略

使用 Shell 静态契约测试覆盖命令分发、默认 Compose 文件、根目录解析、密钥脱敏和危险命令禁用；使用 ShellCheck（若已安装）检查脚本语法。验证阶段在现有 Docker 环境执行 `status`、`restart`、`stop`、`start` 和 `init` 的非破坏性路径，并确认平台健康检查和首页 HTTP 200。
