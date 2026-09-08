# 智能体扩展插件

该插件提供智衡平台的“智能体扩展”菜单、可配置智能体和平台共享技能管理。智能体可配置模型、系统提示词、技能和知识库绑定，保存草稿、试聊并发布为带密钥的 API。知识库检索由独立业务模块通过预留接口接入。

## 功能

- 创建、修改、试聊、发布、取消发布和删除自己的智能体，管理员可管理全部智能体。
- 草稿与已发布配置隔离，发布时固定配置；所选技能按调用时的启用状态和当前内容解析。
- 可配置最长 2000 字符的欢迎语，在试聊开场展示，清空对话后恢复；欢迎语不作为模型消息发送。
- 新建和编辑智能体使用居中配置弹窗，支持手机布局。
- 复用开发接入应用的密钥、模型授权、额度和用量记录。
- 管理员上传并确认安装 `SKILL.md` 或 ZIP 技能包。
- 查看技能列表、详情和主文件内容。
- 启用、停用和删除非内置技能。
- 向普通用户提供已启用技能列表。
- 为 AI 对话解析当前消息选择的技能上下文。
- 启动时同步“深度研究”和“文档总结”两个内置纯提示词技能。

## 接口

智能体管理前缀为 `/agent_extension/agents`：集合支持 `GET` 和 `POST`，`/{agent_id}` 支持 `GET`、`PUT` 和 `DELETE`；`/{agent_id}/publish`、`/{agent_id}/unpublish`、`/{agent_id}/preview` 使用 `POST`。修改、发布和取消发布均携带 `expectedVersion`。

对外接口为 `POST /v1/agents/{agent_id}/chat/completions`，请求仅接受 `messages` 和 `stream`。密钥必须属于已发布配置绑定的应用，并拥有其模型授权。试聊使用登录态，当前返回非流式结果；对外调用支持 JSON 和 SSE。

密钥沿用应用和模型授权范围，可调用同一应用绑定的其他智能体，以及其模型授权允许的已有接口。取消发布停止该智能体的新调用；撤销密钥则会使其在整个应用中失效。

知识库绑定为空时可直接调用；有绑定但检索服务未接入时返回 `AGENT_KNOWLEDGE_UNAVAILABLE`，不会静默忽略知识库。接入契约见 [知识库对接文档](../../../docs/agent-knowledge-integration.md)。

接口前缀为 `/agent_extension/skills`：

- `GET /agent_extension/skills`
- `GET /agent_extension/skills/available`
- `GET /agent_extension/skills/{skill_id}`
- `GET /agent_extension/skills/{skill_id}/content`
- `POST /agent_extension/skills/import/prepare`
- `POST /agent_extension/skills/import/{draft_id}/confirm`
- `PUT /agent_extension/skills/{skill_id}/status`
- `DELETE /agent_extension/skills/{skill_id}`

## 权限

- `agent_extension:agent:list`、`query`、`add`、`edit`、`remove`、`publish`、`test`，均使用完整 `agent_extension:agent:` 前缀。
- 选择技能还需要 `agent_extension:skill:use`；页面选择模型需要 `ai:model:list`；选择或创建 API 应用及管理密钥使用现有 `ai:application:*` 权限。
- `agent_extension:skill:list`
- `agent_extension:skill:query`
- `agent_extension:skill:add`
- `agent_extension:skill:edit`
- `agent_extension:skill:remove`
- `agent_extension:skill:use`

## 存储

智能体配置和发布快照存入 `agent_configuration`。插件版本为 `0.2.1`，通过 `002_agent_management.sql` 建表、`003_agent_welcome_message.sql` 增加欢迎语字段，同时支持 MySQL 和 PostgreSQL；旧记录及缺少欢迎语字段的旧发布快照默认使用空字符串。现有安装需先按项目插件升级流程预演并升级 `agent_extension`，同步菜单和权限，再重启后端和部署前端。插件依赖已启用的 `ai >= 0.8.0`；对外调用还需启用原有 Developer API 配置。

默认存储目录是 `data/agent-skills`，可用 `AGENT_SKILL_DATA_DIR` 覆盖。Docker Compose 将仓库根目录的 `data/agent-skills` 挂载到后端 `/app/data/agent-skills`。

技能包校验上传大小、解压大小、文件数量、路径穿越、链接文件和主文件元数据。插件不会执行包内脚本，也不会自动安装依赖。

## 开发验证

```bash
pytest -q tests/plugins/agent_extension tests/plugins/sample_plugins/test_agent_extension_plugin.py
ruff check plugins/agent_extension tests/plugins/agent_extension
```
