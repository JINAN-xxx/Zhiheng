# 退役字段与精确权限

运行时采用当前事实与明确操作权限，历史升级由冻结迁移负责。生产代码不解释旧角色别名，不保存仅为旧表约束传递的设置。

## 权限

permission_policy 的 MODULE_DEFINITIONS / PERMISSION_CODES 是后端 canonical 目录。普通主体检查模块 enabled、read 和具体 action；未知请求与旧聚合名不授权，管理员既有全权语义保持。前端 hasPermission 使用相同门禁，hasCapability/canUseCapability 显式传操作，不再翻译 access/manage/write。permissionAliases.ts 及运行时 legacy 转换器已删除。

真实OR/AND属于用例本身：模型配置查询允许 config_read/create/update/delete/toggle 的OR；货币报价允许费率read或该模型配置OR；文件用户目录同时要求storage read与system users read。本人账单与跨账户管理读取保持其原身份分支。写命令按实际动作检查：模型create/update，积分adjust/configure/reconcile，用户字段update/toggle/assign_roles，角色update/toggle/grant，公告update/publish/delete等。操作容器的OR不授权任意按钮或字段；确认回调和提交前再次检查实际操作。

0034_permission_tree冻结的历史转换保留，确保从旧库升级结果稳定；普通运行时及测试夹具不导入旧名转换器。Key scope / ResourceAction 的write是独立业务协议，仍同时受当前主体功能权限、owner与资源ACL约束。历史路由alias与角色权限是不同问题，未因权限退役删改已有书签路径。

模型新增、编辑或启用时可以查服务商目录。目录无法验证聊天模型并需试调用时，还必须有模型「试用与连接测试」权限。

MCP 服务管理页接受连接配置、工具授权、调用审计中的任一权限。连接地址和凭据状态只对连接配置人员可见，工具角色名单只对授权人员可见，调用记录由审计权限控制。开发接入调用 MCP 工具需要 MCP 使用权限、有效的应用发布及 Key 工具授权。

助手的本人会话重命名和删除各有权限，两项均依赖助手使用权限和本人会话所有权。对话体验的消息反馈写入单独授权。迁移 `0051_operation_permissions` 将旧角色原本具备的操作映射到对应权限。

## Schema0039

| 退役字段 | 当前事实来源 |
|---|---|
| developer_accounts.limit_tokens / quota_version | 累计额度API已退役；used_tokens/reserved_tokens保留为实际计量统计 |
| chat_context_snapshots.summary_version / estimated_tokens / input_budget / dropped_turns | 当前冻结执行配置与统计由agent_context中的ChatExecutionSpec及Run事实承载 |
| knowledge_graph_attempts.elapsed_seconds | 无生产读写；实际图任务/运行信息继续保留 |

0039物理删除这七列，将usage账户约束替换为used/reserved均非负；保留BIGINT计数、所有主键/外键、Run/Invocation/Hold/账本及确认回执。current spec.input_budget、来源/附件引用、摘要父子关系、解析切换history和技能所有权审计均保留。

旧token_limit不再透传准入、业务用例与计量层。唯一原实际用途是知识重排批次输入预算，现用knowledge_rerank_batch_tokens配置（CORE_KNOWLEDGE_RERANK_BATCH_TOKENS / ZHIHENG_KNOWLEDGE_RERANK_BATCH_TOKENS，1..48000）。原有效值max(1,min(48000,old_limit//2))在实例更新时显式换算，不保留运行时旧环境名适配。默认与当前实例均为48000；既有已冻结重排诊断预算继续按原值执行。

历史迁移原文不改写。本次删除的旧值不能从当前Schema重新构建，降级要求恢复完整备份；升级前应验证备份恢复、保护字段与计数、角色不扩权及checkpoint哈希。历史schema测试仅在core_test_独占数据库中补旧必填列默认，不构成生产兼容路径。
