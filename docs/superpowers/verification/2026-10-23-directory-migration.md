# 目录迁移验证记录

## 结果

当前重构分支已实际迁移为 `backend/`、`frontend/`。启动、离线发布、Compose、CI、测试、文档链接与忽略规则中的目录引用同步更新。Python 分发名、npm 包名、锁文件依赖及日志服务标识保留。

这是目录迁移，现有插件源代码仍保留在工作区，供后续提取业务实现；不代表 AI 领域实现或 LangChain 替换已经完成。

用户随后明确按新平台、新项目建设，设计与代码规范已同步。现有插件实现仅为待提取的源代码，后续新平台默认入口不加载该运行时，也不以旧管理 API、旧表结构或旧发布包兼容为首版约束。目录变化导致旧发布兼容性指纹改变是新发布链边界，未引入旧目录指纹归一化代码。

## 原有修改保护

迁移前核对先前保存的 SHA-256 指纹：55 个原有已跟踪修改文件全部一致，阶段 A 提交没有包含这些文件。

仓库外备份目录：`/home/ja/project/_local_backups/RuoYi-Vue3-FastAPI/20261023-110616-before-directory-migration/`。包含未提交补丁、状态记录、文件清单和归档。归档包含 55 个修改文件及 42,411 个 Git 可见的未跟踪文件，其中包括本地残留目录中的依赖文件；42,466 个条目逐个校验通过。Git 已忽略的运行数据不在归档范围内，本次随目录整体移动。

迁移后逐条读取归档与工作区比较：42,466 个条目完整保留。只有以下 3 个文件替换了目录引用，其余备份内容保持一致：

- `backend/docs/后端目录说明.md`
- `frontend/tests/plugins/yuxiVisualContract.test.js`
- `docs/operations/2026-10-21-desktop-typography-release.md`（原有未跟踪文件）

原有 55 个文件仍全部是未暂存修改。对它们逐一验证：迁移索引的内容等于原有 HEAD 内容加目录引用替换，没有混入原有业务优化。迁移提交与原有优化保持独立。

其他 worktree 和原有 `ruoyi-fastapi-frontend/` 残留目录未清理或修改。原有前端 `dist/` 只移动目录，未重新写入构建产物。

## 验证证据

| 检查 | 结果 |
| --- | --- |
| 既有离线发布回归先改用新目录，保留旧构建实现 | 失败：构建器无法找到新目录下的 `node_modules`；目录常量更新后通过 |
| `python -m pytest tests/unit tests/cli tests/deploy tests/plugins/core tests/plugins/sample_plugins -q --tb=short`，在 `backend/` 执行 | 1,110 通过，1 跳过，102.40 秒 |
| `npm run test:plugin`，在 `frontend/` 执行 | 57 个测试文件通过 |
| 前端 `lint:core`、`format:check`、`typecheck` | 通过，检查范围沿用阶段 A 的新代码范围 |
| 前端浏览器检查辅助函数 unittest | 14 项通过 |
| `VITE_APP_BASE_API=/dev-api npm run build:prod -- --outDir /tmp/zhiheng-directory-migration-build-20261023 --emptyOutDir` | 生产构建通过，原有 `dist/` 未覆盖 |
| `python -m ruff check backend deploy/release deploy/release_cli.py` | 通过 |
| 核心包、核心工具及本次路径缩短涉及的 3 个测试文件格式检查 | 通过 |
| Mypy、核心依赖边界检查 | 通过 |
| 在新目录构建 wheel，并执行 `tools/check_core_wheel.py` | 通过，包含核心包与兼容 CLI |
| 两份 Compose 的 `config --quiet` | 通过；仅在检查进程环境中提供占位值，不写入环境文件、不连接 Docker 服务 |
| 暂存差异空白检查 | 通过 |
| 新项目边界确定后的部署测试复核 | `tests/deploy` 178 项通过 |

独立审查通过：按用户明确的新项目边界处理旧发布链差异，未发现其他必须修复项；独立复核发布构建测试 43 项及原有修改保护校验通过。新平台独立运行和发布仍属于后续 B–F 阶段。

全量 Ruff 格式检查仍有阶段 A 已记录的 7 个原有修改文件未通过，均未批量格式化：`plugins/ai/domain/model_runtime.py`、`plugins/ai/infrastructure/litellm_runtime_adapter.py`、`plugins/ai/service/model_service.py` 和对应 4 个 AI 测试文件。目录路径缩短额外触发的 3 个干净测试文件已单独按 Ruff 格式调整。

未启动部署，未修改数据库，未调用收费模型。本次没有重复执行阶段 A 的完整浏览器场景；已执行前端测试、辅助函数测试和实际生产构建。
