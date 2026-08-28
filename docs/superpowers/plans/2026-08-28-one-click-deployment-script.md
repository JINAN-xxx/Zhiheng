# 一键部署管理脚本实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:executing-plans 逐任务实现此计划。

**目标：** 在仓库根目录提供安全、幂等、可从任意目录执行的 `deploy.sh`，覆盖首次部署、启动、重启、停止、状态、日志和更新。

**架构：** 脚本只负责命令编排和依赖检查，服务定义继续由 `docker-compose.pg.yml` 管理，数据库初始化复用 `deploy/ruoyi/init-shared-postgres.sh`。所有命令通过脚本目录解析仓库根路径，避免依赖当前工作目录。

**技术栈：** POSIX `sh`、Docker Compose、ShellCheck、Node.js 静态契约测试。

---

### 任务 1：新增脚本契约测试

**文件：**
- 创建：`ruoyi-fastapi-frontend/tests/plugins/deployScriptContract.test.js`
- 修改：`ruoyi-fastapi-frontend/tests/plugins/run-plugin-tests.js`

- [x] **步骤 1：编写失败的测试**

断言仓库根目录存在可执行 `deploy.sh`，包含全部命令、默认 Compose 文件、根目录解析、依赖检查、初始化脚本调用和危险删除命令禁用。

- [x] **步骤 2：运行测试验证失败**

运行：`node tests/plugins/deployScriptContract.test.js`

预期：FAIL，报错脚本文件不存在。

### 任务 2：实现一键脚本

**文件：**
- 创建：`deploy.sh`

- [x] **步骤 1：实现依赖和路径函数**

使用 `CDPATH= cd "$(dirname "$0")"` 解析根目录；固定默认 Compose 项目名 `ruoyi-vue3-fastapi`（可由 `RUOYI_COMPOSE_PROJECT` 覆盖）；检查 Docker、Compose 和 Compose 文件；优先使用 `docker compose`，不可用时回退到 `docker-compose`。

- [x] **步骤 2：实现命令分发**

实现 `init` 调用共享 PostgreSQL 初始化脚本并执行 `compose up -d --build`；`start` 使用 `compose start`；`restart` 使用 `compose restart`；`stop` 使用 `compose stop`；`status` 使用 `compose ps`；`logs` 使用 `compose logs -f`；`update` 使用 `compose up -d --build`；`help` 输出中文用法。

- [x] **步骤 3：运行契约测试验证通过**

运行：`node tests/plugins/deployScriptContract.test.js`

预期：PASS。

### 任务 3：补充文档和验证

**文件：**
- 修改：`README.md`
- 修改：`deploy/ruoyi/README.md`

- [x] **步骤 1：补充快速使用说明**

在部署文档中加入从仓库根目录执行 `./deploy.sh init`、日常 `start/restart/stop/status/logs` 和更新命令。

- [x] **步骤 2：运行静态和运行时验证**

运行：`sh -n deploy.sh`、`shellcheck deploy.sh`（已安装时）、`npm run test:plugin`、`./deploy.sh status`、`./deploy.sh restart`，并检查 `curl http://127.0.0.1:12580/` 返回 200。

- [x] **步骤 3：提交实现**

```bash
git add deploy.sh README.md deploy/ruoyi/README.md ruoyi-fastapi-frontend/tests/plugins/deployScriptContract.test.js ruoyi-fastapi-frontend/tests/plugins/run-plugin-tests.js
git commit -m "feat: 增加一键部署管理脚本"
```
