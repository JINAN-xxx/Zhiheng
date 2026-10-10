# 前端四个公共客户端完成记录

日期：2026-09-29。工作区 `/home/ja/project/zhiheng`，分支 `fix/platform-integration-review`。

## 结果

已完成前端分步计划的第 2–5 项：技能选择、模型/知识库选择、应用客户端和开发密钥客户端。加上此前共享 SSE，第 1–5 项现均完成。整体模块化尚未完成，后端剩余事项见末节。

| 公共入口 | 唯一请求实现 | 消费者 |
| --- | --- | --- |
| `agent_extension/public/skills.js` | 可选技能列表 | Chat 的 SkillPicker 与输入触发符处理 |
| `ai/public/resourceSelection.js` | 模型列表、可选知识库列表 | Agent 编辑器 |
| `ai/public/applications.js` | 应用列表、创建 | Agent 编辑器 |
| `ai/public/developerKeys.js` | 密钥列表、创建、轮换、撤销 | Agent API 抽屉；原 AI 管理 API 兼容调用 |

原 API 文件直接 re-export 公共实现，响应、Promise 与错误均透传。URL、参数、路径校验和重复提交配置保持原样。选择项、分页、密钥展示和文档辅助逻辑仍由所属模块实现，public 显式导出所需函数；没有复制这些函数或令 public 转发到旧 API。

四个消费组件仅变更导入，模板、权限判断、加载/竞态处理、草稿与密钥状态机均保留。公共代码入口不意味着新增公开 HTTP 接口或放宽权限。当前两个插件间的静态相对 import 均通过对方 public；业务依赖仍然存在。

## 验证结果

- 新公共入口测试先在缺少入口时失败。实现后确认 9 个迁移请求的新旧导出为同一函数，请求只发送一次，参数引用、Promise、响应及拒绝原因保持一致。
- 覆盖应用 ID 编码及无效 ID 不发请求、分页终止与错误、模型发布/启用判定、知识库错误、技能触发符。密钥列表归一化不暴露签发明文；签发结果单独提取，缺字段拒绝。
- `npm run test:plugin`：53 个测试文件通过，含 9/9 个 Node 子测试。原知识图谱 API 测试改为真实 ESM 加载，所有原行为断言保留。
- 隔离源码副本 `npm run build:docker`：退出码 0。
- `configurable_agent_check.py`：桌面与 390px 移动端通过；覆盖配置保存、应用创建、知识库错误恢复、多轮试聊、发布与草稿隔离、密钥创建/撤销及明文生命周期，共 86 个模拟请求。
- 定向运行现有浏览器函数 `check_skill_management_and_chat_picker`、`check_application_api_docs`、`check_application_usage_dark`，全部通过。
- 独立审查确认 9 个请求仅迁移位置、四个组件去除 import 后内容一致，未发现本轮引入的阻断问题；公共客户端与知识图谱 API 两项测试独立复验通过。
- `git diff --check` 与文档链接检查通过；仓库原 dist 的 1,460 个文件 SHA-256 与本轮开始时相同，临时 Vite 服务已停止。

复验（前端目录）：

```bash
node tests/plugins/publicClients.test.js
node tests/plugins/knowledgeGraphApi.test.js
npm run test:plugin
AGENT_CHECK_BASE_URL=http://127.0.0.1:46588 python3 -u tests/e2e/configurable_agent_check.py
```

技能/应用定向浏览器检查从 `tests/e2e/platform_shell_check.py` 导入上述三个函数，在 Playwright Chromium 中顺序执行；使用 `PLATFORM_SHELL_BASE_URL` 指定同一临时地址。自行复验应替换为自己的测试服务地址。

前态源码、构建与浏览器日志保存于 `/tmp/zhiheng-public-clients-n974ihwj`。构建与 Vite 使用该目录下的 frontend 副本，未覆盖仓库 dist。浏览器使用模拟 API，未验证线上数据库或真实模型。

本轮未重新运行无关的平台权限落地整套截图检查，前轮发现的偶发超时仍需独立排查。依赖检查覆盖当前两个插件的静态相对导入，不代表穷尽动态导入或所有宿主/插件边界。

## 尚待完成

后端应用查询接口、模型删除引用规则、费率归属仍未实施。后端既有 Ruff 问题、浏览器测试稳定性和完整云端 CI 也未完成；不能将本轮前端验收表述为整体模块化或云端流水线全部完成。

改动尚未提交、推送或部署，运行服务未更新。[当前架构状态](../architecture/2026-09-29-module-boundaries.md)与[五步计划](../superpowers/plans/2026-09-29-frontend-public-boundaries.md)已同步更新。
