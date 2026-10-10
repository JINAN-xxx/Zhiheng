# 前端公共边界的逐步实施计划

**目标：** 按用户“逐步拆解”的要求，将前端跨插件依赖分成可独立验证的小项。第 1 项首先完成，第 2–5 项随后一并完成，见[后续实施记录](../../operations/2026-09-29-public-client-completion.md)。后端应用查询在前端这一组之后处理。

**架构：** 通用传输由平台共享层维护；技能、模型、知识库、应用和密钥业务仍由各自模块维护，通过公开入口消费。旧路径可以兼容转发，但公共实现不能反向依赖旧入口。

**技术栈：** JavaScript ESM、ReadableStream、Vue/Vite、Node 行为测试、Playwright 模拟 API 验收。

## 拆解顺序

| 小项 | 当前跨模块使用位置 | 目标与验收 |
| --- | --- | --- |
| 1：共享 SSE 解析 | Chat 的 `api/chat.js`；Work 的 `api/workTask.js` 直接引用 AI 私有 parser | 共享层唯一实现、旧路径兼容；分块、UTF-8、事件类型、错误与取消有回归 |
| 2：技能选择公共入口 | Chat 的 SkillPicker 使用扩展的 available API/normalizer，输入框使用技能触发符工具 | 由扩展模块公开可选技能和选择行为；管理 CRUD 留在技能模块；列表与触发符语义保持一致 |
| 3：模型与知识库选择入口 | AgentEditor 使用 AI 模型/知识库列表、可调用判定、分页收集及 normalizer | 由 AI 提供明确的选择契约；保留现有筛选、分页、返回字段与权限，不直接搬整个管理页面 API |
| 4：应用公共客户端 | AgentEditor 的应用列表/创建与数据归一化 | 明确应用查询与创建入口，保持请求体、错误、重复提交与权限语义 |
| 5：开发密钥公共客户端 | AgentApiDrawer 的列表/创建/撤销、状态与文档辅助函数 | 明确密钥操作契约及公开展示字段；保护新密钥只显示一次、路径编码、失败恢复和权限边界 |

后续后端应用查询、模型删除引用与费率归属分别独立实施，不能把它们与一次前端文件迁移捆绑验收。

## 当前完成状态

| 小项 | 状态 |
| --- | --- |
| 共享 SSE 解析 | 已完成，包含跨块 CRLF 和 reader 提前结束清理修正 |
| 技能选择 | 已完成，Chat 通过扩展模块 public 入口消费 |
| 模型与知识库选择 | 已完成，Agent 通过 AI public 入口消费 |
| 应用客户端 | 已完成，列表/创建定义由 public 入口拥有，旧 API 兼容 |
| 开发密钥客户端 | 已完成，列表/创建/轮换/撤销及展示工具通过 public 入口消费 |

前端这组五项完成不代表整体模块化完成。后端应用查询、模型引用与费率归属尚待处理，云端 CI 尚未运行。

## 首轮实施：共享 SSE 解析

选择平台共享 `src/utils/platformSse.js` 拥有 `parsePlatformSSE` 实现；AI 旧 `utils/sseParser.js` 仅 re-export。Chat 和 Work 都直接依赖共享入口。这仍是平台 JSON 事件协议，包括 `[DONE]` 的既有映射，不承诺完整通用 EventSource/reconnect/id/retry 功能。

不复制解析器到 Work，否则修复需要维护两份；不让公共文件转发到 AI 私有实现，否则依赖方向没有改善。保留业务 HTTP 请求、认证、错误映射、事件消费状态机和部署方式。

公开函数保持：

```javascript
export async function* parsePlatformSSE(source) { /* source: ReadableStream or byte iterable */ }
```

解析器必须正确处理任意字节边界的 UTF-8、LF/CRLF/CR、多行 JSON data、注释、默认事件及 EOF 尾帧。解析错误仍抛出既有公开错误，流读取/AbortError 继续向上传播。正常 EOF 释放 reader；提前结束或解析失败时尽力取消 reader 并释放锁，不让清理失败覆盖原错误。业务 error/done 事件由各业务消费者处理。

### 步骤与文件

- [x] 保存本轮前态与运行 dist 哈希，运行既有 parser 测试作为基线。
- [x] 扩展 `tests/plugins/sseParser.test.js`：先在旧实现复现跨块 CRLF 与提前退出清理问题，再迁到公共入口并验证旧导出身份相同。
- [x] 新增 `tests/plugins/platformStreamApi.test.js`：加载真实 Chat/Work API 和公共 parser，仅模拟认证、fetch、共享 request 与构建时 base URL；检查请求参数、取消 signal、事件和错误透传。
- [x] 新建 `src/utils/platformSse.js`；原 parser 转发；两个 API 修改导入，不改变自身 HTTP 行为。
- [x] 加限定依赖检查：Work 不再导入 AI 私有 SSE；共享 parser 不导入业务插件；两个实际流 API 消费共享入口。
- [x] 前端全部 `npm run test:plugin`、临时副本构建与 Chat/Work 浏览器验收通过。
- [x] 按 requesting-code-review 技能独立只读审查解析边界、资源清理与范围，同时完成本地验证。
- [x] 更新模块梳理和实施记录，检查差异、停止临时服务，保留现有运行 dist。

工作区：`/home/ja/project/zhiheng`，分支：`fix/platform-integration-review`。前序未提交改动继续保留；不提交、推送或部署本轮结果。
