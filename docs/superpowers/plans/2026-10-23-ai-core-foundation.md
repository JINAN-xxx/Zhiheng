# AI 核心重构阶段 A 实现计划

> **面向 AI 代理的工作者：** 使用 subagent-driven-development 逐任务实施，规格审查后再做质量审查。用户已授权开始实施，执行过程无需再次选择工作方式。

**目标：** 建立可运行、可检查、可重复构建的重构基础，并修复已经确认的 Work 恢复生命周期缺口。

**架构：** 在现有项目路径内先引入可安装的 src/zhiheng 核心包，保持现有服务入口作为迁移兼容层。使用 AST 约束新包依赖；前端固定安装环境并验证生产构建；恢复循环与一次性插件 writer 解耦。顶层目录重命名与后续业务迁移在对应阶段执行。

**技术栈：** Python 3.10+、setuptools、pytest、Ruff、Mypy；Vue 3/Vite、TypeScript、ESLint、Prettier、npm lock；目标模型运行主包固定 langchain==1.4.7。

## 执行约束

- 当前分支 refactor/ai-core-modularization，基准 1cd3307b，工作区有已有修改。
- 不回退已有修改，不批量格式化旧代码，不复制或提交 .env、真实密钥、node_modules、构建机文件。
- 新旧实现的生产切换在阶段 B–F 的写入屏障内执行，阶段 A 不修改线上数据库或重启服务。
- 配置/锁文件使用真实工具验证；功能和 bug 修复先观察失败测试，再实现。
- 实现任务按文件职责分开；共享工作区内一个时刻最多一个子实现者，主代理负责独立范围及集成。
- 状态以本计划复选框及实际命令结果为准。

## 文件责任

| 范围 | 文件 |
| --- | --- |
| 新核心包与打包 | zhiheng-fastapi-backend/src/zhiheng、pyproject.toml |
| 依赖方向门禁 | tools/check_architecture.py、后端 tests/unit/test_architecture_rules.py |
| 核心包导入隔离 | 后端 tests/unit/test_core_package.py |
| 后端新代码质量 | 后端开发依赖、类型配置与 .github/workflows 中的新核心门禁 |
| 前端可重复安装 | 前端 package.json、package-lock.json、.gitignore |
| 前端质量与 TS 范围 | eslint/prettier/tsconfig 配置及渐进兼容清单 |
| 生产构建验收 | .github/workflows/playwright.yml、前端现有测试入口 |
| Work 恢复修复 | plugins/agent_extension/hooks.py 与对应后端回归测试 |
| 结果记录 | docs/superpowers/verification/2026-10-23-ai-core-foundation.md |

## 任务 1：建立核心包及真实导入隔离测试

- [x] 编写后端 tests/unit/test_core_package.py，从独立 Python 进程导入 zhiheng，断言没有加载 config、plugins、SQLAlchemy 引擎或连接实例。
- [x] 执行测试，确认当前不存在核心包而失败。
- [x] 创建 src/zhiheng/__init__.py 与 py.typed；更新 setuptools 发现规则，保留 cli 分发与命令入口。
- [x] pytest 的 pythonpath 增加 src，旧测试仍能从后端根目录导入兼容模块。
- [x] 运行测试，并构建 wheel 到临时目录，检查同时包含 cli/main.py、zhiheng/__init__.py 和类型标记。

隔离验证的核心断言：

~~~python
code = """
import sys
import zhiheng
assert 'config.env' not in sys.modules
assert 'plugins' not in sys.modules
assert 'sqlalchemy' not in sys.modules
"""
~~~

验收命令：

~~~bash
python -m pytest tests/unit/test_core_package.py -q
python -m pip wheel --no-deps --no-build-isolation --wheel-dir /tmp/zhiheng-core-wheel .
~~~

## 任务 2：实施可执行的模块依赖门禁

- [x] 先以临时源代码目录编写有行为含义的 AST 检查测试，覆盖允许的 public 依赖、拒绝跨域私有导入、共享层反向依赖、相对导入和动态导入。
- [x] 运行测试，观察检查器缺失导致的失败。
- [x] 实现 tools/check_architecture.py，提供可测试检查函数及命令入口，诊断包括路径、行号和规则。
- [x] 同模块内部导入允许；跨模块只允许 public；shared 不依赖业务或框架；模块不依赖 api/worker/bootstrap/application；runtime 不反向依赖业务。
- [x] 函数内导入按同样规则检查；importlib 的可解析字符串导入检查目标，不可解析的动态导入输出违规。
- [x] SDK 导入仅允许所属 adapters；新核心包禁止导入旧 module_*、plugins 和全局配置。
- [x] 运行门禁和测试，补齐新核心代码 Ruff/Mypy 检查。

最小使用契约：

~~~python
violations = check_boundaries(source_root)
assert violations == []
~~~

验收命令：

~~~bash
python -m pytest tests/unit/test_architecture_rules.py -q
python tools/check_architecture.py zhiheng-fastapi-backend/src/zhiheng
~~~

## 任务 3：前端锁定安装、渐进类型和生产构建门禁

- [x] 保留现有业务依赖语义，生成并提交 package-lock.json；移除锁文件忽略规则。
- [x] 统一 CI 使用 npm ci；确保新重构分支及目标 PR 能触发检查。
- [x] 固定 ESLint、Prettier、TypeScript、vue-tsc 工具版本并使用迁移范围配置；不以全量忽略新增核心代码换取通过。
- [x] 旧 JS 保留明确兼容范围，新 TS app/shared 与迁移模块纳入 strict；提供可扩大的检查入口。
- [x] 生产构建进入前端工作流，浏览器检查针对构建产物运行。
- [x] 本地生产构建输出到临时目录，保留用户工作区原有 dist。
- [x] 运行现有前端插件测试、质量门禁和生产构建；记录真实结果。

目标脚本入口：

~~~text
npm run lint:core
npm run format:check
npm run typecheck
npm run test:plugin
npm run build:docker
~~~

## 任务 4：修复 reader 启动缺少 Work 恢复循环

- [x] 在对应后端测试中调用实际 Agent on_startup，使用 reader context，验证会调用过期恢复且创建周期恢复任务。
- [x] 先运行该测试，观察 writer-only 实现失败。
- [x] 内置技能的全局写入仍只由 writer 执行；进程期 Work 恢复由每个激活进程显式管理。
- [x] 核对 recover_expired 的条件更新，保证重复扫描不产生重复终态；必要的所有权控制以现有数据库状态为准。
- [x] 增加关闭/重复激活回归，避免重复循环或遗留未关闭 task。
- [x] 运行相关 hook、Work 运行和插件启动测试。

测试必须验证真实恢复注册和关闭行为，不只对源码字符串断言 if 分支位置。

## 任务 5：集成审查与证据

- [x] 规格审查：逐项核对核心包、边界规则、前端构建、恢复修复及旧路径兼容。
- [x] 质量审查：检查资源清理、类型、异常、打包和工作区已有修改保护。
- [x] 运行新测试、相关回归、静态检查、前端测试与临时生产构建。
- [x] 记录通过、失败、跳过和未覆盖范围；不把阶段 A 记作整个重构完成。
- [x] 更新本计划状态，形成阶段 A 验证记录。

阶段 B 的核心装配与迁移使用独立实施计划，按整体设计继续推进。
