# 平台目录调整审查问题修复计划

> 面向 AI 代理的工作者：使用 subagent-driven-development 分工执行；各任务按下列文件边界实现，完成后复核规格和代码质量。

**目标：** 修复 ec860293 相对 c37f0fc6 的 7 项部署、升级、CI 和文档回归。

**架构：** 源码目录、CLI 和新部署默认配置统一使用 zhiheng 命名：数据库 `zhiheng-fastapi`、账号 `zhiheng`、平台 Compose 服务 `zhiheng-*`，新完成标记 `zhiheng_bootstrap_state`。密钥使用 `ZHIHENG_DB_PASSWORD` / `ZHIHENG_REDIS_PASSWORD`；旧变量仅作读取兼容，初始化写回规范名并保留值，冲突时拒绝继续。已有旧库通过 `PLATFORM_DB_NAME` / `PLATFORM_DB_USER` 显式选择，历史完成标记继续识别；仅有旧默认库时拒绝自动创建空的新库。同项目 `ruoyi-*` 容器需明确迁移后再更新。保留拆分部署编排入口，新默认项目名为 `zhiheng-vue3-fastapi`，原 `ruoyi` 实例先沿用原发布入口管理并完成迁移。

**技术栈：** Python / pytest、Docker Compose、Vue / Node.js、GitHub Actions。

## 任务 1：部署与已有数据兼容

文件：`compose.yaml`、`deploy/platform.py`、`deploy/platform.env.example`、`deploy/legacy/manage.sh`、恢复的 `docker-compose.pg.yml`、`deploy/zhiheng/init-shared-postgres.sh`、`zhiheng-fastapi-backend/scripts/initialize_platform.py`、`zhiheng-fastapi-frontend/bin/nginx.dockerpg.conf`、后端 `tests/deploy/`。

- [x] 在现有部署测试中覆盖 zhiheng 默认命名、旧容器迁移保护、旧密钥读取与冲突处理、显式选择旧数据库与历史标记、仅有旧库时拒绝创建空库。
- [x] Compose / 部署 runner / nginx 使用 zhiheng-* 服务身份，Compose 用 `${PLATFORM_DB_USER:-zhiheng}` 和 `${PLATFORM_DB_NAME:-zhiheng-fastapi}` 选择数据库；同项目存在 ruoyi-* 容器时，在构建和变更容器前拒绝启动类操作。
- [x] 新初始化标记使用 zhiheng_bootstrap_state，兼容历史 ruoyi_bootstrap_state；仅有旧默认库时中止并提示显式配置 PLATFORM_DB_NAME / PLATFORM_DB_USER 或完成迁移，不自动重命名生产数据。
- [x] 保留拆分部署的 docker-compose.pg.yml 和 deploy/legacy/manage.sh；legacy 表示平台与 LiteLLM 独立编排。新默认项目为 zhiheng-vue3-fastapi，服务、容器、平台网络和配置变量统一 zhiheng，外部 litellm-network 保持原名；RUOYI_* 仅为只读回退兼容。已有实例完成迁移后使用 update，init 面向新数据库。
- [x] 恢复 5 个 shell 部署入口可执行位。
- [x] 运行部署与 SQL 生成测试、Compose 配置解析、临时旧 PostgreSQL 数据升级验证。

## 任务 2：CI 和文档入口

文件：`.github/workflows/playwright.yml`、`.github/workflows/ruff.yml`、`README.md`、`代码结构说明.md`、部署 README 文件；需要时恢复 PostgreSQL 测试工程。

- [x] 将 CI 迁移到真实存在且可验证的测试入口，保留端到端验证，不能只删掉失败任务。
- [x] 不引用已删除的移动端、独立测试路径；按最终 CI 方案更新依赖安装和测试命令。
- [x] 文档说明 zhiheng 默认命名、旧实例备份与容器迁移、保留项目名/数据目录/密钥、显式选择已有数据库，以及拆分部署和统一部署的对应入口。
- [x] 验证 YAML、所有 CI 路径、当前使用的浏览器测试入口和文档本地链接。

## 任务 3：前端契约测试

文件：`zhiheng-fastapi-frontend/tests/plugins/platformShellContract.test.js`、`yuxiVisualContract.test.js`。

- [x] 以已复现的 2 项失败为基线，同步品牌链接与图片标志的契约；保留导航目标、可访问名称和主题表面的检查。
- [x] 运行 `npm run test:plugin`，期望 47 项全部通过。

## 验收

- [x] 按报告逐项核对 7 项问题，进行独立规格与质量复核。
- [x] 运行针对性后端回归、前端测试及相关配置验证，记录实际结果和边界。
- [x] 保留改动供用户检查，不推送或部署生产环境。

验收记录：`docs/operations/2026-09-28-platform-review-fixes.md`。前轮验收中，浏览器像素比较新增 5 项边界测试，完整两轮检查通过；本轮命名统一后的结果由最终验收更新。全仓 Ruff 存量问题按验收记录单独披露。
