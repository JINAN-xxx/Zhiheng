# 目录调整审查修复与验证

修复分支：`fix/platform-integration-review`，基于 `ec860293`。

本次处理 `c37f0fc6 → ec860293` 审查确认的 7 项问题，并按最终要求将源码目录、CLI、平台服务和新部署默认配置统一为 zhiheng。已有数据通过显式数据库配置、旧密钥读取兼容和容器迁移保护保留；部署脚本不自动重命名生产数据库或接管原 ruoyi 实例。

## 修复内容

1. 恢复 5 个部署 shell 入口的可执行权限。
2. 新部署默认使用 `zhiheng-fastapi` / `zhiheng`，支持 `PLATFORM_DB_NAME` / `PLATFORM_DB_USER` 显式选择已有旧库。新标记为 `zhiheng_bootstrap_state`，兼容历史标记；仅有旧默认库时拒绝创建空的新库。规范密钥名为 `ZHIHENG_DB_PASSWORD` / `ZHIHENG_REDIS_PASSWORD`，初始化读取旧值并写回新名称，新旧值冲突时拒绝继续。
3. 平台服务及 nginx 目标统一为 `zhiheng-backend-pg`、`zhiheng-frontend`、`zhiheng-redis`。若同项目仍有 `ruoyi-*` 旧容器，启动类操作在构建和容器变更前停止。迁移前确认并备份数据，停止移除旧平台容器且保留卷，保持原项目名、数据目录和密钥，显式选择已有数据库后执行 `update`。
4. 保留拆分部署 `docker-compose.pg.yml` 和 `deploy/legacy/manage.sh`，其中 `legacy` 表示平台与 LiteLLM 独立编排。新默认项目名为 `zhiheng-vue3-fastapi`，平台服务、容器、网络和配置变量均使用 zhiheng，外部 `litellm-network` 保持原名，旧 `RUOYI_*` 变量仅作只读回退。原实例先由原发布入口管理并完成迁移。拆分 Redis 的旧匿名卷必须通过外部卷覆盖配置显式接回，后续更新持续使用该配置；不能只保留卷后直接运行默认更新命令。具体步骤见部署说明，`init` 面向新数据库。
5. CI 改用现存前端插件测试和 mock API 浏览器检查，Ruff 检查实际后端目录及其测试。
6. 同步品牌链接、头像和主题的前端契约及浏览器定位；保留导航、可访问名称、错误展示、主题和权限断言。
7. 删除已移除工程的开发指引，更新测试安装命令、部署入口和资源身份说明。

另统一 CLI 的 `ZhihengTuiApp`、`_ZHIHENG_COMPLETE`、`ZHIHENG_TUI_REDUCED_MOTION`，旧补全脚本需重新生成；插件开发路径变量使用 `ZHIHENG_*`，旧变量仅作兼容读取。同时补齐目录调整提交中遗漏的文件末尾换行。

## 验证结果

命名统一后重新验证后端、CLI、插件、部署和前端契约。浏览器与截图比较器沿用上一轮已通过的验证，本轮未改产品界面及其比较逻辑。

| 检查 | 结果 |
| --- | --- |
| 后端部署、升级兼容、SQL、CLI 和插件环境测试 | 221 项通过 |
| 前端插件与契约检查 | 47 项通过 |
| 截图比较器边界测试 | 5 项通过 |
| Playwright 工作台检查 | 完整两轮通过，使用 mock API |
| 临时 PostgreSQL 验证 | 5 个场景通过 |
| 初始化 SQL 一致性 | `build_platform_sql.py --check` 通过 |
| 统一与拆分 Compose 配置解析、拆分部署命令目标 | 新旧密码变量两种配置均通过，值保持不变 |
| 部署脚本权限、语法和帮助入口 | 通过 |
| 本次修改的后端 Python 文件 Ruff 检查 | 通过 |
| CI YAML / Shell、文档链接、Git 差异格式 | 通过 |

前端包元数据已改为 `zhiheng-fastapi-frontend`。源码、配置和压缩前端资源已扫描，旧名称仅用于旧资源检测、兼容读取、迁移说明及相应测试。CLI 补全脚本需要重新生成，插件路径的新变量优先于旧变量。

临时 PostgreSQL 使用独立容器和内存数据目录，不暴露宿主端口，验证了旧记录保留、新旧标记识别、已有另一命名库时无创建副作用、未知非空库保护，以及真实完整 SQL 的首次与重复初始化。测试容器已清理。

浏览器在宿主环境遭遇 `ERR_NETWORK_CHANGED`，最终将临时 Vite 和浏览器置于独立网络命名空间运行，功能断言没有放宽。临时 Vite 均已停止。

浏览器截图出现同一圆形头像边缘的少量抗锯齿波动，改为比较同尺寸 RGB 像素：每张图最多 32 个不同像素，且每通道差值最多 8，两个上限必须同时满足。比较器测试验证相同图及边界噪声通过，单点明显变色、较大范围差异和尺寸变化均失败。最终两轮中四张图完全一致，应用页面仅 5 个像素差异、最大通道差 1。

## 可重复执行的检查

在后端目录执行：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q -p no:cacheprovider \
  tests/deploy tests/scripts/test_build_platform_sql.py \
  tests/cli/completion tests/cli/root/test_contract_cli.py tests/cli/tui \
  tests/plugins/core/test_environment.py tests/plugins/core/runtime/service/test_catalog.py
python scripts/build_platform_sql.py --check
```

在前端目录执行：

```bash
npm run test:plugin
python -m unittest discover -s tests/e2e -p test_screenshot_comparison.py -v
```

浏览器依赖、Vite 启动及检查命令见根 README 的“测试与质量检查”。

## 验证边界

全仓后端 Ruff 仍有 215 项范围外的存量问题，不能据本次修复宣称整个 Ruff 工作流已通过。本次没有批量修改这些无关文件或降低检查规则。

未重新构建完整平台镜像，未启动真实 LiteLLM / Milvus 全栈，未调用上游模型服务。浏览器检查使用 mock API，不能替代完整服务之间的集成验收。

改动保留在本地修复分支，未提交、推送或部署到现有运行环境。
