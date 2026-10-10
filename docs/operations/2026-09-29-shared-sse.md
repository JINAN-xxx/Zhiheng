# 前端共享 SSE 解析实施记录

日期：2026-09-29。工作区：`/home/ja/project/zhiheng`，分支：`fix/platform-integration-review`。

## 本轮范围

用户同意逐步拆解后，前端公共边界分为五个小项，本轮完成共享 SSE 解析，后续依次处理技能选择、模型/知识库选择、应用管理及密钥操作。详见[实施计划](../superpowers/plans/2026-09-29-frontend-public-boundaries.md)。

共享实现位于 [platformSse.js](../../zhiheng-fastapi-frontend/src/utils/platformSse.js)。Chat 与 Work 的流 API 均直接导入此入口，旧 AI parser 文件只 re-export 同一个函数，保留原调用方兼容性。

两个业务 API 的其他代码保持原样。共享层只处理平台 JSON SSE 协议：默认事件、多行 data、JSON 对象/数组/原始值以及 `[DONE]` 的既有映射；不提供 EventSource 重连、id/retry 状态管理，也不处理 HTTP 认证或业务 error/done 状态。

## 发现并修正的既有问题

1. 原实现对每个解码块分别替换 CRLF。换行的 CR 和 LF 被拆开时会变成两个 LF，误判事件边界。红灯测试在第 23 个字节处分块时观察到 `content` 被解析成 `message`。现通过跨块记录配对的 LF 修正，并覆盖每个切分位置及逐字节输入，包括中文与 emoji。
2. 原实现提前退出时只释放 reader 锁，没有取消未结束的上游。现正常 EOF 只释放锁；提前结束、无效 JSON 或读取异常时尽力取消 reader 并释放锁。取消清理失败不覆盖原解析/读取异常；AbortError 与原读取异常继续向上传递。AsyncIterable 输入继续使用其原有 return/finally 清理机制。

## 验证

| 检查 | 结果 |
| --- | --- |
| 失败验证 | 在旧 parser 上先复现跨块 CRLF、提前结束和解析失败未取消 reader；公共入口测试先因入口缺失失败 |
| parser 行为 | 原有多行/尾帧断言保留；新增任意切分点、UTF-8、CR、正常 EOF、提前返回、无效 JSON、清理失败、读取失败、AbortError 及异步迭代器关闭检查通过 |
| 真实业务 API | 加载真实 Chat/Work ESM 模块和 parser，仅替换 fetch、认证、共享 request 与构建时 base URL；请求体、Authorization、signal、业务事件、HTTP 错误、空流和早退清理通过 |
| 前端回归 | `npm run test:plugin`：52 个测试文件通过，Node 子测试 9/9 |
| 构建 | 临时源码副本 `npm run build:docker` 退出码 0 |
| 浏览器 | 现有 `chat_work_mode_check.py` 的桌面与 390px 移动端验收通过，涵盖聊天/工作切换、创建失败恢复、两名智能体执行、历史与侧栏导航 |
| 独立复核 | 共享入口、HTTP 契约、换行状态、流清理及测试加载方式通过只读审查；两个目标测试独立复验通过 |
| 运行产物保护 | 仓库原 dist 的 1,460 个文件 SHA-256 前后相同 |
| 差异检查 | `git diff --check` 通过 |

复验命令（前端目录）：

```bash
node tests/plugins/sseParser.test.js
node tests/plugins/platformStreamApi.test.js
npm run test:plugin
CHAT_WORK_CHECK_BASE_URL=http://127.0.0.1:45302 python3 -u tests/e2e/chat_work_mode_check.py
```

本轮源码快照、红灯结果、构建和浏览器日志保存在 `/tmp/zhiheng-shared-sse-m_pnzaoq`。构建与临时 Vite 服务使用其中的 frontend 副本，未覆盖仓库 dist。端口仅为本轮验证地址，复验时使用自己的测试服务。

新增测试由现有 `test:plugin` 自动发现，Chat/Work 浏览器脚本已在前轮接入 CI。本轮不重复运行未涉及的平台权限落地与截图回归；前轮记录的偶发导航超时仍是独立的质量事项。浏览器使用模拟 API，没有验证真实上游模型或线上流量。

## 后续小项

下一步是技能选择公共入口：Chat 的技能列表、列表归一化和输入触发符逻辑仍直接引用扩展插件内部文件。之后再收拢 Agent 使用的模型/知识库选择、应用和密钥客户端；这一组完成后再处理后端应用查询。

本轮改动尚未提交、推送或部署，运行服务未更新。
