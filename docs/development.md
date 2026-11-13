# 开发与测试

## 环境与源码运行

后端支持 Python 3.10+，默认容器使用 Python 3.12。前端使用 Node 24 与 npm 11，依赖按锁文件安装。

~~~bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --require-hashes -r backend/requirements-core.lock
python -m pip install -r backend/requirements-dev-core.txt
cd frontend
npm ci
cd ..
~~~

使用独立开发配置，先按[部署指南](deployment.md)启动依赖，再运行 dev.sh。API 自动重载，前端热更新；Worker 修改后执行 ./dev.sh restart-worker。受管文件路径由实例配置管理，API 与 Worker 使用同一位置。

## 后端检查

~~~bash
cd backend
python -m ruff check --config ruff.toml src tests/unit tests/core ../tools
python -m ruff format --check --config ruff.toml src tests/unit tests/core ../tools
python -m mypy --config-file pyproject.toml
python ../tools/check_architecture.py src/zhiheng
python -m pytest tests/unit -q
~~~

## 前端检查

~~~bash
cd frontend
npm run test:core
npm run lint:core
npm run format:check
npm run typecheck
npm run build:platform
~~~

## 构建验证

后端使用标准 Python wheel；tools/check_core_wheel.py 核对轮子文件、迁移和独立入口。Docker 构建上下文只包含源码和运行所需配置，不包含真实环境文件、过程记录或本机数据。

## 后端集成测试

从 `backend/` 运行 `pytest tests/unit tests/core`。集成测试需显式提供独立的 `ZHIHENG_TEST_DATABASE_URL`、`ZHIHENG_TEST_REDIS_URL`，涉及向量索引时还需 `ZHIHENG_TEST_MILVUS_URI`。这些地址应指向专用测试服务。

每个业务用例拥有独立 PostgreSQL 数据库、Redis 队列、凭据密钥和临时文件目录；清理按创建时登记的精确资源执行。`ZHIHENG_TEST_RESOURCE_LOG` 可记录资源意图、创建和删除，不记录连接秘密。

## 可选的 schema 模板

设置 `ZHIHENG_TEST_SCHEMA_TEMPLATE=1` 可减少反复应用同一组迁移的成本。每个 pytest 进程创建一个仅含当前迁移和默认目录数据的模板，并禁止连接修改；普通 app 用例从该模板创建各自的新数据库，随后照常检查迁移版本、初始化账号。数据库中的测试数据不跨用例复用。

仅使用 `settings` 的迁移专项仍从空数据库开始，继续验证真实升级、降级及回填。模板本身和每例数据库都登记并精确清理。未设置该变量时保留原初始化流程。

本机资源有限时，可按测试模块分进程执行，释放 SDK 和连接缓存；每例超时与大型参数化模块的总时限应分别设置。中断后的补验保留原结果和尝试标记，不覆盖业务断言失败。统计按唯一测试 ID 对齐，不能把调用阶段通过视作整个中断会话已完成。

图谱并行专项包含连续三次构建，每次最多轮询 120 秒；其进程时限需覆盖完整三轮及资源清理，不能直接套用普通用例的 120 秒时限。延长测试外层时限不修改业务任务时限或跳过构建／计费断言。

长参数化模块建议进一步按最多25项切分为独立 pytest 进程，保留每批完整的退出状态、调用和清理结果。被内存上限中断的模块应整组复验；只有完整完成的模块才能在同一源码快照的续跑中复用。大文件参数使用简短用例标识，避免把二进制内容展开为数 MB 的节点名。

## 浏览器回归

`frontend/tests/browser/run_regressions.py` 发现所有 `*regressions.py` 中本地声明的测试方法，避免把继承来的同一用例重复计数。默认单进程串行执行；失败保留原始结果，不自动重试。

```bash
python frontend/tests/browser/run_regressions.py \
  --base-url http://127.0.0.1:15191 \
  --source-url http://127.0.0.1:15192 \
  --output /tmp/platform-browser-results
```

