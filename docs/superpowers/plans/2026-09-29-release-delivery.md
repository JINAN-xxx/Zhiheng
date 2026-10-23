# 版本化发布与回滚实现计划

> **面向 AI 代理的工作者：** 使用 subagent-driven-development 执行有独立边界的任务，并完成规格与代码审查。用户已授权实现，沿用当前工作区未提交改造，不额外提交或部署现有实例。

**目标：** 构建可搬运的版本化镜像包，支持独立实例部署、校验和兼容应用回滚。

**架构：** `manifest.py` 管理产物契约，`build.py` 构建和打包，`environment.py` 管理实例配置，`manager.py` 管理部署事务，`cli.py` 提供命令。运行编排独立于源码编排，所有业务数据固定在安装目录之外的版本无关位置。

**技术栈：** Python 标准库、Docker / Compose、现有 Vue/Vite 和 FastAPI。

## 任务 1：发布契约与构建

- 创建 `deploy/release/{__init__,manifest,build}.py`、`deploy/release/data-version`、发布 Dockerfile；在 `backend/tests/deploy/test_release_build.py` 验证。
- [x] 先验证版本非法、文件篡改、符号链接、镜像错配、重复版本与敏感环境文件排除失败。
- [x] 实现清单校验、源码快照摘要、隔离前端构建、后端镜像、镜像归档及完整运行包。
- [x] 运行 `python3 -m pytest -q tests/deploy/test_release_build.py` 并审查规格与代码。

## 任务 2：实例配置与运行编排

- 创建 `deploy/release/environment.py` 和 `deploy/compose.release.yaml`；新增 `tests/deploy/test_release_environment.py`。
- [x] 先验证配置稳定、密钥不入包、绝对数据目录、环境覆盖隔离和镜像化编排。
- [x] 复用既有环境配置初始化，生成持久传输密钥；按清单锁定镜像并输出私有实例配置。
- [x] 校验 `docker compose ... config --quiet` 和行为测试；真实密钥不打印。

## 任务 3：部署事务与 CLI

- 创建 `deploy/release/{manager,cli}.py`、`deploy/release_cli.py`、`deploy/release.sh`；修改 `deploy/platform.py` 的 release 分派；新增 `tests/deploy/test_release_manager.py`。
- [x] 先验证健康检查失败不前移版本、同契约失败自动回退、不同契约停机前拒绝、互斥锁和首次部署重试。
- [x] 实现 `build / verify / deploy / rollback / status` 命令、原子状态、操作日志与实例所有权检查。
- [x] 回滚只重建应用，不运行旧初始化，不删除卷；验证运行镜像与前后端版本。

## 任务 4：演练与文档

- 更新 `README.md`、`deploy/README.md`、`代码结构说明.md`，新增 `docs/operations/2026-09-30-release-delivery.md`。
- [x] 运行部署测试、Python 检查与 Shell / Compose 校验。
- [x] 实际生成发布包；用隔离实例验证部署、升级、回滚、失败恢复，完成独立审查。
- [x] 核对既有服务容器和前端 dist 未修改，记录结果与数据库升级限制。

验收完成，详见[发布与回滚记录](../../operations/2026-09-30-release-delivery.md)。因存储约束仅保留最新完整发布包，临时测试实例和重复归档已清理。
