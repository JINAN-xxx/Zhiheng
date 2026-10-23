# AI 核心重构阶段 A 验证记录

日期：2026-10-23。分支：refactor/ai-core-modularization。范围：工程基础、依赖边界门禁、可重复前端构建与 Work 恢复生命周期修复。

本记录只证明阶段 A 的交付；AI 业务迁出插件、LangChain 1.4.7 接入替换、数据库 migrator/独立 worker 及全部业务域迁移继续按整体设计分阶段执行。未部署线上服务，未执行真实付费模型调用。

## 1. 本批实现

- 后端加入 src/zhiheng 与 py.typed，可独立导入，导入不加载旧配置、插件、FastAPI 或 SQLAlchemy。
- setuptools 在保留现有 CLI 的同时分发新核心包，wheel 检查验证核心、类型标记与 CLI，拒绝环境文件和旧运行时进入核心分发。
- tools/check_architecture.py 检查绝对/相对/函数内导入、常见动态导入和别名，约束跨域 public 接口、shared/runtime 依赖方向、旧模块引用与供应商 SDK 的 adapter 边界。
- 新核心有独立 Ruff/Mypy/pytest/wheel CI；现有后端、格式、前端工作流覆盖 refactor 分支。
- 前端提交 npm v3 锁文件，1093 条包记录均有精确版本和 integrity；既有业务依赖未漂移。
- 固定前端检查工具，限定新增 app/shared/modules 目录启用严格类型、类型感知 lint 与格式门禁；旧代码范围见前端 QUALITY.md。
- 前端 CI 使用 npm ci，生产构建后运行 preview 浏览器验收。
- Work 过期恢复不再依赖一次性 startup writer，reader 激活会执行恢复并启动循环；顺序重复激活复用循环，关闭时取消并移除任务。

## 2. 先失败后通过的证据

| 行为 | 修改前观察 | 修改后结果 |
| --- | --- | --- |
| 核心包独立导入 | 独立进程无法导入 zhiheng，类型标记不存在 | 导入及 wheel 分发验证通过 |
| 私有与反向依赖检查 | 根命名空间、相对导入、importlib 别名、Google/Groq SDK 等 7 个回归用例返回空违规 | 对应规则全部检出，新核心测试共 35 项通过 |
| wheel 中环境文件拒绝 | 根目录 .env 用例没有抛出错误 | 环境文件与旧运行时拒绝用例通过 |
| reader 恢复循环 | reader 未调用恢复，缺少 recovery_task，2 项回归失败 | Hook、顺序重复激活与关闭通过 |

静态依赖门禁针对受控代码约定，不是执行不可信 Python 的安全沙箱。

## 3. 后端实际验证

本地 Python 为 3.12.9，Mypy 1.19.1 安装于独立临时工具环境。CI 配置 Python 3.10 和 3.13，但本次没有执行远端 CI 矩阵。

~~~sh
python -m pytest tests/server tests/cli/root tests/plugins/core/runtime \
  tests/plugins/agent_extension/test_skill_hooks.py \
  tests/plugins/agent_extension/test_work_task_run_service.py tests/unit -q --tb=short
~~~

结果：461 passed，约 68 秒。

~~~sh
python -m ruff check --config ruff.toml src tests/unit \
  plugins/agent_extension/hooks.py tests/plugins/agent_extension/test_skill_hooks.py \
  ../tools/check_architecture.py ../tools/check_core_wheel.py
python -m ruff format --check --config ruff.toml src tests/unit \
  plugins/agent_extension/hooks.py tests/plugins/agent_extension/test_skill_hooks.py \
  ../tools/check_architecture.py ../tools/check_core_wheel.py
/tmp/codex-ai-core-tools/bin/python -m mypy --config-file pyproject.toml
python ../tools/check_architecture.py src/zhiheng
~~~

结果：本批 lint、格式、strict 类型和依赖方向检查通过。

~~~sh
python -m pip wheel --no-deps --no-build-isolation --wheel-dir /tmp/codex-ai-core-wheels .
python ../tools/check_core_wheel.py /tmp/codex-ai-core-wheels
~~~

结果：wheel 构建与内容验证通过，包含 cli/main.py、zhiheng/__init__.py、zhiheng/py.typed。

## 4. 前端实际验证

本地 Node.js 24.14.0、npm 11.9.0。

- 独立临时目录全新 npm ci 成功：安装 1018 个适用于本机平台的包。
- 锁文件与 package.json 的 dependencies/devDependencies 一致，完整性字段齐全；保留跨平台原生可选包。
- npm run lint:core、npm run format:check、npm run typecheck 通过。
- npm run test:plugin：57 个测试文件通过。
- Python 浏览器 helper unittest：14 项通过。
- 生产构建输出到 /tmp/zhiheng-frontend-quality-build-20261023，退出码 0。
- preview 使用临时端口 15184，五组现有 Mock API 浏览器脚本全部通过，验收后已停止。

~~~sh
VITE_APP_BASE_API=/dev-api npm run build:prod -- \
  --outDir /tmp/zhiheng-frontend-quality-build-20261023
~~~

浏览器覆盖：chat_work_mode_check.py、platform_shell_check.py、model_vision_check.py、desktop_layout_check.py、desktop_review_fixes_check.py。这证明生产产物与 Mock API 交互，不代表已连接真实后端、供应商和存储。

普通 npm registry 在本环境出现 TLS 重置；通过公开 registry.npmmirror.com 完成安装和冻结锁验证，未修改全局 registry，未使用 legacy-peer-deps 跳过兼容检查。

## 5. 审查与工作区保护

规格审查和代码质量审查均已完成。本批规格问题修复后重新执行相关测试，包括根命名空间导入、importlib 模块别名及现有供应商 SDK 的检测缺口。

开始实施前记录的 55 个既有已修改文件，字节指纹保持一致；本批提交只包含本批改动和已确认设计文档。前端既有 dist 未改变，生产构建使用临时目录。

## 6. 已有基线与未覆盖范围

全仓 Ruff lint 通过；全仓格式检查有 7 个既有 AI 改动文件未通过，均在实施前的修改指纹清单内，本批未重排其内容：

- plugins/ai/domain/model_runtime.py
- plugins/ai/infrastructure/litellm_runtime_adapter.py
- plugins/ai/service/model_service.py
- tests/plugins/ai/test_graph_configuration.py
- tests/plugins/ai/test_knowledge_graph.py
- tests/plugins/ai/test_litellm_runtime_adapter.py
- tests/plugins/ai/test_model_service.py

因此不能宣称全仓质量检查或全部 GitHub CI 已绿色。本批核心门禁与恢复修复通过，旧格式差异需在相应业务域迁移时收敛。

未覆盖：真实模型供应商、线上容器强杀、PostgreSQL 多进程锁故障窗口、完整生产压测和灾备恢复；也没有把尚未迁移的旧前端声明为已通过 strict 类型检查。

## 7. 后续阶段

阶段 B 按已确认整体设计继续处理核心 AI 装配、显式路由、独立迁移入口与对应前端切片；阶段 C 执行 langchain==1.4.7 的连接/凭据/模型契约和公开 API 替换。generator 退出线上与真实文件调度迁移仍按各自验收门槛实施。