`--base-url` 使用当前前端构建的独立静态站点。站点的 `/api` 与 `/v1` 应返回 404，不能代理正常业务实例；用例自行提供受控 HTTP/SSE 响应。`--source-url` 是同一源码的 Vite 服务，只用于直接导入 `/src/` 的组件测试。静态站点还应提供 `/openapi/resources-v1.json` 与 `/openapi/inference-v1.json`。该层验证真实浏览器交互与接口边界，API、Worker、数据库及真实模型协议需要另做集成测试。

运行前设置 `PLATFORM_SCHEMA_FIXTURES` 为上述两份公开 schema 的目录，`PLATFORM_RESOURCE_SCREENSHOTS` 为可写截图目录。各模块可用自己的 `*_EVIDENCE` 环境变量设置截图目录。需要 Python Playwright 和 Chromium；Node 与前端依赖版本按 `frontend/package.json`。

## Windows 与局域网 HTTP

使用 Windows Python 安装的 Playwright，可通过 `--executable-path` 指定 Chrome 或 Edge。`--map-host lan-check.test` 仅给该测试浏览器添加 `lan-check.test → 127.0.0.1` 映射，不修改系统 DNS。配合 `--base-url http://lan-check.test:端口` 验证非安全上下文；必须另外核实 `window.isSecureContext === false`，不能打开“把不安全地址视为安全”的浏览器开关。

读取复制结果需要同一端口的可信 localhost 页面。设置 `PLATFORM_CLIPBOARD_READER_URL=http://127.0.0.1:端口/platform.html` 和 `CLIPBOARD_READ_ORIGIN=http://127.0.0.1:端口`。读取端验证浏览器的真实剪贴板，不给受测页面补造 `navigator.clipboard`。

浏览器内的 HTTP 探针使用页面的同源 fetch；Playwright 独立 APIRequestContext 不继承浏览器 DNS 映射。此方式验证浏览器限制，不代表实际另一台设备的网络、防火墙和路由也已通过。真实跨设备入口须单独检查。

## 结果与资源

- `manifest.json` 保存用例清单、站点和并发数；每个 worker 的 JSONL 保存结果、断言、页面异常、脚本 HTTP 错误、失败及未完成请求。
- `--tests-file` 接受用例 ID 的 JSON 数组，可做修复后的定向复核。复核应使用新输出目录，保留第一次失败证据。
- `--case-timeout` 可覆盖全部用例时限。默认每例 90 秒，明确声明的跨页面综合检查可使用单独时限（11 页刷新检查为 180 秒）；超时保留失败，不转为成功。
- `--workers` 默认 1。资源足够时可显式设为 2–4；共享剪贴板的用例应保持串行。
- 可选 `--host-memory-status` 接收外部监控的 JSON 文件，优先使用 `commit_limit`、`commit_bytes` 与 `available_phys_mb`；旧监控仅有 `commit_percent` 时仍兼容。剩余可提交内存不超过 1536 MiB（旧监控提交率达到 92%）或可用物理内存不超过 1500 MiB 时，下一用例等待；15 分钟仍无空间会明确中断，不跳过用例。

测试清单的通过数与真正执行的测试 ID 对齐。中断、跳过、环境失败和业务断言失败分别报告，不能用后续复核覆盖首次失败。

## 准备发布源码

使用 tools/export_source.py 从当前工作区导出源码快照，包含新增和已修改文件，排除删除文件、环境秘密、运行数据、历史过程记录、缓存和旧 Git 历史。

~~~bash
python tools/export_source.py --check
python tools/export_source.py --output /absolute/path/outside-project/zhiheng-ai-source.zip
~~~

新仓库需要从该快照重新初始化 Git，才能让已删除的旧源码和过程文档不进入新仓库历史。不要把旧仓库 .git 目录一起复制。运行数据库、向量、用户文件和部署密钥使用独立备份迁移，不通过源码包发布。
