# 新平台文档

日常开发以实际代码、当前代码规范和各域验收为准。知识库向量检索、LightRAG 图谱及后台管理已交付，正常实例数据库版本为 `0011_builtin_admin_role`，旧模型服务已恢复。MinerU 保留本机部署准备，按当前安排暂缓接入。

| 文档                                                                                 | 用途                                                                               |
| ------------------------------------------------------------------------------------ | ---------------------------------------------------------------------------------- |
| [项目说明与启动](../README.md)                                                       | 安装、配置、迁移、开发和验证命令                                                   |
| [当前代码结构](../代码结构说明.md)                                                   | 已实现目录、模块职责和依赖边界                                                     |
| [整体架构设计](superpowers/specs/2026-10-23-ai-core-platform-redesign-design.md)     | 新平台架构决策与后续能力规划                                                       |
| [代码规范](architecture/2026-10-23-code-standards.md)                                | Python、TypeScript、事务、异步与模块边界要求                                       |
| [首条 AI 链路计划](superpowers/plans/2026-10-23-first-ai-slice.md)                   | 本轮实现范围、接口契约和任务清单                                                   |
| [首条 AI 链路验收](superpowers/verification/2026-10-23-first-ai-slice.md)            | 已运行的验证、交付范围及未覆盖项                                                   |
| [功能对齐计划](superpowers/plans/2026-10-23-feature-parity.md)                       | 开发接入、技能、智能体、Work、文件与知识库的实际推进状态                           |
| [开发接入与模型管理验收](superpowers/verification/2026-10-23-developer-models.md)    | 本组 API、原页面、真实容器与数据升级的验证                                         |
| [技能模块计划](superpowers/plans/2026-10-23-skills.md)                               | 已完成的两阶段安装、提示词包与聊天选择实现                                         |
| [技能验收](superpowers/verification/2026-10-23-skills.md)                            | 安装、聊天冻结、原界面、迁移和真实容器验证                                         |
| [智能体计划](superpowers/plans/2026-10-23-agents.md)                                 | 已完成的草稿/发布、预览、公开调用及统一运行实现                                    |
| [智能体验收](superpowers/verification/2026-10-23-agents.md)                          | 版本快照、真实JSON/SSE、推理停止、原界面与数据升级                                 |
| [Work计划](superpowers/plans/2026-10-23-work.md)                                     | 已完成的持久任务、冻结成员、单成员执行、共享历史与取消                             |
| [Work验收](superpowers/verification/2026-10-23-work.md)                              | 原工作界面、真实接力/停止/失联收敛与数据升级                                       |
| [受管文件与聊天附件验收](superpowers/verification/2026-10-23-files.md)               | 原文件治理、真实解析/图片链路、安装包、迁移和正常实例验证                          |
| [知识库索引与检索验收](superpowers/verification/2026-10-27-knowledge.md)             | Milvus、来源权限、四种 AI 组合、消息角色修复及正常实例升级                         |
| [正式模型服务恢复](superpowers/verification/2026-10-27-model-service-restoration.md) | 旧服务连接与四个模型恢复、真实供应商调用和原页面复查                               |
| [LightRAG 图谱验收](superpowers/verification/2026-10-27-lightrag.md)                 | 真实 SDK、原图谱页面、来源权限、费用、回归限制与正常实例升级                       |
| [LightRAG 接入设计](superpowers/specs/2026-10-27-lightrag-design.md)                 | 图存储、受治理回调、版本、聚合来源权限与原图谱页面的实施边界                       |
| [LightRAG 可行性实验](superpowers/plans/2026-10-27-lightrag-feasibility.md)          | 正式接入前验证真实 SDK、无 DDL 角色、单槽回调与取消收尾                            |
| [服务恢复与健康检查验收](superpowers/verification/2026-10-26-service-recovery.md)    | Redis 原数据恢复、真实就绪状态、配置增量审查与正常实例复测                         |
| [文件、知识库、图谱与MinerU计划](superpowers/plans/2026-10-23-files-knowledge.md)    | 受管字节、真实解析/索引/检索、版本化图谱与解析连接的后续分组                       |
| [原前端界面复用计划](superpowers/plans/2026-10-23-preserve-frontend-ui.md)           | 已完成登录、工作台、聊天、模型和用量界面适配及部署；验证见 AI 链路验收中的复验记录 |
| [部署说明](../deploy/core/README.md)                                                 | 新实例启动、健康检查和数据备份要求                                                 |

旧项目的插件、LiteLLM、旧页面和旧实例发布文档已移出当前文档树，历史版本可从 Git 查询。未提交文档、旧设计预览和重复前端目录已在仓库外保存归档，位置见[旧源码归档说明](../legacy/README.md)。早期验收记录中的路径描述反映当时状态，当前归档位置以清单为准。

`legacy/` 保留旧源码及原有未提交优化，供后续按新边界提取业务能力。运行数据、虚拟环境、缓存和历史 Git 工作树属于本地工作资料，不作为新平台业务目录；可通过本地 VS Code 的 `files.exclude` 设置隐藏这些目录。

## 当前控制台 UI

- [控制台 UI 接入计划](superpowers/plans/2026-10-28-console-ui.md)
- [2026-10-28 控制台 UI 验收](superpowers/verification/2026-10-28-console-ui.md)

- [消息操作与源码运行验收](superpowers/verification/2026-10-28-message-actions.md)
- [知识库与存储入口整合验收](superpowers/verification/2026-10-28-knowledge-storage.md)
- [工作空间独立入口验收](superpowers/verification/2026-10-28-work-isolation.md)
- [后台管理恢复验收](superpowers/verification/2026-10-28-admin-console.md)
- [内置角色与账号菜单入口](superpowers/verification/2026-10-29-builtin-role.md)
- [统一白色界面与关闭主题切换](superpowers/verification/2026-10-29-white-console.md)
- [历史记录弹窗与模型生成设置](superpowers/verification/2026-10-29-chat-history-settings.md)
- [工作记录弹窗](superpowers/verification/2026-10-29-work-history-modal.md)
- [登录页设计与验收](superpowers/verification/2026-10-29-login-ui.md)
- [个人资料与全平台界面一致性](superpowers/verification/2026-10-29-profile-and-ui-consistency.md)
