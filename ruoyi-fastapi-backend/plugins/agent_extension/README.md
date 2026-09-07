# 智能体扩展插件

该插件提供智衡平台的“智能体扩展”统一菜单和平台共享技能管理。第一期只交付技能管理；知识库、工具和 MCP 由独立业务模块后续接入同一菜单清单。

## 功能

- 管理员上传并确认安装 `SKILL.md` 或 ZIP 技能包。
- 查看技能列表、详情和主文件内容。
- 启用、停用和删除非内置技能。
- 向普通用户提供已启用技能列表。
- 为 AI 对话解析当前消息选择的技能上下文。
- 启动时同步“深度研究”和“文档总结”两个内置纯提示词技能。

## 接口

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

- `agent_extension:skill:list`
- `agent_extension:skill:query`
- `agent_extension:skill:add`
- `agent_extension:skill:edit`
- `agent_extension:skill:remove`
- `agent_extension:skill:use`

## 存储

默认存储目录是 `data/agent-skills`，可用 `AGENT_SKILL_DATA_DIR` 覆盖。Docker Compose 将仓库根目录的 `data/agent-skills` 挂载到后端 `/app/data/agent-skills`。

技能包校验上传大小、解压大小、文件数量、路径穿越、链接文件和主文件元数据。插件不会执行包内脚本，也不会自动安装依赖。

## 开发验证

```bash
pytest -q tests/plugins/agent_extension tests/plugins/sample_plugins/test_agent_extension_plugin.py
ruff check plugins/agent_extension tests/plugins/agent_extension
```
