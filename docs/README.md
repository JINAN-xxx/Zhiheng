# 新平台文档

日常开发以实际代码、当前代码规范和首条 AI 链路验收为准。整体架构设计包含后续目标，不能把其中的知识库、Agent、Work 等规划视为已经实现。

| 文档 | 用途 |
| --- | --- |
| [项目说明与启动](../README.md) | 安装、配置、迁移、开发和验证命令 |
| [当前代码结构](../代码结构说明.md) | 已实现目录、模块职责和依赖边界 |
| [整体架构设计](superpowers/specs/2026-10-23-ai-core-platform-redesign-design.md) | 新平台架构决策与后续能力规划 |
| [代码规范](architecture/2026-10-23-code-standards.md) | Python、TypeScript、事务、异步与模块边界要求 |
| [首条 AI 链路计划](superpowers/plans/2026-10-23-first-ai-slice.md) | 本轮实现范围、接口契约和任务清单 |
| [首条 AI 链路验收](superpowers/verification/2026-10-23-first-ai-slice.md) | 已运行的验证、交付范围及未覆盖项 |
| [原前端界面复用计划](superpowers/plans/2026-10-23-preserve-frontend-ui.md) | 已完成登录、工作台、聊天、模型和用量界面适配及部署；验证见 AI 链路验收中的复验记录 |
| [部署说明](../deploy/core/README.md) | 新实例启动、健康检查和数据备份要求 |

旧项目的插件、LiteLLM、旧页面和旧实例发布文档已移出当前文档树，历史版本可从 Git 查询。未提交文档、旧设计预览和重复前端目录已在仓库外保存归档，位置见[旧源码归档说明](../legacy/README.md)。早期验收记录中的路径描述反映当时状态，当前归档位置以清单为准。

`legacy/` 保留旧源码及原有未提交优化，供后续按新边界提取业务能力。运行数据、虚拟环境、缓存和历史 Git 工作树属于本地工作资料，不作为新平台业务目录；可通过本地 VS Code 的 `files.exclude` 设置隐藏这些目录。
